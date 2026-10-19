param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$PayloadRoot,
    [string]$PortableRoot = "",
    [switch]$SkipPortableCheck
)

# End-to-end acceptance for the runtime dependency installer (installer/deps_setup.iss):
# silent per-user install to %LOCALAPPDATA%\ReceivingAssistant\runtime, hash parity with the
# payload, ffmpeg/ffprobe -version, WebView2 AppContainer ACLs, the portable host resolving the
# shared runtime via --dependency-check (PATH limited to System32), a reinstall that purges stale
# files ([InstallDelete]), and a silent uninstall that removes the runtime but never touches the
# business data directory %LOCALAPPDATA%\ReceivingAssistant\v3.

$ErrorActionPreference = "Stop"

$desktopRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$verifyRoot = Join-Path $desktopRoot "build\verify-deps"
$reportPath = Join-Path $verifyRoot "deps-installer-report.json"
$logPath = Join-Path $verifyRoot "deps-install.log"

$Installer = (Resolve-Path -LiteralPath $Installer).Path
$PayloadRoot = (Resolve-Path -LiteralPath $PayloadRoot).Path
foreach ($relative in @("ffmpeg\ffmpeg.exe", "ffmpeg\ffprobe.exe", "webview2\msedgewebview2.exe")) {
    if (-not (Test-Path -LiteralPath (Join-Path $PayloadRoot $relative) -PathType Leaf)) {
        throw "Payload root does not look like a dependency payload (missing $relative): $PayloadRoot"
    }
}
if (-not $SkipPortableCheck) {
    if (-not $PortableRoot) { $PortableRoot = Join-Path $desktopRoot "dist\portable" }
    $PortableRoot = (Resolve-Path -LiteralPath $PortableRoot).Path
    if (-not (Test-Path -LiteralPath (Join-Path $PortableRoot "Receiving.Native.exe") -PathType Leaf)) {
        throw "Portable package not found: $PortableRoot"
    }
    if (Test-Path -LiteralPath (Join-Path $PortableRoot "installed-mode.txt")) {
        throw "Portable root must not contain installed-mode.txt: $PortableRoot"
    }
    if (Test-Path -LiteralPath (Join-Path $PortableRoot "runtime")) {
        throw "Portable root already bundles runtime\; it would shadow the shared runtime: $PortableRoot"
    }
}

$appRoot = Join-Path $env:LOCALAPPDATA "ReceivingAssistant"
$sharedRoot = Join-Path $appRoot "runtime"
$sharedFfmpeg = Join-Path $sharedRoot "ffmpeg"
$sharedWebView = Join-Path $sharedRoot "webview2"
$userDataRoot = Join-Path $appRoot "v3"
$uninstallKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\{ECF1BF3B-E563-49DA-9060-D8E25941148C}_is1"
# Chinese display name "Shou Huo Xiao Zhu Shou Yun Xing Yi Lai", built from code points so this script stays ASCII.
$expectedDisplayName = -join ([char[]]@(0x6536, 0x8D27, 0x5C0F, 0x52A9, 0x624B, 0x8FD0, 0x884C, 0x4F9D, 0x8D56))

function Get-UninstallValue {
    param([string]$Name)
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($uninstallKey)
    if (-not $key) { return $null }
    try { return $key.GetValue($Name) } finally { $key.Dispose() }
}

function Test-UninstallKey {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($uninstallKey)
    if ($key) { $key.Dispose(); return $true }
    return $false
}

function Invoke-Process {
    param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSeconds = 600, [switch]$Capture)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    if ($Capture) {
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
    }
    foreach ($argument in $Arguments) { $psi.ArgumentList.Add($argument) }
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = ""
    $stderr = ""
    if ($Capture) {
        $outTask = $process.StandardOutput.ReadToEndAsync()
        $errTask = $process.StandardError.ReadToEndAsync()
    }
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch { }
        throw "Process timed out after $TimeoutSeconds s: $FilePath"
    }
    if ($Capture) {
        $stdout = $outTask.GetAwaiter().GetResult()
        $stderr = $errTask.GetAwaiter().GetResult()
    }
    return [pscustomobject]@{ ExitCode = $process.ExitCode; StdOut = $stdout; StdErr = $stderr }
}

function Assert-SameFile {
    param([string]$Relative)
    $source = Join-Path $PayloadRoot $Relative
    $installed = Join-Path $sharedRoot $Relative
    if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) { throw "Installed runtime is missing $Relative" }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $installed).Hash) {
        throw "Hash mismatch for $Relative"
    }
}

