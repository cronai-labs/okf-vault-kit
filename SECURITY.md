# Security

**Reporting:** use GitHub's private vulnerability reporting — the **Security** tab of this
repository, then **Report a vulnerability**. It is enabled here. Please do not open a public
issue. The process, response times and credit policy are the organisation's
[security policy](https://github.com/cronai-labs/.github/blob/main/SECURITY.md); what follows is
what is specific to this kit.

## The boundary that matters

This kit hands a **local language model** tools that read and write a folder of your notes. The
model is untrusted input: a clipped web page, a pasted meeting recap or a shared note can carry
instructions, and a 2B model will follow some of them. Everything in
[`mcp/bridge_policy.py`](mcp/bridge_policy.py) exists to decide what the model may do regardless
of what it was told to do.

So the reports we most want are **policy-layer bypasses** — any way to make the bridge do
something the policy says it will not:

- read a note marked `sensitivity: confidential` through *any* channel: read, search, list,
  backlinks, todos, the four `graph_*` tools, or a context pack
- write into a denied folder, or escape the vault root, by any spelling of a path
- defeat `--read-only`, `--propose`, the write limits, or the audit log
- get `graph_query` to do anything other than read
- make a write land with no audit line

Also in scope: anything that makes `kit.py doctor --report` disclose what it promises not to
(note content, paths from inside a vault, a username, endpoint credentials), and anything that
causes the kit to make a network connection other than to the endpoint you configured.

## What is deliberately not a vulnerability

- **The kit ships no sandbox.** A container answers "what can this code do to my machine", never
  "what may this model do to my notes". [`docs/security.md`](docs/security.md) explains what your
  OS already provides if you want process isolation too.
- **The model can write to your vault by design**, within the policy. That is the product. Reports
  that a model wrote a note it was asked to write are not findings.
- **Local trust.** Anyone who can run commands as you can read your vault directly. The threat
  model starts at the model, not at your own shell.
- A finding that needs an already-compromised machine, or a scanner result with no demonstrated
  impact.

## Checking the claims yourself

Everything above is checkable against the source, and
[`docs/it-review.md`](docs/it-review.md) is written for exactly that — it is the page to forward
to a security team, and it names the code to read for each claim.
