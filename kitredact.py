"""The redactor, in one place, because a second implementation is a second thing to attack.

Imported by `kit.py` (for `doctor --report`) and by `testkit/testkit.py` (for the tester's
report). Both make the same promise to the person who mails the file back, so both must keep
it with the same code.

Deliberately stdlib-only — no PyYAML, no kitlib. The test kit runs on machines where the
install is broken, which is the whole reason it exists; a redactor that needs a working
dependency tree is a redactor that is absent exactly when the report is most sensitive.
"""
from __future__ import annotations

import getpass
import re
from pathlib import Path

# Hostnames that disclose nothing, so the report keeps them instead of writing `<host>`. This is
# a redaction allowlist, never a bind address — `0.0.0.0` here means "if the user typed it, it is
# not a secret", and nothing in the kit listens on it.
_SAFE_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "host.docker.internal"}  # nosec B104 - an allowlist, nothing binds
# A path token may swallow a space, `,`, `;` or `)` only when the path demonstrably continues —
# another separator follows before the field ends. That keeps `LM Studio/models/x.gguf` and
# `a,b/x.gguf` single tokens that reduce to one basename, without eating the next column of the
# report or the endpoint that follows it.
_PATH_GOES_ON = r"[^,;\[\]\n]*[\\/]"
# `]` stays inside the URL token so a bracketed IPv6 host survives as one piece: cutting the
# token at `]` left `?api_key=...` outside every rule, and `[::1]` is a host _SAFE_HOSTS claims
# to support. The report wraps each URL in `[...]`, so the closing bracket comes off as trailing
# punctuation below instead.
_URL_OR_PATH = re.compile(
    r"(?P<url>[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s]+)"
    r"|(?<![\w.@\-/])(?P<hostport>[A-Za-z0-9][A-Za-z0-9.\-]*\.[A-Za-z0-9.\-]*:\d{2,5}(?:/[^\s\]]*)?)"
    # A path is what unambiguously walks a directory tree: absolute, a drive, a UNC share, `~`,
    # or an explicit `./` / `../`. A BARE `dir/file` is deliberately NOT matched: half the report
    # is install hints (`@tobilu/qmd`, `09-archive/**`, `docs/quickstart.md`) and treating those
    # as paths reduces them to a basename, which costs more than the disclosure it prevents.
    # The residual gap — a relative `--vault Client/notes` — is stated in ALPHA.md.
    r"|(?<![\w.~])(?P<path>(?:[A-Za-z]:[\\/]|\\\\[^\s\\/]|~?/|\.{1,2}[\\/])"
    rf"(?:[^\s\]\),;]"
    rf"|[ \t](?![ \t]*[A-Za-z][A-Za-z0-9+.\-]*://)(?={_PATH_GOES_ON})"
    rf"|[\),;](?=[^\s\[\]\n]*[\\/]))+)"
)
_TRAILING_PUNCT = "'\"),.;:]"


def _basename(token: str) -> str:
    """A filesystem path reduced to the part that identifies it and discloses nothing else."""
    token = token.replace("\\", "/").rstrip("/")
    return token.rpartition("/")[2] or "<path>"


def _redact_url(url: str) -> str:
    """Keep the shape of an endpoint, drop who and where it is.

    A base URL is the one report field a user hand-edits, so it carries whatever their site put
    there: basic-auth credentials in the userinfo, an API key in the query or the fragment, a
    tenant or site id in the path, and a hostname that on macOS is the owner's full name by
    default. Trying to keep the "useful" half of that is how it leaks, so for anything but a
    loopback address only the scheme and the port survive — enough to say "an OpenAI-compatible
    server on 1234", which is the diagnostic value. Loopback keeps its path: there is no site
    behind 127.0.0.1 to disclose, and `/v1` versus `/v1/openai` is a real misconfiguration.
    """
    scheme, _, rest = url.partition("://")
    netloc, slash, tail = rest.partition("/")
    host = netloc.rpartition("@")[2]                  # anything before @ is a credential
    port = host.rpartition(":")[2] if ":" in host and host.rpartition(":")[2].isdigit() else ""
    bare = host[: -len(port) - 1] if port else host
    shown_port = f":{port}" if port else ""
    if bare.lower() in _SAFE_HOSTS:
        return f"{scheme}://{bare}{shown_port}{slash}" + tail.split("?", 1)[0].split("#", 1)[0]
    return f"{scheme}://<host>{shown_port}" + ("/<path>" if slash or tail else "")


def _redact_user(text: str, names: set[str]) -> str:
    """Drop every token carrying the owner's name, not just an exact match of the login.

    A vault, a laptop and a model folder are named after the person rather than the account —
    `Tobias-Notizen`, `Tobias MacBook Pro`, `tobias.lueder`. Splicing out only the login leaves
    `<user>-Notizen`, which still names the owner, so the whole token goes and the separators
    inside a name are matched loosely. Over-redacting a row label costs a word; under-redacting
    costs the identity of whoever mailed us the report.
    """
    for name in names:
        parts = [re.escape(part) for part in re.split(r"[._\-\s]+", name) if part]
        if len("".join(parts)) < 3:        # a one- or two-character login matches half the report
            continue
        loose = r"[._\-\s]*".join(parts)
        text = re.sub(rf"[A-Za-z0-9._\-]*{loose}[A-Za-z0-9._\-]*", "<user>", text, flags=re.IGNORECASE)
    return text


def _scrub(text: str) -> str:
    """Strip the four things a diagnosis must not carry: credentials, hosts, paths, the owner.

    Model ids are the reason the path rule exists: llama-server reports the loaded file, so an id
    is often an absolute path under the user's home — sometimes as a `file://` URL, and often with
    a space in it, because `LM Studio` is the vendor's own default folder. The basename still
    identifies the model, which is what a diagnosis needs.
    """
    def one(m: re.Match) -> str:
        if m.group("hostport"):                        # `host:port/v1` typed without a scheme
            return _redact_url("http://" + m.group("hostport"))
        if not m.group("url"):
            return _basename(m.group("path"))
        url, trail = m.group("url"), ""
        while url and url[-1] in _TRAILING_PUNCT:      # the report quotes and brackets its URLs
            url, trail = url[:-1], url[-1] + trail
        if url.lower().startswith("file:"):            # a local file, not an endpoint: same rule as a path
            return _basename(url.partition("://")[2] or url.partition(":")[2]) + trail
        return _redact_url(url) + trail

    text = _URL_OR_PATH.sub(one, text)
    return _redact_user(text, {getpass.getuser(), Path.home().name})

def scrub(text: str) -> str:
    """Public name for the one redactor. See `_scrub`."""
    return _scrub(text)
