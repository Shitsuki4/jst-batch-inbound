param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$PayloadRoot,
    [string]$Target = "",
    [switch]$SkipDependencyCheck
)

# End-to-end acceptance for the full offline installer: silent install, bundled
# dependency check with a minimal PATH, hash parity with the payload, a reinstall
# that must preserve user data while purging stale program files ([InstallDelete]), and a
# silent uninstall that must also preserve user data.

$ErrorActionPreference = "Stop"

$desktopRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$verifyRoot = Join-Path $desktopRoot "build\verify"
$reportPath = Join-Path $verifyRoot "installer-report.json"
$logPath = Join-Path $verifyRoot "install.log"

function Assert-UnderDesktop {
    param([string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $prefix = $desktopRoot.TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside desktop: $full"
    }
    return $full
}

if (-not $Target) {
    $Target = Join-Path $verifyRoot "installed"
}
$Target = Assert-UnderDesktop -Path $Target

$Installer = (Resolve-Path -LiteralPath $Installer).Path
$PayloadRoot = (Resolve-Path -LiteralPath $PayloadRoot).Path
if (-not (Test-Path -LiteralPath $PayloadRoot -PathType Container)) {
    throw "Payload root not found: $PayloadRoot"
}
if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot "Receiving.Native.exe") -PathType Leaf)) {
    throw "Payload root does not look like an offline payload: $PayloadRoot"
}

$uninstallKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\{80E21946-081D-4C07-A40D-60C2244D7066}_is1"
$userDataRoot = Join-Path $env:LOCALAPPDATA "ReceivingAssistant\v3"

function Test-UninstallKey {
    try {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($uninstallKey)
        if ($key) { $key.Dispose(); return $true }
        return $false
    } catch {
        return $false
    }
}

function Invoke-Process {
    param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSeconds = 600)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    foreach ($argument in $Arguments) { $psi.ArgumentList.Add($argument) }
    $process = [System.Diagnostics.Process]::Start($psi)
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch { }
        throw "Process timed out after $TimeoutSeconds s: $FilePath"
    }
    return $process.ExitCode
}

# WebView2 fixed-version runtime must grant read/execute to the AppContainer package SIDs,
# otherwise the renderer cannot load msedgewebview2.exe and the business window crashes on
# Windows 10. Read the rules as SIDs directly: translating the NTAccount names back throws
# IdentityNotMappedException on .NET.
function Assert-WebViewAcl {
    param([string]$WebViewRoot)
    if (-not (Test-Path -LiteralPath $WebViewRoot -PathType Container)) {
        throw "WebView2 runtime directory not found: $WebViewRoot"
    }
    $rules = (Get-Acl -LiteralPath $WebViewRoot).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
    $sids = @($rules | ForEach-Object { $_.IdentityReference.Value })
    foreach ($sid in @("S-1-15-2-1", "S-1-15-2-2")) {
        if ($sids -notcontains $sid) {
            $listing = (& (Join-Path $env:SystemRoot "System32\icacls.exe") $WebViewRoot 2>&1 | Out-String)
            throw "WebView2 runtime directory is missing the AppContainer ACL for $sid. SIDs: $($sids -join ', '). icacls:`n$listing"
        }
    }
}

New-Item -ItemType Directory -Force -Path $verifyRoot | Out-Null

$checks = New-Object System.Collections.Generic.List[string]
$sentinel = $null
$ok = $false
$errorMessage = $null

