param(
    [string]$Configuration = "Release",
    [string]$Runtime = "win-x64",
    [string]$Version = "3.0.0",
    [string]$Python = "",
    [string]$Dotnet = "",
    [switch]$SkipTests,
    [switch]$RunSynthetic,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"

$desktopRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $desktopRoot "..")).Path
$distRoot = Join-Path $desktopRoot "dist"
$nativeOut = Join-Path $distRoot "native"
$backendDist = Join-Path $distRoot "backend-dist"
$workRoot = Join-Path $desktopRoot "build\portable"
$portableRoot = Join-Path $distRoot "portable"
$portableBackend = Join-Path $portableRoot "backend"
$testProject = Join-Path $desktopRoot "tests\RecorderRegression\RecorderRegression.csproj"
$nativeProject = Join-Path $desktopRoot "src\Receiving.Native.csproj"
$backendSpec = Join-Path $desktopRoot "build_backend.spec"
$verifyScript = Join-Path $desktopRoot "verify-package.ps1"

function Resolve-Executable {
    param(
        [string]$Requested,
        [string[]]$Candidates,
        [string]$Name
    )

    if ($Requested) {
        if (-not (Test-Path -LiteralPath $Requested -PathType Leaf)) {
            throw "$Name was not found: $Requested"
        }
        return (Resolve-Path -LiteralPath $Requested).Path
    }

    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "$Name was not found. Pass -$Name explicitly or install it first."
}

$Python = Resolve-Executable -Requested $Python -Candidates @(
    (Join-Path $repoRoot ".build-env\Scripts\python.exe"),
    (Join-Path $repoRoot "native-trial\tools\python\python.exe")
) -Name "python"

$Dotnet = Resolve-Executable -Requested $Dotnet -Candidates @(
    (Join-Path $repoRoot "native-trial\tools\dotnet\dotnet.exe")
) -Name "dotnet"

$dotnetRoot = Split-Path -Parent $Dotnet
if (Test-Path -LiteralPath (Join-Path $dotnetRoot "shared")) {
    $env:DOTNET_ROOT = $dotnetRoot
    $env:DOTNET_ROOT_X64 = $dotnetRoot
}
$env:PYTHONIOENCODING = "utf-8"

