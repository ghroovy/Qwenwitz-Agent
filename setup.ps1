<#
Qwenwitz Agent - one-command Windows setup.

Creates the Python environment, installs dependencies, writes .env with your
game + mod paths, optionally downloads the local Qwen model and builds the
vanilla identifier index. Safe to re-run at any time.

Usage:
  .\setup.ps1                              # interactive prompts
  .\setup.ps1 -GamePath "C:\...\Hearts of Iron IV" -ModPath "C:\...\MyMod"
  .\setup.ps1 -SkipModel                   # deterministic agent only
  .\setup.ps1 -SkipIndex                   # keep an existing index
#>
[CmdletBinding()]
param(
    [string]$GamePath = "",
    [string]$ModPath = "",
    [string]$Model = "Qwen/Qwen3.5-2B",
    [switch]$SkipModel,
    [switch]$SkipIndex
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Root ".venv\Scripts\python.exe"

function Find-Python {
    foreach ($cand in @("python", "py -3", "python3")) {
        try {
            $v = Invoke-Expression "$cand --version" 2>$null
            if ($v -match "Python 3\.(1[0-9]|[2-9])") { return $cand }
        } catch { }
    }
    throw "Python 3.10+ not found. Install it from python.org and re-run setup."
}

function Set-EnvValue {
    param([string]$Key, [string]$Value, [string]$File)
    $lines = @(Get-Content $File -ErrorAction SilentlyContinue)
    $out = @()
    $found = $false
    foreach ($line in $lines) {
        if ($line -match "^$Key=") {
            if (-not $found) { $out += "$Key=$Value"; $found = $true }
            continue
        }
        $out += $line
    }
    if (-not $found) { $out += "$Key=$Value" }
    $out | Set-Content $File -Encoding UTF8
}

Write-Host ""
Write-Host "Qwenwitz Agent setup" -ForegroundColor Cyan
Write-Host "--------------------" -ForegroundColor Cyan

# 1. Python virtual environment -------------------------------------------
if (-not (Test-Path $Py)) {
    $pyCmd = Find-Python
    Write-Host "[1/6] Creating virtual environment (.venv) ..."
    Invoke-Expression "$pyCmd -m venv `"$Root\.venv`""
} else {
    Write-Host "[1/6] Virtual environment already exists."
}

# 2. Dependencies ----------------------------------------------------------
Write-Host "[2/6] Installing dependencies ..."
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -r (Join-Path $Root "requirements.txt") --quiet

# 3. .env with game + mod paths --------------------------------------------
Write-Host "[3/6] Configuring .env ..."
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvFile
}
if (-not $GamePath) { $GamePath = (Select-String -Path $EnvFile -Pattern "^HOI4_GAME_PATH=" | Select-Object -First 1).Line.Split("=", 2)[1].Trim().Trim('"') }
if (-not $ModPath) { $ModPath = (Select-String -Path $EnvFile -Pattern "^HOI4_WORKSPACE_PATH=" | Select-Object -First 1).Line.Split("=", 2)[1].Trim().Trim('"') }
if (-not $GamePath) {
    $GamePath = Read-Host "Hearts of Iron IV install folder (e.g. C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV)"
}
if (-not $ModPath) {
    $ModPath = Read-Host "Your mod workspace folder (the agent only edits this folder)"
}
Set-EnvValue "HOI4_GAME_PATH" $GamePath $EnvFile
Set-EnvValue "HOI4_WORKSPACE_PATH" $ModPath $EnvFile

# 4. Reasoning model (recommended) -----------------------------------------
if (-not $SkipModel) {
    Write-Host "[4/6] Installing the Qwen reasoning model ($Model)."
    & $Py -m pip install torch transformers "huggingface_hub[cli]" --quiet
    & (Join-Path $Root ".venv\Scripts\hf.exe") download $Model
    Set-EnvValue "HOI4_AGENT_MODEL" $Model $EnvFile
} else {
    Write-Host "[4/6] Skipping model install (-SkipModel) - the agent will run "
    Write-Host "      deterministic-only, which is NOT recommended."
}

# 5. Optional vanilla index ------------------------------------------------
if (-not $SkipIndex -and (Test-Path $GamePath)) {
    $IndexDir = Join-Path $Root "data\processed\index"
    if (-not (Test-Path (Join-Path $IndexDir "focuses.json"))) {
        Write-Host "[5/6] Building the vanilla identifier index (one-time, a few minutes) ..."
        & $Py (Join-Path $Root "archive\training\scripts\01_extract_sources.py")
        & $Py (Join-Path $Root "archive\training\scripts\03_make_index.py")
    } else {
        Write-Host "[5/6] Identifier index already exists."
    }
} else {
    Write-Host "[5/6] Skipping index build."
}

# 6. Smoke test ------------------------------------------------------------
Write-Host "[6/6] Smoke test ..."
& $Py -c "from hoi4_agent.server import Hoi4Server; print('server ping:', Hoi4Server().handle('ping', {}))"
& $Py -m hoi4_agent.cli --help | Select-Object -First 1

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Reasoning model: $Model"
Write-Host "Next: open this repository in VS Code and press F5, or install the VSIX."
Write-Host "The agent will edit only: $ModPath"
