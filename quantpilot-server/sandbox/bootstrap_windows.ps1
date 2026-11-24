# QuantPilot sandbox bootstrap for Windows PowerShell.
#
# Run this in YOUR OWN PowerShell terminal (not through an AI tool):
#   powershell -ExecutionPolicy Bypass -File sandbox\bootstrap_windows.ps1
#
# Steps: start machine -> build sandbox image (via CN mirror) -> smoke test.
# NOTE: ASCII-only messages on purpose (Windows PowerShell 5.1 mangles
#       non-ASCII text in BOM-less UTF-8 files).

$ErrorActionPreference = "Stop"
$SandboxDir = $PSScriptRoot
$ServerDir = Split-Path -Parent $SandboxDir
$Podman = "C:\Users\kylin\AppData\Local\Programs\Podman\podman.exe"
$MachineImage = Join-Path $SandboxDir "podman-machine-os-wsl.tar.gz"
$DataDir = Join-Path $SandboxDir "data\cn_data"
$SmokeFile = Join-Path $SandboxDir "smoke.py"
$BaseMirror = "docker.m.daocloud.io/library/python:3.10-slim-bookworm"

function Write-Step($msg) { Write-Host "`n========== $msg ==========" -ForegroundColor Cyan }

if (-not (Test-Path $Podman)) { throw "podman.exe not found: $Podman" }
if (-not (Test-Path (Join-Path $DataDir "calendars"))) { throw "qlib data not ready: $DataDir" }

# ---- 1. machine init (only if absent) ----
Write-Step "[1/4] Init machine"
$machines = & $Podman machine list --format "{{.Name}}" 2>$null
if ($machines -match "podman-machine-default") {
    Write-Host "Machine already exists, skip init."
} elseif (Test-Path $MachineImage) {
    Write-Host "Creating machine from local image (no network download)..."
    & $Podman machine init --image $MachineImage podman-machine-default
    if ($LASTEXITCODE -ne 0) { throw "machine init failed, exit code $LASTEXITCODE" }
} else {
    throw "No machine and no local image at $MachineImage (run fetch_machine_image.ps1 first)"
}

# ---- 2. machine start ----
Write-Step "[2/4] Start machine"
$running = & $Podman machine list --format "{{.Name}} {{.LastUp}}" 2>$null
if ($running -match "podman-machine-default.*Currently running") {
    Write-Host "Machine already running, skip start."
} else {
    & $Podman machine start podman-machine-default
    if ($LASTEXITCODE -ne 0) { throw "machine start failed, exit code $LASTEXITCODE" }
}
& $Podman machine list

# ---- 3. build image via CN mirror ----
Write-Step "[3/4] Build sandbox image quantpilot/sandbox:0.1"
$existing = & $Podman images --format "{{.Repository}}:{{.Tag}}" 2>$null
Push-Location $ServerDir
try {
    if ($existing -match "quantpilot/sandbox:0.1") {
        Write-Host "Image already built, skip."
    } else {
        Write-Host "Building with mirror base: $BaseMirror"
        & $Podman build --build-arg BASE_IMAGE=$BaseMirror -f sandbox/Containerfile -t quantpilot/sandbox:0.1 sandbox/
        if ($LASTEXITCODE -ne 0) { throw "image build failed, exit code $LASTEXITCODE" }
    }
} finally {
    Pop-Location
}

# ---- 4. smoke test ----
Write-Step "[4/4] Smoke test (run qlib inside container)"
$smokeCode = @'
import json
import qlib
from qlib.constant import REG_CN
qlib.init(provider_uri="/data/qlib", region=REG_CN)
from qlib.data import D
cal = D.calendar()
print("QUANTPLOT_SMOKE:" + json.dumps({"calendar_len": len(cal), "last": str(cal[-1])}))
'@
Set-Content -Path $SmokeFile -Value $smokeCode -Encoding ASCII

& $Podman run --rm --network=none `
    -v "${DataDir}:/data/qlib:ro" `
    -v "${SandboxDir}:/work:ro" `
    -e "SOLUTION_PATH=/work/smoke.py" `
    quantpilot/sandbox:0.1
if ($LASTEXITCODE -ne 0) { throw "smoke test failed, exit code $LASTEXITCODE" }

Write-Host "`nDONE: machine up, image built, qlib reachable in container." -ForegroundColor Green
Write-Host "Paste the full output back so I can continue with the 10 acceptance cases."
