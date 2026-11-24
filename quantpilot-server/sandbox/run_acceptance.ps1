# QuantPilot Phase 0 acceptance runner (Windows PowerShell, run in YOUR terminal).
#
# Prerequisites: bootstrap_windows.ps1 already ran (machine up, image built,
# qlib data ready). This script runs the 10 acceptance solutions inside the
# sandbox and reports the success rate (pass bar: >= 6/10).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File sandbox\run_acceptance.ps1

$ErrorActionPreference = "Stop"
$SandboxDir = $PSScriptRoot
$ServerDir = Split-Path -Parent $SandboxDir
$Podman = "C:\Users\kylin\AppData\Local\Programs\Podman\podman.exe"
$DataDir = Join-Path $SandboxDir "data\cn_data"
$AcceptDir = Join-Path $SandboxDir "work\acceptance"
$ManifestPath = Join-Path $AcceptDir "manifest.json"
$ReportPath = Join-Path $AcceptDir "report.json"
$Image = "quantpilot/sandbox:0.1"

function Write-Step($msg) { Write-Host "`n========== $msg ==========" -ForegroundColor Cyan }

if (-not (Test-Path $Podman)) { throw "podman.exe not found" }
if (-not (Test-Path $ManifestPath)) { throw "manifest not found: $ManifestPath (run gen_acceptance_solutions.py first)" }

$cases = Get-Content $ManifestPath -Raw | ConvertFrom-Json
Write-Host "Loaded $($cases.Count) acceptance cases."

$results = @()
$successCount = 0

foreach ($case in $cases) {
    $solution = Join-Path $AcceptDir $case.solution
    if (-not (Test-Path $solution)) {
        Write-Host "[$($case.id)] SKIP - solution file missing"
        $results += [pscustomobject]@{ id = $case.id; success = $false; reason = "solution missing" }
        continue
    }

    Write-Host "[$($case.id)] running: $($case.expression)" -ForegroundColor Yellow
    $stdout = & $Podman run --rm --network=none `
        --memory=2g --cpus=2 --pids-limit=128 `
        -v "${DataDir}:/data/qlib:ro" `
        -v "${AcceptDir}:/work:ro" `
        -e "QLIB_PROVIDER_URI=/data/qlib" `
        -e "SOLUTION_PATH=/work/$($case.solution)" `
        $Image 2>&1 | Out-String

    # Extract the QUANTPLOT_RESULT line
    $marker = "QUANTPLOT_RESULT:"
    $line = ($stdout -split "`n" | Where-Object { $_ -like "*${marker}*" } | Select-Object -Last 1)
    $ok = $false
    $reason = ""
    $metrics = $null
    if ($line) {
        $idx = $line.IndexOf($marker)
        $jsonPart = $line.Substring($idx + $marker.Length).Trim()
        try {
            $obj = $jsonPart | ConvertFrom-Json
            $metrics = $obj.metrics
            # Success bar: valid metrics with finite annual_return
            if ($null -ne $metrics -and $null -ne $metrics.annual_return) {
                $ok = $true
            } else {
                $reason = "metrics missing annual_return"
            }
        } catch {
            $reason = "result JSON parse failed: $($_.Exception.Message)"
        }
    } else {
        $reason = "no QUANTPLOT_RESULT marker"
    }

    if ($ok) {
        $successCount++
        $ar = [math]::Round($metrics.annual_return, 4)
        $sh = [math]::Round($metrics.sharpe, 4)
        Write-Host "[$($case.id)] OK   annual_return=$ar sharpe=$sh" -ForegroundColor Green
        $results += [pscustomobject]@{ id = $case.id; success = $true; annual_return = $ar; sharpe = $sh; reason = "" }
    } else {
        Write-Host "[$($case.id)] FAIL reason=$reason" -ForegroundColor Red
        Write-Host ($stdout.Substring(0, [math]::Min(500, $stdout.Length))) -ForegroundColor DarkGray
        $results += [pscustomobject]@{ id = $case.id; success = $false; reason = $reason }
    }
}

$total = $cases.Count
Write-Step "RESULT: $successCount / $total passed (bar: >= 6)"
if ($successCount -ge 6) {
    Write-Host "PHASE 0 ACCEPTANCE: PASS" -ForegroundColor Green
} else {
    Write-Host "PHASE 0 ACCEPTANCE: FAIL" -ForegroundColor Red
}

$report = [pscustomobject]@{
    generated_at = (Get-Date).ToString("o")
    success_count = $successCount
    total = $total
    passed = ($successCount -ge 6)
    cases = $results
}
$report | ConvertTo-Json -Depth 6 | Set-Content -Path $ReportPath -Encoding UTF8
Write-Host "Report saved: $ReportPath"
Write-Host "Paste the full output back so I can record the result."