try {
    if (Test-UninstallKey) {
        throw "An installation already exists in HKCU ($uninstallKey); refusing to overwrite a live install during acceptance."
    }
    if (Test-Path -LiteralPath $Target) {
        throw "Target directory already exists: $Target"
    }

    Write-Host "== Silent install =="
    $code = Invoke-Process -FilePath $Installer -Arguments @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS",
        "/TASKS=", "/DIR=$Target", "/LOG=$logPath")
    if ($code -ne 0) { throw "Installer exited with code $code" }
    if (-not (Test-Path -LiteralPath (Join-Path $Target "Receiving.Native.exe") -PathType Leaf)) {
        throw "Installed Receiving.Native.exe not found under $Target"
    }
    $checks.Add("Silent install succeeded and produced Receiving.Native.exe")

    foreach ($relative in @(
            "Receiving.Native.dll",
            "runtime\ffmpeg\ffmpeg.exe",
            "runtime\ffmpeg\ffprobe.exe",
            "runtime\webview2\msedgewebview2.exe",
            "installed-mode.txt")) {
        $source = Join-Path $PayloadRoot $relative
        $installed = Join-Path $Target $relative
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Payload is missing $relative" }
        if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) { throw "Installed tree is missing $relative" }
        $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash
        $installedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installed).Hash
        if ($sourceHash -ne $installedHash) { throw "Hash mismatch for $relative" }
    }
    $checks.Add("Bundled host, FFmpeg and WebView2 files match the payload byte-for-byte")

    Assert-WebViewAcl -WebViewRoot (Join-Path $Target "runtime\webview2")
    $checks.Add("WebView2 runtime directory grants read/execute to ALL APPLICATION PACKAGES and ALL RESTRICTED APPLICATION PACKAGES")

    if (-not $SkipDependencyCheck) {
        $out = Join-Path $verifyRoot "dependency-check.json"
        if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
        $installedExe = Join-Path $Target "Receiving.Native.exe"
        $savedPath = $env:PATH
        $env:PATH = Join-Path $env:SystemRoot "System32"
        try {
            $code = Invoke-Process -FilePath $installedExe -Arguments @("--dependency-check", $out) -TimeoutSeconds 180
        } finally {
            $env:PATH = $savedPath
        }
        if ($code -ne 0) { throw "dependency-check exited with code $code" }
        $state = Get-Content -LiteralPath $out -Raw | ConvertFrom-Json
        if (-not $state.Ready) { throw "dependency-check reported not ready: $out" }
        if (-not ([string]$state.Engine).StartsWith($Target, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "dependency-check resolved Engine outside the install dir: $($state.Engine)"
        }
        $checks.Add("--dependency-check passed with PATH limited to System32; Engine resolved inside the install dir")
    }

    New-Item -ItemType Directory -Force -Path $userDataRoot | Out-Null
    $sentinel = Join-Path $userDataRoot ("installer-test-" + [guid]::NewGuid().ToString("N") + ".txt")
    Set-Content -LiteralPath $sentinel -Value "acceptance-only" -Encoding ASCII

    # Simulate leftovers from an older build; [InstallDelete] must purge these program-only dirs.
    $staleFiles = @()
    foreach ($relative in @("backend\_internal", "runtime\webview2")) {
        $dir = Join-Path $Target $relative
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) { throw "Installed tree is missing $relative" }
        $stale = Join-Path $dir ("stale-from-old-build-" + [guid]::NewGuid().ToString("N") + ".txt")
        Set-Content -LiteralPath $stale -Value "stale" -Encoding ASCII
        $staleFiles += $stale
    }

    Write-Host "== Reinstall (must preserve user data, purge stale program files) =="
    $code = Invoke-Process -FilePath $Installer -Arguments @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/TASKS=", "/DIR=$Target")
    if ($code -ne 0) { throw "Reinstall exited with code $code" }
    if ((Get-Content -LiteralPath $sentinel -Raw).Trim() -ne "acceptance-only") {
        throw "Sentinel file changed during reinstall"
    }
    $checks.Add("Reinstall preserved the independent user data directory")
    foreach ($stale in $staleFiles) {
        if (Test-Path -LiteralPath $stale) { throw "Stale file survived reinstall: $stale" }
    }
    foreach ($relative in @("backend\Receiving.Service.exe", "runtime\webview2\msedgewebview2.exe")) {
        $source = Join-Path $PayloadRoot $relative
        $installed = Join-Path $Target $relative
        if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) { throw "Reinstall is missing $relative" }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $installed).Hash) {
            throw "Hash mismatch for $relative after reinstall"
        }
    }
    $payloadInternal = @(Get-ChildItem -LiteralPath (Join-Path $PayloadRoot "backend\_internal") -Recurse -File).Count
    $installedInternal = @(Get-ChildItem -LiteralPath (Join-Path $Target "backend\_internal") -Recurse -File).Count
    if ($payloadInternal -ne $installedInternal) {
        throw "backend\_internal file count differs after reinstall: payload $payloadInternal, installed $installedInternal"
    }
    $checks.Add("Reinstall purged stale files in backend\_internal and runtime\webview2 and restored them from the payload")

    # [InstallDelete] recreates runtime\webview2, so the AppContainer ACLs must be re-applied on upgrade.
    Assert-WebViewAcl -WebViewRoot (Join-Path $Target "runtime\webview2")
    $checks.Add("Reinstall re-applied the WebView2 AppContainer ACLs to the rebuilt runtime directory")

    Write-Host "== Silent uninstall =="
    $uninstaller = Join-Path $Target "unins000.exe"
    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) { throw "Uninstaller not found: $uninstaller" }
    $code = Invoke-Process -FilePath $uninstaller -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    if ($code -ne 0) { throw "Uninstaller exited with code $code" }
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (-not (Test-Path -LiteralPath (Join-Path $Target "Receiving.Native.exe"))) { break }
        Start-Sleep -Milliseconds 500
    }
    if (Test-Path -LiteralPath (Join-Path $Target "Receiving.Native.exe")) {
        throw "Receiving.Native.exe still present after uninstall"
    }
    if ((Get-Content -LiteralPath $sentinel -Raw).Trim() -ne "acceptance-only") {
        throw "Sentinel file changed during uninstall"
    }
    $checks.Add("Uninstall removed the program and preserved the user data directory")

    $ok = $true
} catch {
    $errorMessage = $_.Exception.Message
} finally {
    if ($sentinel -and (Test-Path -LiteralPath $sentinel)) {
        Remove-Item -LiteralPath $sentinel -Force
    }
}

[ordered]@{ ok = $ok; checks = $checks; error = $errorMessage } |
    ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath $reportPath -Encoding UTF8
Get-Content -LiteralPath $reportPath -Raw

if (-not $ok) { exit 1 }
