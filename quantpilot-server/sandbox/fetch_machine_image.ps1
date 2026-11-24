# 手动下载 podman WSL machine 镜像（当 podman machine init 内置下载器在网络代理下卡住时）。
#
# 用法（PowerShell）：
#   powershell -ExecutionPolicy Bypass -File sandbox\fetch_machine_image.ps1
#
# 原理：
#   podman machine init 默认从 quay.io/podman/machine-os 拉取镜像，其内置下载器
#   在部分代理环境下会卡在 blob 下载（0 字节长时间不动）。本脚本改用 curl 拉取
#   wsl + x86_64 对应的 layer，输出到本地 tar.gz，再用：
#       podman machine init --image file:///C:/path/to/image.tar.gz
#   导入，绕过内置下载器。
#
# 输出文件：sandbox/podman-machine-os-wsl.tar.gz

$ErrorActionPreference = "Stop"

$IMAGE_TAG = "6.2"
$OUTPUT = Join-Path $PSScriptRoot "podman-machine-os-wsl.tar.gz"
$REGISTRY = "quay.io"
$REPO = "podman/machine-os"

function Get-RegistryToken {
    $authUrl = "https://$REGISTRY/v2/auth?service=$REGISTRY&scope=repository:${REPO}:pull"
    $auth = Invoke-RestMethod -Uri $authUrl -TimeoutSec 30
    return $auth.token
}

function Invoke-RegistryGet {
    param([string]$Url, [string]$Token, [string]$Accept)
    $headers = @{ "Authorization" = "Bearer $Token" }
    if ($Accept) { $headers["Accept"] = $Accept }
    return Invoke-RestMethod -Uri $Url -Headers $headers -TimeoutSec 30
}

Write-Host "[1/4] 获取 registry token"
$token = Get-RegistryToken

Write-Host "[2/4] 查询镜像 index，定位 wsl/x86_64 manifest"
$index = Invoke-RegistryGet -Url "https://$REGISTRY/v2/$REPO/manifests/$IMAGE_TAG" -Token $token -Accept "application/vnd.oci.image.index.v1+json"
$wslManifest = $index.manifests | Where-Object {
    $_.annotations.disktype -eq "wsl" -and
    ($_.platform.architecture -eq "x86_64" -or $_.platform.architecture -eq "amd64")
} | Select-Object -First 1
if (-not $wslManifest) { throw "未找到 wsl/x86_64 manifest" }
Write-Host "      manifest: $($wslManifest.digest)"

Write-Host "[3/4] 查询 layer（磁盘镜像）地址"
$manifest = Invoke-RegistryGet -Url "https://$REGISTRY/v2/$REPO/manifests/$($wslManifest.digest)" -Token $token -Accept "application/vnd.oci.image.manifest.v1+json"
$layer = $manifest.layers | Select-Object -First 1
$sizeMB = [math]::Round($layer.size / 1MB, 1)
Write-Host "      layer: $($layer.digest) ($sizeMB MB)"

Write-Host "[4/4] 下载 layer 到 $OUTPUT"
if (Test-Path $OUTPUT) { Remove-Item $OUTPUT -Force }
$blobUrl = "https://$REGISTRY/v2/$REPO/blobs/$($layer.digest)"
& curl.exe -fL --retry 3 --retry-delay 2 -o $OUTPUT -H "Authorization: Bearer $token" $blobUrl
if ($LASTEXITCODE -ne 0) { throw "curl 下载失败，退出码 $LASTEXITCODE" }

$actualMB = [math]::Round((Get-Item $OUTPUT).Length / 1MB, 1)
Write-Host "完成：$OUTPUT ($actualMB MB)"
Write-Host ""
Write-Host "下一步："
Write-Host "  podman machine init --image file:///C:/path/to/podman-machine-os-wsl.tar.gz"
Write-Host "  podman machine start"
