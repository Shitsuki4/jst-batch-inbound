param(
    [Parameter(Mandatory = $true)]
    [string]$PackageRoot,
    [switch]$SkipBackendStart
)

$ErrorActionPreference = "Stop"
$package = (Resolve-Path -LiteralPath $PackageRoot).Path

function Assert-File {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required package file is missing: $Path"
    }
    if ((Get-Item -LiteralPath $Path).Length -le 0) {
        throw "Required package file is empty: $Path"
    }
}

function Assert-Directory {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Required package directory is missing: $Path"
    }
}

$nativeExe = Join-Path $package "Receiving.Native.exe"
$nativeDll = Join-Path $package "Receiving.Native.dll"
$nativeDeps = Join-Path $package "Receiving.Native.deps.json"
$nativeRuntime = Join-Path $package "Receiving.Native.runtimeconfig.json"
$backendExe = Join-Path $package "backend\Receiving.Service.exe"
$backendInternal = Join-Path $package "backend\_internal"

Assert-File $nativeExe
Assert-File $nativeDll
Assert-File $nativeDeps
Assert-File $nativeRuntime
Assert-File $backendExe
Assert-Directory $backendInternal
# Remote management page (IPv6, read-only) static assets must ship with the backend.
foreach ($asset in @("index.html", "remote.js", "remote.css", "icon.svg")) {
    Assert-File (Join-Path $backendInternal ("remote_web\" + $asset))
}

if (Test-Path -LiteralPath (Join-Path $package "installed-mode.txt")) {
    throw "Portable package must not contain installed-mode.txt"
}

if (-not $SkipBackendStart) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()

    $token = [Guid]::NewGuid().ToString("N")
    $verifyRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("receiving-package-verify-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $verifyRoot | Out-Null

    $envNames = @("JST_DB_DIR", "JST_NATIVE_PORT", "JST_NATIVE_TOKEN", "JST_NO_BROWSER")
    $previous = @{}
    foreach ($name in $envNames) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    }

    $process = $null
    try {
        $env:JST_DB_DIR = $verifyRoot
        $env:JST_NATIVE_PORT = $port.ToString()
        $env:JST_NATIVE_TOKEN = $token
        $env:JST_NO_BROWSER = "1"

        $process = Start-Process -FilePath $backendExe -WorkingDirectory (Split-Path -Parent $backendExe) -PassThru -WindowStyle Hidden
        $healthUrl = "http://127.0.0.1:$port/api/native/health"
        $headers = @{ "X-Native-Token" = $token }
        $ready = $false

        for ($i = 0; $i -lt 80; $i++) {
            if ($process.HasExited) {
                throw "Packaged backend exited before becoming healthy with code $($process.ExitCode)"
            }
            try {
                $response = Invoke-WebRequest -Uri $healthUrl -Headers $headers -UseBasicParsing -TimeoutSec 2
                if ($response.StatusCode -eq 200 -and $response.Content -match '"ok"\s*:\s*true') {
                    $ready = $true
                    break
                }
            } catch {
            }
            Start-Sleep -Milliseconds 250
        }

        if (-not $ready) {
            throw "Packaged backend did not become healthy within 20 seconds"
        }

        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/native/shutdown" -Method Post -Headers $headers -UseBasicParsing -TimeoutSec 3 | Out-Null
        } catch {
        }

        if (-not $process.WaitForExit(20000)) {
            throw "Packaged backend did not stop within 20 seconds"
        }
        if ($process.ExitCode -ne 0) {
            throw "Packaged backend stopped with exit code $($process.ExitCode)"
        }
    } finally {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
        }

        foreach ($name in $envNames) {
            if ($null -eq $previous[$name]) {
                Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
            } else {
                Set-Item -Path "Env:$name" -Value $previous[$name]
            }
        }

        $tempPrefix = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        $resolvedVerify = [System.IO.Path]::GetFullPath($verifyRoot)
        if ($resolvedVerify.StartsWith($tempPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedVerify -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "Package verification passed: $package"
