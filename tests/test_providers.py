"""Provider slots: selection order, and the fallback that keeps a blocked machine usable."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from tests.helpers import VAULT, temp_vault

import kitgraph
import kitproviders as kp


class Selection(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.pop("KIT_SEARCH_PROVIDER", None)

    def tearDown(self):
        os.environ.pop("KIT_SEARCH_PROVIDER", None)
        if self.saved is not None:
            os.environ["KIT_SEARCH_PROVIDER"] = self.saved

    def test_explicit_wins_over_everything(self):
        self.assertEqual(kp.search_provider(VAULT, "naive").name, "naive")

    def test_env_overrides_auto(self):
        os.environ["KIT_SEARCH_PROVIDER"] = "naive"
        self.assertEqual(kp.search_provider(VAULT).name, "naive")

    def test_vault_config_overrides_auto(self):
        v = temp_vault()
        try:
            (v / ".kit").mkdir(exist_ok=True)
            (v / ".kit" / "config.yml").write_text("providers:\n  search: naive\n", encoding="utf-8",
                                                   newline="\n")
            self.assertEqual(kp.search_provider(v).name, "naive")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_broken_config_does_not_break_the_cli(self):
        v = temp_vault()
        try:
            (v / ".kit").mkdir(exist_ok=True)
            (v / ".kit" / "config.yml").write_text("providers: [this is not a mapping\n", encoding="utf-8",
                                                   newline="\n")
            self.assertIsNotNone(kp.search_provider(v).name)
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)

    def test_unknown_provider_is_an_error_not_a_silent_default(self):
        with self.assertRaises(SystemExit):
            kp.search_provider(VAULT, "nope")

    def test_auto_never_returns_an_unusable_provider(self):
        p = kp.search_provider(VAULT)
        self.assertIsNone(p.available(), f"auto picked {p.name}, which reports: {p.available()}")


class NaiveIsTheFloor(unittest.TestCase):
    """The point of the slot: a machine that cannot install qmd still gets a working vault."""

    def test_always_available(self):
        self.assertIsNone(kp.NaiveSearch().available())

    def test_finds_the_note_a_question_is_about(self):
        hits = kp.NaiveSearch().query(Path(VAULT), "what is blocking the pilot", 5, None)
        self.assertTrue(hits, "no hits at all")
        self.assertTrue(any("search-relaunch" in h.file for h in hits),
                        f"expected the project note, got {[h.file for h in hits]}")

    def test_hits_carry_what_the_prompt_builder_needs(self):
        hit = kp.NaiveSearch().query(Path(VAULT), "hybrid search", 1, None)[0]
        self.assertTrue(hit.file and hit.title and hit.snippet)
        self.assertEqual(set(hit.as_dict()), {"file", "title", "snippet", "score"})


class QmdSlot(unittest.TestCase):
    def test_reports_a_reason_when_missing(self):
        q = kp.QmdSearch()
        if q.available() is None:
            self.assertTrue(Path(q.binary()).is_absolute(), "qmd must be spawned by resolved path")
        else:
            self.assertIn("bun install", q.available())


class Status(unittest.TestCase):
    def test_every_slot_is_reported(self):
        slots = {s for s, _, _ in kp.status(VAULT, "http://127.0.0.1:9/v1") if s.strip()}
        self.assertEqual(slots, {"search", "llm", "embed", "graph"})


class ConfigThatSomeoneHalfEdited(unittest.TestCase):
    """`providers:` with nothing under it is one deleted line away from the documented sample."""

    def vault_with(self, config: str) -> Path:
        v = temp_vault()
        (v / ".kit").mkdir(exist_ok=True)
        (v / ".kit" / "config.yml").write_text(config, encoding="utf-8", newline="\n")
        self.addCleanup(shutil.rmtree, v.parent, ignore_errors=True)
        return v

    def test_empty_providers_key_resolves_instead_of_crashing(self):
        v = self.vault_with("providers:\n")
        self.assertEqual(kp.load_config(v), {})
        self.assertIsNotNone(kp.search_provider(v).name)

    def test_scalar_providers_key_resolves_instead_of_crashing(self):
        v = self.vault_with("providers: naive\n")
        self.assertEqual(kp.load_config(v), {})
        self.assertIsNotNone(kp.search_provider(v).name)

    def test_a_slot_with_no_value_reads_as_auto(self):
        v = self.vault_with("providers:\n  search:\n")
        self.assertIsNone(kp.search_provider(v).available(),
                          "an empty search: must mean 'choose for me', not the provider named None")

    def test_status_reports_an_unknown_provider_as_a_row(self):
        v = self.vault_with("providers:\n  search: qdm\n")
        rows = kp.status(v, "http://127.0.0.1:9/v1")          # doctor must survive to print its table
        search = [r for r in rows if r[0] == "search"]
        self.assertTrue(search, rows)
        self.assertIn("qdm", search[0][1])
        self.assertIn("unknown search provider", search[0][2])
        self.assertEqual({s for s, _, _ in rows if s.strip()}, {"search", "llm", "embed", "graph"})


class CollectionScope(unittest.TestCase):
    """`llm ask` must query the collection `init` registered, not everything qmd has ever indexed."""

    def test_defaults_to_the_name_init_uses(self):
        self.assertEqual(kp.collection_name(None), kp.DEFAULT_COLLECTION)
        self.assertEqual(kp.collection_name(VAULT), kp.DEFAULT_COLLECTION)

    def test_reads_what_init_wrote_into_the_vault(self):
        v = temp_vault()
        try:
            (v / ".kit").mkdir(exist_ok=True)
            (v / ".kit" / "config.yml").write_text('collection: "work-notes"\n', encoding="utf-8", newline="\n")
            self.assertEqual(kp.collection_name(v), "work-notes")
            self.assertEqual(kp.collection_name(v, "explicit"), "explicit")
        finally:
            shutil.rmtree(v.parent, ignore_errors=True)


class QmdHitIds(unittest.TestCase):
    def test_a_qmd_uri_becomes_a_vault_relative_path(self):
        self.assertEqual(kp.hit_rel("qmd://vault/07-knowledge/hybrid-search.md"), "07-knowledge/hybrid-search.md")
        self.assertEqual(kp.hit_rel("qmd://vault/07-knowledge/hybrid-search.md?index=other"),
                         "07-knowledge/hybrid-search.md")
        self.assertEqual(kp.hit_rel("07-knowledge/hybrid-search.md"), "07-knowledge/hybrid-search.md")

    def test_a_qmd_hit_resolves_to_a_graph_node(self):
        """The graph packs `llm ask --graph` attaches hang on this: a collection-prefixed id matches nothing."""
        g = kitgraph.build_graph(VAULT)
        nid = kitgraph.resolve_id(g, kp.hit_rel("qmd://vault/07-knowledge/hybrid-search.md"))
        self.assertEqual(nid, "07-knowledge/hybrid-search.md")


class QmdIsNotAllowedToHang(unittest.TestCase):
    """qmd downloads its rerank model on first use; a blocked network must not stall `llm ask`."""

    def test_query_passes_a_timeout_and_falls_back_on_one(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(kw)
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

        out = io.StringIO()
        with mock.patch.object(kp.QmdSearch, "binary", return_value="qmd"), \
             mock.patch.object(kp.subprocess, "run", fake_run), \
             mock.patch.dict(os.environ, {"KIT_QMD_TIMEOUT": "1"}), \
             contextlib.redirect_stdout(out):
            hits = kp.QmdSearch().query(Path(VAULT), "anything", 5, "vault")
        self.assertEqual(hits, [], "an empty result is what makes the naive fallback engage")
        self.assertEqual(seen.get("timeout"), 1.0)
        self.assertIn("falling back", out.getvalue())

    def test_a_bad_timeout_value_does_not_break_the_command(self):
        with mock.patch.dict(os.environ, {"KIT_QMD_TIMEOUT": "soon"}):
            self.assertEqual(kp._qmd_timeout(), kp.QMD_TIMEOUT)
        with mock.patch.dict(os.environ, {"KIT_QMD_TIMEOUT": "0"}):
            self.assertEqual(kp._qmd_timeout(), kp.QMD_TIMEOUT, "0 must not read as 'wait forever'")


class Models(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"data": [{"id": "stub-model"}]}).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass


class LoopbackIgnoresTheProxy(unittest.TestCase):
    """Managed machines set HTTP_PROXY; urllib then sends localhost to the proxy unless NO_PROXY says otherwise."""

    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), Models)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_port}/v1"

    def test_recognises_the_loopback_spellings(self):
        for url in ("http://localhost:1234/v1", "http://127.0.0.1:1234/v1", "http://[::1]:1234/v1"):
            self.assertTrue(kp.is_loopback(url), url)
        self.assertFalse(kp.is_loopback("http://api.example.com/v1"))

    def test_the_endpoint_is_reached_with_proxy_vars_set(self):
        dead = "http://127.0.0.1:1"                      # a proxy that answers nothing
        with mock.patch.dict(os.environ, {"HTTP_PROXY": dead, "HTTPS_PROXY": dead, "ALL_PROXY": dead},
                             clear=False):
            os.environ.pop("NO_PROXY", None); os.environ.pop("no_proxy", None)
            self.assertEqual(kp.proxy_vars()[:1], ["HTTPS_PROXY"])
            self.assertEqual(kp.OpenAICompatLLM(self.base).models(), ["stub-model"])
            # what the kit used to do: a plain opener, whose ProxyHandler reads the environment.
            # (urllib.request.urlopen caches one opener per process, so it cannot be used here —
            # an earlier test may have built it before these variables were set.)
            proxied = urllib.request.build_opener(urllib.request.ProxyHandler())
            with self.assertRaises(Exception):
                proxied.open(f"{self.base}/models", timeout=2)


class ReasoningModel(BaseHTTPRequestHandler):
    """A server that thinks before it answers, the way an OpenAI-compatible one reports it.

    The chain of thought arrives beside the answer — LM Studio calls the field `reasoning_content`,
    other servers `reasoning` — and is drawn from the same max_tokens pool. Under 200 tokens here
    the whole budget goes into thinking and the answer comes back empty with `finish_reason:
    length`, which is what the kit's documented default model does for real.
    """

    THINKING = "We need to reply with exactly the token and nothing else."

    def _send(self, body: dict):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        self._send({"data": [{"id": "thinker"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        self.server.last_payload = body
        enough = body.get("max_tokens", 0) >= 200
        field = "reasoning" if body.get("model") == "other-spelling" else "reasoning_content"
        self._send({"choices": [{"finish_reason": "stop" if enough else "length",
                                 "message": {"role": "assistant",
                                             "content": "KIT-OK." if enough else "",
                                             field: self.THINKING}}]})

    def log_message(self, *args):
        pass


class NoCompletion(ReasoningModel):
    def do_POST(self):
        self._send({"choices": []})


class ReasoningBudget(unittest.TestCase):
    """A reasoning model spends max_tokens on its own thinking before the answer starts."""

    def serve(self, handler=ReasoningModel) -> str:
        srv = HTTPServer(("127.0.0.1", 0), handler)
        srv.last_payload = {}
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.srv = srv
        return f"http://127.0.0.1:{srv.server_port}/v1"

    def setUp(self):
        self.llm = kp.OpenAICompatLLM(self.serve())

    def ask(self, model="thinker", **kw) -> kp.ChatResult:
        return self.llm.complete(model, [{"role": "user", "content": "hi"}], **kw)

    def test_the_request_budgets_thinking_on_top_of_the_answer(self):
        res = self.ask(max_tokens=64)
        self.assertEqual(self.srv.last_payload["max_tokens"], 64 + kp.reasoning_headroom())
        self.assertEqual(res.budget, 64 + kp.reasoning_headroom())
        self.assertEqual(res.finish_reason, "stop", "the headroom is what lets the answer through")

    def test_chain_of_thought_is_never_the_answer(self):
        res = self.ask(max_tokens=64)
        self.assertEqual(res.content, "KIT-OK.")
        self.assertEqual(res.reasoning, ReasoningModel.THINKING)
        self.assertNotIn("We need to reply",
                         self.llm.chat("thinker", [{"role": "user", "content": "hi"}]))

    def test_both_spellings_of_the_reasoning_field_are_understood(self):
        for model in ("thinker", "other-spelling"):          # reasoning_content · reasoning
            with self.subTest(model=model):
                self.assertEqual(self.ask(model, max_tokens=64).reasoning, ReasoningModel.THINKING)

    def test_a_budget_spent_on_thinking_is_diagnosed_not_returned_as_an_answer(self):
        with mock.patch.dict(os.environ, {"KIT_LLM_REASONING_TOKENS": "0"}):
            res = self.ask(max_tokens=20)
            hint = res.budget_hint()
        self.assertEqual(res.content, "")
        self.assertTrue(res.out_of_budget)
        self.assertIn("20-token budget", hint)
        self.assertIn("KIT_LLM_REASONING_TOKENS", hint)
        self.assertIn("thinking toggle", hint)

    def test_a_complete_answer_carries_no_hint(self):
        res = self.ask(max_tokens=64)
        self.assertFalse(res.out_of_budget)
        self.assertEqual(res.budget_hint(), "")

    def test_a_response_without_a_completion_is_an_error_the_cli_catches(self):
        llm = kp.OpenAICompatLLM(self.serve(NoCompletion))
        with self.assertRaises(ValueError):
            llm.complete("thinker", [{"role": "user", "content": "hi"}])

    def test_a_bad_headroom_value_does_not_break_the_command(self):
        with mock.patch.dict(os.environ, {"KIT_LLM_REASONING_TOKENS": "lots"}):
            self.assertEqual(kp.reasoning_headroom(), kp.REASONING_HEADROOM)
        with mock.patch.dict(os.environ, {"KIT_LLM_REASONING_TOKENS": "-1"}):
            self.assertEqual(kp.reasoning_headroom(), kp.REASONING_HEADROOM)
        with mock.patch.dict(os.environ, {"KIT_LLM_REASONING_TOKENS": "0"}):
            self.assertEqual(kp.reasoning_headroom(), 0, "0 must mean 'no headroom', not 'the default'")


class EndpointScheme(unittest.TestCase):
    """An endpoint is an HTTP endpoint. urllib also speaks file: and ftp:, and a base URL is the
    one field a user hand-edits -- a mistyped scheme must fail loudly, not read a local file and
    return its bytes as if a model had answered."""

    def test_non_http_schemes_are_refused(self):
        for url in ("file:///etc/passwd", "ftp://example.invalid/x", "/not/absolute"):
            with self.assertRaises(ValueError, msg=url) as ctx:
                kp.urlopen(url, timeout=1)
            self.assertIn("http", str(ctx.exception))

    def test_http_and_https_are_still_accepted(self):
        # reaching the network is not the point; getting past the scheme check is
        for url in ("http://127.0.0.1:9/v1/models", "https://127.0.0.1:9/v1/models"):
            try:
                kp.urlopen(url, timeout=0.2)
            except ValueError as exc:            # must never be the scheme guard
                self.assertNotIn("must be http", str(exc))
            except Exception:
                pass                              # a connection error is the expected outcome
