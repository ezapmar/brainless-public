# brainless installer for Windows. One line from PowerShell:
#
#   irm https://raw.githubusercontent.com/ezapmar/brainless-public/main/install.ps1 | iex
#
# Or with options (run the script directly):
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Vault $HOME\brainless
#   ... -Yes -InitArgs '--provider','ollama','--no-schedule'
# Piped through iex it takes no parameters; set $env:BRAINLESS_VAULT or
# $env:BRAINLESS_REPO first instead.
#
# What it does, idempotently (the Windows twin of install.sh, lite profile only):
#   1. checks git and Python 3.11+ (the py launcher first, then python)
#   2. clones the engine into the vault directory (or pulls if it is already there)
#   3. creates .venv and installs the core Python dependency (markitdown)
#   4. installs the `brainless` command into ~\.local\bin and puts that on your PATH
#   5. hands over to `brainless init`: your name and language, folders, a model
#      (Ollama, an API key kept in Credential Manager, or a CLI you are signed in
#      to), and the one Task Scheduler entry
#
# Nothing here touches files outside the vault, ~\.local\bin and ~\.config\brainless,
# plus your user PATH and, if you agree to background runs, one scheduled task.
[CmdletBinding()]
param(
  [string]$Vault = $(if ($env:BRAINLESS_VAULT) { $env:BRAINLESS_VAULT } else { Join-Path $HOME 'brainless' }),
  [string]$Repo = $(if ($env:BRAINLESS_REPO) { $env:BRAINLESS_REPO } else { 'https://github.com/ezapmar/brainless-public.git' }),
  [switch]$Yes,
  [string[]]$InitArgs = @()
)
$ErrorActionPreference = 'Stop'

function Say($msg) { Write-Host "==> $msg" -ForegroundColor White }
function Ok($msg) { Write-Host "    ok: $msg" }
function Warn($msg) { Write-Host "    WARNING: $msg" -ForegroundColor Yellow }
function Die($msg) { Write-Host $msg -ForegroundColor Red; exit 1 }
# True when a native command exits 0. Its stderr is dropped, and Windows
# PowerShell 5.1 would turn that stderr into a terminating error under 'Stop'.
function Test-Native([string]$exe, [string[]]$a) {
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $exe @a 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) } catch { return $false }
  finally { $ErrorActionPreference = $old }
}

# 1. prerequisites
Say 'Checking prerequisites'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Die 'git is required: winget install --id Git.Git -e  (then open a new PowerShell)'
}
$Py = $null
$probe = 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
foreach ($cand in @(@('py', '-3.14'), @('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('python'), @('python3'))) {
  $exe = Get-Command $cand[0] -ErrorAction SilentlyContinue
  # The Microsoft Store stub (WindowsApps\python.exe) opens the Store instead of running.
  if (-not $exe -or $exe.Source -like '*\WindowsApps\*') { continue }
  $cargs = @($cand | Select-Object -Skip 1) + @('-c', $probe)
  if (Test-Native $cand[0] $cargs) { $Py = $cand; break }
}
if (-not $Py) { Die 'Python 3.11 or newer is required: winget install --id Python.Python.3.13 -e  (then open a new PowerShell)' }
$PyExe = $Py[0]; $PyArgs = @($Py | Select-Object -Skip 1)
$pyver = & $PyExe @PyArgs -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
Ok "$((git --version) -replace 'git version ', 'git '), Python $pyver"

# 2. clone or update
$Vault = [IO.Path]::GetFullPath(($Vault -replace '^~', $HOME))
if (Test-Path (Join-Path $Vault '.git')) {
  Say "Updating existing vault at $Vault"
  git -C $Vault pull --rebase --autostash --quiet
  if ($LASTEXITCODE -ne 0) { Warn 'pull failed; continuing with the local copy' }
} elseif ((Test-Path $Vault) -and (Get-ChildItem -Force $Vault | Select-Object -First 1)) {
  Die "$Vault exists and is not a brainless checkout; pass -Vault <empty or existing vault dir>"
} else {
  Say "Cloning into $Vault"
  git clone --quiet $Repo $Vault
  if ($LASTEXITCODE -ne 0) { Die "git clone $Repo failed" }
}
Set-Location $Vault
New-Item -ItemType Directory -Force logs | Out-Null

# 3. venv + core dependency
Say 'Python environment (.venv)'
$VenvPy = Join-Path $Vault '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPy)) {
  & $PyExe @PyArgs -m venv .venv
  if ($LASTEXITCODE -ne 0) { Die 'could not create .venv' }
}
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -r requirements-core.txt
if ($LASTEXITCODE -ne 0) { Die 'pip install -r requirements-core.txt failed' }
if (Test-Native $VenvPy @('-c', 'import pdfminer, openpyxl, pptx, mammoth')) { Ok 'markitdown with pdf, docx, xlsx, pptx converters' }
else { Warn 'markitdown document extras missing; PDF and Office conversion will fail until fixed' }

# 4. brainless command
Say 'Installing the brainless command'
$BinDir = Join-Path $HOME '.local\bin'
$ConfDir = Join-Path $HOME '.config\brainless'
New-Item -ItemType Directory -Force $BinDir, $ConfDir | Out-Null
$Pointer = Join-Path $ConfDir 'vault'
$current = if (Test-Path $Pointer) { (Get-Content $Pointer -TotalCount 1) } else { '' }
if ($current -and ($current -ne $Vault)) {
  # Another vault is already the default; a test install must not take it over.
  Warn "the brainless command stays on $current"
  Warn "to switch: write $Vault into $Pointer  (or set `$env:BRAINLESS_VAULT)"
} else {
  # No BOM: the .cmd shim reads this file with `set /p`.
  [IO.File]::WriteAllText($Pointer, "$Vault`n", (New-Object Text.UTF8Encoding $false))
}
Copy-Item -Force (Join-Path $Vault 'bin\brainless.cmd') (Join-Path $BinDir 'brainless.cmd')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $BinDir)) {
  [Environment]::SetEnvironmentVariable('Path', ((@($userPath, $BinDir) | Where-Object { $_ }) -join ';'), 'User')
  Ok "added $BinDir to your PATH (new terminals pick it up)"
}
if (-not (($env:Path -split ';') -contains $BinDir)) { $env:Path = "$env:Path;$BinDir" }
Ok "brainless -> $(Join-Path $BinDir 'brainless.cmd')"

# 5. the questions live in `brainless init`
Say 'Setting up (brainless init)'
$env:BRAINLESS_VAULT = $Vault
$env:PYTHONUTF8 = '1'
$wizard = @()
if ($Yes) { $wizard += '--yes' }
$wizard += $InitArgs
& $VenvPy (Join-Path $Vault 'tools\init_wizard.py') @wizard
exit $LASTEXITCODE
