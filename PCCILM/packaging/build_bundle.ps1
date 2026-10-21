<#
Builds the installable app folder, always at the same place:

    <repo>\Builds\ContextImportance\        (ContextDashboard.exe in there is the app; BUILD_INFO.txt says when/what)

    ContextDashboard.exe (+ runtime)   the window and tray (root ContextDashboard\ project: Memory + Privacy modules)
    caprlm\                            CAPRLM privacy service (frozen Python) + Tesseract OCR; started by the dashboard
    collector\                         the C# collector (self-contained)
    worker\                            worker.exe + adapt.exe (frozen Python, no torch)
    models\mobileclip_s0_image.onnx    the MobileCLIP-S0 image encoder

Run from anywhere:  powershell -File packaging\build_bundle.ps1
Needs: .NET 8+ SDK, packaging\.venv-build (see packaging\README.md), and the exported ONNX model
(python -m features.onnx_export).  Nothing here touches your data, databases or model versions.
#>
param(
    [string]$Onnx = "$env:LOCALAPPDATA\context-importance\mobileclip_s0_image.onnx",
    [switch]$SkipWorker,
    [switch]$SkipCaprlm,
    [string]$OutDir,   # default: <repo>\Builds\ContextImportance. It is wiped and rebuilt, so there is only ever one build.
    [string]$Rid = 'win-x64'
)
$ErrorActionPreference = 'Stop'
$packaging = $PSScriptRoot
$system = Split-Path $packaging
$repo = Split-Path $system
if (-not $OutDir) { $OutDir = Join-Path $repo 'Builds\ContextImportance' }
$stage = [IO.Path]::GetFullPath($OutDir)
if ($stage.TrimEnd('\').Length -le 3 -or $stage -eq $repo) { throw "refusing to wipe $stage" }

if (-not (Test-Path $Onnx)) { throw "ONNX model not found: $Onnx. Export it first: python -m features.onnx_export" }

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null

Write-Host '== dashboard'
dotnet publish (Join-Path $repo 'UI\ContextDashboard\ContextDashboard.csproj') -c Release -r $Rid --self-contained -o $stage --nologo -v q
if ($LASTEXITCODE) { throw 'dashboard publish failed' }

Write-Host '== collector'
dotnet publish (Join-Path $system 'csharp-collector\src\CsharpCollector') -c Release -r $Rid --self-contained -o (Join-Path $stage 'collector') --nologo -v q
if ($LASTEXITCODE) { throw 'collector publish failed' }

if (-not $SkipWorker) {
    Write-Host '== worker (PyInstaller)'
    $py = Join-Path $packaging '.venv-build\Scripts\python.exe'
    if (-not (Test-Path $py)) { throw "build venv missing: $py" }
    Push-Location $packaging
    try {
        $ErrorActionPreference = 'Continue'   # PyInstaller logs its progress on stderr, which is not a failure
        & $py -m PyInstaller worker.spec --noconfirm --distpath dist --workpath build --log-level WARN
        $ErrorActionPreference = 'Stop'
        if ($LASTEXITCODE) { throw 'PyInstaller failed' }
    } finally { Pop-Location }
}
Copy-Item (Join-Path $packaging 'dist\worker') (Join-Path $stage 'worker') -Recurse

Write-Host '== llm (llama.cpp server for memory summaries: dedicated GPU via Vulkan, else CPU)'
& (Join-Path $packaging 'fetch_llama.ps1')
$llm = Join-Path $stage 'llm'
New-Item -ItemType Directory -Force $llm | Out-Null
$runtime = Join-Path $packaging 'llm-runtime'
# Only the server and its libraries: the other llama.cpp tools (bench, quantize, cli, ...) bring their own *-impl.dll.
Get-ChildItem $runtime -File | Where-Object {
    $_.Name -in @('llama-server.exe', 'llama-server-impl.dll', 'RELEASE.txt') -or $_.Name -like 'LICENSE*' -or
    ($_.Extension -eq '.dll' -and $_.Name -notlike '*-impl.dll')
} | Copy-Item -Destination $llm

Write-Host '== caprlm (privacy service)'
$caprlmBuild = Join-Path $repo 'CAPRLM\packaging'
if (-not $SkipCaprlm) { & (Join-Path $caprlmBuild 'build_service.ps1'); if ($LASTEXITCODE) { throw 'caprlm build failed' } }
Copy-Item (Join-Path $caprlmBuild 'dist\caprlm-service') (Join-Path $stage 'caprlm') -Recurse

Write-Host '== model'
New-Item -ItemType Directory -Force (Join-Path $stage 'models') | Out-Null
Copy-Item $Onnx (Join-Path $stage 'models\mobileclip_s0_image.onnx')

$commit = (git -C $repo rev-parse --short HEAD 2>$null)
$dirty = if (git -C $repo status --porcelain 2>$null) { ' (uncommitted changes)' } else { '' }
@("Built:  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')", "Commit: $commit$dirty", "Folder: $stage", "Run:    $stage\ContextDashboard.exe") |
    Set-Content (Join-Path $stage 'BUILD_INFO.txt') -Encoding utf8

$size = (Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
'{0}: {1:N0} MB' -f $stage, $size
Get-ChildItem $stage -Directory | ForEach-Object {
    '  {0,-12} {1,6:N0} MB' -f $_.Name, ((Get-ChildItem $_.FullName -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
}