function Assert-FileCount {
    param([string]$Relative)
    $payloadCount = @(Get-ChildItem -LiteralPath (Join-Path $PayloadRoot $Relative) -Recurse -File).Count
    $installedCount = @(Get-ChildItem -LiteralPath (Join-Path $sharedRoot $Relative) -Recurse -File).Count
    if ($payloadCount -ne $installedCount) {
        throw "$Relative file count differs: payload $payloadCount, installed $installedCount"
    }
}

function Invoke-DependencyCheck {
    param([string]$Name)
    $out = Join-Path $verifyRoot "$Name.json"
    if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
    $savedPath = $env:PATH
    $env:PATH = Join-Path $env:SystemRoot "System32"
    try {
        $result = Invoke-Process -FilePath (Join-Path $PortableRoot "Receiving.Native.exe") -Arguments @("--dependency-check", $out) -TimeoutSeconds 180
    } finally {
        $env:PATH = $savedPath
    }
    if (-not (Test-Path -LiteralPath $out -PathType Leaf)) { throw "dependency-check wrote no report (exit $($result.ExitCode))" }
    $state = Get-Content -LiteralPath $out -Raw | ConvertFrom-Json
    return [pscustomobject]@{ ExitCode = $result.ExitCode; State = $state; Path = $out }
}

