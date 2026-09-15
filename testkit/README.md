# Test kit

One command that walks the documented path end to end and writes a single report to send back.

```bash
./testkit/run.sh                 # macOS, Linux, WSL2
.\testkit\run.ps1                # Windows (PowerShell)
```

Add `--quick` to skip the two slow legs (the qmd index and the model), which is the right first
run on a metered or proxied connection.

## What it does

It runs the same path `docs/quickstart.md` describes — `doctor`, the offline suite, `init`,
`validate`, `graph`, `reconcile`, `todos`, `minutes`, `mcp-config`, then qmd, the model endpoint
and a real MCP session — and records each step as **PASS**, **FAIL** or **SKIP with a reason**.

A step you cannot run is never a failure. If the registry is blocked, qmd skips and everything
else still reports. That report is more useful to us than a successful one, because it tells us
where a managed machine stops.

## What it does not do

- It never writes into your vault: `init` creates a throwaway one in a temp directory, deleted
  at the end unless you pass `--keep`.
- It never touches your qmd index: the collection it creates lives in a redirected config and
  cache directory.
- It never writes your MCP client config: `mcp-config` is only printed.

## The report

`testkit-report.md`, next to the repository. It carries the same guarantee as
`kit.py doctor --report`: **no note content, no paths from inside a vault, and no username** —
URLs are reduced to scheme and port, home directories and usernames are replaced.

Read it before sending it. Then add a paragraph at the bottom under *Anything else* — that part
is worth more to us than the table above it.
