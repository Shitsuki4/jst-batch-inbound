param(
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$OfflineInstaller,
    [Parameter(Mandatory = $true)][string]$DepsInstaller,
    [Parameter(Mandatory = $true)][string]$PayloadRoot,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$PortableRoot = "",
    # Scenario C imports test data into %LOCALAPPDATA%\ReceivingAssistant\v3 and deletes it with
    # /PURGEDATA afterwards. It always runs on GitHub Actions (ephemeral runner); on a workstation
    # only with this switch.
    [switch]$AllowDataReset
)

# End-to-end acceptance for the slim upgrade installer (installer/offline_setup.iss with /DUpgradeOnly):
#  A. a machine with neither the offline full install nor the deps installer: refused, nothing installed;
#  B. over an offline full install: refused while the installed runtime differs from the pinned build,
#     then an in-place upgrade that replaces the program, purges stale backend files, keeps runtime\
#     and the v3 data untouched, and still resolves the bundled runtime; uninstall removes everything
#     the full installer put there;
#  C. over the deps installer with a portable folder: installs, resolves the shared runtime, copies the
#     portable data into v3 (/PORTABLEDATA) without touching the portable folder, refuses a second
#     import over existing data, and a /PURGEDATA uninstall leaves the shared runtime and the portable
#     folder (including its recordings) alone.

$ErrorActionPreference = "Stop"

$desktopRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$verifyRoot = Join-Path $desktopRoot "build\verify-upgrade"
$reportPath = Join-Path $verifyRoot "upgrade-installer-report.json"
$Target = Join-Path $verifyRoot "installed"
$portableCopy = Join-Path $verifyRoot "portable-copy"

$Installer = (Resolve-Path -LiteralPath $Installer).Path
$OfflineInstaller = (Resolve-Path -LiteralPath $OfflineInstaller).Path
$DepsInstaller = (Resolve-Path -LiteralPath $DepsInstaller).Path
$PayloadRoot = (Resolve-Path -LiteralPath $PayloadRoot).Path
if (-not $PortableRoot) { $PortableRoot = Join-Path $desktopRoot "dist\portable" }
$PortableRoot = (Resolve-Path -LiteralPath $PortableRoot).Path
if (Test-Path -LiteralPath (Join-Path $PayloadRoot "runtime")) { throw "Upgrade payload must not contain runtime\: $PayloadRoot" }

$appRoot = Join-Path $env:LOCALAPPDATA "ReceivingAssistant"
$sharedRoot = Join-Path $appRoot "runtime"
$sharedFfmpeg = Join-Path $sharedRoot "ffmpeg"
$sharedWebView = Join-Path $sharedRoot "webview2"
$userDataRoot = Join-Path $appRoot "v3"
$appKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\{80E21946-081D-4C07-A40D-60C2244D7066}_is1"
$depsKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\{ECF1BF3B-E563-49DA-9060-D8E25941148C}_is1"

function Get-Text {
    param([int[]]$Codes)
    return -join ($Codes | ForEach-Object { [char]$_ })
}
$recordingsName = Get-Text @(0x5F55, 0x50CF)

function Test-Key {
    param([string]$Key)
    $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Key)
    if ($k) { $k.Dispose(); return $true }
    return $false
}

function Get-KeyValue {
    param([string]$Key, [string]$Name)
    $k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Key)
    if (-not $k) { return $null }
    try { return $k.GetValue($Name) } finally { $k.Dispose() }
}

