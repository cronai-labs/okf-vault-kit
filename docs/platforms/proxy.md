# Behind a corporate proxy

The page to read first if your machine is managed. Everything here is about *getting the tools
installed*; once they are, the kit makes no outbound connections of its own — see
[it-review.md](../it-review.md).

Symptoms that mean "read this page": `SSL: CERTIFICATE_VERIFY_FAILED`, `unable to get local issuer
certificate`, a download that hangs at 0%, or `winget` working while `curl | iex` does not.

## 1. Find out what you are behind

```bash
echo "$HTTPS_PROXY $https_proxy $NO_PROXY"     # macOS / Linux / WSL
```

```powershell
[System.Net.WebRequest]::DefaultWebProxy.GetProxy("https://example.com")   # Windows
netsh winhttp show proxy
```

Two different problems hide here, and they need different fixes:

- **A proxy you must route through.** Set `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY` — and put the loopback
  names in `NO_PROXY`:

  ```bash
  export NO_PROXY="localhost,127.0.0.1,::1${NO_PROXY:+,$NO_PROXY}"
  ```

  Without them, Python, curl and most CLI tools send even `http://localhost:1234` to the proxy, which
  answers for nobody: a running LM Studio then reads as "not reachable". The kit's own calls bypass the
  proxy for loopback addresses (`kit.py doctor` prints a `proxy` row when the variables are set), but the
  tools around it do not.
- **TLS inspection.** The proxy re-signs traffic with a company CA. Downloads *connect* and then
  fail certificate validation, because each tool ships its own trust store and none of them knows
  your CA. This is the one that eats afternoons.

## 2. Trust the company CA

Every tool has its own store, so each needs telling separately. Get the CA bundle from your IT
department (`.pem`/`.crt`).

| Tool | How | Verified against |
|---|---|---|
| uv | `--system-certs`, or `UV_SYSTEM_CERTS=1` | uv 0.12.13 |
| Bun | `--use-system-ca` | bun 1.3.14 |
| Python / pip | `SSL_CERT_FILE=/path/ca.pem` (also `REQUESTS_CA_BUNDLE` for pip) | — |
| Node (if you use npm instead of bun) | `NODE_EXTRA_CA_CERTS=/path/ca.pem` | — |
| git | `git config --global http.sslCAInfo /path/ca.pem` | — |

"Verified against" means someone ran `--help` on that version and read the flag back. The unmarked
rows are the standard spellings for those tools; confirm them on your machine rather than trusting
this table.

On Windows the company CA is usually already in the machine certificate store, which is why
`--system-certs` and `--use-system-ca` are the short path — no file to chase.

**Do not** reach for `UV_INSECURE_HOST`, `NODE_TLS_REJECT_UNAUTHORIZED=0` or `--no-check-certificate`.
They turn a certificate problem into a silent downgrade of every connection the tool makes, and on
a work machine that is a conversation you do not want to have.

## 3. Install through channels the proxy already allows

On Windows, prefer **winget** over the vendors' `curl | iex` scripts. winget goes through the
Microsoft CDN and the system proxy, which corporate networks almost always permit; astral.sh and
GitHub release downloads frequently are not.

```powershell
winget install --id astral-sh.uv --exact
winget install --id Oven-sh.Bun --exact
```

If winget itself is unavailable (it is sometimes removed from managed images), ask IT for the MSI,
or install uv from PyPI with a Python you already have: `pip install uv`. Internal PyPI mirrors are
common and usually reachable.

**On WSL2 and Ubuntu that advice needs two packages first.** Ubuntu ships a Python without `pip`
and without `ensurepip`, so both `pip install uv` and `python3 -m venv` fail before they start —
and the error names the version-pinned package, not the generic one:

```bash
sudo apt-get install -y python3.12-venv python3-pip     # 3.12 on Ubuntu 24.04; match your Python
python3 -m venv ~/tools && ~/tools/bin/pip install uv
export PATH="$HOME/tools/bin:$PATH"
```

**If `sudo` is refused** — the likeliest outcome on a managed machine — you do not need uv at all.
The kit runs on plain `python3` with PyYAML, which the distribution packages:

```bash
sudo apt-get install -y python3-yaml     # the one package worth asking IT for
python3 kit.py doctor
python3 kit.py test
```

Verified on a clean Ubuntu 24.04 machine: the whole offline suite passes that way, with no uv, no
Bun and no Node installed. You lose qmd (semantic search falls back to term search) and the
dependency-provisioning convenience, nothing else.

## 4. Package registries

`bun install -g @tobilu/qmd` needs the npm registry. Many companies proxy it through Artifactory or
Nexus:

```bash
bun install -g @tobilu/qmd --registry https://artifactory.example.com/api/npm/npm-remote/
# or persist it
npm config set registry https://artifactory.example.com/api/npm/npm-remote/   # bun reads .npmrc
```

**If the registry is blocked entirely, you are not stuck.** qmd is one provider in the `search`
slot; the kit falls back to `naive` — term frequency over the files, no index, no dependencies:

```bash
uv run kit.py doctor          # shows which provider is active and why
```

You lose semantic search. You keep the vault, the validator, the graph, the local model and the
MCP tools. Say so in your feedback and we will know which half of the product got tested.

## 5. Model downloads

Models come from Hugging Face, once. If it is blocked:

- point at an internal mirror with `HF_ENDPOINT=https://hf-mirror.example.com`, or
- download the GGUF on a machine that can reach it and import it locally — LM Studio imports a
  `.gguf` from disk, and `llama-server -m ./model.gguf` never touches the network at all.

qmd downloads its own embedding and reranking models on first `qmd embed`. Same story: without them
you fall back to keyword search, which is still useful.

## 6. Prove it worked

```bash
uv run kit.py doctor
```

One line per tool, and the provider table at the bottom. That output is what to send us if
something is still wrong — `kit.py doctor --report doctor.txt` writes it to a file with no note
content in it.
