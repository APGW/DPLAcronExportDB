param(
    [string]$OutputPath = "",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputPath) {
    $OutputPath = Join-Path $projectRoot "dist\dpl-acron-debian13.tar.gz"
}
$absoluteOutput = [IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $absoluteOutput
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

if (-not $SkipTests) {
    Push-Location $projectRoot
    try {
        $env:PYTHONDONTWRITEBYTECODE = "1"
        python -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) {
            throw "Tests fehlgeschlagen."
        }
    }
    finally {
        Pop-Location
    }
}

$dplSource = Get-ChildItem -LiteralPath $projectRoot -File |
    Where-Object { $_.Name -like "2026-06-12 DPL_PWK Lippestra*Braukgraben.xlsx" } |
    Select-Object -First 1
if (-not $dplSource) {
    throw "Die mitzuliefernde DPL wurde nicht gefunden."
}

$bundleItems = @(
    "app.py",
    "pyproject.toml",
    "README.md",
    "DEPLOY_DEBIAN13.md",
    "PROXMOX_CT.md",
    "TIBAUM_CHECK.md",
    "dpl_export",
    "deploy",
    "brauckgraben_neu.csv.xlsx",
    "shhamm_provider1_WINCC60.csv",
    $dplSource.Name
)

$temporaryOutput = "$absoluteOutput.tmp"
if (Test-Path -LiteralPath $temporaryOutput) {
    Remove-Item -LiteralPath $temporaryOutput -Force
}
Push-Location $projectRoot
try {
    & tar.exe -czf $temporaryOutput --exclude="__pycache__" --exclude="*.pyc" @bundleItems
    if ($LASTEXITCODE -ne 0) {
        throw "Das Debian-Bundle konnte nicht erstellt werden."
    }
    Move-Item -LiteralPath $temporaryOutput -Destination $absoluteOutput -Force
}
finally {
    Pop-Location
    if (Test-Path -LiteralPath $temporaryOutput) {
        Remove-Item -LiteralPath $temporaryOutput -Force
    }
}

$bundle = Get-Item -LiteralPath $absoluteOutput
$checksum = (Get-FileHash -Algorithm SHA256 -LiteralPath $bundle.FullName).Hash.ToLowerInvariant()
$checksumPath = "$absoluteOutput.sha256"
Set-Content -LiteralPath $checksumPath -Encoding ascii -NoNewline -Value "$checksum  $($bundle.Name)`n"
Write-Host "Bundle erstellt: $($bundle.FullName)"
Write-Host "Größe: $([math]::Round($bundle.Length / 1MB, 2)) MB"
Write-Host "SHA-256: $checksum"
Write-Host "Prüfsumme: $checksumPath"
