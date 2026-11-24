# QuantPilot: run a REAL qlib backtest in the podman sandbox.
#
# Run in YOUR OWN PowerShell terminal (not via an AI tool):
#   powershell -ExecutionPolicy Bypass -File sandbox\run_real_strategy.ps1
#
# Prereqs (already on disk):
#   - qlib data at sandbox\data\cn_data  (6177 stocks)
#   - real LLM-generated solution at sandbox\work\real\solution_1.py
#   - podman machine image tar (for init) at sandbox\podman-machine-os-wsl.tar.gz
#
# Steps: ensure machine -> ensure image (CN mirror) -> run REAL strategy -> print result.
# ASCII-only messages on purpose (Windows PowerShell 5.1 mangles non-ASCII).

$ErrorActionPreference = "Stop"
$SandboxDir = $PSScriptRoot
$ServerDir = Split-Path -Parent $SandboxDir
$Podman = "C:\Users\kylin\AppData\Local\Programs\Podman\podman.exe"
$MachineImage = Join-Path $SandboxDir "podman-machine-os-wsl.tar.gz"
$DataDir = Join-Path $SandboxDir "data\cn_data"
$RealWorkDir = Join-Path $SandboxDir "work\real"
$Solution = Join-Path $RealWorkDir "solution_1.py"
$BaseMirror = "docker.m.daocloud.io/library/python:3.10-slim-bookworm"
$Image = "quantpilot/sandbox:0.1"

function Write-Step($msg) { Write-Host "`n========== $msg ==========" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red }

if (-not (Test-Path $Podman)) { throw "podman.exe not found: $Podman" }
if (-not (Test-Path (Join-Path $DataDir "calendars"))) { throw "qlib data not ready: $DataDir" }
if (-not (Test-Path $Solution)) { throw "real solution not found: $Solution" }

# ---- 1. ensure machine ----
Write-Step "[1/3] Ensure podman machine"
$machines = & $Podman machine list --format "{{.Name}}" 2>$null
if ($machines -match "podman-machine-default") {
    Write-Ok "Machine exists."
} elseif (Test-Path $MachineImage) {
    Write-Host "Creating machine from local image..."
    & $Podman machine init --image $MachineImage podman-machine-default
    if ($LASTEXITCODE -ne 0) { throw "machine init failed, exit $LASTEXITCODE" }
} else {
    throw "No machine and no image tar at $MachineImage"
}
$running = & $Podman machine list --format "{{.Name}} {{.LastUp}}" 2>$null
if ($running -match "podman-machine-default.*Currently running") {
    Write-Ok "Machine already running."
} else {
    & $Podman machine start podman-machine-default
    if ($LASTEXITCODE -ne 0) { throw "machine start failed, exit $LASTEXITCODE" }
    Write-Ok "Machine started."
}
& $Podman machine list

# ---- 2. ensure image (CN mirror) ----
Write-Step "[2/3] Ensure sandbox image $Image"
$existing = & $Podman images --format "{{.Repository}}:{{.Tag}}" 2>$null
Push-Location $ServerDir
try {
    if ($existing -match [regex]::Escape($Image)) {
        Write-Ok "Image already built."
    } else {
        Write-Host "Building with mirror base: $BaseMirror"
        & $Podman build --build-arg BASE_IMAGE=$BaseMirror -f sandbox/Containerfile -t $Image sandbox/
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Build with daocloud failed. Trying 1ms.run mirror..."
            & $Podman build --build-arg BASE_IMAGE="docker.1ms.run/library/python:3.10-slim-bookworm" -f sandbox/Containerfile -t $Image sandbox/
            if ($LASTEXITCODE -ne 0) { throw "image build failed with both mirrors" }
        }
        Write-Ok "Image built."
    }
} finally {
    Pop-Location
}

# ---- 3. run REAL strategy ----
Write-Step "[3/3] Run REAL qlib backtest (solution_1.py)"
# podman writes harmless warnings (e.g. "Gym has been unmaintained...") to stderr.
# In PowerShell 5.1, native stderr + $ErrorActionPreference=Stop + 2>&1 turns any
# warning into a terminating error. Temporarily relax it so the real result shows.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $Podman run --rm --network=none `
        -v "${DataDir}:/data/qlib:ro" `
        -v "${RealWorkDir}:/work:ro" `
        -e "QLIB_PROVIDER_URI=/data/qlib" `
        -e "SOLUTION_PATH=/work/solution_1.py" `
        -e "QP_TIMEOUT_SEC=600" `
        $Image 2>&1 | Tee-Object -Variable runOutput
    $runExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEap
}
if ($runExit -ne 0) { Write-Fail "container run exit $runExit" }

Write-Host "`n===== FULL OUTPUT ABOVE =====`n" -ForegroundColor Cyan
if ($runOutput -match "QUANTPLOT_RESULT:") {
    Write-Ok "REAL backtest produced QUANTPLOT_RESULT. Paste the full output back to the assistant."
} else {
    Write-Fail "No QUANTPLOT_RESULT in output. Paste the full output back for diagnosis."
}
