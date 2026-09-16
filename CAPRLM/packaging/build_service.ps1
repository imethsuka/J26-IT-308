<#
Builds the frozen CAPRLM service: CAPRLM\packaging\dist\caprlm-service\   (caprlm-service.exe + libraries + spaCy model + risk model)
and copies the Tesseract OCR binary beside it:                            CAPRLM\packaging\dist\caprlm-service\tesseract\

Run from anywhere:  powershell -File CAPRLM\packaging\build_service.ps1
Needs: Python 3.13 on PATH (py -3.13), internet the first time (pip + the spaCy model), and Tesseract installed
(UB-Mannheim build, C:\Program Files\Tesseract-OCR) unless -SkipTesseract.
Train the risk model first if CAPRLM\research\artifacts\caprlm_model.joblib is missing:  python -m research.eval.run_comparison
Nothing here touches the repo's data or your user data.
#>
param(
    [string]$Tesseract = 'C:\Program Files\Tesseract-OCR',
    [switch]$SkipTesseract
)
$ErrorActionPreference = 'Stop'
$packaging = $PSScriptRoot
$caprlm = Split-Path $packaging
$venv = Join-Path $packaging '.venv-build'
$py = Join-Path $venv 'Scripts\python.exe'

if (-not (Test-Path (Join-Path $caprlm 'research\artifacts\caprlm_model.joblib'))) {
    Write-Warning 'No saved risk model: the service will train one on first start (slow). Run: python -m research.eval.run_comparison'
}

if (-not (Test-Path $py)) {
    Write-Host '== build venv'
    py -3.13 -m venv $venv
    if ($LASTEXITCODE) { throw 'could not create the build venv' }
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet 'scikit-learn>=1.5' joblib 'spacy>=3.7' 'fastapi>=0.110' 'uvicorn>=0.27' 'pydantic>=2' pillow pytesseract pyinstaller
    if ($LASTEXITCODE) { throw 'pip install failed' }
    & $py -m spacy download en_core_web_sm
    if ($LASTEXITCODE) { throw 'spaCy model download failed' }
}

Write-Host '== caprlm-service (PyInstaller)'
Push-Location $packaging
try {
    $ErrorActionPreference = 'Continue'   # PyInstaller logs its progress on stderr, which is not a failure
    & $py -m PyInstaller caprlm_service.spec --noconfirm --distpath dist --workpath build --log-level WARN
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE) { throw 'PyInstaller failed' }
} finally { Pop-Location }

$out = Join-Path $packaging 'dist\caprlm-service'
if (-not $SkipTesseract) {
    if (-not (Test-Path (Join-Path $Tesseract 'tesseract.exe'))) { throw "Tesseract not found at $Tesseract (use -Tesseract <folder> or -SkipTesseract)" }
    Write-Host '== tesseract'
    $dest = Join-Path $out 'tesseract'
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    New-Item -ItemType Directory -Force $dest | Out-Null
    # The binary, its DLLs, and English data only: the other language packs are the bulk of the install.
    Get-ChildItem $Tesseract -File | Where-Object { $_.Name -eq 'tesseract.exe' -or $_.Extension -eq '.dll' } | Copy-Item -Destination $dest
    $tessdata = Join-Path $dest 'tessdata'
    New-Item -ItemType Directory -Force $tessdata | Out-Null
    foreach ($f in 'eng.traineddata', 'osd.traineddata') {
        $src = Join-Path $Tesseract "tessdata\$f"
        if (Test-Path $src) { Copy-Item $src $tessdata }
    }
}

$size = (Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
'{0}: {1:N0} MB' -f $out, $size