if (-not $NoClean -and (Test-Path -LiteralPath $distRoot)) {
    $resolvedDist = (Resolve-Path -LiteralPath $distRoot).Path
    $desktopPrefix = $desktopRoot.TrimEnd('\') + '\'
    if (-not $resolvedDist.StartsWith($desktopPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a directory outside desktop: $resolvedDist"
    }
    Remove-Item -LiteralPath $resolvedDist -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $distRoot, $nativeOut, $backendDist, $workRoot, $portableRoot, $portableBackend | Out-Null

Write-Host "== Compile Python backend =="
& $Python -m compileall -q (Join-Path $desktopRoot "backend\app")
if ($LASTEXITCODE -ne 0) {
    throw "Python compileall failed with exit code $LASTEXITCODE"
}

if (-not $SkipTests) {
    Write-Host "== Python regression =="
    & $Python (Join-Path $desktopRoot "backend\app\regression_runner.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Python regression failed with exit code $LASTEXITCODE"
    }

    Write-Host "== Recorder regression build =="
    & $Dotnet build $testProject -c $Configuration
    if ($LASTEXITCODE -ne 0) {
        throw "Recorder regression build failed with exit code $LASTEXITCODE"
    }

    $testExe = Join-Path $desktopRoot "tests\RecorderRegression\bin\$Configuration\net10.0-windows\RecorderRegression.exe"
    if (-not (Test-Path -LiteralPath $testExe -PathType Leaf)) {
        throw "Recorder regression executable was not produced: $testExe"
    }

    Write-Host "== Recorder core regression =="
    $testOutput = Join-Path $distRoot "regression\recorder-core"
    New-Item -ItemType Directory -Force -Path $testOutput | Out-Null
    & $testExe --output $testOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Recorder core regression failed with exit code $LASTEXITCODE"
    }
} else {
    $testExe = Join-Path $desktopRoot "tests\RecorderRegression\bin\$Configuration\net10.0-windows\RecorderRegression.exe"
}

if ($RunSynthetic) {
    if (-not (Test-Path -LiteralPath $testExe -PathType Leaf)) {
        & $Dotnet build $testProject -c $Configuration
        if ($LASTEXITCODE -ne 0) {
            throw "Recorder regression build failed with exit code $LASTEXITCODE"
        }
    }

    $ffmpeg = $env:SYNTH_FFMPEG
    $ffprobe = $env:SYNTH_FFPROBE
    if (-not $ffmpeg) {
        $ffmpegCommand = Get-Command "ffmpeg.exe" -ErrorAction SilentlyContinue
        if ($ffmpegCommand) {
            $ffmpeg = $ffmpegCommand.Source
        }
    }
    if (-not $ffprobe) {
        $ffprobeCommand = Get-Command "ffprobe.exe" -ErrorAction SilentlyContinue
        if ($ffprobeCommand) {
            $ffprobe = $ffprobeCommand.Source
        }
    }
    if (-not $ffmpeg -or -not (Test-Path -LiteralPath $ffmpeg -PathType Leaf)) {
        throw "FFmpeg is required for -RunSynthetic. Set SYNTH_FFMPEG or install ffmpeg.exe."
    }
    if (-not $ffprobe -or -not (Test-Path -LiteralPath $ffprobe -PathType Leaf)) {
        throw "ffprobe is required for -RunSynthetic. Set SYNTH_FFPROBE or install ffprobe.exe."
    }

    $env:SYNTH_FFMPEG = (Resolve-Path -LiteralPath $ffmpeg).Path
    $env:SYNTH_FFPROBE = (Resolve-Path -LiteralPath $ffprobe).Path
    Write-Host "== Synthetic 30-second recording verification =="
    $syntheticOutput = Join-Path $distRoot "regression\recorder-synthetic"
    New-Item -ItemType Directory -Force -Path $syntheticOutput | Out-Null
    & $testExe --synthetic --output $syntheticOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Synthetic recorder verification failed with exit code $LASTEXITCODE"
    }
}

Write-Host "== Publish native WPF application =="
& $Dotnet publish $nativeProject -c $Configuration -r $Runtime --self-contained true -o $nativeOut
if ($LASTEXITCODE -ne 0) {
    throw "dotnet publish failed with exit code $LASTEXITCODE"
}

Write-Host "== Build Python backend =="
& $Python -m PyInstaller --noconfirm --clean --distpath $backendDist --workpath $workRoot $backendSpec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$builtBackend = Join-Path $backendDist "backend"
if (-not (Test-Path -LiteralPath $builtBackend -PathType Container)) {
    throw "PyInstaller backend output was not produced: $builtBackend"
}

Write-Host "== Assemble portable package =="
Copy-Item -Path (Join-Path $nativeOut "*") -Destination $portableRoot -Recurse -Force
Copy-Item -Path (Join-Path $builtBackend "*") -Destination $portableBackend -Recurse -Force

$commit = "unknown"
try {
    $commit = (& git -C $repoRoot rev-parse --short HEAD 2>$null)
} catch {
}

@(
    "Product=Receiving.Native"
    "Version=$Version"
    "Runtime=$Runtime"
    "Commit=$commit"
    "BuiltAt=$([DateTimeOffset]::UtcNow.ToString("o"))"
) | Set-Content -LiteralPath (Join-Path $portableRoot "BUILD-INFO.txt") -Encoding UTF8

$zipPath = Join-Path $distRoot "jst-batch-inbound-desktop-$Version-$Runtime.zip"
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $portableRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal

$nativeExe = Join-Path $portableRoot "Receiving.Native.exe"
$backendExe = Join-Path $portableBackend "Receiving.Service.exe"
$hashPath = Join-Path $distRoot "SHA256SUMS.txt"
@(
    "$((Get-FileHash -Algorithm SHA256 -LiteralPath $nativeExe).Hash.ToLowerInvariant())  Receiving.Native.exe"
    "$((Get-FileHash -Algorithm SHA256 -LiteralPath $backendExe).Hash.ToLowerInvariant())  backend/Receiving.Service.exe"
    "$((Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($zipPath))"
) | Set-Content -LiteralPath $hashPath -Encoding ASCII

Write-Host "== Verify portable package =="
& $verifyScript -PackageRoot $portableRoot
if ($LASTEXITCODE -ne 0) {
    throw "Package verification failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Portable directory: $portableRoot"
Write-Host "ZIP package:        $zipPath"
Write-Host "SHA256:             $hashPath"
