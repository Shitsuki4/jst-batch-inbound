; 收货小助手 3.0 完整离线安装程序 (Inno Setup 6)
; 由 desktop/build-offline-windows.ps1 调用 ISCC.exe 编译。
; 可选命令行定义：/DPayloadDir=... /DOutputDir=... /DAppVersion=... /DOutputBaseFilename=...

#ifndef PayloadDir
  #define PayloadDir "..\dist\offline-payload"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif
#ifndef AppVersion
  #define AppVersion "3.0.1"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "收货小助手-" + AppVersion + "-离线完整安装程序"
#endif

[Setup]
AppId={{80E21946-081D-4C07-A40D-60C2244D7066}
AppName=收货小助手
AppVersion={#AppVersion}
AppVerName=收货小助手 {#AppVersion} 离线完整版
DefaultDirName={localappdata}\Programs\ReceivingAssistant
DefaultGroupName=收货小助手
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile=..\src\app.ico
UninstallDisplayIcon={app}\Receiving.Native.exe
WizardStyle=modern
WizardImageFile=wizard.bmp
WizardImageStretch=yes
DisableWelcomePage=no
Compression=lzma2/fast
SolidCompression=yes
LZMANumBlockThreads=2
CloseApplications=no
RestartApplications=no
; ReceivingAssistantInstalled is held by the WPF host; ReceivingAssistantService is held by the
; Python backend (Receiving.Service.exe) in native mode, which can outlive the host while it
; finishes an in-flight ERP write. Setup and uninstall both refuse while either is held.
AppMutex=Local\ReceivingAssistantInstalled,Local\ReceivingAssistantService
DisableProgramGroupPage=yes
UninstallDisplayName=收货小助手（保留业务数据）

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; Flags: checkedonce

[InstallDelete]
; Upgrades must not leave stale PyInstaller modules or an older WebView2 fixed runtime behind.
; Both directories are program files only: user data, settings, WebView2 profile and recordings
; live under %LOCALAPPDATA%\ReceivingAssistant\v3 (AppPaths.DataRoot in installed mode).
Type: filesandordirs; Name: "{app}\backend\_internal"
Type: filesandordirs; Name: "{app}\runtime\webview2"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\收货小助手"; Filename: "{app}\Receiving.Native.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\收货小助手"; Filename: "{app}\Receiving.Native.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{autoprograms}\收货小助手使用说明"; Filename: "{app}\先读我.html"

[Run]
Filename: "{app}\Receiving.Native.exe"; Description: "启动收货小助手"; Flags: nowait postinstall skipifsilent

[Code]
procedure InitializeWizard();
begin
  WizardForm.WelcomeLabel2.Caption :=
    '收货、对单与视频举证，一次安装。' + #13#10 + #13#10 +
    '已包含 .NET、Python、FFmpeg/ffprobe 和 WebView2 固定运行时，安装和启动无需联网下载这些依赖；ERP 业务仍需要网络。' + #13#10 + #13#10 +
    '业务数据与录像独立保存，卸载默认保留。不会自动导入或覆盖旧版数据。';
end;

const
  ServiceMutex = 'Local\ReceivingAssistantService';

{ True when a Receiving.Service.exe started from this install directory is still running.
  Uses WMI (same-user processes are visible without elevation); falls back to the service mutex. }
function IsInstalledServiceRunning(): Boolean;
var
  ExePath, Escaped: String;
  Locator, Services, Processes: Variant;
begin
  Result := CheckForMutexes(ServiceMutex);
  if Result then
    exit;
  ExePath := ExpandConstant('{app}\backend\Receiving.Service.exe');
  if not FileExists(ExePath) then
    exit;
  Escaped := ExePath;
  StringChangeEx(Escaped, '\', '\\', True);
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Services := Locator.ConnectServer('.', 'root\CIMV2');
    Processes := Services.ExecQuery('SELECT ProcessId FROM Win32_Process WHERE ExecutablePath = "' + Escaped + '"');
    Result := Processes.Count > 0;
  except
    Log('WMI process query failed: ' + GetExceptionMessage);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if IsInstalledServiceRunning() then
    Result :=
      '检测到收货小助手的业务服务（Receiving.Service.exe）仍在运行，可能正在安全收尾、提交入库数据。' + #13#10 + #13#10 +
      '请先打开“收货小助手”，等待当前任务完成后通过正常方式退出程序，然后重新运行本安装程序。' + #13#10 + #13#10 +
      '为避免入库数据丢失或重复提交，安装程序不会强制结束该进程。';
end;
