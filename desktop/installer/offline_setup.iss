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
  #define AppVersion "3.0.0"
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
AppMutex=Local\ReceivingAssistantInstalled
DisableProgramGroupPage=yes
UninstallDisplayName=收货小助手（保留业务数据）

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; Flags: checkedonce

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
