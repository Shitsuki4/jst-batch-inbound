param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$PayloadRoot,
    [string]$Target = "",
    [switch]$SkipDependencyCheck,
    # The /PURGEDATA check deletes %LOCALAPPDATA%\ReceivingAssistant\v3. It always runs on GitHub
    # Actions (ephemeral runner); on a workstation it only runs with this switch.
    [switch]$AllowDataPurge
)

# End-to-end acceptance for the full offline installer: silent install, bundled
# dependency check with a minimal PATH, hash parity with the payload, a reinstall
# that must preserve user data while purging stale program files ([InstallDelete]), the Start
# menu shortcuts (including the uninstall shortcut), an uninstall that is refused while the app
# runs unless /FORCECLOSE is given, a default silent uninstall that preserves user data, a
# reinstall afterwards, and a /PURGEDATA uninstall that removes the v3 data but never the
# separate runtime-dependency directory.

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

function Get-RegisteredUninstaller {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($uninstallKey)
    if (-not $key) { throw "Uninstall entry not found: $uninstallKey" }
    try { $value = [string]$key.GetValue("UninstallString") } finally { $key.Dispose() }
    $path = $value.Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Registered uninstaller not found: '$value'" }
    return $path
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

function Get-Text {
    param([int[]]$Codes)
    return -join ($Codes | ForEach-Object { [char]$_ })
}

function Test-Mutex {
    param([string]$Name)
    $mutex = $null
    try {
        if ([System.Threading.Mutex]::TryOpenExisting($Name, [ref]$mutex)) {
            $mutex.Dispose()
            return $true
        }
    } catch [System.UnauthorizedAccessException] {
        return $true
    }
    return $false
}

function Get-ProcessesUnder {
    param([string]$Root)
    $prefix = $Root.TrimEnd('\') + '\'
    return @(Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
        })
}

function Wait-Removed {
    param([string]$Path, [int]$TimeoutSeconds = 60)
    for ($attempt = 0; $attempt -lt ($TimeoutSeconds * 2); $attempt++) {
        if (-not (Test-Path -LiteralPath $Path)) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Still present after $TimeoutSeconds s: $Path"
}

function Get-ShortcutTarget {
    param([string]$Path)
    $target = ""
    try { $target = [string](New-Object -ComObject WScript.Shell).CreateShortcut($Path).TargetPath } catch { }
    if (-not $target) {
        try {
            $folder = (New-Object -ComObject Shell.Application).Namespace((Split-Path -Parent $Path))
            $item = $folder.ParseName((Split-Path -Leaf $Path))
            if ($item -and $item.IsLink) { $target = [string]$item.GetLink.Path }
        } catch { }
    }
    return $target
}

function Assert-Shortcut {
    param([string]$Path, [string]$ExpectedTarget)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Start menu shortcut not found: $Path" }
    $target = Get-ShortcutTarget -Path $Path
    if ($target) {
        if (-not [string]::Equals([System.IO.Path]::GetFullPath($target), [System.IO.Path]::GetFullPath($ExpectedTarget), [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Shortcut $Path points at '$target', expected '$ExpectedTarget'"
        }
        return
    }
    # Neither shell API resolved the target; fall back to the link's own LinkInfo / ID list,
    # which store the target path (ANSI and/or UTF-16).
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $leaf = Split-Path -Leaf $ExpectedTarget
    $ansi = [System.Text.Encoding]::Default.GetString($bytes)
    $wide = [System.Text.Encoding]::Unicode.GetString($bytes)
    $wideOdd = if ($bytes.Length -gt 1) { [System.Text.Encoding]::Unicode.GetString($bytes, 1, $bytes.Length - 1) } else { "" }
    foreach ($text in @($ansi, $wide, $wideOdd)) {
        if ($text.IndexOf($leaf, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { return }
    }
    throw "Shortcut $Path does not reference $leaf ($($bytes.Length) bytes)"
}

New-Item -ItemType Directory -Force -Path $verifyRoot | Out-Null

$checks = New-Object System.Collections.Generic.List[string]
$sentinel = $null
$appProcess = $null
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

    $startMenu = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
    $appName = Get-Text @(0x6536, 0x8D27, 0x5C0F, 0x52A9, 0x624B)
    $uninstallName = (Get-Text @(0x5378, 0x8F7D)) + $appName
    $appShortcut = Join-Path $startMenu ($appName + ".lnk")
    $uninstallShortcut = Join-Path $startMenu ($uninstallName + ".lnk")
    $uninstaller = Join-Path $Target "unins000.exe"
    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) { throw "Uninstaller not found: $uninstaller" }
    Assert-Shortcut -Path $appShortcut -ExpectedTarget (Join-Path $Target "Receiving.Native.exe")
    Assert-Shortcut -Path $uninstallShortcut -ExpectedTarget $uninstaller
    $checks.Add("Start menu has the app shortcut and the uninstall shortcut pointing at unins000.exe")

    Write-Host "== Uninstall while the app is running (must be refused without /FORCECLOSE) =="
    $appProcess = Start-Process -FilePath (Join-Path $Target "Receiving.Native.exe") -WorkingDirectory $Target -PassThru
    $mutexSeen = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($appProcess.HasExited) { throw "Receiving.Native.exe exited early with code $($appProcess.ExitCode)" }
        if (Test-Mutex "Local\ReceivingAssistantInstalled") { $mutexSeen = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $mutexSeen) { throw "Receiving.Native.exe did not create Local\ReceivingAssistantInstalled within 60 s" }
    # Let the host start its backend / WebView2 children so the force-close covers a realistic process tree.
    Start-Sleep -Seconds 10
    $running = @(Get-ProcessesUnder -Root $Target)
    if ($running.Count -lt 1) { throw "No process from the install dir is running before the uninstall test" }
    $checks.Add("Started the installed app: $($running.Count) process(es) from the install dir ($((@($running | ForEach-Object { $_.Name }) | Sort-Object -Unique) -join ', '))")

    $refusedLog = Join-Path $verifyRoot "uninstall-refused.log"
    $code = Invoke-Process -FilePath $uninstaller -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$refusedLog")
    Start-Sleep -Seconds 3
    if (-not (Test-Path -LiteralPath (Join-Path $Target "Receiving.Native.exe"))) { throw "Uninstall without /FORCECLOSE removed the program while the app was running" }
    if ($appProcess.HasExited) { throw "Uninstall without /FORCECLOSE terminated the running app" }
    if (-not (Test-UninstallKey)) { throw "Uninstall without /FORCECLOSE removed the uninstall entry" }
    if (-not (Test-Path -LiteralPath $refusedLog) -or -not ((Get-Content -LiteralPath $refusedLog -Raw) -match "FORCECLOSE was not given")) {
        throw "Uninstall log does not show the running-app refusal: $refusedLog"
    }
    $checks.Add("Silent uninstall without /FORCECLOSE was refused (exit $code) and left the running app, program files and uninstall entry intact")

    Write-Host "== Uninstall with /FORCECLOSE (default: keep all data) =="
    $dataDirBefore = Test-Path -LiteralPath (Join-Path $userDataRoot "data")
    $forceLog = Join-Path $verifyRoot "uninstall-force.log"
    $code = Invoke-Process -FilePath $uninstaller -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/FORCECLOSE", "/LOG=$forceLog")
    if ($code -ne 0) { throw "Uninstaller with /FORCECLOSE exited with code $code (log: $forceLog)" }
    if (-not $appProcess.WaitForExit(30000)) { throw "The running app survived uninstall /FORCECLOSE" }
    Wait-Removed -Path (Join-Path $Target "Receiving.Native.exe")
    $left = @(Get-ProcessesUnder -Root $Target)
    if ($left.Count -gt 0) { throw "Processes from the install dir survived /FORCECLOSE: $(($left | ForEach-Object { "$($_.ProcessId) $($_.ExecutablePath)" }) -join '; ')" }
    if (Test-UninstallKey) { throw "Uninstall entry still present after uninstall /FORCECLOSE" }
    foreach ($shortcut in @($appShortcut, $uninstallShortcut)) {
        if (Test-Path -LiteralPath $shortcut) { throw "Start menu shortcut survived uninstall: $shortcut" }
    }
    if ((Get-Content -LiteralPath $sentinel -Raw).Trim() -ne "acceptance-only") {
        throw "Sentinel file changed during uninstall"
    }
    if ($dataDirBefore -and -not (Test-Path -LiteralPath (Join-Path $userDataRoot "data"))) {
        throw "Default silent uninstall deleted $userDataRoot\data"
    }
    # The uninstaller deletes its own unins000.exe/.dat last; a reinstall that starts before that
    # finishes picks unins001.exe instead. Wait so the reinstall below starts from a clean state.
    # Not fatal: the reinstall reads the registered uninstaller from the uninstall entry anyway.
    try {
        Wait-Removed -Path $uninstaller -TimeoutSeconds 30
        Wait-Removed -Path ([System.IO.Path]::ChangeExtension($uninstaller, ".dat")) -TimeoutSeconds 30
    } catch {
        Write-Host "Note: $($_.Exception.Message)"
    }
    $checks.Add("Silent uninstall /FORCECLOSE ended the app's processes, removed the program, entry and shortcuts, and kept all user data (default)")

    Write-Host "== Reinstall after uninstall =="
    $code = Invoke-Process -FilePath $Installer -Arguments @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/TASKS=", "/DIR=$Target")
    if ($code -ne 0) { throw "Reinstall after uninstall exited with code $code" }
    if (-not (Test-Path -LiteralPath (Join-Path $Target "Receiving.Native.exe") -PathType Leaf)) { throw "Reinstall after uninstall produced no Receiving.Native.exe" }
    $uninstaller = Get-RegisteredUninstaller
    Assert-Shortcut -Path $uninstallShortcut -ExpectedTarget $uninstaller
    if ((Get-Content -LiteralPath $sentinel -Raw).Trim() -ne "acceptance-only") { throw "Sentinel file changed during reinstall after uninstall" }
    $checks.Add("Reinstall after uninstall succeeded and found the kept user data")

    if ($AllowDataPurge -or $env:GITHUB_ACTIONS -eq "true") {
        Write-Host "== Uninstall with /PURGEDATA (must delete v3 data, keep runtime) =="
        $runtimeRoot = Join-Path $env:LOCALAPPDATA "ReceivingAssistant\runtime"
        $runtimeExisted = Test-Path -LiteralPath $runtimeRoot
        New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
        $runtimeSentinel = Join-Path $runtimeRoot ("offline-verify-" + [guid]::NewGuid().ToString("N") + ".txt")
        Set-Content -LiteralPath $runtimeSentinel -Value "runtime-must-survive" -Encoding ASCII
        $exportDir = Join-Path $Target ("backend\" + (Get-Text @(0x5BFC, 0x51FA)))
        New-Item -ItemType Directory -Force -Path $exportDir | Out-Null
        Set-Content -LiteralPath (Join-Path $exportDir "verify-export.csv") -Value "a,b" -Encoding ASCII
        $recordingDir = Join-Path $userDataRoot (Get-Text @(0x5F55, 0x50CF))
        New-Item -ItemType Directory -Force -Path $recordingDir | Out-Null
        Set-Content -LiteralPath (Join-Path $recordingDir "verify.mp4") -Value "not-a-video" -Encoding ASCII
        try {
            $purgeLog = Join-Path $verifyRoot "uninstall-purge.log"
            $code = Invoke-Process -FilePath $uninstaller -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/PURGEDATA", "/LOG=$purgeLog")
            if ($code -ne 0) { throw "Uninstaller with /PURGEDATA exited with code $code (log: $purgeLog)" }
            Wait-Removed -Path (Join-Path $Target "Receiving.Native.exe")
            Wait-Removed -Path $userDataRoot
            Wait-Removed -Path $exportDir
            if (-not (Test-Path -LiteralPath $runtimeSentinel)) { throw "/PURGEDATA touched the runtime-dependency directory: $runtimeRoot" }
            if (Test-UninstallKey) { throw "Uninstall entry still present after uninstall /PURGEDATA" }
        } finally {
            Remove-Item -LiteralPath $runtimeSentinel -Force -ErrorAction SilentlyContinue
            if (-not $runtimeExisted) { Remove-Item -LiteralPath $runtimeRoot -Force -ErrorAction SilentlyContinue }
        }
        $checks.Add("Silent uninstall /PURGEDATA deleted $userDataRoot and the install-dir exports, and left $runtimeRoot untouched")
    } else {
        Write-Host "== Silent uninstall (purge check skipped; pass -AllowDataPurge to run it) =="
        $code = Invoke-Process -FilePath $uninstaller -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
        if ($code -ne 0) { throw "Uninstaller exited with code $code" }
        Wait-Removed -Path (Join-Path $Target "Receiving.Native.exe")
        $checks.Add("Skipped the /PURGEDATA check outside CI (it would delete $userDataRoot); uninstalled with data kept")
    }

    $ok = $true
} catch {
    $errorMessage = $_.Exception.Message
} finally {
    if ($appProcess -and -not $appProcess.HasExited) {
        try { $appProcess.Kill($true) } catch { }
    }
    if ($sentinel -and (Test-Path -LiteralPath $sentinel)) {
        Remove-Item -LiteralPath $sentinel -Force
    }
}

[ordered]@{ ok = $ok; checks = $checks; error = $errorMessage } |
    ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath $reportPath -Encoding UTF8
Get-Content -LiteralPath $reportPath -Raw

if (-not $ok) { exit 1 }
