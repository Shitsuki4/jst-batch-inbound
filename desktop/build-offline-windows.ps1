param(
    [string]$Version = "4.1.5",
    [string]$Runtime = "win-x64",
    [string]$PortableRoot = "",
    [Parameter(Mandatory = $true)][string]$WebView2Cab,
    [Parameter(Mandatory = $true)][string]$WebView2CabSha256,
    [string]$FfmpegArchive = "",
    [string]$FfmpegArchiveSha256 = "",
    [string]$FfmpegBuildDir = "",
    [string]$InstallerName = "",
    [Parameter(Mandatory = $true)][string]$InnoCompiler
)

# Builds the full offline installer: portable package + bundled WebView2 fixed
# runtime + bundled FFmpeg/ffprobe + installed-mode marker, compiled with Inno Setup.
# Run desktop/build-windows.ps1 first so desktop/dist/portable exists.

$ErrorActionPreference = "Stop"

$desktopRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$distRoot = Join-Path $desktopRoot "dist"
$installerSource = Join-Path $desktopRoot "installer\offline_setup.iss"
$docsRoot = Join-Path $desktopRoot "installer\docs"
$workRoot = Join-Path $desktopRoot "build\offline"
$payloadRoot = Join-Path $distRoot "offline-payload"
$installerDir = Join-Path $distRoot "installer"
$hashPath = Join-Path $distRoot "SHA256SUMS.txt"

if (-not $PortableRoot) {
    $PortableRoot = Join-Path $distRoot "portable"
}

if (-not $FfmpegBuildDir -and -not $FfmpegArchive) {
    throw "Provide either -FfmpegArchive (with -FfmpegArchiveSha256) or -FfmpegBuildDir."
}
if ($FfmpegArchive -and -not $FfmpegArchiveSha256) {
    throw "-FfmpegArchiveSha256 is required when -FfmpegArchive is used."
}
if ($FfmpegBuildDir -and $FfmpegArchive) {
    throw "Pass only one of -FfmpegArchive or -FfmpegBuildDir."
}

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
    if (Test-Path -LiteralPath $full) {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

function Assert-Hash {
    param([string]$Path, [string]$Expected, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label not found: $Path"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    $want = $Expected.Trim().ToLowerInvariant()
    if ($actual -ne $want) {
        throw "$Label hash mismatch.`n  expected: $want`n  actual:   $actual`n  path:     $Path"
    }
    Write-Host "$Label sha256 verified: $actual"
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
if (-not (Test-Path -LiteralPath $installerSource -PathType Leaf)) {
    throw "Inno Setup script not found: $installerSource"
}
if (-not (Test-Path -LiteralPath $docsRoot -PathType Container)) {
    throw "Installer docs directory not found: $docsRoot"
}

Assert-Hash -Path $WebView2Cab -Expected $WebView2CabSha256 -Label "WebView2 fixed runtime CAB"
if ($FfmpegArchive) {
    Assert-Hash -Path $FfmpegArchive -Expected $FfmpegArchiveSha256 -Label "FFmpeg full build archive"
}

$InnoCompiler = (Resolve-Path -LiteralPath $InnoCompiler).Path

Remove-Tree $payloadRoot
Remove-Tree $installerDir
Remove-Tree $workRoot
New-Item -ItemType Directory -Force -Path $distRoot, $payloadRoot, $installerDir, $workRoot | Out-Null

Write-Host "== Assemble offline payload =="
Copy-Item -Path (Join-Path $portableRoot "*") -Destination $payloadRoot -Recurse -Force

Write-Host "== Unpack WebView2 fixed runtime =="
$webviewWork = Join-Path $workRoot "webview"
New-Item -ItemType Directory -Force -Path $webviewWork | Out-Null
$expand = Join-Path $env:SystemRoot "System32\expand.exe"
if (-not (Test-Path -LiteralPath $expand -PathType Leaf)) {
    throw "expand.exe was not found at $expand"
}
& $expand -F:* $WebView2Cab $webviewWork | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to expand the WebView2 CAB (exit code $LASTEXITCODE)"
}
$webviewSource = $webviewWork
if (-not (Test-Path -LiteralPath (Join-Path $webviewWork "msedgewebview2.exe") -PathType Leaf)) {
    $subdirs = @(Get-ChildItem -LiteralPath $webviewWork -Directory)
    if ($subdirs.Count -ne 1) {
        throw "Unexpected WebView2 CAB layout under $webviewWork"
    }
    $webviewSource = $subdirs[0].FullName
}
$webviewDest = Join-Path $payloadRoot "runtime\webview2"
New-Item -ItemType Directory -Force -Path $webviewDest | Out-Null
Copy-Item -Path (Join-Path $webviewSource "*") -Destination $webviewDest -Recurse -Force
if (-not (Test-Path -LiteralPath (Join-Path $webviewDest "msedgewebview2.exe") -PathType Leaf)) {
    throw "Bundled WebView2 runtime is missing msedgewebview2.exe"
}

Write-Host "== Unpack FFmpeg full build =="
if ($FfmpegBuildDir) {
    $ffmpegBuild = Get-Item -LiteralPath (Resolve-Path -LiteralPath $FfmpegBuildDir).Path
    if (-not (Test-Path -LiteralPath (Join-Path $ffmpegBuild.FullName "bin\ffmpeg.exe") -PathType Leaf)) {
        $nested = Get-ChildItem -LiteralPath $ffmpegBuild.FullName -Directory |
            Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "bin\ffmpeg.exe") -PathType Leaf } |
            Select-Object -First 1
        if ($nested) { $ffmpegBuild = $nested }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ffmpegBuild.FullName "bin\ffmpeg.exe") -PathType Leaf)) {
        throw "FfmpegBuildDir does not contain bin\ffmpeg.exe: $FfmpegBuildDir"
    }
} else {
    $ffmpegWork = Join-Path $workRoot "ffmpeg"
    New-Item -ItemType Directory -Force -Path $ffmpegWork | Out-Null
    $archive = (Resolve-Path -LiteralPath $FfmpegArchive).Path
    if ($archive.ToLowerInvariant().EndsWith(".zip")) {
        Expand-Archive -LiteralPath $archive -DestinationPath $ffmpegWork -Force
    } else {
        $sevenZip = $null
        foreach ($candidate in @(
                (Get-Command "7z.exe" -ErrorAction SilentlyContinue).Source,
                "C:\Program Files\7-Zip\7z.exe",
                "C:\Program Files (x86)\7-Zip\7z.exe")) {
            if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
                $sevenZip = $candidate
                break
            }
        }
        if (-not $sevenZip) {
            throw "7-Zip (7z.exe) is required to extract $archive"
        }
        & $sevenZip x $archive "-o$ffmpegWork" -y | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to extract the FFmpeg archive (exit code $LASTEXITCODE)"
        }
    }
    $ffmpegBuild = Get-ChildItem -LiteralPath $ffmpegWork -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "bin\ffmpeg.exe") -PathType Leaf } |
        Select-Object -First 1
    if (-not $ffmpegBuild) {
        throw "Could not locate bin\ffmpeg.exe inside the FFmpeg archive"
    }
}
$ffmpegDest = Join-Path $payloadRoot "runtime\ffmpeg"
New-Item -ItemType Directory -Force -Path $ffmpegDest | Out-Null
foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
    $source = Join-Path $ffmpegBuild.FullName "bin\$tool"
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "FFmpeg build is missing bin\$tool"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $ffmpegDest $tool) -Force
}
foreach ($doc in @("LICENSE", "README.txt")) {
    $source = Join-Path $ffmpegBuild.FullName $doc
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $ffmpegDest $doc) -Force
    }
}

