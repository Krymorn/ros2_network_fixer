# =============================================================================
#  ros2_network_fixer — Quick Start for Windows (PowerShell)
#
#  Run in PowerShell:
#    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#    .\scripts\quickstart.ps1
#
#  Or pipe from the internet (PowerShell 5+ / 7+):
#    iex (irm 'https://raw.githubusercontent.com/YOUR_USERNAME/ros2-network-fixer/main/scripts/quickstart.ps1')
# =============================================================================

$ErrorActionPreference = 'Stop'

function Write-Step   { Write-Host "`n  $args" -ForegroundColor White }
function Write-OK     { Write-Host "  [OK] $args" -ForegroundColor Green }
function Write-Warn   { Write-Host "  [!]  $args" -ForegroundColor Yellow }
function Write-Err    { Write-Host "  [X]  $args" -ForegroundColor Red }
function Write-Info   { Write-Host "  ->   $args" -ForegroundColor Cyan }

Write-Step "ROS 2 Cross-Platform Network Fixer - Quick Start"

# ── Find Python ──────────────────────────────────────────────────────────────
$python = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match 'Python (\d+)\.(\d+)') {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 8) {
                $python = $candidate
                Write-OK "Using: $ver"
                break
            }
        }
    } catch { }
}

if (-not $python) {
    Write-Err "Python 3.8 or newer is required but was not found."
    Write-Err "Download from https://python.org and re-run this script."
    exit 1
}

# ── Install ───────────────────────────────────────────────────────────────────
$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot   = Split-Path -Parent $scriptDir

if (Test-Path (Join-Path $repoRoot 'pyproject.toml')) {
    Write-Step "Installing from local clone..."
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet $repoRoot
} else {
    Write-Step "Installing from PyPI..."
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet ros2-network-fixer
}

Write-OK "Installation complete."

# ── Launch ────────────────────────────────────────────────────────────────────
Write-Step "Launching ros2_network_fixer..."
Write-Host ""

& $python -m ros2_network_fixer.cli $args
