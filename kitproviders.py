"""kitproviders — the swappable parts, behind one small interface each.

Nothing in the kit should assume a specific tool. qmd is very good and it is also a choice: on a
managed machine the package registry may be blocked, and in a year there may be something better.
So the pieces that touch the outside world sit in slots:

    search   find notes for a question          qmd · naive
    llm      answer from an OpenAI-compatible endpoint   openai-compat
    embed    vectors for semantic search        qmd
    graph    turn the vault into nodes+edges    builtin (kitgraph)

The search slot is the one you choose, first hit wins:

    explicit argument  ->  KIT_SEARCH_PROVIDER env  ->  `providers.search` in .kit/config.yml  ->  auto

`auto` picks the first provider that reports itself available, in the order registered. That is what
makes a blocked registry survivable: without qmd the search slot falls back to `naive`, which needs
nothing but the files, and the rest of the kit carries on. The llm slot is chosen by URL
(`KIT_LLM_BASE_URL`); embed and graph have one implementation each so far.

Adding a provider means adding a class here and registering it. It does not mean touching kit.py.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import kitlib

CONFIG_REL = Path(".kit") / "config.yml"
DEFAULT_COLLECTION = "vault"
QMD_TIMEOUT = 120.0

STOPWORDS = {
    "the", "and", "for", "with", "what", "why", "how", "who", "which", "when", "where", "did", "does",
    "was", "were", "are", "our", "this", "that", "from", "into", "about",
    "der", "die", "das", "und", "ist", "wie", "wer", "mit", "von", "für", "auf", "ein", "eine", "nicht", "wird",
}


@dataclass
class Hit:
    file: str
    title: str
    snippet: str
    score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"file": self.file, "title": self.title, "snippet": self.snippet, "score": self.score}


# ---------------------------------------------------------------- http

_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_loopback(url: str) -> bool:
    """True when `url` points at this machine — the only destination the kit talks to by default."""
    host = urllib.parse.urlsplit(url).hostname or ""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def urlopen(target: "str | urllib.request.Request", timeout: float):
    """`urlopen`, with the proxy switched off for loopback addresses.

    urllib routes every request through HTTP(S)_PROXY unless NO_PROXY names the host, and a managed
    machine sets those variables. Without this, a running LM Studio on 127.0.0.1 reads as "not
    reachable" and the prompt body — note content — is sent to the corporate proxy instead.
    """
    url = target.full_url if isinstance(target, urllib.request.Request) else target
    # An endpoint is an HTTP endpoint. urllib also speaks file: and ftp:, so a base URL that is
    # mistyped or copied from somewhere else could make this read a local file and report it as a
    # model answer; refuse the scheme rather than discover that later.
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"endpoint must be http or https, not {scheme or 'a relative URL'}: {url}")
    if is_loopback(url):
        return _DIRECT.open(target, timeout=timeout)
    return urllib.request.urlopen(target, timeout=timeout)  # nosec B310 - scheme checked above


def proxy_vars() -> list[str]:
    """Which proxy environment variables are set — the row `doctor` prints when any of them is."""
    return [v for v in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
            if os.environ.get(v)]


# ---------------------------------------------------------------- config

def read_config(vault: Path | None) -> dict[str, Any]:
    """The vault's `.kit/config.yml` as a mapping — {} when it is missing, empty or not a mapping."""
    if not vault:
        return {}
    path = Path(vault) / CONFIG_REL
    if not path.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a broken config must not stop the CLI working
        return {}
    return data if isinstance(data, dict) else {}


def load_config(vault: Path | None) -> dict[str, Any]:
    """Provider choices from the vault's `.kit/config.yml`, if it has one.

    `providers:` with nothing under it, or a scalar, is a config someone half-edited — it must read
    as "no choice made", not crash the command that asked.
    """
    providers = read_config(vault).get("providers")
    return providers if isinstance(providers, dict) else {}


def collection_name(vault: Path | None, explicit: str | None = None) -> str:
    """The qmd collection to query: explicit > what `init` wrote into the vault > the init default.

    Without this the query is unscoped and hits from unrelated collections end up in the prompt.
    """
    if explicit:
        return explicit
    configured = read_config(vault).get("collection")
    return str(configured) if isinstance(configured, (str, int)) and str(configured).strip() else DEFAULT_COLLECTION


