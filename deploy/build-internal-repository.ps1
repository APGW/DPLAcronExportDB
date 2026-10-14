param(
    [string]$OutputPath = "",
    [string]$InternalBaseUrl = "https://dpl-acron.intern",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputPath) {
    $OutputPath = Join-Path $projectRoot "dist\internal-repository"
}
$repositoryRoot = [IO.Path]::GetFullPath($OutputPath)
$releaseBundle = Join-Path $projectRoot "dist\dpl-acron-debian13.tar.gz"

if ($SkipTests) {
    & (Join-Path $PSScriptRoot "build-bundle.ps1") -SkipTests
}
else {
    & (Join-Path $PSScriptRoot "build-bundle.ps1")
}
if ($LASTEXITCODE -ne 0) {
    throw "Das Release-Bundle konnte nicht erstellt werden."
}

$runtime = Get-Content -LiteralPath (Join-Path $projectRoot "dpl_export\runtime.py") -Raw
$versionMatch = [regex]::Match($runtime, 'APP_VERSION\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw "APP_VERSION konnte nicht ermittelt werden."
}
$appVersion = $versionMatch.Groups[1].Value
$releaseDirectory = Join-Path $repositoryRoot "releases\$appVersion"
$ctDirectory = Join-Path $repositoryRoot "ct"
$installDirectory = Join-Path $repositoryRoot "install"
$jsonDirectory = Join-Path $repositoryRoot "json"
$docsDirectory = Join-Path $repositoryRoot "docs"
$testDirectory = Join-Path $repositoryRoot "test"
New-Item -ItemType Directory -Force -Path $releaseDirectory, $ctDirectory, $installDirectory, $jsonDirectory, $docsDirectory, $testDirectory | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "PROXMOX_CT.md") -Destination (Join-Path $docsDirectory "PROXMOX_CT.md") -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "PROXMOX_TEST.md") -Destination (Join-Path $docsDirectory "PROXMOX_TEST.md") -Force

$publishedBundle = Join-Path $releaseDirectory "dpl-acron-debian13.tar.gz"
$publishedInstaller = Join-Path $releaseDirectory "proxmox-install-lxc.sh"
Copy-Item -LiteralPath $releaseBundle -Destination $publishedBundle -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "proxmox-install-lxc.sh") -Destination $publishedInstaller -Force

$bundleHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $publishedBundle).Hash.ToLowerInvariant()
$installerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $publishedInstaller).Hash.ToLowerInvariant()
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$communityTemplate = Get-Content -LiteralPath (Join-Path $PSScriptRoot "community-ct.sh") -Raw
$communityScript = $communityTemplate.Replace("@@APP_VERSION@@", $appVersion)
$communityScript = $communityScript.Replace("@@BUNDLE_SHA256@@", $bundleHash)
$communityPath = Join-Path $ctDirectory "dpl-acron.sh"
[IO.File]::WriteAllText($communityPath, $communityScript, $utf8NoBom)

$containerInstaller = Join-Path $installDirectory "dpl-acron-install.sh"
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "community-install.sh") -Destination $containerInstaller -Force
$communityHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $communityPath).Hash.ToLowerInvariant()
$containerInstallerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $containerInstaller).Hash.ToLowerInvariant()
$acceptanceTest = Join-Path $testDirectory "proxmox-acceptance.sh"
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "proxmox-acceptance.sh") -Destination $acceptanceTest -Force
$acceptanceTestHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $acceptanceTest).Hash.ToLowerInvariant()

$metadata = [ordered]@{
    name = "DPL2ACRON"
    slug = "dpl-acron"
    categories = @(8, 19, 25)
    date_created = "2026-09-07"
    description = "Interner Dienst zur DPL-Prüfung und Erzeugung von ACRON-Exportdateien mit lokaler MariaDB."
    type = "ct"
    updateable = $true
    privileged = $false
    interface_port = 443
    documentation = "$($InternalBaseUrl.TrimEnd('/'))/docs/PROXMOX_CT.md"
    website = "$($InternalBaseUrl.TrimEnd('/'))/"
    repository = "$($InternalBaseUrl.TrimEnd('/'))/"
    architectures = @("amd64")
    platforms = @("pve")
    logo = "$($InternalBaseUrl.TrimEnd('/'))/assets/agw-logo.png"
    install_methods = @(
        [ordered]@{
            type = "default"
            script = "ct/dpl-acron.sh"
            config_path = "/etc/dpl-acron/database.env"
            resources = [ordered]@{
                cpu = 2
                ram = 2048
                hdd = 8
                os = "Debian"
                version = "13"
            }
        }
    )
    default_credentials = [ordered]@{ username = "admin"; password = $null }
    notes = @(
        [ordered]@{ text = "Nur über Intranet oder VPN veröffentlichen."; type = "warning" }
    )
}
[IO.File]::WriteAllText((Join-Path $jsonDirectory "dpl-acron.json"), ($metadata | ConvertTo-Json -Depth 4), $utf8NoBom)
New-Item -ItemType Directory -Force -Path (Join-Path $repositoryRoot "assets") | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "dpl_export\web\assets\agw-logo.png") -Destination (Join-Path $repositoryRoot "assets\agw-logo.png") -Force

$manifest = [ordered]@{
    application = "DPL2ACRON"
    version = $appVersion
    bundle = "releases/$appVersion/dpl-acron-debian13.tar.gz"
    bundle_sha256 = $bundleHash
    installer = "releases/$appVersion/proxmox-install-lxc.sh"
    installer_sha256 = $installerHash
    community_script = "ct/dpl-acron.sh"
    community_script_sha256 = $communityHash
    community_install = "install/dpl-acron-install.sh"
    community_install_sha256 = $containerInstallerHash
    acceptance_test = "test/proxmox-acceptance.sh"
    acceptance_test_sha256 = $acceptanceTestHash
}
$manifestJson = $manifest | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $repositoryRoot "release.json"), $manifestJson, $utf8NoBom)

Write-Host "Internes Repository erstellt: $repositoryRoot"
Write-Host "Version: $appVersion"
Write-Host "Community Script: $communityPath"
Write-Host "Community Script SHA-256: $communityHash"
Write-Host "Veröffentlichen Sie den gesamten Ordner unverändert über internes HTTPS."
