; 收货小助手 4.0 完整离线安装程序 (Inno Setup 6)
; 由 desktop/build-offline-windows.ps1 调用 ISCC.exe 编译。
; 可选命令行定义：/DPayloadDir=... /DOutputDir=... /DAppVersion=... /DOutputBaseFilename=...
;
; 卸载程序命令行开关（unins000.exe）：
;   /FORCECLOSE  静默卸载时，若小助手仍在运行则强制结束安装目录内的进程后继续（默认拒绝卸载）。
;   /PURGEDATA   静默卸载时删除全部业务数据（默认保留全部数据）；交互卸载时预选“完全卸载”。
; 无论哪种方式，都只会删除 %LOCALAPPDATA%\ReceivingAssistant\v3 与安装目录内的数据，
; 绝不触碰独立的“运行依赖”目录 %LOCALAPPDATA%\ReceivingAssistant\runtime。
;
; 同一份脚本加 /DUpgradeOnly 编译出“升级安装程序”（desktop/build-upgrade-windows.ps1）：
;   载荷和便携版 ZIP 一样，不带 runtime\（WebView2 固定运行时、FFmpeg/ffprobe），AppId 相同，
;   装到同一个安装目录，是对离线完整版的原地升级；数据仍在 %LOCALAPPDATA%\ReceivingAssistant\v3。
;   安装前核对这台电脑上程序将要用的运行组件（安装目录 runtime\ 优先，否则运行依赖安装程序的
;   共享目录，与 AppPaths.cs / Dependencies.cs 的查找顺序一致）与本版本固定版本的 SHA256 完全一致，
;   缺失或不一致就拒绝安装，并提示改用离线完整安装程序。必需定义：
;   /DReqFfmpegSha=... /DReqFfprobeSha=... /DReqWebView2Sha=...（小写 SHA256）
;   /DReqWebView2Version=... /DReqFfmpegVersion=...（只用于提示文字）
;   安装程序命令行开关：/PORTABLEDATA="便携版文件夹"  静默安装时把便携版数据复制到安装版
;   （只复制、不移动，安装版已有数据时不导入；交互安装时在“便携版数据”页面选择）。

#ifndef PayloadDir
  #define PayloadDir "..\dist\offline-payload"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif
#ifndef AppVersion
  #define AppVersion "4.1.5"
#endif
#ifdef UpgradeOnly
  #ifndef ReqFfmpegSha
    #error 升级安装程序需要 /DReqFfmpegSha
  #endif
  #ifndef ReqFfprobeSha
    #error 升级安装程序需要 /DReqFfprobeSha
  #endif
  #ifndef ReqWebView2Sha
    #error 升级安装程序需要 /DReqWebView2Sha
  #endif
  #ifndef ReqWebView2Version
    #define ReqWebView2Version ""
  #endif
  #ifndef ReqFfmpegVersion
    #define ReqFfmpegVersion ""
  #endif
#endif
#ifndef OutputBaseFilename
  #ifdef UpgradeOnly
    #define OutputBaseFilename "收货小助手-" + AppVersion + "-升级安装程序"
  #else
    #define OutputBaseFilename "收货小助手-" + AppVersion + "-离线完整安装程序"
  #endif
#endif