def _choice(slot: str, explicit: str | None, vault: Path | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get(f"KIT_{slot.upper()}_PROVIDER")
    if env:
        return env
    want = load_config(vault).get(slot)
    return str(want) if want else "auto"           # a key with no value means "no choice made"


# ---------------------------------------------------------------- search

class SearchProvider(Protocol):
    name: str

    def available(self) -> str | None:
        """Return a reason string when unusable, None when ready."""

    def query(self, vault: Path, question: str, n: int, collection: str | None) -> list[Hit]:
        ...


def _qmd_timeout() -> float:
    """Seconds to wait for `qmd query`. A junk or non-positive KIT_QMD_TIMEOUT means the default,
    never "wait forever" — the unbounded wait is the failure this exists to prevent."""
    try:
        want = float(os.environ.get("KIT_QMD_TIMEOUT") or QMD_TIMEOUT)
    except ValueError:
        return QMD_TIMEOUT
    return want if want > 0 else QMD_TIMEOUT


class QmdSearch:
    name = "qmd"
    blurb = "hybrid BM25 + vectors + rerank, on-device"

    def binary(self) -> str | None:
        # resolved path, never the bare name: Windows CreateProcess only appends .exe and would
        # miss the qmd.cmd shim that `npm install -g` writes
        return shutil.which("qmd")

    def available(self) -> str | None:
        return None if self.binary() else "not installed — bun install -g @tobilu/qmd"

    def query(self, vault: Path, question: str, n: int, collection: str | None) -> list[Hit]:
        exe = self.binary()
        if not exe:
            return []
        cmd = [exe, "query", question, "--format", "json", "-n", str(n)]
        if collection:
            cmd += ["-c", collection]
        timeout = _qmd_timeout()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            # qmd downloads its rerank model on first use; on a blocked network that never finishes,
            # and an unbounded wait here means no output, no fallback and nothing to read.
            print(f"qmd did not answer within {timeout:g}s — falling back to plain search "
                  f"(set KIT_QMD_TIMEOUT to change)")
            return []
        except OSError:
            return []
        if r.returncode != 0 or not r.stdout.strip():
            return []
        try:
            raw = json.loads(r.stdout)
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, list):
            return []
        return [Hit(h.get("file", ""), h.get("title", ""), h.get("snippet", ""), h.get("score")) for h in raw]


def hit_rel(file: str) -> str:
    """Vault-relative path of a search hit.

    qmd reports `qmd://<collection>/<relpath>` and appends `?index=<name>` for a non-default index;
    everything downstream — the graph, the sensitivity check — speaks vault-relative paths.
    """
    if file.startswith("qmd://"):
        file = file.split("/", 3)[-1].split("?")[0]
    return file


class NaiveSearch:
    name = "naive"
    blurb = "term frequency over the files; no index, no dependencies"

    def available(self) -> str | None:
        return None  # always: it is the floor the kit stands on

    def query(self, vault: Path, question: str, n: int, collection: str | None) -> list[Hit]:
        terms = [t.lower() for t in re.findall(r"\w+", question) if len(t) > 2 and t.lower() not in STOPWORDS]
        if not terms:
            return []
        phrase = " ".join(terms)
        scored: list[Hit] = []
        for note in kitlib.load_vault(vault):
            if note.path.name in kitlib.RESERVED_FILENAMES or note.rel.startswith("90-templates"):
                continue
            text = note.body.lower()
            title = str((note.frontmatter or {}).get("title", note.path.stem)).lower()
            score = sum(text.count(t) for t in terms) + 5 * text.count(phrase) + (10 if phrase in title else 0)
            if not score:
                continue
            idx = min((text.find(t) for t in terms if t in text), default=0)
            scored.append(Hit(note.rel, (note.frontmatter or {}).get("title", note.path.stem),
                              note.body[max(0, idx - 200): idx + 400].strip(), score))
        scored.sort(key=lambda h: -(h.score or 0))
        return scored[:n]


SEARCH_PROVIDERS: list[Any] = [QmdSearch(), NaiveSearch()]


# ---------------------------------------------------------------- llm

ANSWER_TOKENS = 800          # how much answer the kit asks for by default
REASONING_HEADROOM = 512     # what a reasoning model spends before the answer starts


def reasoning_headroom() -> int:
    """Tokens every request carries on top of the answer budget the caller asked for.

    A reasoning model emits its chain of thought into the same `max_tokens` pool as the answer, so a
    budget sized for the answer alone is spent before the answer begins and the server returns
    `finish_reason: length` with empty content. The headroom costs nothing on a model that does not
    think: `max_tokens` is a ceiling, not a target, and a plain model stops when it is done.
    `KIT_LLM_REASONING_TOKENS=0` removes it; a junk or negative value reads as the default.
    """
    try:
        want = int(os.environ.get("KIT_LLM_REASONING_TOKENS") or REASONING_HEADROOM)
    except ValueError:
        return REASONING_HEADROOM
    return want if want >= 0 else REASONING_HEADROOM


