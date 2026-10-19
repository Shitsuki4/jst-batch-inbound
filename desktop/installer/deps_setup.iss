; 收货小助手运行依赖安装程序 (Inno Setup 6)
; 与便携 ZIP 配套：把 FFmpeg/ffprobe 与 WebView2 固定运行时安装到
; %LOCALAPPDATA%\ReceivingAssistant\runtime，便携版启动时自动使用，断网电脑无需 winget。
; 由 desktop/build-deps-windows.ps1 调用 ISCC.exe 编译。
; 可选命令行定义：/DPayloadDir=... /DOutputDir=... /DAppVersion=... /DOutputBaseFilename=...
;
; 只写入 runtime\ffmpeg 与 runtime\webview2，绝不触碰 %LOCALAPPDATA%\ReceivingAssistant\v3 的业务数据。

#ifndef PayloadDir
  #define PayloadDir "..\dist\deps-payload"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\deps-installer"
#endif
#ifndef AppVersion
  #define AppVersion "3.0.2"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "收货小助手-" + AppVersion + "-运行依赖安装程序"
#endif

[Setup]
AppId={{ECF1BF3B-E563-49DA-9060-D8E25941148C}
AppName=收货小助手运行依赖
AppVersion={#AppVersion}
AppVerName=收货小助手运行依赖 {#AppVersion}
; 固定位置：便携版只在这里查找共享运行依赖（AppPaths.SharedRuntime）。
DefaultDirName={localappdata}\ReceivingAssistant\runtime
DisableDirPage=yes
UsePreviousAppDir=no
DisableProgramGroupPage=yes
CreateAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile=..\src\app.ico
UninstallDisplayIcon={uninstallexe}
UninstallDisplayName=收货小助手运行依赖
UninstallFilesDir={app}\uninstall
WizardStyle=modern
WizardImageFile=wizard.bmp
WizardImageStretch=yes
DisableWelcomePage=no
Compression=lzma2/fast
SolidCompression=yes
LZMANumBlockThreads=2
CloseApplications=no
RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[InstallDelete]
; 升级时先清空两个运行时目录，避免旧版 FFmpeg / WebView2 文件残留。只删程序文件，不碰 v3 业务数据。
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: filesandordirs; Name: "{app}\webview2"

[Files]
Source: "{#PayloadDir}\ffmpeg\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\webview2\*"; DestDir: "{app}\webview2"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\docs\*"; DestDir: "{app}"; Flags: ignoreversion

[Run]
; WebView2 固定版本 120+ 在 Windows 10 上以 AppContainer 运行渲染进程，需授予读取/执行权限，
; 否则启动即崩溃（Microsoft “Distribute your app and the WebView2 Runtime” 第 6 步）。
Filename: "{sys}\icacls.exe"; Parameters: """{app}\webview2"" /grant *S-1-15-2-2:(OI)(CI)(RX) /C /Q"; Flags: runhidden waituntilterminated; StatusMsg: "正在配置 WebView2 运行时权限……"
Filename: "{sys}\icacls.exe"; Parameters: """{app}\webview2"" /grant *S-1-15-2-1:(OI)(CI)(RX) /C /Q"; Flags: runhidden waituntilterminated; StatusMsg: "正在配置 WebView2 运行时权限……"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: filesandordirs; Name: "{app}\webview2"
Type: dirifempty; Name: "{app}\uninstall"
Type: dirifempty; Name: "{app}"

[Code]
procedure InitializeWizard();
begin
  WizardForm.WelcomeLabel2.Caption :=
    '为收货小助手便携版离线安装运行依赖：FFmpeg/ffprobe（录像）和 WebView2 固定运行时（业务界面）。' + #13#10 + #13#10 +
    '安装到当前用户的 %LOCALAPPDATA%\ReceivingAssistant\runtime，不需要管理员权限，也不需要联网或 winget。' + #13#10 + #13#10 +
    '不会修改收货数据库、设置或录像。安装完成后，在小助手的环境检查窗口点“重新检查”即可。' + #13#10 +
    '已安装离线完整版的电脑不需要本程序。';
end;

{ True when ffmpeg / ffprobe / msedgewebview2 launched from this runtime directory is still running
  (for example the portable app is recording). Same-user processes are visible to WMI without elevation. }
function IsRuntimeInUse(): Boolean;
var
  Prefix, Path: String;
  Locator, Services, Processes, Item: Variant;
  I: Integer;
begin
  Result := False;
  Prefix := Lowercase(AddBackslash(ExpandConstant('{app}')));
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Services := Locator.ConnectServer('.', 'root\CIMV2');
    Processes := Services.ExecQuery(
      'SELECT ExecutablePath FROM Win32_Process WHERE Name = "ffmpeg.exe" OR Name = "ffprobe.exe" OR Name = "msedgewebview2.exe"');
    for I := 0 to Processes.Count - 1 do
    begin
      Item := Processes.ItemIndex(I);
      if not VarIsNull(Item.ExecutablePath) then
      begin
        Path := Lowercase(Item.ExecutablePath);
        if Copy(Path, 1, Length(Prefix)) = Prefix then
        begin
          Result := True;
          exit;
        end;
      end;
    end;
  except
    Log('WMI process query failed: ' + GetExceptionMessage);
    Result := False;
  end;
end;

function InUseMessage(): String;
begin
  Result :=
    '检测到收货小助手正在使用这些运行依赖（录像或业务界面仍在运行）。' + #13#10 + #13#10 +
    '请先打开收货小助手，结束当前录像并通过正常方式退出程序，然后重新运行本程序。';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if IsRuntimeInUse() then
    Result := InUseMessage();
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if IsRuntimeInUse() then
  begin
    SuppressibleMsgBox(InUseMessage(), mbError, MB_OK, IDOK);
    Result := False;
  end;
end;
