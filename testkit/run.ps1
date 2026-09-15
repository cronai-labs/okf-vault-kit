# Test kit bootstrap for native Windows. Run from the repository root:
#   .\testkit\run.ps1            full run
#   .\testkit\run.ps1 --quick    skip the qmd index and model legs
#
# If PowerShell refuses to run this, it is the execution policy, not the script:
#   powershell -ExecutionPolicy Bypass -File .\testkit\run.ps1
$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host @'
uv is not installed, and the kit needs it.

  winget install --id=astral-sh.uv -e
  behind a proxy:   see docs\platforms\proxy.md first -- the installer is often blocked

Then open a new terminal and run this script again.
'@
  exit 1
}

# The console code page is what broke the CLI before; make the test run honest about UTF-8.
$env:PYTHONIOENCODING = 'utf-8'
# Join-Path, not a literal backslash: this script also runs under pwsh on WSL and macOS.
# --no-project keeps uv from building a .venv inside the tester's checkout.
$harness = Join-Path (Join-Path $here 'testkit') 'testkit.py'
& uv run --no-project $harness @args
exit $LASTEXITCODE