@dataclass
class ChatResult:
    """One completion, with the parts an OpenAI-compatible server keeps apart.

    `reasoning` is the model's chain of thought — LM Studio puts it in `reasoning_content`, other
    servers in `reasoning`. It is never the answer and must never be shown as one.
    """

    content: str = ""
    reasoning: str = ""
    finish_reason: str = ""
    budget: int = 0

    @property
    def out_of_budget(self) -> bool:
        """The budget ran out before any answer appeared — on a reasoning model, inside the thinking."""
        return self.finish_reason == "length" and not self.content.strip()

    def budget_hint(self) -> str:
        """One line naming what happened and the budget it happened under; empty when nothing did."""
        if not self.out_of_budget:
            return ""
        return (f"the model spent its whole {self.budget}-token budget thinking and produced no answer "
                f"— raise KIT_LLM_REASONING_TOKENS (now {reasoning_headroom()}) or turn the model's "
                f"thinking toggle off")


class OpenAICompatLLM:
    """Every endpoint we care about speaks this: LM Studio, llama.cpp's llama-server, Ollama, vLLM.

    Which is why the kit has no opinion about which one you run — it has an opinion about the URL.
    """

    name = "openai-compat"
    blurb = "any /v1/chat/completions endpoint"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def available(self) -> str | None:
        try:
            self.models()
            return None
        except Exception as exc:  # noqa: BLE001
            return f"{self.base_url} not reachable ({exc.__class__.__name__})"

    def models(self, timeout: float = 5.0) -> list[str]:
        with urlopen(f"{self.base_url}/models", timeout=timeout) as r:
            return [m.get("id", "") for m in json.load(r).get("data", []) if m.get("id")]

    def complete(self, model: str, messages: list[dict], temperature: float = 0.2,
                 max_tokens: int = ANSWER_TOKENS) -> ChatResult:
        """Ask for `max_tokens` worth of *answer*; the request budgets that plus thinking headroom."""
        budget = max_tokens + reasoning_headroom()
        payload: dict[str, Any] = {"messages": messages, "temperature": temperature,
                                   "max_tokens": budget, "stream": False}
        if not model:
            ids = self.models()
            model = ids[0] if ids else ""
        if model:
            payload["model"] = model
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {os.environ.get('KIT_LLM_API_KEY', 'local')}"})
        with urlopen(req, timeout=int(os.environ.get("KIT_LLM_TIMEOUT", "180"))) as r:
            data = json.load(r)
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"no completion in the response from {self.base_url}")
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        return ChatResult(content=str(message.get("content") or ""),
                          reasoning=str(message.get("reasoning_content") or message.get("reasoning") or ""),
                          finish_reason=str(choice.get("finish_reason") or ""),
                          budget=budget)

    def chat(self, model: str, messages: list[dict], temperature: float = 0.2,
             max_tokens: int = ANSWER_TOKENS) -> str:
        """The answer alone. Callers that need to diagnose a missing one use `complete`."""
        return self.complete(model, messages, temperature, max_tokens).content


# ---------------------------------------------------------------- resolution

def search_provider(vault: Path | None = None, explicit: str | None = None):
    """The search provider to use, honouring explicit > env > vault config > auto."""
    want = _choice("search", explicit, vault)
    if want != "auto":
        for p in SEARCH_PROVIDERS:
            if p.name == want:
                return p
        raise SystemExit(f"unknown search provider {want!r} (have: {', '.join(p.name for p in SEARCH_PROVIDERS)})")
    for p in SEARCH_PROVIDERS:
        if p.available() is None:
            return p
    return SEARCH_PROVIDERS[-1]


def llm_provider(base_url: str) -> OpenAICompatLLM:
    return OpenAICompatLLM(base_url)


def status(vault: Path | None, base_url: str) -> list[tuple[str, str, str]]:
    """(slot, provider, state) for every slot — what `doctor` prints."""
    rows: list[tuple[str, str, str]] = []
    active = None
    try:
        active = search_provider(vault)
    except SystemExit as exc:
        # doctor exists to surface a misconfiguration; a bad provider name is a row, not the end
        rows.append(("search", _choice("search", None, vault), str(exc)))
    for p in SEARCH_PROVIDERS:
        why = p.available()
        mark = "active" if p is active else ("ready" if why is None else why)
        rows.append(("search" if p is active else "", p.name, mark))
    llm = llm_provider(base_url)
    rows.append(("llm", llm.name, llm.available() or f"active — {base_url}"))
    rows.append(("embed", "qmd", QmdSearch().available() or "active"))
    rows.append(("graph", "builtin", "active"))
    return rows
