param(
    [string]$Version = "4.1.5",
    [string]$PortableRoot = "",
    [string]$ReferenceRuntime = "",
    [string]$InstallerName = "",
    [Parameter(Mandatory = $true)][string]$InnoCompiler
)

# Builds the slim upgrade installer: the portable package + installed-mode marker + docs, with NO
# runtime\ (WebView2 fixed runtime, FFmpeg/ffprobe), compiled from installer\offline_setup.iss with
# /DUpgradeOnly. It installs in place over the offline full install (same AppId and directory) or
# next to the deps installer's shared runtime, and refuses unless the runtime the app will use is
# exactly the pinned build. The pinned build is read from -ReferenceRuntime, by default the offline
# payload's runtime\ (run desktop/build-offline-windows.ps1 first), so both installers always pin
# the same files. Also writes dist\runtime-requirements.json, which CI puts into the release notes
# for the auto-updater (backend/app/updater.py parse_runtime_requirements).

$ErrorActionPreference = "Stop"

$desktopRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$distRoot = Join-Path $desktopRoot "dist"
$installerSource = Join-Path $desktopRoot "installer\offline_setup.iss"
$docsRoot = Join-Path $desktopRoot "installer\docs"
$payloadRoot = Join-Path $distRoot "upgrade-payload"
$installerDir = Join-Path $distRoot "upgrade-installer"
$requirementsPath = Join-Path $distRoot "runtime-requirements.json"
$hashPath = Join-Path $distRoot "SHA256SUMS.txt"

if (-not $PortableRoot) { $PortableRoot = Join-Path $distRoot "portable" }
if (-not $ReferenceRuntime) { $ReferenceRuntime = Join-Path $distRoot "offline-payload\runtime" }

function Assert-UnderDesktop {
    param([string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $prefix = $desktopRoot.TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside desktop: $full"
    }
    return $full
}

function Remove-Tree {
    param([string]$Path)
    $full = Assert-UnderDesktop -Path $Path
    if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Recurse -Force }
}

if (-not (Test-Path -LiteralPath $PortableRoot -PathType Container)) {
    throw "Portable package not found: $PortableRoot. Run desktop/build-windows.ps1 first."
}
$portableRoot = (Resolve-Path -LiteralPath $PortableRoot).Path
foreach ($required in @(
        "Receiving.Native.exe",
        "Receiving.Native.dll",
        "Receiving.Native.runtimeconfig.json",
        "backend\Receiving.Service.exe",
        "backend\_internal")) {
    if (-not (Test-Path -LiteralPath (Join-Path $portableRoot $required))) {
        throw "Portable package is incomplete, missing: $required ($portableRoot)"
    }
}
if (Test-Path -LiteralPath (Join-Path $portableRoot "installed-mode.txt")) {
    throw "Portable package must not contain installed-mode.txt: $portableRoot"
}
# The payload must run with only the deps installer's runtime: no .NET install is assumed.
$runtimeConfig = Get-Content -Raw -LiteralPath (Join-Path $portableRoot "Receiving.Native.runtimeconfig.json") | ConvertFrom-Json
if (-not $runtimeConfig.runtimeOptions.includedFrameworks) {
    throw "Portable package is not self-contained (runtimeconfig has no includedFrameworks); the upgrade installer cannot rely on an installed .NET."
}

$referenceRoot = (Resolve-Path -LiteralPath $ReferenceRuntime).Path
$pinned = [ordered]@{
    "msedgewebview2.exe" = Join-Path $referenceRoot "webview2\msedgewebview2.exe"
    "ffmpeg.exe"         = Join-Path $referenceRoot "ffmpeg\ffmpeg.exe"
    "ffprobe.exe"        = Join-Path $referenceRoot "ffmpeg\ffprobe.exe"
}
$sha = [ordered]@{}
foreach ($name in $pinned.Keys) {
    if (-not (Test-Path -LiteralPath $pinned[$name] -PathType Leaf)) {
        throw "Reference runtime is missing $name ($($pinned[$name])). Build the offline installer first."
    }
    $sha[$name] = (Get-FileHash -Algorithm SHA256 -LiteralPath $pinned[$name]).Hash.ToLowerInvariant()
    Write-Host "Pinned $name sha256: $($sha[$name])"
}
$webviewVersion = (Get-Item -LiteralPath $pinned["msedgewebview2.exe"]).VersionInfo.FileVersion
if (-not $webviewVersion -or $webviewVersion -notmatch '^\d+(\.\d+){3}$') {
    throw "Could not read the WebView2 runtime version from msedgewebview2.exe: '$webviewVersion'"
}
$ffmpegBanner = (& $pinned["ffmpeg.exe"] -hide_banner -version 2>&1 | Select-Object -First 1) -join ""
if ($ffmpegBanner -notmatch '^ffmpeg version (\d+(\.\d+)+)') {
    throw "Could not read the FFmpeg version: '$ffmpegBanner'"
}
$ffmpegVersion = $Matches[1]
Write-Host "Pinned runtime: WebView2 $webviewVersion, FFmpeg $ffmpegVersion"

