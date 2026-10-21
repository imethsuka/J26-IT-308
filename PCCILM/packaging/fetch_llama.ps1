<#
Downloads llama.cpp's prebuilt Windows server (Vulkan build) into packaging\llm-runtime\, for the memory summaries.

    powershell -File packaging\fetch_llama.ps1

Why this build: Vulkan runs on any dedicated GPU (NVIDIA, AMD, Intel Arc) through the normal graphics driver, with no CUDA
install, and the same folder also carries the CPU backends, so one download covers GPU and CPU-only PCs. The worker
(adaptation/llm_runtime.py) starts llama-server.exe on 127.0.0.1 only while memories are being written, then stops it.

The release is pinned and its SHA-256 is checked, so a build never picks up a different binary. To move to a newer
release, change $Tag and $Sha256 together (the digest is shown on the GitHub release page and in its API).
build_bundle.ps1 runs this when the folder is missing and copies it to <bundle>\llm\.
#>
param([switch]$Force)
$ErrorActionPreference = 'Stop'

$Tag = 'b11480'   # 2026-10-08
$Asset = "llama-$Tag-bin-win-vulkan-x64.zip"
$Sha256 = '94cf0c41c7257c1a40d4bc7063593471004d56fe288724e6ccdcb607a12c8710'

$target = Join-Path $PSScriptRoot 'llm-runtime'
$marker = Join-Path $target 'RELEASE.txt'
if (-not $Force -and (Test-Path $marker) -and ((Get-Content $marker -Raw).Trim() -eq $Asset)) {
    Write-Host "llama.cpp $Tag already in $target"
    return
}

$download = Join-Path ([IO.Path]::GetTempPath()) ("llama-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $download | Out-Null
try {
    $zip = Join-Path $download $Asset
    Write-Host "downloading $Asset"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest "https://github.com/ggml-org/llama.cpp/releases/download/$Tag/$Asset" -OutFile $zip -UseBasicParsing
    $actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256) { throw "checksum mismatch for $Asset`: expected $Sha256, got $actual" }

    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    Expand-Archive $zip -DestinationPath $target
    # Some releases nest everything in one folder: flatten so llama-server.exe sits at the top.
    $server = Get-ChildItem $target -Recurse -Filter 'llama-server.exe' | Select-Object -First 1
    if (-not $server) { throw "llama-server.exe not found in $Asset" }
    if ($server.DirectoryName -ne $target) {
        Get-ChildItem $server.DirectoryName | Move-Item -Destination $target
    }
    Set-Content $marker $Asset -Encoding ascii
    Write-Host "llama.cpp $Tag ready in $target"
} finally {
    Remove-Item $download -Recurse -Force -ErrorAction SilentlyContinue
}