function Wait-Gone {
    param([string]$Path, [int]$Seconds = 60)
    for ($i = 0; $i -lt ($Seconds * 2); $i++) {
        if (-not (Test-Path -LiteralPath $Path)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return -not (Test-Path -LiteralPath $Path)
}

New-Item -ItemType Directory -Force -Path $verifyRoot | Out-Null

$checks = New-Object System.Collections.Generic.List[string]
$sentinel = $null
$ok = $false
$errorMessage = $null

try {
    if (Test-UninstallKey) {
        throw "A dependency runtime is already registered in HKCU ($uninstallKey); refusing to overwrite it during acceptance."
    }
    foreach ($dir in @($sharedFfmpeg, $sharedWebView)) {
        if (Test-Path -LiteralPath $dir) { throw "Shared runtime directory already exists: $dir" }
    }

    New-Item -ItemType Directory -Force -Path $userDataRoot | Out-Null
    $sentinel = Join-Path $userDataRoot ("deps-installer-test-" + [guid]::NewGuid().ToString("N") + ".txt")
    Set-Content -LiteralPath $sentinel -Value "acceptance-only" -Encoding ASCII
    $userFilesBefore = @(Get-ChildItem -LiteralPath $userDataRoot -Recurse -File | ForEach-Object { $_.FullName }) | Sort-Object

    Write-Host "== Silent install =="
    $result = Invoke-Process -FilePath $Installer -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$logPath")
    if ($result.ExitCode -ne 0) { throw "Installer exited with code $($result.ExitCode)" }
    foreach ($relative in @("ffmpeg\ffmpeg.exe", "ffmpeg\ffprobe.exe", "webview2\msedgewebview2.exe")) {
        Assert-SameFile -Relative $relative
    }
    foreach ($dll in @(Get-ChildItem -LiteralPath (Join-Path $PayloadRoot "ffmpeg") -Filter "*.dll" -File)) {
        Assert-SameFile -Relative ("ffmpeg\" + $dll.Name)
    }
    Assert-FileCount -Relative "ffmpeg"
    Assert-FileCount -Relative "webview2"
    $checks.Add("Silent per-user install wrote ffmpeg/ffprobe and the WebView2 fixed runtime to $sharedRoot, matching the payload")

    $displayName = Get-UninstallValue -Name "DisplayName"
    if ($displayName -ne $expectedDisplayName) { throw "Unexpected uninstall DisplayName: '$displayName'" }
    $uninstallString = [string](Get-UninstallValue -Name "UninstallString")
    if (-not $uninstallString) { throw "UninstallString is missing" }
    $checks.Add("Separate HKCU uninstall entry registered with the expected display name")

    foreach ($tool in @("ffmpeg", "ffprobe")) {
        $exe = Join-Path $sharedFfmpeg "$tool.exe"
        $run = Invoke-Process -FilePath $exe -Arguments @("-hide_banner", "-version") -TimeoutSeconds 60 -Capture
        if ($run.ExitCode -ne 0) { throw "$tool -version exited with code $($run.ExitCode): $($run.StdErr)" }
        if ($run.StdOut -notmatch "^$tool version ") { throw "$tool -version printed unexpected output: $($run.StdOut.Substring(0, [Math]::Min(200, $run.StdOut.Length)))" }
        Write-Host ($run.StdOut -split "`n")[0]
    }
    $checks.Add("Installed ffmpeg -version and ffprobe -version run successfully")

    # Read the rules as SIDs directly: translating "APPLICATION PACKAGE AUTHORITY\..." NTAccount names
    # back to SIDs throws IdentityNotMappedException on .NET.
    $rules = (Get-Acl -LiteralPath $sharedWebView).GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
    $sids = @($rules | ForEach-Object { $_.IdentityReference.Value })
    foreach ($sid in @("S-1-15-2-1", "S-1-15-2-2")) {
        if ($sids -notcontains $sid) {
            $listing = (& (Join-Path $env:SystemRoot "System32\icacls.exe") $sharedWebView 2>&1 | Out-String)
            throw "WebView2 runtime directory is missing the AppContainer ACL for $sid. SIDs: $($sids -join ', '). icacls:`n$listing"
        }
    }
    $checks.Add("WebView2 runtime directory grants read/execute to ALL APPLICATION PACKAGES and ALL RESTRICTED APPLICATION PACKAGES")

    if (-not $SkipPortableCheck) {
        $check = Invoke-DependencyCheck -Name "portable-dependency-check"
        $state = $check.State
        if ($check.ExitCode -ne 0 -or -not $state.Ready) { throw "Portable dependency-check is not ready (exit $($check.ExitCode)): $($state.Detail)" }
        if (-not ([string]$state.Engine).StartsWith($sharedFfmpeg + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Portable host resolved Engine outside the shared runtime: $($state.Engine)"
        }
        if ([string]$state.BrowserFolder -ne $sharedWebView) {
            throw "Portable host did not select the shared WebView2 runtime: '$($state.BrowserFolder)'"
        }
        $checks.Add("Portable host --dependency-check (PATH limited to System32) is ready and resolves FFmpeg and WebView2 from the shared runtime")
    }

    # Simulate leftovers from an older runtime; [InstallDelete] must purge them on upgrade.
    $staleFiles = @()
    foreach ($dir in @($sharedFfmpeg, $sharedWebView)) {
        $stale = Join-Path $dir ("stale-from-old-build-" + [guid]::NewGuid().ToString("N") + ".txt")
        Set-Content -LiteralPath $stale -Value "stale" -Encoding ASCII
        $staleFiles += $stale
    }

    Write-Host "== Reinstall (must purge stale runtime files) =="
    $result = Invoke-Process -FilePath $Installer -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    if ($result.ExitCode -ne 0) { throw "Reinstall exited with code $($result.ExitCode)" }
    foreach ($stale in $staleFiles) {
        if (Test-Path -LiteralPath $stale) { throw "Stale file survived reinstall: $stale" }
    }
    Assert-SameFile -Relative "ffmpeg\ffmpeg.exe"
    Assert-SameFile -Relative "webview2\msedgewebview2.exe"
    Assert-FileCount -Relative "webview2"
    $checks.Add("Reinstall purged stale files in runtime\ffmpeg and runtime\webview2 and restored them from the payload")

    Write-Host "== Silent uninstall =="
    $uninstaller = $uninstallString.Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) { throw "Uninstaller not found: $uninstaller" }
    $result = Invoke-Process -FilePath $uninstaller -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    if ($result.ExitCode -ne 0) { throw "Uninstaller exited with code $($result.ExitCode)" }
    foreach ($dir in @($sharedFfmpeg, $sharedWebView)) {
        if (-not (Wait-Gone -Path $dir)) { throw "Runtime directory still present after uninstall: $dir" }
    }
    for ($i = 0; $i -lt 60 -and (Test-UninstallKey); $i++) { Start-Sleep -Milliseconds 500 }
    if (Test-UninstallKey) { throw "Uninstall entry still registered after uninstall" }
    $checks.Add("Silent uninstall removed runtime\ffmpeg, runtime\webview2 and the uninstall entry")

    if ((Get-Content -LiteralPath $sentinel -Raw).Trim() -ne "acceptance-only") {
        throw "Sentinel file in the business data directory changed"
    }
    $userFilesAfter = @(Get-ChildItem -LiteralPath $userDataRoot -Recurse -File | ForEach-Object { $_.FullName }) | Sort-Object
    if (Compare-Object -ReferenceObject @($userFilesBefore) -DifferenceObject @($userFilesAfter)) {
        throw "Files under $userDataRoot changed during install/uninstall"
    }
    $checks.Add("Business data directory %LOCALAPPDATA%\ReceivingAssistant\v3 was untouched by install, reinstall and uninstall")

    if (-not $SkipPortableCheck) {
        $after = Invoke-DependencyCheck -Name "portable-dependency-check-after-uninstall"
        if (([string]$after.State.Engine).StartsWith($sharedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Portable host still resolves FFmpeg from the removed shared runtime: $($after.State.Engine)"
        }
        if ($after.State.BrowserFolder) {
            throw "Portable host still selects a WebView2 folder after uninstall: $($after.State.BrowserFolder)"
        }
        $checks.Add("After uninstall the portable host falls back (BrowserFolder=null -> system Evergreen; FFmpeg -> settings/winget/PATH)")
    }

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