Write-Host "== Add installed-mode marker and documentation =="
Set-Content -LiteralPath (Join-Path $payloadRoot "installed-mode.txt") `
    -Value "User data: %LOCALAPPDATA%/ReceivingAssistant/v3" -Encoding UTF8
Copy-Item -Path (Join-Path $docsRoot "*") -Destination $payloadRoot -Recurse -Force

$forbidden = @(
    "SHA256SUMS.txt",
    "验收报告.md",
    "native-settings.json",
    "native-connection.json",
    "service-exit-warning.txt",
    "native-errors.log")
foreach ($name in $forbidden) {
    if (Test-Path -LiteralPath (Join-Path $payloadRoot $name)) {
        throw "Offline payload must not contain user data or reports: $name"
    }
}
$strayData = Get-ChildItem -Path (Join-Path $payloadRoot "*") -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".db", ".sqlite", ".sqlite3" }
if ($strayData) {
    throw "Offline payload contains database files: $($strayData.FullName -join ', ')"
}

$payloadFiles = @(Get-ChildItem -LiteralPath $payloadRoot -Recurse -File)
$payloadBytes = ($payloadFiles | Measure-Object -Property Length -Sum).Sum
Write-Host ("Offline payload: {0} files, {1:N0} bytes" -f $payloadFiles.Count, $payloadBytes)

Write-Host "== Compile offline installer =="
$isccArgs = @("/Qp", "/DPayloadDir=$payloadRoot", "/DOutputDir=$installerDir", "/DAppVersion=$Version")
if ($InstallerName) {
    # Release uploads reject non-ASCII asset names, so callers that publish the
    # installer can request an ASCII base name while local builds keep the
    # Chinese default from offline_setup.iss.
    $isccArgs += "/DOutputBaseFilename=$InstallerName"
}
$isccArgs += $installerSource
& $InnoCompiler @isccArgs
if ($LASTEXITCODE -ne 0) {
    throw "ISCC failed with exit code $LASTEXITCODE"
}

$installerExe = Get-ChildItem -LiteralPath $installerDir -Filter "*.exe" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $installerExe) {
    throw "Inno Setup did not produce an installer in $installerDir"
}

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
Write-Host "Offline payload:   $payloadRoot"
Write-Host "Offline installer: $($installerExe.FullName)"
Write-Host ("Installer size:    {0:N0} bytes ({1:N1} MiB)" -f $installerExe.Length, ($installerExe.Length / 1MB))
Write-Host "Installer SHA256:  $installerSha"
