<#
 OKF Vault Kit — Windows installer (winget). qmd is installed through Bun (single binary, no Node/npm);
 kit.py and the MCP bridge run through uv (no pip, no venv). Idempotent.
 Run:  Set-ExecutionPolicy -Scope Process Bypass; .\scripts\install-windows.ps1
#>
$ErrorActionPreference = "Stop"

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function WingetInstall($id) {
  $installed = winget list --id $id --exact 2>$null | Select-String $id
  if ($installed) { Write-Host "present: $id" } else { winget install --id $id --exact --silent --accept-package-agreements --accept-source-agreements }
}

if (-not (Have winget)) { throw "winget missing - install 'App Installer' from the Microsoft Store" }

Write-Host "== runtimes and tools"
foreach ($id in @("Oven-sh.Bun", "astral-sh.uv", "Obsidian.Obsidian", "ElementLabs.LMStudio", "BurntSushi.ripgrep.MSVC", "sharkdp.fd", "jqlang.jq")) { WingetInstall $id }
if (-not (Have python)) { WingetInstall "Python.Python.3.12" }   # uv can also fetch a Python for you: uv python install 3.12

# refresh PATH for this session
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User") + ";$env:USERPROFILE\.bun\bin"

Write-Host "== qmd via bun"
if (Have qmd) { Write-Host "qmd present" } else { bun install -g @tobilu/qmd }

Write-Host "== optional: yakitrak/obsidian-cli via scoop"
if (Have scoop) {
  scoop bucket add scoop-yakitrak https://github.com/yakitrak/scoop-yakitrak.git 2>$null | Out-Null
  scoop install obsidian-cli 2>$null | Out-Null
} else { Write-Host "scoop not installed - skip (optional). https://scoop.sh" }

Write-Host @"

Next:
  1. Launch LM Studio once (registers lms), then:  lms get openbmb/MiniCPM5-2B-GGUF ; lms server start
  2. Obsidian -> Settings -> General -> Command line interface -> Register CLI  (adds Obsidian.com next to Obsidian.exe; put that folder on PATH)
  3. uv run kit.py doctor
  4. uv run kit.py init --target "$env:USERPROFILE\Notes\vault" --actor human:<you>
NVIDIA users: if qmd embedding crashes, set QMD_EMBED_PARALLELISM=1 (or QMD_LLAMA_GPU=vulkan).
"@