function Get-Uninstaller {
    param([string]$Key)
    $value = [string](Get-KeyValue -Key $Key -Name "UninstallString")
    $path = $value.Trim().Trim('"')
    if (-not $path -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Registered uninstaller not found for ${Key}: '$value'" }
    return $path
}

function Invoke-Process {
    param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSeconds = 900)
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

function Wait-Removed {
    param([string]$Path, [int]$TimeoutSeconds = 60)
    for ($i = 0; $i -lt ($TimeoutSeconds * 2); $i++) {
        if (-not (Test-Path -LiteralPath $Path)) { return }
        Start-Sleep -Milliseconds 500
    }
    throw "Still present after $TimeoutSeconds s: $Path"
}

function Wait-KeyGone {
    param([string]$Key)
    for ($i = 0; $i -lt 120 -and (Test-Key $Key); $i++) { Start-Sleep -Milliseconds 500 }
    if (Test-Key $Key) { throw "Uninstall entry still registered: $Key" }
}

function Get-Sha {
    param([string]$Path)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
}

function Get-TreeHashes {
    param([string]$Root)
    $prefix = $Root.TrimEnd('\').Length + 1
    return @(Get-ChildItem -LiteralPath $Root -Recurse -File | Sort-Object FullName | ForEach-Object {
            "{0} {1}" -f $_.FullName.Substring($prefix), (Get-Sha $_.FullName)
        })
}

function Read-Log {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) { return Get-Content -LiteralPath $Path -Raw }
    return ""
}

function Invoke-Upgrade {
    param([string]$Log, [string[]]$Extra = @())
    return Invoke-Process -FilePath $Installer -Arguments (@("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/TASKS=", "/LOG=$Log") + $Extra)
}

function Invoke-DependencyCheck {
    param([string]$Exe, [string]$Name)
    $out = Join-Path $verifyRoot "$Name.json"
    if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
    $savedPath = $env:PATH
    $env:PATH = Join-Path $env:SystemRoot "System32"
    try { $code = Invoke-Process -FilePath $Exe -Arguments @("--dependency-check", $out) -TimeoutSeconds 180 } finally { $env:PATH = $savedPath }
    if (-not (Test-Path -LiteralPath $out -PathType Leaf)) { throw "dependency-check wrote no report (exit $code)" }
    $state = Get-Content -LiteralPath $out -Raw | ConvertFrom-Json
    if ($code -ne 0 -or -not $state.Ready) { throw "$Name is not ready (exit $code): $($state.Detail)" }
    return $state
}

function Uninstall-Registered {
    param([string]$Key, [string[]]$Extra = @())
    $uninstaller = Get-Uninstaller -Key $Key
    $code = Invoke-Process -FilePath $uninstaller -Arguments (@("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") + $Extra)
    if ($code -ne 0) { throw "Uninstaller $uninstaller exited with code $code" }
    Wait-KeyGone -Key $Key
    try {
        Wait-Removed -Path $uninstaller -TimeoutSeconds 30
        Wait-Removed -Path ([System.IO.Path]::ChangeExtension($uninstaller, ".dat")) -TimeoutSeconds 30
    } catch { Write-Host "Note: $($_.Exception.Message)" }
}

function Test-DataPresent {
    if (Test-Path -LiteralPath (Join-Path $userDataRoot "native-settings.json")) { return $true }
    $data = Join-Path $userDataRoot "data"
    return (Test-Path -LiteralPath $data) -and @(Get-ChildItem -LiteralPath $data -Force).Count -gt 0
}

New-Item -ItemType Directory -Force -Path $verifyRoot | Out-Null
$checks = New-Object System.Collections.Generic.List[string]
$ok = $false
$errorMessage = $null

try {
    if (-not ($AllowDataReset -or $env:GITHUB_ACTIONS -eq "true")) {
        throw "This acceptance imports into and purges $userDataRoot; run it on CI or pass -AllowDataReset."
    }
    foreach ($key in @($appKey, $depsKey)) {
        if (Test-Key $key) { throw "Already registered in HKCU, refusing to run over a real install: $key" }
    }
    foreach ($dir in @($sharedFfmpeg, $sharedWebView)) {
        if (Test-Path -LiteralPath $dir) { throw "Shared runtime directory already exists: $dir" }
    }
    if (Test-DataPresent) { throw "$userDataRoot already holds data; refusing to run." }
    if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }

    # ---------------------------------------------------------------- A. neither prerequisite
    Write-Host "== A. Upgrade installer on a machine without any runtime (must refuse) =="
    $logA = Join-Path $verifyRoot "a-refused.log"
    $code = Invoke-Upgrade -Log $logA -Extra @("/DIR=$Target")
    if ($code -eq 0) { throw "Upgrade installer succeeded without any runtime (log: $logA)" }
    if (Test-Path -LiteralPath (Join-Path $Target "Receiving.Native.exe")) { throw "Refused upgrade still installed files into $Target" }
    if (Test-Key $appKey) { throw "Refused upgrade registered an uninstall entry" }
    if ((Read-Log $logA) -notmatch "Upgrade installer refused") { throw "Log of the refused run has no refusal line: $logA" }
    $checks.Add("A: without the offline full install or the deps installer the upgrade installer refuses (exit $code) and installs nothing")

    # ---------------------------------------------------------------- B. over the offline full install
    Write-Host "== B. Offline full install, then upgrade in place =="
    $code = Invoke-Process -FilePath $OfflineInstaller -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS", "/TASKS=", "/DIR=$Target", "/LOG=$(Join-Path $verifyRoot 'b-full.log')")
    if ($code -ne 0) { throw "Offline full installer exited with code $code" }
    $runtimeDir = Join-Path $Target "runtime"
    $ffprobe = Join-Path $runtimeDir "ffmpeg\ffprobe.exe"
    $runtimeBefore = Get-TreeHashes $runtimeDir
    if ($runtimeBefore.Count -lt 10) { throw "Offline full install has no runtime\ ($runtimeDir)" }
    New-Item -ItemType Directory -Force -Path (Join-Path $userDataRoot "data") | Out-Null
    $sentinel = Join-Path $userDataRoot ("data\upgrade-verify-" + [guid]::NewGuid().ToString("N") + ".txt")
    Set-Content -LiteralPath $sentinel -Value "keep-me" -Encoding ASCII
    $staleBackend = Join-Path $Target "backend\_internal\stale-from-old-build.txt"
    Set-Content -LiteralPath $staleBackend -Value "stale" -Encoding ASCII

    # B1: a runtime that differs from the pinned build must be refused, before anything is touched.
    $ffprobeBackup = Join-Path $verifyRoot "ffprobe.exe.bak"
    Copy-Item -LiteralPath $ffprobe -Destination $ffprobeBackup -Force
    [System.IO.File]::AppendAllText($ffprobe, "x")
    $logB1 = Join-Path $verifyRoot "b1-mismatch.log"
    try { $code = Invoke-Upgrade -Log $logB1 } finally { Copy-Item -LiteralPath $ffprobeBackup -Destination $ffprobe -Force }
    if ($code -eq 0) { throw "Upgrade installer accepted a runtime that differs from the pinned build (log: $logB1)" }
    if (-not (Test-Path -LiteralPath $staleBackend)) { throw "Refused upgrade still ran [InstallDelete]" }
    if ((Read-Log $logB1) -notmatch "Runtime file differs: .*ffprobe\.exe") { throw "Mismatch log does not name ffprobe.exe: $logB1" }
    $checks.Add("B1: an installed runtime that differs from the pinned build (ffprobe.exe changed) is refused (exit $code) before anything is changed")

    # B2: the real upgrade, into the previous directory (no /DIR).
    $logB2 = Join-Path $verifyRoot "b2-upgrade.log"
    $code = Invoke-Upgrade -Log $logB2
    if ($code -ne 0) { throw "Upgrade over the offline full install exited with code $code (log: $logB2)" }
    if (Test-Path -LiteralPath $staleBackend) { throw "Upgrade did not purge stale backend\_internal files" }
    $runtimeAfter = Get-TreeHashes $runtimeDir
    if (Compare-Object $runtimeBefore $runtimeAfter) { throw "Upgrade changed the installed runtime\" }
    foreach ($relative in @("Receiving.Native.exe", "Receiving.Native.dll", "backend\Receiving.Service.exe", "installed-mode.txt")) {
        $installed = Join-Path $Target $relative
        if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) { throw "Upgrade is missing $relative" }
        if ((Get-Sha $installed) -ne (Get-Sha (Join-Path $PayloadRoot $relative))) { throw "Upgrade did not install the payload's $relative" }
    }
    if ([string](Get-KeyValue -Key $appKey -Name "DisplayVersion") -ne $Version) { throw "DisplayVersion after upgrade is not $Version" }
    if ((Get-Content -LiteralPath $sentinel -Raw).Trim() -ne "keep-me") { throw "Upgrade changed the v3 data" }
    if (Test-Path -LiteralPath (Join-Path $userDataRoot "portable-import.json")) { throw "Upgrade over a full install ran a portable import" }
    $state = Invoke-DependencyCheck -Exe (Join-Path $Target "Receiving.Native.exe") -Name "b-dependency-check"
    if ([string]$state.BrowserFolder -ne (Join-Path $runtimeDir "webview2")) { throw "Upgraded app did not select its own WebView2 runtime: '$($state.BrowserFolder)'" }
    if (-not ([string]$state.Engine).StartsWith((Join-Path $runtimeDir "ffmpeg") + "\", [System.StringComparison]::OrdinalIgnoreCase)) { throw "Upgraded app resolved FFmpeg outside its runtime: $($state.Engine)" }
    $checks.Add("B2: upgrade over the offline full install replaced the program in the same directory, purged stale backend files, kept runtime\ byte-for-byte and the v3 data, set DisplayVersion $Version, and --dependency-check is ready on the bundled runtime")

    Uninstall-Registered -Key $appKey
    Wait-Removed -Path (Join-Path $Target "Receiving.Native.exe")
    Wait-Removed -Path (Join-Path $runtimeDir "webview2\msedgewebview2.exe")
    Wait-Removed -Path (Join-Path $runtimeDir "ffmpeg\ffmpeg.exe")
    if ((Get-Content -LiteralPath $sentinel -Raw).Trim() -ne "keep-me") { throw "Uninstall changed the v3 data" }
    $checks.Add("B3: uninstalling after the upgrade removes the program and the runtime the full installer put there, and keeps the v3 data")
    # v3 held no data when this test started; drop what B left (sentinel, files the dependency check wrote).
    Remove-Item -LiteralPath $userDataRoot -Recurse -Force

    # ---------------------------------------------------------------- C. over the deps installer + portable
    Write-Host "== C. Deps installer + portable folder, then upgrade with data import =="
    $code = Invoke-Process -FilePath $DepsInstaller -Arguments @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
    if ($code -ne 0) { throw "Deps installer exited with code $code" }
    $sharedBefore = Get-TreeHashes $sharedRoot

    if (Test-Path -LiteralPath $portableCopy) { Remove-Item -LiteralPath $portableCopy -Recurse -Force }
    Copy-Item -LiteralPath $PortableRoot -Destination $portableCopy -Recurse
    $pData = Join-Path $portableCopy "data"
    New-Item -ItemType Directory -Force -Path (Join-Path $pData "backups\20260101-000000"), (Join-Path $pData "updates"), (Join-Path $portableCopy $recordingsName) | Out-Null
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    foreach ($name in @("jst.db", "catalog.db", "backups\20260101-000000\jst.db")) {
        $bytes = New-Object byte[] 70000
        $rng.GetBytes($bytes)
        [System.IO.File]::WriteAllBytes((Join-Path $pData $name), $bytes)
    }
    Set-Content -LiteralPath (Join-Path $pData "backups\20260101-000000\manifest.json") -Value '{"files":[]}' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $pData "updates\cached-installer.exe") -Value "download cache" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $pData "native-connection.json") -Value '{"port":1}' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $portableCopy "native-settings.json") -Value '{"SchemaVersion":3,"Quality":25}' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $portableCopy "$recordingsName\keep.mp4") -Value "not-a-video" -Encoding ASCII
    $portableBefore = Get-TreeHashes $portableCopy

    $logC = Join-Path $verifyRoot "c-upgrade-import.log"
    $code = Invoke-Upgrade -Log $logC -Extra @("/DIR=$Target", "/PORTABLEDATA=$portableCopy")
    if ($code -ne 0) { throw "Upgrade over the deps installer exited with code $code (log: $logC)" }
    if (-not (Test-Path -LiteralPath (Join-Path $Target "Receiving.Native.exe") -PathType Leaf)) { throw "Upgrade over the deps installer installed no program" }
    if (Test-Path -LiteralPath (Join-Path $Target "runtime")) { throw "Upgrade installer created runtime\ in $Target" }
    if ([string](Get-KeyValue -Key $appKey -Name "DisplayVersion") -ne $Version) { throw "DisplayVersion is not $Version" }
    $state = Invoke-DependencyCheck -Exe (Join-Path $Target "Receiving.Native.exe") -Name "c-dependency-check"
    if ([string]$state.BrowserFolder -ne $sharedWebView) { throw "Installed app did not select the shared WebView2 runtime: '$($state.BrowserFolder)'" }
    if (-not ([string]$state.Engine).StartsWith($sharedFfmpeg + "\", [System.StringComparison]::OrdinalIgnoreCase)) { throw "Installed app resolved FFmpeg outside the shared runtime: $($state.Engine)" }
    $checks.Add("C1: over the deps installer the upgrade installer installs the app (no runtime\ of its own) and --dependency-check is ready on the shared runtime")

    $logText = Read-Log $logC
    if ($logText -notmatch "Portable import from .* exit=0") { throw "Portable import did not succeed (log: $logC)" }
    $v3Data = Join-Path $userDataRoot "data"
    foreach ($name in @("jst.db", "catalog.db", "backups\20260101-000000\jst.db", "backups\20260101-000000\manifest.json")) {
        $copied = Join-Path $v3Data $name
        if (-not (Test-Path -LiteralPath $copied -PathType Leaf)) { throw "Import did not copy data\$name" }
        if ((Get-Sha $copied) -ne (Get-Sha (Join-Path $pData $name))) { throw "Imported data\$name differs from the portable copy" }
    }
    foreach ($name in @("updates", "native-connection.json")) {
        if (Test-Path -LiteralPath (Join-Path $v3Data $name)) { throw "Import copied data\$name, which must be skipped" }
    }
    $settings = Get-Content -LiteralPath (Join-Path $userDataRoot "native-settings.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$settings.Directory -ne (Join-Path $portableCopy $recordingsName)) { throw "Imported settings do not keep recording into the portable folder: '$($settings.Directory)'" }
    if ([int]$settings.Quality -ne 25) { throw "Imported settings lost the portable values (Quality=$($settings.Quality))" }
    if (-not (Test-Path -LiteralPath (Join-Path $userDataRoot "portable-import.json") -PathType Leaf)) { throw "Import wrote no portable-import.json record" }
    if (@(Get-ChildItem -LiteralPath $userDataRoot -Directory -Filter ".portable-import-*" -Force).Count -gt 0) { throw "Import left a staging folder" }
    if (Compare-Object $portableBefore (Get-TreeHashes $portableCopy)) { throw "Import changed the portable folder" }
    $checks.Add("C2: /PORTABLEDATA copied the portable data\ (DBs and backups, hash-verified; update cache and runtime state skipped) and settings into v3, kept recording into <portable>\recordings, and left the portable folder byte-for-byte unchanged")

    $v3Before = Get-TreeHashes $userDataRoot
    $logC3 = Join-Path $verifyRoot "c3-second-import.log"
    $code = Invoke-Upgrade -Log $logC3 -Extra @("/PORTABLEDATA=$portableCopy")
    if ($code -ne 0) { throw "Re-running the upgrade installer exited with code $code (log: $logC3)" }
    if ((Read-Log $logC3) -notmatch "Portable import from .* exit=2") { throw "Second import over existing data was not refused (log: $logC3)" }
    if (Compare-Object $v3Before (Get-TreeHashes $userDataRoot)) { throw "Refused second import changed v3" }
    $checks.Add("C3: a second import over existing installed data is refused (exit 2) and v3 stays unchanged, while the upgrade itself still succeeds")

    Uninstall-Registered -Key $appKey -Extra @("/PURGEDATA")
    Wait-Removed -Path (Join-Path $Target "Receiving.Native.exe")
    Wait-Removed -Path $userDataRoot
    if (Compare-Object $sharedBefore (Get-TreeHashes $sharedRoot)) { throw "Uninstalling the app changed the shared runtime" }
    if (Compare-Object $portableBefore (Get-TreeHashes $portableCopy)) { throw "Uninstall /PURGEDATA changed the portable folder (or its recordings)" }
    $checks.Add("C4: uninstall /PURGEDATA removed the app and v3, and left the deps installer's shared runtime and the portable folder (including its recordings) untouched")

    Uninstall-Registered -Key $depsKey
    Wait-Removed -Path $sharedWebView
    $ok = $true
} catch {
    $errorMessage = $_.Exception.Message
} finally {
    foreach ($key in @($appKey, $depsKey)) {
        if (Test-Key $key) {
            try { Uninstall-Registered -Key $key -Extra @("/PURGEDATA") } catch { Write-Host "Cleanup: $($_.Exception.Message)" }
        }
    }
}

[ordered]@{ ok = $ok; checks = $checks; error = $errorMessage } |
    ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath $reportPath -Encoding UTF8
Get-Content -LiteralPath $reportPath -Raw

if (-not $ok) { exit 1 }
