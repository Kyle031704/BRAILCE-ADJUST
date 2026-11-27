# Brailce: test -> deploy to Pi -> restart app -> upload to GitHub -> live Pi log.
#
# Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File tools\test_deploy_push.ps1
#
# Options (skip steps that are already done):
#   -SkipTests    do not run pytest
#   -SkipDeploy   do not upload to the Pi (the app is still restarted if .env exists)
#   -SkipGit      do not commit/push to GitHub
#   -NoLog        do not show the live Pi log at the end
#
# The Pi is reached with PuTTY's plink using PI_IP / PI_USER / PI_PASS from .env,
# so there are no password prompts. Stops at the first failure.

param(
    [switch]$SkipTests,
    [switch]$SkipDeploy,
    [switch]$SkipGit,
    [switch]$NoLog
)

$ErrorActionPreference = "Stop"
$Repo   = "https://github.com/Kyle031704/BRAILCE-ADJUST.git"
$Branch = "lcd-redesign-color-scan"
$Plink  = "C:\Program Files\PuTTY\plink.exe"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`nHUMINTO: $msg" -ForegroundColor Red; exit 1 }

# Pi connection details from .env
$envVars = @{}
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_]+)\s*=\s*(.*)$') { $envVars[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'") }
    }
}
$HavePi = [bool]($envVars["PI_IP"] -and $envVars["PI_USER"] -and $envVars["PI_PASS"])
$PiTarget = "$($envVars['PI_USER'])@$($envVars['PI_IP'])"

function Invoke-Pi([string]$command, [switch]$Interactive) {
    # Runs a shell command on the Pi. "SUDO" in the command becomes a password-fed sudo.
    $pw = $envVars["PI_PASS"].Replace("'", "'\''")
    $cmd = $command.Replace("SUDO", "echo '$pw' | sudo -S -p ''")
    if (-not (Test-Path $Plink)) { Fail "walang PuTTY plink sa $Plink." }
    if ($Interactive) {
        & $Plink -batch -t -pw $envVars["PI_PASS"] $PiTarget $cmd
    } else {
        & $Plink -batch -pw $envVars["PI_PASS"] $PiTarget $cmd
    }
}

# 1. Tests
if (-not $SkipTests) {
    Step "1/4 Tests"
    python -m pytest brailce/tests -q
    if ($LASTEXITCODE -ne 0) { Fail "may pumalyang test. Walang na-deploy o na-upload." }
}

# 2. Deploy to the Pi
if (-not $SkipDeploy) {
    Step "2/4 Deploy sa Pi"
    if ($HavePi) {
        python tools/deploy_to_pi.py
        if ($LASTEXITCODE -ne 0) { Fail "pumalya ang deploy. Hindi pa na-upload sa GitHub." }
    } else {
        Write-Host "Walang kumpletong .env (PI_IP, PI_USER, PI_PASS) kaya nilaktawan ang deploy." -ForegroundColor Yellow
    }
}

# 3. Restart the app on the Pi
if ($HavePi) {
    Step "3/4 I-restart ang app sa Pi ($PiTarget)"
    Invoke-Pi "SUDO systemctl restart brailce.service && sleep 2 && systemctl is-active brailce.service"
    if ($LASTEXITCODE -ne 0) { Fail "hindi tumakbo ang app sa Pi pagkatapos i-restart." }
}

# 4. Upload to GitHub
if (-not $SkipGit) {
    Step "4/4 Upload sa GitHub"
    if (-not (Test-Path .git)) {
        git init
        git remote add origin $Repo
        git fetch origin
        if ($LASTEXITCODE -ne 0) { Fail "hindi ma-fetch ang GitHub repo (internet o access)." }
        git reset origin/master          # keeps every local file, only sets the base commit
        git checkout -b $Branch
    } else {
        $current = git rev-parse --abbrev-ref HEAD
        if ($current -ne $Branch) { git checkout -B $Branch }
    }

    git add -A
    $staged = git diff --cached --name-only
    if ($staged -match '(^|/)\.env$') { git reset -q; Fail ".env ay kasama sa upload (may password). Hindi itinuloy." }
    if ($staged) {
        Write-Host "`nMga file na i-a-upload:" -ForegroundColor Cyan
        $staged | ForEach-Object { Write-Host "  $_" }
        git commit -m "LCD redesign (fonts, layout, Braille cells) and coloured Braille scanning" `
                   -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
        if ($LASTEXITCODE -ne 0) { Fail "pumalya ang commit." }
    } else {
        Write-Host "Walang bagong pagbabago; ipu-push lang ang mga naka-commit na." -ForegroundColor Yellow
    }
    git push -u origin $Branch
    if ($LASTEXITCODE -ne 0) { Fail "pumalya ang push (login o write access sa repo)." }

    Write-Host "`nNa-upload na. Gumawa ng pull request dito:" -ForegroundColor Green
    Write-Host "  https://github.com/Kyle031704/BRAILCE-ADJUST/compare/master...$Branch"
}

# Live log from the Pi while you test the device (Ctrl+C to stop)
if ($HavePi -and -not $NoLog) {
    Write-Host "`nTAPOS. Subukan na ang device. Log ng Pi sa ibaba (Ctrl+C para huminto):" -ForegroundColor Green
    Invoke-Pi "SUDO journalctl -u brailce.service -f -n 30" -Interactive
}