[Setup]
AppId={{80E21946-081D-4C07-A40D-60C2244D7066}
AppName=收货小助手
AppVersion={#AppVersion}
#ifdef UpgradeOnly
AppVerName=收货小助手 {#AppVersion}（升级安装）
#else
AppVerName=收货小助手 {#AppVersion} 离线完整版
#endif
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
; finishes an in-flight ERP write. Setup refuses while either is held. The uninstaller first runs
; its own check in [Code] (InitializeUninstall / usAppMutexCheck), which can force-close the
; processes started from {app} after an explicit confirmation; this AppMutex check then runs as a
; final safety net (for example a portable copy's backend still holding the service mutex).
AppMutex=Local\ReceivingAssistantInstalled,Local\ReceivingAssistantService
DisableProgramGroupPage=yes
UninstallDisplayName=收货小助手

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Messages]
ConfirmUninstall=确定要卸载 %1 吗？%n%n程序文件将被删除，数据按上一步的选择处理。
UninstalledMost=%1 卸载完成。%n%n选择保留的数据及其所在文件夹仍在原位置，如以后不再需要，可以手动删除。

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; Flags: checkedonce

[InstallDelete]
; Upgrades must not leave stale PyInstaller modules or an older WebView2 fixed runtime behind.
; Both directories are program files only: user data, settings, WebView2 profile and recordings
; live under %LOCALAPPDATA%\ReceivingAssistant\v3 (AppPaths.DataRoot in installed mode).
; The upgrade installer (/DUpgradeOnly) brings no runtime\, so it must keep the installed one.
Type: filesandordirs; Name: "{app}\backend\_internal"
#ifndef UpgradeOnly
Type: filesandordirs; Name: "{app}\runtime\webview2"
#endif

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\收货小助手"; Filename: "{app}\Receiving.Native.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\收货小助手"; Filename: "{app}\Receiving.Native.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{autoprograms}\收货小助手使用说明"; Filename: "{app}\先读我.html"
Name: "{autoprograms}\卸载收货小助手"; Filename: "{uninstallexe}"; Comment: "卸载收货小助手，可选择保留或删除业务数据"

[Run]
; WebView2 固定版本在 Windows 10 上以 AppContainer 运行渲染进程，必须给运行时目录授予
; 读取/执行权限，否则业务界面启动即闪退（Microsoft “Distribute your app and the WebView2 Runtime” 第 6 步）。
; 升级时 [InstallDelete] 会删除并重建 runtime\webview2，所以每次安装后都要重新授权。
; 升级安装程序不动运行时目录（完整版或运行依赖安装程序装的时候已经授过权），不需要这一步。
#ifndef UpgradeOnly
Filename: "{sys}\icacls.exe"; Parameters: """{app}\runtime\webview2"" /grant *S-1-15-2-2:(OI)(CI)(RX) /C /Q"; Flags: runhidden waituntilterminated; StatusMsg: "正在配置 WebView2 运行时权限……"
Filename: "{sys}\icacls.exe"; Parameters: """{app}\runtime\webview2"" /grant *S-1-15-2-1:(OI)(CI)(RX) /C /Q"; Flags: runhidden waituntilterminated; StatusMsg: "正在配置 WebView2 运行时权限……"
#endif
Filename: "{app}\Receiving.Native.exe"; Description: "启动收货小助手"; Flags: nowait postinstall skipifsilent

[Code]
#ifdef UpgradeOnly
{ ------------------------------------------------------------------------------------------
  Upgrade installer only: runtime prerequisite check and optional portable data import
  ------------------------------------------------------------------------------------------ }
const
  ReqFfmpegSha = '{#ReqFfmpegSha}';
  ReqFfprobeSha = '{#ReqFfprobeSha}';
  ReqWebView2Sha = '{#ReqWebView2Sha}';
  ReqWebView2Version = '{#ReqWebView2Version}';
  ReqFfmpegVersion = '{#ReqFfmpegVersion}';
  FullInstallerName = '收货小助手-{#AppVersion}-离线完整安装程序.exe';
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{80E21946-081D-4C07-A40D-60C2244D7066}_is1';

var
  ImportPage: TWizardPage;
  ImportCheck: TNewCheckBox;
  ImportEdit: TNewEdit;
  ImportBrowse: TNewButton;
  ImportSource: String;

function SharedRuntimeDir(): String;
begin
  Result := ExpandConstant('{localappdata}\ReceivingAssistant\runtime');
end;

function InstalledDataRoot(): String;
begin
  Result := ExpandConstant('{localappdata}\ReceivingAssistant\v3');
end;

{ Install directory of an existing install with this AppId (offline full or an earlier upgrade), '' if none. }
function PreviousAppDir(): String;
begin
  Result := '';
  if not RegQueryStringValue(HKCU, UninstallKey, 'Inno Setup: App Path', Result) then
    Result := '';
  Result := RemoveBackslashUnlessRoot(Result);
end;

{ The runtime files the installed app will really use from AppDir (AppPaths.BrowserFolder and
  Dependencies.Resolve in installed mode): the install's own runtime\ first, then the shared one. }
function RuntimeWebView2Exe(const Dir: String): String;
begin
  if DirExists(Dir + '\runtime\webview2') then
    Result := Dir + '\runtime\webview2\msedgewebview2.exe'
  else
    Result := SharedRuntimeDir() + '\webview2\msedgewebview2.exe';
end;

function RuntimeFfmpegFile(const Dir, Name: String): String;
begin
  Result := Dir + '\runtime\ffmpeg\' + Name;
  if not FileExists(Result) then
    Result := SharedRuntimeDir() + '\ffmpeg\' + Name;
end;

{ 0 = matches, 1 = missing, 2 = present but a different build. }
function RuntimeFileState(const Path, Sha: String): Integer;
var
  Actual: String;
begin
  Result := 1;
  if not FileExists(Path) then
    exit;
  try
    Actual := Lowercase(GetSHA256OfFile(Path));
  except
    Log('Hashing ' + Path + ' failed: ' + GetExceptionMessage);
    exit;
  end;
  if Actual = Sha then
    Result := 0
  else
  begin
    Result := 2;
    Log('Runtime file differs: ' + Path + ' sha256=' + Actual + ' expected=' + Sha);
  end;
end;

{ '' when the runtime the app will use from AppDir is complete and exactly the pinned build. }
function RuntimeProblem(const Dir: String): String;
var
  Files, Shas: array[0..2] of String;
  I, State, Missing, Different: Integer;
  Names: String;
begin
  Files[0] := RuntimeWebView2Exe(Dir);
  Files[1] := RuntimeFfmpegFile(Dir, 'ffmpeg.exe');
  Files[2] := RuntimeFfmpegFile(Dir, 'ffprobe.exe');
  Shas[0] := ReqWebView2Sha;
  Shas[1] := ReqFfmpegSha;
  Shas[2] := ReqFfprobeSha;
  Missing := 0;
  Different := 0;
  Names := '';
  for I := 0 to 2 do
  begin
    State := RuntimeFileState(Files[I], Shas[I]);
    Log('Runtime check: ' + Files[I] + ' state=' + IntToStr(State));
    if State = 1 then
      Missing := Missing + 1
    else if State = 2 then
      Different := Different + 1;
    if State <> 0 then
    begin
      if Names <> '' then
        Names := Names + '、';
      Names := Names + ExtractFileName(Files[I]);
    end;
  end;
  Result := '';
  if Missing = 3 then
    Result :=
      '这台电脑上没有找到收货小助手的运行组件（WebView2 固定运行时和 FFmpeg/ffprobe）。' + #13#10 + #13#10 +
      '升级安装程序只能用在已经装过“离线完整安装程序”或“运行依赖安装程序”的电脑上，它本身不带这些组件。' + #13#10 + #13#10 +
      '请改用 ' + FullInstallerName + '（自带全部组件）。本次没有做任何改动。'
  else if (Missing > 0) or (Different > 0) then
    Result :=
      '这台电脑上的运行组件和 {#AppVersion} 要求的版本不一致或不完整（' + Names + '）。' + #13#10 +
      '本版本需要 WebView2 ' + ReqWebView2Version + '、FFmpeg ' + ReqFfmpegVersion + '。' + #13#10 + #13#10 +
      '请改用 ' + FullInstallerName + '（自带全部组件，会一并更新运行组件）。本次没有做任何改动。';
end;

function DefaultAppDir(): String;
begin
  Result := PreviousAppDir();
  if Result = '' then
    Result := ExpandConstant('{localappdata}\Programs\ReceivingAssistant');
end;

function InitializeSetup(): Boolean;
var
  Problem: String;
begin
  Result := True;
  Problem := RuntimeProblem(DefaultAppDir());
  if Problem <> '' then
  begin
    Log('Upgrade installer refused: ' + Problem);
    SuppressibleMsgBox(Problem, mbCriticalError, MB_OK, IDOK);
    Result := False;
  end;
end;

function DirHasEntries(const Dir: String): Boolean;
var
  F: TFindRec;
begin
  Result := False;
  if FindFirst(Dir + '\*', F) then
  try
    repeat
      if (F.Name <> '.') and (F.Name <> '..') then
      begin
        Result := True;
        exit;
      end;
    until not FindNext(F);
  finally
    FindClose(F);
  end;
end;

{ Same rule as PortableImport.cs: never import over existing installed-mode data. }
function InstalledDataPresent(): Boolean;
begin
  Result := FileExists(InstalledDataRoot() + '\native-settings.json') or DirHasEntries(InstalledDataRoot() + '\data');
end;

function LooksLikePortableWithData(const Dir: String): Boolean;
begin
  Result := FileExists(Dir + '\Receiving.Native.exe') and not FileExists(Dir + '\installed-mode.txt') and
    (FileExists(Dir + '\data\jst.db') or FileExists(Dir + '\data\catalog.db') or FileExists(Dir + '\data\unplanned.db') or
     FileExists(Dir + '\data\express.db') or FileExists(Dir + '\data\purchase_watch.db') or
     FileExists(Dir + '\data\joint_receipts.db') or FileExists(Dir + '\data\learned_codes.db') or
     FileExists(Dir + '\data\operation_log.db'));
end;

function CleanDir(const Dir: String): String;
begin
  Result := Trim(Dir);
  StringChangeEx(Result, '"', '', True);
  Result := RemoveBackslashUnlessRoot(Result);
end;

procedure ImportControlsUpdate(Sender: TObject);
begin
  ImportEdit.Enabled := ImportCheck.Checked;
  ImportBrowse.Enabled := ImportCheck.Checked;
end;

procedure ImportBrowseClick(Sender: TObject);
var
  Dir: String;
begin
  Dir := ImportEdit.Text;
  if BrowseForFolder('选择便携版所在的文件夹（里面有 Receiving.Native.exe 和 data 文件夹）', Dir, False) then
    ImportEdit.Text := Dir;
end;

procedure CreateImportPage();
var
  Info: TNewStaticText;
begin
  ImportPage := CreateCustomPage(wpSelectDir, '便携版数据', '可选：把便携版的数据复制到安装版');
  Info := TNewStaticText.Create(ImportPage);
  Info.Parent := ImportPage.Surface;
  Info.WordWrap := True;
  Info.AutoSize := False;
  Info.Width := ImportPage.SurfaceWidth;
  Info.Height := ScaleY(118);
  Info.Caption :=
    '安装版的数据放在 ' + InstalledDataRoot() + '，和便携版文件夹是分开的，目前这里还没有数据。' + #13#10 + #13#10 +
    '如果你之前用的是便携版（解压出来的 jst-batch-inbound-desktop-…-win-x64 文件夹），可以勾选下面这项，选中那个文件夹，' +
    '安装完成后会把它的业务数据和设置复制过来。只复制、不移动，便携版文件夹不会有任何改动；录像不复制，继续保存在原来的位置。' + #13#10 + #13#10 +
    '复制前请先退出便携版。不勾选也可以，以后需要时在安装版“设置 → 备份与恢复 → 从文件恢复”里选择便携版 data\backups 下的备份。';
  ImportCheck := TNewCheckBox.Create(ImportPage);
  ImportCheck.Parent := ImportPage.Surface;
  ImportCheck.Top := Info.Top + Info.Height + ScaleY(8);
  ImportCheck.Width := ImportPage.SurfaceWidth;
  ImportCheck.Caption := '把便携版的数据复制到安装版';
  ImportCheck.OnClick := @ImportControlsUpdate;
  ImportEdit := TNewEdit.Create(ImportPage);
  ImportEdit.Parent := ImportPage.Surface;
  ImportEdit.Top := ImportCheck.Top + ImportCheck.Height + ScaleY(8);
  ImportEdit.Width := ImportPage.SurfaceWidth - ScaleX(90);
  ImportBrowse := TNewButton.Create(ImportPage);
  ImportBrowse.Parent := ImportPage.Surface;
  ImportBrowse.Top := ImportEdit.Top - ScaleY(1);
  ImportBrowse.Left := ImportPage.SurfaceWidth - ScaleX(80);
  ImportBrowse.Width := ScaleX(80);
  ImportBrowse.Height := ScaleY(23);
  ImportBrowse.Caption := '浏览(&R)…';
  ImportBrowse.OnClick := @ImportBrowseClick;
  ImportEdit.Text := CleanDir(ExpandConstant('{param:PORTABLEDATA|}'));
  ImportCheck.Checked := ImportEdit.Text <> '';
  ImportControlsUpdate(nil);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = ImportPage.ID) and InstalledDataPresent();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Dir: String;
begin
  Result := True;
  if WizardSilent() then
    exit;
  if CurPageID = wpSelectDir then
  begin
    Dir := RemoveBackslashUnlessRoot(WizardDirValue());
    if FileExists(Dir + '\Receiving.Native.exe') and not FileExists(Dir + '\installed-mode.txt') then
    begin
      MsgBox('这个文件夹里是便携版收货小助手，安装会覆盖它。请换一个安装位置。', mbError, MB_OK);
      Result := False;
    end;
  end
  else if CurPageID = ImportPage.ID then
  begin
    ImportSource := '';
    if ImportCheck.Checked then
    begin
      Dir := CleanDir(ImportEdit.Text);
      if not LooksLikePortableWithData(Dir) then
      begin
        MsgBox('没有在这个文件夹里找到便携版的数据。' + #13#10 + #13#10 +
          '请选择便携版解压出来的那个文件夹：里面有 Receiving.Native.exe，还有放业务数据的 data 文件夹（data\jst.db 等）。' + #13#10 +
          '不想复制的话，取消勾选即可。', mbError, MB_OK);
        Result := False;
        exit;
      end;
      ImportSource := Dir;
    end;
  end;
end;

#endif

procedure InitializeWizard();
begin
#ifdef UpgradeOnly
  WizardForm.WelcomeLabel2.Caption :=
    '把收货小助手升级到 {#AppVersion}。' + #13#10 + #13#10 +
    '这是精简的升级安装程序，不带 WebView2 和 FFmpeg：用的是这台电脑上“离线完整安装程序”或“运行依赖安装程序”已经装好的组件，' +
    '所以安装前会先核对它们的版本。' + #13#10 + #13#10 +
    '装过离线完整版的电脑会原地升级，数据保持不变；只装过运行依赖、一直用便携版的电脑，可以在下一步选择把便携版数据复制过来。';
  CreateImportPage();
#else
  WizardForm.WelcomeLabel2.Caption :=
    '收货、对单与视频举证，一次安装。' + #13#10 + #13#10 +
    '已包含 .NET、Python、FFmpeg/ffprobe 和 WebView2 固定运行时，安装和启动无需联网下载这些依赖；ERP 业务仍需要网络。' + #13#10 + #13#10 +
    '业务数据与录像独立保存，卸载时默认保留，也可以选择删除。不会自动导入或覆盖旧版数据。';
#endif
end;

const
  ServiceMutex = 'Local\ReceivingAssistantService';
  AppMutexes = 'Local\ReceivingAssistantInstalled,Local\ReceivingAssistantService';
  { Must match remote_access.firewall_rule_name() without the port number. }
  FirewallRulePrefix = '收货小助手远程管理页 TCP ';
  { Data categories offered by the uninstaller }
  CatDatabase = 0;
  CatRecordings = 1;
  CatExports = 2;
  CatSettings = 3;
  CatCache = 4;
  CatLast = 4;
  { Uninstall data modes }
  ModeKeepAll = 0;
  ModeCustom = 1;
  ModePurge = 2;

type
  TIntArray = array of Integer;

var
  { Uninstall state, shared between InitializeUninstall and CurUninstallStepChanged }
  DataRoot, AppDir, RuntimeRoot, RecordingDir: String;
  DataMode: Integer;
  KeepCat: array[0..4] of Boolean;
  CatPresent: array[0..4] of Boolean;
  CatSize: array[0..4] of Int64;
  ForceCloseSwitch, PurgeSwitch, KillAgreed: Boolean;
  { Uninstall options form controls (only valid while the form is shown) }
  RadioKeep, RadioCustom, RadioPurge: TNewRadioButton;
  CatBoxes: array[0..4] of TNewCheckBox;

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
#ifdef UpgradeOnly
  { The install directory is final here (the directory page may have changed it): check again. }
  Result := RuntimeProblem(RemoveBackslashUnlessRoot(ExpandConstant('{app}')));
  if Result <> '' then
  begin
    Log('Upgrade installer refused at PrepareToInstall: ' + Result);
    exit;
  end;
#endif
  if IsInstalledServiceRunning() then
    Result :=
      '检测到收货小助手的业务服务（Receiving.Service.exe）仍在运行，可能正在安全收尾、提交入库数据。' + #13#10 + #13#10 +
      '请先打开“收货小助手”，等待当前任务完成后通过正常方式退出程序，然后重新运行本安装程序。' + #13#10 + #13#10 +
      '为避免入库数据丢失或重复提交，安装程序不会强制结束该进程。';
end;

{ ------------------------------------------------------------------------------------------
  Uninstaller
  ------------------------------------------------------------------------------------------ }

function HasSwitch(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
    begin
      Result := True;
      exit;
    end;
end;

function StartsWithText(const S, Prefix: String): Boolean;
begin
  Result := (Length(S) >= Length(Prefix)) and (CompareText(Copy(S, 1, Length(Prefix)), Prefix) = 0);
end;

function IsUnder(const Path, Root: String): Boolean;
begin
  Result := (Root <> '') and StartsWithText(Path, AddBackslash(Root));
end;

{ The only places the uninstaller may delete data from: inside the v3 data root or inside the
  install directory, and never inside the separate runtime-dependency directory. }
function IsSafeTarget(const Path: String): Boolean;
begin
  Result := False;
  if (DataRoot = '') or (AppDir = '') or (RuntimeRoot = '') then
    exit;
  if Pos('..', Path) > 0 then
    exit;
  if IsUnder(Path, RuntimeRoot) or (CompareText(RemoveBackslash(Path), RuntimeRoot) = 0) then
    exit;
  Result := IsUnder(Path, DataRoot) or ((Length(AppDir) > 3) and IsUnder(Path, AppDir));
end;

function JsonUnescape(const S: String): String;
var
  I: Integer;
  C: String;
begin
  Result := '';
  I := 1;
  while I <= Length(S) do
  begin
    C := Copy(S, I, 1);
    if (C = '\') and (I < Length(S)) then
    begin
      C := Copy(S, I + 1, 1);
      if (C = 'u') and (I + 5 <= Length(S)) then
      begin
        Result := Result + Chr(StrToIntDef('$' + Copy(S, I + 2, 4), 63));
        I := I + 6;
      end
      else
      begin
        if C = 'n' then
          C := ' '
        else if C = 't' then
          C := ' ';
        Result := Result + C;
        I := I + 2;
      end;
    end
    else
    begin
      Result := Result + C;
      I := I + 1;
    end;
  end;
end;

{ Recording directory saved in native-settings.json ("Directory"); empty when not set. }
function ReadRecordingDir(): String;
var
  Raw: AnsiString;
  S, Value: String;
  P, I: Integer;
  Escaped: Boolean;
begin
  Result := '';
  if not LoadStringFromFile(DataRoot + '\native-settings.json', Raw) then
    exit;
  S := Raw;
  P := Pos('"Directory"', S);
  if P = 0 then
    exit;
  S := Copy(S, P + Length('"Directory"'), Length(S));
  P := Pos(':', S);
  if P = 0 then
    exit;
  S := Copy(S, P + 1, Length(S));
  P := Pos('"', S);
  if (P = 0) or (Trim(Copy(S, 1, P - 1)) <> '') then
    exit;
  S := Copy(S, P + 1, Length(S));
  Value := '';
  Escaped := False;
  for I := 1 to Length(S) do
  begin
    if (not Escaped) and (Copy(S, I, 1) = '"') then
    begin
      Result := RemoveBackslashUnlessRoot(JsonUnescape(Value));
      exit;
    end;
    Escaped := (not Escaped) and (Copy(S, I, 1) = '\');
    Value := Value + Copy(S, I, 1);
  end;
end;

procedure AddPath(var List: TArrayOfString; const Path: String);
begin
  SetArrayLength(List, GetArrayLength(List) + 1);
  List[GetArrayLength(List) - 1] := Path;
end;

{ Real paths behind each category. Derived from AppPaths.DataRoot (desktop/src/AppPaths.cs),
  MainWindow (Program.cs: native-settings.json, 录像, data, webview-profile, video-cues,
  native-errors.log), Backend.cs (service-exit-warning.txt) and the Python backend, which gets
  JST_DB_DIR=v3\data (server.py: *.db, backups, logs, edge-login, report-clipboard; login_memory.py:
  login-session.dpapi; pda.py: pda-private; remote_access.py: remote-access (enabled flag, admin
  password hash, remote TLS certificate/key); native_entry.py / service_runtime.py: connection files).
  VideoLibraryWindow.cs writes the built-in player page to DataRoot\library-player (regenerated on use).
  The frozen backend writes exports and diag.log next to its exe: <app>\backend\导出 and
  <app>\backend\data\diag.log. Entries containing * are file wildcards. }
function CategoryPaths(const Cat: Integer): TArrayOfString;
begin
  SetArrayLength(Result, 0);
  case Cat of
    CatDatabase:
      begin
        AddPath(Result, DataRoot + '\data\*.db*');
        AddPath(Result, DataRoot + '\data\backups');
        AddPath(Result, DataRoot + '\service-exit-warning.txt');
      end;
    CatRecordings:
      begin
        AddPath(Result, DataRoot + '\录像');
        if (RecordingDir <> '') and IsUnder(RecordingDir, DataRoot) and
           (CompareText(RecordingDir, DataRoot + '\录像') <> 0) then
          AddPath(Result, RecordingDir);
      end;
    CatExports:
      AddPath(Result, AppDir + '\backend\导出');
    CatSettings:
      begin
        AddPath(Result, DataRoot + '\native-settings.json');
        AddPath(Result, DataRoot + '\native-settings.json.tmp');
        AddPath(Result, DataRoot + '\data\login-session.dpapi');
        AddPath(Result, DataRoot + '\data\.login-*');
        AddPath(Result, DataRoot + '\data\edge-login');
        AddPath(Result, DataRoot + '\data\pda-private');
        { Remote admin page: enabled switch, admin password hash, TLS key. Deleting settings must not
          leave the page able to re-open itself with the old password after a reinstall. }
        AddPath(Result, DataRoot + '\data\remote-access');
      end;
    CatCache:
      begin
        AddPath(Result, DataRoot + '\webview-profile');
        AddPath(Result, DataRoot + '\video-cues');
        AddPath(Result, DataRoot + '\library-player');
        AddPath(Result, DataRoot + '\native-errors.log');
        AddPath(Result, DataRoot + '\data\logs');
        AddPath(Result, DataRoot + '\data\report-clipboard');
        AddPath(Result, DataRoot + '\data\native-connection.json');
        AddPath(Result, DataRoot + '\data\native-connection.json.tmp');
        AddPath(Result, DataRoot + '\data\service-instance.json');
        AddPath(Result, AppDir + '\backend\data');
      end;
  end;
end;

function CategoryName(const Cat: Integer): String;
begin
  case Cat of
    CatDatabase: Result := '收货数据库与自动备份';
    CatRecordings: Result := '录像文件';
    CatExports: Result := '导出文件';
    CatSettings: Result := '本机设置与登录记忆';
    CatCache: Result := '缓存与日志';
  else
    Result := '';
  end;
end;

function CategoryHint(const Cat: Integer): String;
begin
  case Cat of
    CatDatabase: Result := '收货记录、对单/联合收货/采购监控数据、操作记录、业务设置';
    CatRecordings: Result := '默认录像目录';
    CatExports: Result := 'Excel/CSV 导出';
    CatSettings: Result := '录像设置、保持登录、PDA 配对、远程管理页';
    CatCache: Result := '浏览器缓存、日志，可自动重建';
  else
    Result := '';
  end;
end;

function CategoryLocation(const Cat: Integer): String;
begin
  case Cat of
    CatDatabase: Result := DataRoot + '\data';
    CatRecordings: Result := DataRoot + '\录像';
    CatExports: Result := AppDir + '\backend\导出';
    CatSettings: Result := DataRoot + '（native-settings.json、data\login-session.dpapi、data\edge-login、data\pda-private、data\remote-access）';
    CatCache: Result := DataRoot + '（webview-profile、video-cues、library-player、data\logs 等）';
  else
    Result := '';
  end;
end;

function DirSize(const Dir: String): Int64;
var
  R: TFindRec;
  Size: Int64;
begin
  Result := 0;
  if FindFirst(Dir + '\*', R) then
  try
    repeat
      if (R.Name <> '.') and (R.Name <> '..') then
      begin
        if (R.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        begin
          { Do not follow junctions / symbolic links }
          if (R.Attributes and $400) = 0 then
            Result := Result + DirSize(Dir + '\' + R.Name);
        end
        else if FileSize64(Dir + '\' + R.Name, Size) then
          Result := Result + Size;
      end;
    until not FindNext(R);
  finally
    FindClose(R);
  end;
end;

{ Size of the files matched by a wildcard (non-recursive) }
function WildcardSize(const Pattern: String; var Found: Boolean): Int64;
var
  R: TFindRec;
  Size: Int64;
begin
  Result := 0;
  if FindFirst(Pattern, R) then
  try
    repeat
      if (R.Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0 then
      begin
        Found := True;
        if FileSize64(ExtractFilePath(Pattern) + R.Name, Size) then
          Result := Result + Size;
      end;
    until not FindNext(R);
  finally
    FindClose(R);
  end;
end;

procedure ScanCategories();
var
  Cat, I: Integer;
  Paths: TArrayOfString;
  Found: Boolean;
  Size: Int64;
begin
  for Cat := 0 to CatLast do
  begin
    Found := False;
    CatSize[Cat] := 0;
    Paths := CategoryPaths(Cat);
    for I := 0 to GetArrayLength(Paths) - 1 do
    begin
      if Pos('*', Paths[I]) > 0 then
        CatSize[Cat] := CatSize[Cat] + WildcardSize(Paths[I], Found)
      else if DirExists(Paths[I]) then
      begin
        Found := True;
        CatSize[Cat] := CatSize[Cat] + DirSize(Paths[I]);
      end
      else if FileExists(Paths[I]) then
      begin
        Found := True;
        if FileSize64(Paths[I], Size) then
          CatSize[Cat] := CatSize[Cat] + Size;
      end;
    end;
    CatPresent[Cat] := Found;
    Log(Format('Data category %d (%s): present=%d', [Cat, CategoryName(Cat), Ord(Found)]));
  end;
end;

function SizeText(const Bytes: Int64): String;
begin
  if Bytes < 1024 then
    Result := IntToStr(Bytes) + ' B'
  else if Bytes < 1048576 then
    Result := IntToStr(Bytes div 1024) + ' KB'
  else if Bytes < 1073741824 then
    Result := IntToStr(Bytes div 1048576) + ' MB'
  else
    Result := IntToStr(Bytes div 1073741824) + '.' + IntToStr(((Bytes mod 1073741824) * 10) div 1073741824) + ' GB';
end;

procedure UpdateCategoryBoxes(Sender: TObject);
var
  Cat: Integer;
begin
  for Cat := 0 to CatLast do
    CatBoxes[Cat].Enabled := RadioCustom.Checked and CatPresent[Cat];
end;

function ExternalRecordingNote(): String;
begin
  Result := '';
  if (RecordingDir <> '') and not IsUnder(RecordingDir, DataRoot) then
    Result := #13#10 + '• 录像目录已改为“' + RecordingDir + '”，不在数据目录内，卸载不会删除其中的录像（包括“完全卸载”）。';
end;

{ Shows the data options form. Returns False when the user cancels the uninstall. }
function ShowDataOptionsForm(): Boolean;
var
  Form: TSetupForm;
  Intro, KeepNote, PurgeNote, Note: TNewStaticText;
  OkButton, CancelButton: TNewButton;
  Cat, Y, W: Integer;
  Caption: String;
begin
  Form := CreateCustomForm(ScaleX(580), ScaleY(470), False, False);
  try
    Form.Caption := '卸载收货小助手';

    Intro := TNewStaticText.Create(Form);
    Intro.Parent := Form;
    Intro.AutoSize := False;
    Intro.WordWrap := True;
    Intro.Left := ScaleX(16);
    Intro.Top := ScaleY(14);
    Intro.Width := Form.ClientWidth - ScaleX(32);
    Intro.Height := ScaleY(36);
    Intro.Caption := '程序文件将被删除。请选择如何处理本机的收货数据（' + DataRoot + '）：';

    Y := ScaleY(58);
    RadioKeep := TNewRadioButton.Create(Form);
    RadioKeep.Parent := Form;
    RadioKeep.Left := ScaleX(16);
    RadioKeep.Top := Y;
    RadioKeep.Width := Form.ClientWidth - ScaleX(32);
    RadioKeep.Height := ScaleY(20);
    RadioKeep.Caption := '保留全部数据（推荐，与以前的卸载方式相同）';
    RadioKeep.OnClick := @UpdateCategoryBoxes;

    KeepNote := TNewStaticText.Create(Form);
    KeepNote.Parent := Form;
    KeepNote.AutoSize := False;
    KeepNote.WordWrap := True;
    KeepNote.Left := ScaleX(36);
    KeepNote.Top := Y + ScaleY(21);
    KeepNote.Width := Form.ClientWidth - ScaleX(52);
    KeepNote.Height := ScaleY(18);
    KeepNote.Caption := '重新安装后可继续使用原有的收货记录、录像、导出文件和设置。';

    Y := Y + ScaleY(48);
    RadioCustom := TNewRadioButton.Create(Form);
    RadioCustom.Parent := Form;
    RadioCustom.Left := ScaleX(16);
    RadioCustom.Top := Y;
    RadioCustom.Width := Form.ClientWidth - ScaleX(32);
    RadioCustom.Height := ScaleY(20);
    RadioCustom.Caption := '自定义：勾选要保留的数据，未勾选的将被删除';
    RadioCustom.OnClick := @UpdateCategoryBoxes;

    Y := Y + ScaleY(24);
    for Cat := 0 to CatLast do
    begin
      CatBoxes[Cat] := TNewCheckBox.Create(Form);
      CatBoxes[Cat].Parent := Form;
      CatBoxes[Cat].Left := ScaleX(36);
      CatBoxes[Cat].Top := Y;
      CatBoxes[Cat].Width := Form.ClientWidth - ScaleX(52);
      CatBoxes[Cat].Height := ScaleY(20);
      Caption := '保留' + CategoryName(Cat) + '（' + CategoryHint(Cat) + '）';
      if CatPresent[Cat] then
        Caption := Caption + '  约 ' + SizeText(CatSize[Cat])
      else
        Caption := Caption + '  未发现';
      CatBoxes[Cat].Caption := Caption;
      CatBoxes[Cat].Checked := KeepCat[Cat] or not CatPresent[Cat];
      Y := Y + ScaleY(24);
    end;

    Y := Y + ScaleY(4);
    RadioPurge := TNewRadioButton.Create(Form);
    RadioPurge.Parent := Form;
    RadioPurge.Left := ScaleX(16);
    RadioPurge.Top := Y;
    RadioPurge.Width := Form.ClientWidth - ScaleX(32);
    RadioPurge.Height := ScaleY(20);
    RadioPurge.Caption := '完全卸载：删除以上所有数据（无法恢复）';
    RadioPurge.OnClick := @UpdateCategoryBoxes;

    PurgeNote := TNewStaticText.Create(Form);
    PurgeNote.Parent := Form;
    PurgeNote.AutoSize := False;
    PurgeNote.WordWrap := True;
    PurgeNote.Left := ScaleX(36);
    PurgeNote.Top := Y + ScaleY(21);
    PurgeNote.Width := Form.ClientWidth - ScaleX(52);
    PurgeNote.Height := ScaleY(18);
    PurgeNote.Caption := '删除整个数据目录及安装目录内的导出文件。建议先把录像复制到其他位置备份。';

    Y := Y + ScaleY(50);
    Note := TNewStaticText.Create(Form);
    Note.Parent := Form;
    Note.AutoSize := False;
    Note.WordWrap := True;
    Note.Left := ScaleX(16);
    Note.Top := Y;
    Note.Width := Form.ClientWidth - ScaleX(32);
    Note.Height := Form.ClientHeight - Y - ScaleY(50);
    Note.Caption :=
      '• “收货小助手运行依赖”（' + RuntimeRoot + '）是独立安装的程序，本次卸载不会删除；如需移除，请在 Windows“设置 › 应用”中单独卸载它。' +
      ExternalRecordingNote();

    OkButton := TNewButton.Create(Form);
    OkButton.Parent := Form;
    OkButton.Caption := '下一步';
    OkButton.Top := Form.ClientHeight - ScaleY(23 + 14);
    OkButton.Height := ScaleY(23);
    OkButton.ModalResult := mrOk;
    OkButton.Default := True;

    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent := Form;
    CancelButton.Caption := '取消';
    CancelButton.Top := Form.ClientHeight - ScaleY(23 + 14);
    CancelButton.Height := ScaleY(23);
    CancelButton.ModalResult := mrCancel;
    CancelButton.Cancel := True;

    W := Form.CalculateButtonWidth([OkButton.Caption, CancelButton.Caption]);
    OkButton.Width := W;
    CancelButton.Width := W;
    CancelButton.Left := Form.ClientWidth - ScaleX(16) - W;
    OkButton.Left := CancelButton.Left - ScaleX(8) - W;

    case DataMode of
      ModeCustom: RadioCustom.Checked := True;
      ModePurge: RadioPurge.Checked := True;
    else
      RadioKeep.Checked := True;
    end;
    UpdateCategoryBoxes(nil);
    Form.ActiveControl := OkButton;

    Result := Form.ShowModal() = mrOk;
    if Result then
    begin
      if RadioPurge.Checked then
        DataMode := ModePurge
      else if RadioCustom.Checked then
        DataMode := ModeCustom
      else
        DataMode := ModeKeepAll;
      for Cat := 0 to CatLast do
        KeepCat[Cat] := CatBoxes[Cat].Checked or not CatPresent[Cat];
    end;
  finally
    Form.Free();
  end;
end;

function DeletionSummary(const Everything: Boolean): String;
var
  Cat: Integer;
begin
  Result := '';
  for Cat := 0 to CatLast do
    if CatPresent[Cat] and (Everything or not KeepCat[Cat]) then
      Result := Result + '• ' + CategoryName(Cat) + '（约 ' + SizeText(CatSize[Cat]) + '）：' + CategoryLocation(Cat) + #13#10;
end;

{ Data choice loop: options form, then an explicit second confirmation for any deletion. }
function ChooseDataMode(): Boolean;
var
  Cat: Integer;
  AnyDelete: Boolean;
  Text: String;
begin
  Result := False;
  while True do
  begin
    if not ShowDataOptionsForm() then
    begin
      Log('Uninstall cancelled on the data options form.');
      exit;
    end;
    if DataMode = ModeCustom then
    begin
      AnyDelete := False;
      for Cat := 0 to CatLast do
        if not KeepCat[Cat] then
          AnyDelete := True;
      if not AnyDelete then
        DataMode := ModeKeepAll;
    end;
    if DataMode = ModeKeepAll then
    begin
      Result := True;
      exit;
    end;

    if DataMode = ModePurge then
      Text :=
        '完全卸载将永久删除以下数据，删除后无法恢复：' + #13#10 + #13#10 +
        DeletionSummary(True) +
        '• 数据目录中的其他所有文件：' + DataRoot + #13#10 + #13#10 +
        '如果录像还可能用于售后举证，请先点“否”，把录像文件夹复制到其他磁盘备份后再卸载。' + #13#10 +
        '运行依赖（' + RuntimeRoot + '）不会被删除。' + #13#10 + #13#10 +
        '确定要删除全部数据吗？'
    else
      Text :=
        '将永久删除以下数据，删除后无法恢复：' + #13#10 + #13#10 +
        DeletionSummary(False) + #13#10 +
        '其余数据会保留在原位置。确定要删除吗？';
    if MsgBox(Text, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      Result := True;
      exit;
    end;
  end;
end;

function FriendlyProcessName(const Name: String): String;
var
  L: String;
begin
  L := Lowercase(Name);
  if L = 'receiving.native.exe' then
    Result := '收货小助手主程序（Receiving.Native.exe）'
  else if L = 'receiving.service.exe' then
    Result := '业务服务，负责读写 ERP（Receiving.Service.exe）'
  else if L = 'ffmpeg.exe' then
    Result := '录像进程（ffmpeg.exe）'
  else if L = 'ffprobe.exe' then
    Result := '录像检测（ffprobe.exe）'
  else if L = 'msedgewebview2.exe' then
    Result := '业务界面（msedgewebview2.exe）'
  else
    Result := Name;
end;

{ Processes whose executable lives under <app> (the uninstaller's own first phase excluded).
  Returns False when WMI could not be queried. }
function FindAppProcesses(var Pids: TIntArray; var Summary: String): Boolean;
var
  Locator, Services, Processes, Item: Variant;
  I: Integer;
  Path, Name: String;
  Names: TStringList;
begin
  Result := False;
  SetArrayLength(Pids, 0);
  Summary := '';
  Names := TStringList.Create;
  try
    try
      Locator := CreateOleObject('WbemScripting.SWbemLocator');
      Services := Locator.ConnectServer('.', 'root\CIMV2');
      Processes := Services.ExecQuery('SELECT ProcessId, Name, ExecutablePath FROM Win32_Process');
      for I := 0 to Processes.Count - 1 do
      begin
        Item := Processes.ItemIndex(I);
        if not VarIsNull(Item.ExecutablePath) then
        begin
          Path := Item.ExecutablePath;
          Name := Item.Name;
          if IsUnder(Path, AppDir) and not StartsWithText(Name, 'unins') then
          begin
            SetArrayLength(Pids, GetArrayLength(Pids) + 1);
            Pids[GetArrayLength(Pids) - 1] := Item.ProcessId;
            if Names.IndexOf(FriendlyProcessName(Name)) < 0 then
              Names.Add(FriendlyProcessName(Name));
            Log(Format('Running from install dir: PID %d %s', [Pids[GetArrayLength(Pids) - 1], Path]));
          end;
        end;
      end;
      Result := True;
    except
      Log('WMI process query failed: ' + GetExceptionMessage);
    end;
    for I := 0 to Names.Count - 1 do
      Summary := Summary + '• ' + Names.Strings[I] + #13#10;
  finally
    Names.Free;
  end;
end;

function AskForceClose(const Summary: String): Boolean;
var
  Text: String;
begin
  Text :=
    '以下进程仍在运行：' + #13#10 + Summary + #13#10 +
    '强制结束会立即中断它们：' + #13#10 +
    '• 如果正在提交入库或写入 ERP，这次提交可能被中断。卸载后请在聚水潭核对相关入库单，避免漏单或重复提交。' + #13#10 +
    '• 正在录制的录像会丢失尚未保存的部分。' + #13#10 + #13#10 +
    '更稳妥的做法：点“取消”，回到小助手结束当前录像、等提交完成后正常退出，再重新卸载。';
  Result := TaskDialogMsgBox('收货小助手仍在运行', Text, mbError, MB_OKCANCEL, [
    '强制结束并继续卸载' + #13#10 + '立即结束安装目录内的小助手进程，然后继续卸载',
     '取消' + #13#10 + '不结束任何进程，退出卸载，程序和数据都不改动'], 0) = IDOK;
end;

procedure KillPids(const Pids: TIntArray);
var
  I, Code: Integer;
  Params: String;
begin
  if GetArrayLength(Pids) = 0 then
    exit;
  { Kill exactly the matched processes (no /T): every child we started also runs from <app>
    and is matched itself, while unrelated programs the user opened stay untouched. }
  Params := '/F';
  for I := 0 to GetArrayLength(Pids) - 1 do
    Params := Params + ' /PID ' + IntToStr(Pids[I]);
  if Exec(ExpandConstant('{sys}\taskkill.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Log(Format('taskkill %s exited with %d', [Params, Code]))
  else
    Log('Failed to start taskkill: ' + SysErrorMessage(Code));
end;

function InitializeUninstall(): Boolean;
var
  Cat: Integer;
  Pids: TIntArray;
  Summary: String;
  WmiOk, Running: Boolean;
begin
  Result := False;
  AppDir := RemoveBackslashUnlessRoot(ExpandConstant('{app}'));
  DataRoot := ExpandConstant('{localappdata}\ReceivingAssistant\v3');
  RuntimeRoot := ExpandConstant('{localappdata}\ReceivingAssistant\runtime');
  ForceCloseSwitch := HasSwitch('/FORCECLOSE');
  PurgeSwitch := HasSwitch('/PURGEDATA');
  KillAgreed := False;
  RecordingDir := ReadRecordingDir();
  Log('Install dir: ' + AppDir + '; data root: ' + DataRoot + '; recording dir: ' + RecordingDir);
  for Cat := 0 to CatLast do
    KeepCat[Cat] := True;
  ScanCategories();

  if PurgeSwitch then
    DataMode := ModePurge
  else
    DataMode := ModeKeepAll;
  if not UninstallSilent then
  begin
    if not ChooseDataMode() then
      exit;
  end;
  Log(Format('Data mode: %d (0=keep all, 1=custom, 2=purge)', [DataMode]));

  WmiOk := FindAppProcesses(Pids, Summary);
  Running := (GetArrayLength(Pids) > 0) or CheckForMutexes(AppMutexes);
  if Running then
  begin
    if not WmiOk then
    begin
      SuppressibleMsgBox(
        '收货小助手正在运行，但无法确定它的进程位置，因此不会强制结束。' + #13#10 + #13#10 +
        '请先在小助手里结束当前录像、等提交完成后正常退出，然后重新卸载。', mbError, MB_OK, IDOK);
      exit;
    end;
    if Summary = '' then
      Summary := '• 收货小助手（可能是另一个副本，例如便携版）' + #13#10;
    if UninstallSilent then
    begin
      if not ForceCloseSwitch then
      begin
        Log('App is running and /FORCECLOSE was not given; refusing silent uninstall.');
        SuppressibleMsgBox(
          '收货小助手正在运行，静默卸载已取消，程序和数据均未改动。' + #13#10 +
          '请先正常退出小助手；如确需强制结束，请在命令行加上 /FORCECLOSE。', mbError, MB_OK, IDOK);
        exit;
      end;
      KillAgreed := True;
    end
    else
    begin
      if not AskForceClose(Summary) then
      begin
        Log('User declined to force-close the running app.');
        exit;
      end;
      KillAgreed := True;
    end;
  end;
  Result := True;
end;

{ Runs after the standard "confirm uninstall" question and right before the AppMutex check. }
procedure ForceCloseIfNeeded();
var
  Pids: TIntArray;
  Summary: String;
  Attempt: Integer;
begin
  FindAppProcesses(Pids, Summary);
  if GetArrayLength(Pids) = 0 then
    exit;
  if not KillAgreed then
  begin
    { Started after the first check }
    if UninstallSilent then
    begin
      if not ForceCloseSwitch then
        RaiseException('收货小助手在卸载过程中被启动，卸载已中止，程序和数据均未改动。');
    end
    else if not AskForceClose(Summary) then
      RaiseException('已取消卸载，程序和数据均未改动。');
    KillAgreed := True;
  end;

  for Attempt := 1 to 3 do
  begin
    KillPids(Pids);
    Sleep(1000);
    FindAppProcesses(Pids, Summary);
    if GetArrayLength(Pids) = 0 then
      break;
  end;
  if GetArrayLength(Pids) > 0 then
    RaiseException('无法结束以下进程，卸载已中止，程序和数据均未改动：' + #13#10 + Summary + #13#10 +
      '请重启电脑后再卸载。');

  { The killed processes held the app mutexes; give Windows a moment to release them. If one is
    still held (for example by a portable copy), the AppMutex check that follows refuses. }
  for Attempt := 1 to 20 do
  begin
    if not CheckForMutexes(AppMutexes) then
      break;
    Sleep(250);
  end;
  Log('Force-close finished; app mutexes still held: ' + IntToStr(Ord(CheckForMutexes(AppMutexes))));
end;

procedure SafeDelete(const Path: String);
begin
  if not IsSafeTarget(Path) then
  begin
    Log('Refusing to delete outside the data/install directories: ' + Path);
    exit;
  end;
  if Pos('*', Path) > 0 then
  begin
    if not DelTree(Path, False, True, False) then
      Log('Could not delete all of ' + Path);
  end
  else if DirExists(Path) then
  begin
    if not DelTree(Path, True, True, True) then
      Log('Could not delete all of ' + Path);
  end
  else if FileExists(Path) then
  begin
    if not DeleteFile(Path) then
      Log('Could not delete ' + Path);
  end;
end;

procedure DeleteChosenData();
var
  Cat, I: Integer;
  Paths: TArrayOfString;
begin
  if DataMode = ModeKeepAll then
  begin
    Log('Keeping all user data.');
    exit;
  end;
  for Cat := 0 to CatLast do
    if (DataMode = ModePurge) or not KeepCat[Cat] then
    begin
      Log('Deleting data category: ' + CategoryName(Cat));
      Paths := CategoryPaths(Cat);
      for I := 0 to GetArrayLength(Paths) - 1 do
        SafeDelete(Paths[I]);
    end;
  if DataMode = ModePurge then
  begin
    { Whole data root, including files no category knows about. }
    if (CompareText(DataRoot, ExpandConstant('{localappdata}\ReceivingAssistant\v3')) = 0) and DirExists(DataRoot) then
    begin
      Log('Deleting data root: ' + DataRoot);
      if not DelTree(DataRoot, True, True, True) then
        Log('Could not delete all of ' + DataRoot);
    end;
  end;
  { Only removes directories that are empty now }
  RemoveDir(DataRoot + '\data');
  RemoveDir(DataRoot);
  RemoveDir(AppDir + '\backend');
  RemoveDir(AppDir);
end;

function PowerShellExe(): String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

{ The remote admin page (remote_access.py open_firewall) adds inbound rules named
  "收货小助手远程管理页 TCP <端口>" through an elevated netsh. Without the program they only keep a
  port open, so the uninstaller always removes them, whichever data is kept. Reading the rules
  works unelevated; deleting needs elevation: run directly when already elevated, otherwise ask
  for UAC in an interactive uninstall. A silent, unelevated uninstall only logs the manual command. }
procedure RemoveRemoteFirewallRules();
var
  Code: Integer;
  Pattern, Check, Remove: String;
  Present: Boolean;
begin
  Pattern := FirewallRulePrefix + '*';
  Check := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "if (Get-NetFirewallRule -DisplayName ''' +
    Pattern + ''' -ErrorAction SilentlyContinue) { exit 10 } else { exit 11 }"';
  Remove := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-NetFirewallRule -DisplayName ''' +
    Pattern + ''' -ErrorAction SilentlyContinue | Remove-NetFirewallRule"';
  { Unknown result (PowerShell missing or failed): fall back to "the remote page was ever configured". }
  Code := -1;
  Present := DirExists(DataRoot + '\data\remote-access');
  if Exec(PowerShellExe(), Check, '', SW_HIDE, ewWaitUntilTerminated, Code) then
  begin
    if Code = 10 then
      Present := True
    else if Code = 11 then
      Present := False;
  end;
  Log(Format('Remote firewall rules present: %d (check exit %d)', [Ord(Present), Code]));
  if not Present then
    exit;
  if IsAdmin then
  begin
    if Exec(PowerShellExe(), Remove, '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
      Log('Remote firewall rules removed (elevated).')
    else
      Log('Could not remove the remote firewall rules (exit ' + IntToStr(Code) + ').');
  end
  else if UninstallSilent then
    Log('Remote firewall rules left in place: silent uninstall is not elevated. Remove manually: ' +
      'netsh advfirewall firewall delete rule name="' + FirewallRulePrefix + '<端口>"')
  else
  begin
    MsgBox('检测到远程管理页添加过的 Windows 防火墙放行规则（' + FirewallRulePrefix + '…）。' + #13#10 +
      '卸载后这些规则已无用途，接下来会请求一次管理员权限将其删除。', mbInformation, MB_OK);
    if ShellExec('runas', PowerShellExe(), Remove, '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Log('Remote firewall rules removal requested with UAC (exit ' + IntToStr(Code) + ').')
    else
    begin
      Log('UAC declined or failed for the firewall rule removal: ' + SysErrorMessage(Code));
      MsgBox('未能删除防火墙放行规则（未获得管理员授权）。' + #13#10 +
        '可在“Windows Defender 防火墙 - 高级设置 - 入站规则”中删除名称以“' + FirewallRulePrefix + '”开头的规则。',
        mbError, MB_OK);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  case CurUninstallStep of
    usAppMutexCheck:
      ForceCloseIfNeeded();
    usPostUninstall:
      begin
        RemoveRemoteFirewallRules();
        DeleteChosenData();
      end;
  end;
end;

#ifdef UpgradeOnly
{ Upgrade installer only (needs JsonUnescape above): copy portable data after the files are installed. }
{ Value of a top-level string field in the ASCII-escaped JSON report PortableImport.cs writes. }
function JsonStringField(const S, Key: String): String;
var
  P, I: Integer;
  T, Value: String;
  Escaped: Boolean;
begin
  Result := '';
  P := Pos('"' + Key + '":"', S);
  if P = 0 then
    exit;
  T := Copy(S, P + Length(Key) + 4, Length(S));
  Value := '';
  Escaped := False;
  for I := 1 to Length(T) do
  begin
    if (not Escaped) and (Copy(T, I, 1) = '"') then
    begin
      Result := JsonUnescape(Value);
      exit;
    end;
    Escaped := (not Escaped) and (Copy(T, I, 1) = '\');
    Value := Value + Copy(T, I, 1);
  end;
end;

procedure RunPortableImport();
var
  Report, Message: String;
  Raw: AnsiString;
  Code: Integer;
begin
  if WizardSilent() then
    ImportSource := CleanDir(ExpandConstant('{param:PORTABLEDATA|}'));
  if ImportSource = '' then
    exit;
  WizardForm.StatusLabel.Caption := '正在复制便携版数据……';
  Report := ExpandConstant('{tmp}\portable-import.json');
  DeleteFile(Report);
  if not Exec(ExpandConstant('{app}\Receiving.Native.exe'),
      '--import-portable "' + ImportSource + '" --report "' + Report + '"', ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := -1;
  Message := '';
  if LoadStringFromFile(Report, Raw) then
    Message := JsonStringField(Raw, 'message');
  Log('Portable import from ' + ImportSource + ' exit=' + IntToStr(Code) + ' message=' + Message);
  if Code = 0 then
    SuppressibleMsgBox(Message, mbInformation, MB_OK, IDOK)
  else
  begin
    if Message = '' then
      Message := '复制便携版数据没有完成（代码 ' + IntToStr(Code) + '）。便携版文件夹没有改动。';
    SuppressibleMsgBox('收货小助手已经安装好，但没有导入便携版数据：' + #13#10 + #13#10 + Message, mbError, MB_OK, IDOK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunPortableImport();
end;
#endif