$InnoCompiler = (Resolve-Path -LiteralPath $InnoCompiler).Path
Remove-Tree $payloadRoot
Remove-Tree $installerDir
New-Item -ItemType Directory -Force -Path $distRoot, $payloadRoot, $installerDir | Out-Null

Write-Host "== Assemble upgrade payload (portable + installed-mode marker + docs, no runtime) =="
Copy-Item -Path (Join-Path $portableRoot "*") -Destination $payloadRoot -Recurse -Force
Set-Content -LiteralPath (Join-Path $payloadRoot "installed-mode.txt") `
    -Value "User data: %LOCALAPPDATA%/ReceivingAssistant/v3" -Encoding UTF8
Copy-Item -Path (Join-Path $docsRoot "*") -Destination $payloadRoot -Recurse -Force

if (Test-Path -LiteralPath (Join-Path $payloadRoot "runtime")) {
    throw "Upgrade payload must not contain runtime\ (it would replace the installed runtime)"
}
$runtimeFiles = Get-ChildItem -LiteralPath $payloadRoot -Recurse -File |
    Where-Object { $_.Name -in @("ffmpeg.exe", "ffprobe.exe", "msedgewebview2.exe") }
if ($runtimeFiles) { throw "Upgrade payload contains runtime files: $($runtimeFiles.FullName -join ', ')" }
foreach ($name in @("SHA256SUMS.txt", "验收报告.md", "native-settings.json", "native-connection.json",
        "service-exit-warning.txt", "native-errors.log")) {
    if (Test-Path -LiteralPath (Join-Path $payloadRoot $name)) {
        throw "Upgrade payload must not contain user data or reports: $name"
    }
}
$strayData = Get-ChildItem -Path (Join-Path $payloadRoot "*") -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".db", ".sqlite", ".sqlite3" }
if ($strayData) { throw "Upgrade payload contains database files: $($strayData.FullName -join ', ')" }
$payloadFiles = @(Get-ChildItem -LiteralPath $payloadRoot -Recurse -File)
$payloadBytes = ($payloadFiles | Measure-Object -Property Length -Sum).Sum
Write-Host ("Upgrade payload: {0} files, {1:N0} bytes" -f $payloadFiles.Count, $payloadBytes)

Write-Host "== Compile upgrade installer =="
$isccArgs = @("/Qp", "/DUpgradeOnly", "/DPayloadDir=$payloadRoot", "/DOutputDir=$installerDir", "/DAppVersion=$Version",
    "/DReqFfmpegSha=$($sha['ffmpeg.exe'])", "/DReqFfprobeSha=$($sha['ffprobe.exe'])",
    "/DReqWebView2Sha=$($sha['msedgewebview2.exe'])", "/DReqWebView2Version=$webviewVersion",
    "/DReqFfmpegVersion=$ffmpegVersion")
if ($InstallerName) { $isccArgs += "/DOutputBaseFilename=$InstallerName" }
$isccArgs += $installerSource
& $InnoCompiler @isccArgs
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }

$installerExe = Get-ChildItem -LiteralPath $installerDir -Filter "*.exe" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installerExe) { throw "Inno Setup did not produce an installer in $installerDir" }

$requirements = [ordered]@{ webview2 = $webviewVersion; ffmpeg = $ffmpegVersion; sha256 = $sha }
[System.IO.File]::WriteAllText($requirementsPath, ($requirements | ConvertTo-Json -Compress -Depth 4),
    [System.Text.UTF8Encoding]::new($false))

$installerSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $installerExe.FullName).Hash.ToLowerInvariant()
$installerLine = "$installerSha  $($installerExe.Name)"
if (Test-Path -LiteralPath $hashPath) {
    $keep = Get-Content -LiteralPath $hashPath |
        Where-Object { $_ -notmatch [regex]::Escape($installerExe.Name) + '$' }
    @($keep) + $installerLine | Set-Content -LiteralPath $hashPath -Encoding UTF8
} else {
    $installerLine | Set-Content -LiteralPath $hashPath -Encoding UTF8
}

Write-Host ""
Write-Host "Upgrade payload:      $payloadRoot"
Write-Host ("Upgrade installer:    {0} ({1:N1} MiB)" -f $installerExe.FullName, ($installerExe.Length / 1MB))
Write-Host "Runtime requirements: $requirementsPath"
