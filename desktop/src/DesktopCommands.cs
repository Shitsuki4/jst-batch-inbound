using System.Windows;
using System.Text.Json;
using System.Diagnostics;
using Microsoft.Web.WebView2.Core;
namespace Receiving.Native;
public sealed partial class MainWindow {
 readonly SemaphoreSlim commandGate=new(1);UIElement? exportContent,libraryContent;Window? exportWindow,libraryWindow;
 VideoHotkeys? hotkeys;VideoSounds? sounds;VideoLibrary? videoLibrary;string archiveHint="",testSegmentId="",settingsLoadWarning="",cleanupHint="";bool settingsLoadFailed;DateTimeOffset nextCleanupCheck;DateTimeOffset? testDeadline;
 VideoLibrary Library => videoLibrary!=null&&videoLibrary.Root.Equals(ArchiveLayout.Root(rec.DirectoryPath),StringComparison.OrdinalIgnoreCase)?videoLibrary:videoLibrary=new VideoLibrary(rec.DirectoryPath);
 object VideoSettings(){
  var data=JsonSerializer.Deserialize<Dictionary<string,object?>>(JsonSerializer.Serialize(settings,UserSettings.WebJson))!;
  data["camera"]=rec.Camera;data["resolution"]=rec.Resolution;data["directory"]=rec.DirectoryPath;data["engine"]=rec.Engine;
  data["enabled"]=enabled.IsChecked==true;data["active"]=rec.Active;data["ready"]=rec.Ready;data["tracking"]=rec.Current?.Tracking??"";
  data["test"]=rec.Current?.BatchKey.StartsWith("TEST-",StringComparison.Ordinal)==true;data["status"]=rec.Status;data["warning"]=rec.Warning;
  data["diskConfigured"]=settings.DiskConfigured;data["hotkeysRegistered"]=hotkeys?.Registered==true;data["hotkeyStatus"]=hotkeys?.Status??"窗口准备中，全局快捷键尚未注册";
  data["effectiveMode"]=rec.Current?.EffectiveMode??"";data["archiveHint"]=archiveHint;data["settingsWarning"]=settingsLoadWarning;data["cleanupHint"]=cleanupHint;return data;
 }
 bool CommandSourceAllowed(string value)=>Uri.TryCreate(value,UriKind.Absolute,out var uri)&&Uri.TryCreate(backend.Url,UriKind.Absolute,out var local)&&uri.Scheme==local.Scheme&&uri.Authority==local.Authority&&uri.AbsolutePath is "/" or "/index.html" or "/joint.html" or "/unplanned.html";
 async void ReceiveCommand(object? sender,CoreWebView2WebMessageReceivedEventArgs e){
  if(!CommandSourceAllowed(e.Source))return;string id="";bool entered=false;
  try{using var doc=JsonDocument.Parse(e.WebMessageAsJson);var m=doc.RootElement;if(!m.TryGetProperty("kind",out var kind)||kind.GetString()!="receiving-command")return;
   id=m.GetProperty("id").GetString()??"";if(id.Length is <1 or >100)return;var action=m.GetProperty("action").GetString()??"";var args=m.TryGetProperty("args",out var a)?a:default;
   entered=await commandGate.WaitAsync(0);if(!entered)throw new Exception("已有原生操作进行中，请等待完成。");
   var result=await DesktopCommand(action,args);web.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(new{kind="receiving-reply",id,ok=true,result},UserSettings.WebJson));
  }catch(Exception ex){if(id!=""&&web.CoreWebView2!=null)web.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(new{kind="receiving-reply",id,ok=false,error=ex.Message}));}
  finally{if(entered)commandGate.Release();}
 }
 static string Arg(JsonElement args,string name){if(args.ValueKind!=JsonValueKind.Object||!args.TryGetProperty(name,out var v)||v.ValueKind!=JsonValueKind.String)throw new Exception("缺少参数："+name);var s=v.GetString()?.Trim()??"";if(s.Length>2000||s.Contains('\0'))throw new Exception("无效参数："+name);return s;}
 async Task HotkeyAction(string action){if(closing||!await commandGate.WaitAsync(0))return;try{await DesktopCommand(action);}catch(Exception ex){status.Text="快捷键操作未完成："+ex.Message;sounds?.Play("error",settings);}finally{commandGate.Release();}}
 async Task ContinueRecording(){
  if(enabled.IsChecked!=true)throw new Exception("请先在设置 → 视频举证启用自动录像");if(currentTracking==""||currentKey=="")throw new Exception("请先重新确认物流单号");
  using var response=await backend.Get("/api/native/context?key="+Uri.EscapeDataString(currentKey));var context=response.RootElement.GetProperty("context").Deserialize<VideoContext>(ArchiveLayout.Json)!;
  if(context.Tracking!=currentTracking)throw new Exception("物流关联已变化，请重新扫描确认，未开始录像");
  Library.Associate(context);await rec.StartAsync(currentTracking,currentKey,context);
 }
 async Task<object> DesktopCommand(string action,JsonElement args=default){
  if(closing)throw new Exception("程序正在退出");
  if(UiTest.SettingsOnly && action is "cameras" or "cameraModes" or "cameraControls" or "setCameraControl" or "testStart" or "continue")throw new InvalidOperationException("Settings-only acceptance forbids camera access");
  switch(action){
   case "openUrl":AppUpdate.OpenUrl(Arg(args,"url"));return new{message="已在浏览器打开发布页"};
   case "applyUpdate":return await ApplyUpdate();
   case "settings":return VideoSettings();
   case "validateTracking":settings.ValidateTracking(Arg(args,"tracking"));return new{valid=true};
   case "preview":preview.Show();return new{message="浮窗已显示；隐藏不停止录像"};
   case "togglePreview":if(preview.IsVisible)preview.Hide();else preview.Show();return new{message=preview.IsVisible?"浮窗已显示":"浮窗已隐藏，录像不受影响"};
   case "stop":case "continue":
    await eventsGate.WaitAsync();try{if(action=="stop")await rec.StopAsync("用户结束本单录像");else await ContinueRecording();await PublishVideo();return VideoSettings();}finally{eventsGate.Release();}
   case "directory":case "currentDirectory":
    var directory=action=="currentDirectory"&&rec.Current!=null?Path.GetDirectoryName(rec.Current.File)!:rec.DirectoryPath;
    directory=ArchiveLayout.Checked(rec.DirectoryPath,directory,true);Directory.CreateDirectory(directory);Process.Start(new ProcessStartInfo(directory){UseShellExecute=true});return new{message="已打开录像目录"};
   case "diagnostics":Clipboard.SetText(rec.Diagnostics);return new{message="已复制录像诊断（不含视频），可粘贴反馈"};
   case "export":case "library":case "cleanup":
    if(await HasPending(false))throw new Exception("请先完成商品数量确认 / 保存后再打开此模块");
    OpenNativePanel(action=="cleanup"?"library":action);if(action!="export")await OpenLibraryAsync(action=="cleanup");return new{message="已打开"};
   case "soundTest":var cue=Arg(args,"cue");if(cue is not("start" or "stop" or "saved" or "duplicate" or "error"))throw new Exception("未知提示音");if(!UiTest.Active)sounds?.Play(cue,settings,true);return new{message=sounds?.Error==""?"已按保存的音量试听（不会录入录像）":sounds?.Error};
   case "auto":
    if(args.ValueKind!=JsonValueKind.Object||!args.TryGetProperty("enabled",out var flag)||flag.ValueKind is not(JsonValueKind.True or JsonValueKind.False))throw new Exception("自动录像状态无效");
    if(!args.TryGetProperty("confirmed",out var confirmed)||confirmed.ValueKind!=JsonValueKind.True)throw new Exception("请明确确认自动录像开关");
    if(settingsLoadFailed)throw new Exception("视频配置读取失败，请先核对并保存视频设置，原配置未覆盖");
    if(flag.GetBoolean()&&!settings.DiskConfigured)throw new Exception("请先在本软件保存磁盘预警和停止GB（预警 > 停止 > 0）");
    await eventsGate.WaitAsync();try{
     if(!flag.GetBoolean())await rec.StopAsync("关闭自动录像");
     var changed=settings.Copy();changed.AutoRecordEnabled=flag.GetBoolean();WriteSettings(changed);
     settings=changed;enabled.IsChecked=changed.AutoRecordEnabled;await PublishVideo();return VideoSettings();
    }finally{eventsGate.Release();}
   case "saveSettings":case "chooseDirectory":case "cameras":case "cameraModes":case "cameraControls":case "setCameraControl":case "testStart":case "testStop":
    await eventsGate.WaitAsync();try{
     if(action=="testStop"){
      if(rec.Current?.BatchKey.StartsWith("TEST-",StringComparison.Ordinal)!=true)throw new Exception("当前不是测试录像；实际收货请使用结束本单录像");
      if(rec.Active)await rec.StopAsync("结束测试并验证文件");testDeadline=null;
      if(rec.Current.State!="saved")throw new Exception(rec.Status);return VideoSettings();
     }
     if(rec.Active)throw new Exception("正在录像，请先结束录像后再修改 / 测试，不会切断实际收货");
     if(action=="cameras")return new{cameras=await rec.Cameras()};
     if(action=="cameraModes"){
      var camera=Arg(args,"camera");if(string.IsNullOrWhiteSpace(camera)||camera.Any(char.IsControl))throw new Exception("请先填写摄像头");
      var engine=Arg(args,"engine");if(string.IsNullOrWhiteSpace(engine))engine=rec.Engine;
      if(!Path.IsPathFullyQualified(engine)||!File.Exists(engine)||!string.Equals(Path.GetExtension(engine),".exe",StringComparison.OrdinalIgnoreCase))throw new Exception("请使用安装版内置 FFmpeg.exe 的完整路径");
      var result=await Recorder.Run(engine,["-hide_banner","-list_options","true","-f","dshow","-i","video="+camera]);var modes=CameraMode.Parse(result.Err);
      if(modes.Count==0)throw new Exception("设备未报告可用录像模式，请检查驱动、连接和占用情况后重新检测");return new{camera,engine,modes=modes.Select(m=>new{format=m.Format,width=m.Width,height=m.Height,fps=m.Fps,label=m.ToString()})};
     }
     if(action=="cameraControls")return new{controls=CameraControls.Read(Arg(args,"camera"))};
     if(action=="setCameraControl")return new{controls=CameraControls.Set(Arg(args,"camera"),args)};
     if(action=="chooseDirectory"){var picker=new Microsoft.Win32.OpenFolderDialog();return new{directory=picker.ShowDialog(this)==true?picker.FolderName:""};}
     if(action=="testStart"){
      if(await HasPending(false))throw new Exception("请先确认或取消当前商品，再开始测试");
      if(enabled.IsChecked==true)throw new Exception("请先关闭扫码自动录像，再开始独立的30秒摄像头测试");
      await rec.StartAsync("TEST-摄像头验收","TEST-"+Guid.NewGuid().ToString("N"));testSegmentId=rec.Current!.Id;testDeadline=DateTimeOffset.Now.AddSeconds(30);return VideoSettings();
     }
     var saved=UserSettings.FromWeb(settings,args);var probe=FindTool("ffprobe.exe",Path.Combine(Path.GetDirectoryName(saved.Engine)!,"ffprobe.exe"));if(!File.Exists(probe))throw new Exception("未找到配套 ffprobe.exe，请修复安装");
     if(saved.Camera!=settings.Camera||saved.Resolution!=settings.Resolution||saved.FrameRate!=settings.FrameRate||saved.CaptureFormat!=settings.CaptureFormat||saved.Engine!=settings.Engine){
      var detected=await Recorder.Run(saved.Engine,["-hide_banner","-list_options","true","-f","dshow","-i","video="+saved.Camera]);
      CameraMode.Choose(CameraMode.Parse(detected.Err),saved.Resolution,saved.FrameRate,saved.CaptureFormat);
     }
     saved.Left=preview.Left;saved.Top=preview.Top;saved.Width=preview.Width;saved.Height=preview.Height;
     using var registration=hotkeys?.Prepare(saved);
     WriteSettings(saved);settingsLoadFailed=false;settingsLoadWarning="";nextCleanupCheck=default;cleanupHint="";settings=saved;enabled.IsChecked=saved.AutoRecordEnabled;rec.Configure(saved);rec.Probe=probe;preview.Configure(saved);registration?.Commit();
     return new{message="视频设置已保存；业务设置保持不变。全局快捷键："+(hotkeys?.Status??"未注册")};
    }finally{eventsGate.Release();}
   default:throw new Exception("不支持的桌面操作");
  }
 }
 // Installs a package the backend downloaded and verified. Replies to the web page first, then exits through the normal safe-exit path.
 async Task<object> ApplyUpdate(){
  if(UiTest.Active)throw new Exception("验收模式不允许安装更新");
  if(rec.Active)throw new Exception("正在录像，请先结束本单录像再安装更新");
  if(await HasPending(true))throw new Exception("请先保存或取消待录入商品 / 设置草稿，再安装更新");
  UpdatePlan plan;
  using(var response=await backend.Post("/api/update/prepare",new{recording=rec.Active})){
   var info=response.RootElement.GetProperty("install");
   string Str(string name)=>info.TryGetProperty(name,out var v)&&v.ValueKind==JsonValueKind.String?v.GetString()??"":"";
   plan=await Task.Run(()=>AppUpdate.Prepare(Str("kind"),Str("version"),Str("path"),Str("sha256")));
  }
  var text=plan.Kind=="installer"
   ?$"已下载并校验新版本 {plan.Version}。\n\n程序将安全退出（保存录像与设置），随后打开安装程序。是否继续？"
   :$"已下载并校验新版本 {plan.Version}，已解压到：\n{plan.Target}\n\n程序将安全退出，随后打开新版本文件夹。是否继续？";
  if(MessageBox.Show(this,text,"安装更新",MessageBoxButton.YesNo,MessageBoxImage.Question)!=MessageBoxResult.Yes)return new{started=false,message="已取消安装更新"};
  if(rec.Active||await HasPending(true))throw new Exception("确认期间出现录像或待保存内容，已取消安装更新");
  var pid=Environment.ProcessId;
  _=Dispatcher.BeginInvoke(new Action(async()=>{await Task.Delay(400);if(closing)return;await ShutdownSafely(()=>AppUpdate.Launch(plan,pid));}),System.Windows.Threading.DispatcherPriority.Background);
  return new{started=true,kind=plan.Kind,version=plan.Version,message=plan.Kind=="installer"?"即将退出并启动安装程序":"即将退出并打开新版本文件夹"};
 }

 async Task CheckCleanupReminder(){
  if(settings.CleanupDays==0){cleanupHint="";return;}if(rec.Active||DateTimeOffset.Now<nextCleanupCheck)return;
  nextCleanupCheck=DateTimeOffset.Now.AddHours(24);var store=Library;var days=settings.CleanupDays;
  try{var candidates=await Task.Run(()=>store.CleanupCandidates(days,DateTimeOffset.Now,null));cleanupHint=candidates.Count==0?"":$"清理提醒：{candidates.Count} 段已保存录像超过{days}天（{candidates.Sum(i=>i.Bytes)/1073741824.0:0.00} GB）；可预览并手动勾选。未自动删除，售后保护不进入候选。";}
  catch(Exception ex){cleanupHint="清理提醒检查失败，未删除任何文件："+ex.Message;}
 }
 void OpenNativePanel(string kind){
  var existing=kind=="export"?exportWindow:libraryWindow;if(existing!=null){existing.Activate();return;}
  var content=kind=="export"?exportContent:libraryContent;
  var win=kind=="export"
   ?new Window{Title="明细导出 · 秒级扫描记录",Owner=this,Width=1180,Height=760,MinWidth=820,MinHeight=580,WindowStartupLocation=WindowStartupLocation.CenterOwner,Content=content,FontFamily=FontFamily,FontSize=FontSize}
   :new Window{Title="录像查询 · 视频举证",Owner=this,Width=Math.Min(1440,Math.Max(1024,SystemParameters.WorkArea.Width-40)),Height=Math.Min(900,Math.Max(640,SystemParameters.WorkArea.Height-40)),MinWidth=1000,MinHeight=600,WindowStartupLocation=WindowStartupLocation.CenterOwner,Content=content,FontFamily=FontFamily,FontSize=FontSize};
  if(kind=="export")exportWindow=win;else libraryWindow=win;
  win.Closed+=(_,_)=>{if(kind!="export")libraryView?.Detach();win.Content=null;if(kind=="export")exportWindow=null;else libraryWindow=null;};win.Show();
 }
 void WriteSettings(UserSettings value)=>value.Write(Path.Combine(root,"native-settings.json"));
}
