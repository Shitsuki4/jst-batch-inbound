using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using System.Data;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
namespace Receiving.Native;
public sealed partial class MainWindow:Window {
 readonly string root=AppPaths.DataRoot;readonly Backend backend=new();readonly Recorder rec=new();readonly WebView2 web=new();readonly TextBlock status=new(){FontSize=13};readonly CheckBox enabled=new(){Content="启用扫码自动录像",Margin=new Thickness(12),VerticalAlignment=VerticalAlignment.Center}; readonly SemaphoreSlim eventsGate=new(1);PreviewWindow preview;UserSettings settings=new();bool closing,ticking;int failed;readonly DispatcherTimer timer=new(){Interval=TimeSpan.FromMilliseconds(350)};string currentTracking="",currentKey="";
 readonly DataGrid exportGrid=new(){IsReadOnly=true,AutoGenerateColumns=true,CanUserAddRows=false};readonly Dictionary<string,TextBox> filters=new();readonly ComboBox exportMode=new(){Width=200};JsonElement? exportResult;
 public MainWindow(){
  if(UiTest.Active)root=UiTest.Output;
  if(UiTest.Hidden){ShowActivated=false;ShowInTaskbar=false;Opacity=0;}
  Directory.CreateDirectory(root);
  Title=ShellChrome.WindowTitle;Width=1450;Height=950;MinWidth=1024;MinHeight=700;Background=Palette.Bg;Foreground=Palette.Text;FontFamily=Palette.Font;FontSize=14;
  try{settings=UserSettings.Read(Path.Combine(root,"native-settings.json"));}
  catch(Exception ex){settings=new();settingsLoadWarning="视频配置读取失败，原文件保留，自动录像关闭；请在设置中核对后显式保存："+ex.Message;settingsLoadFailed=true;}
  enabled.IsChecked=settings.AutoRecordEnabled&&!settingsLoadFailed;

  rec.DirectoryPath=string.IsNullOrEmpty(settings.Directory)?Path.Combine(root,"录像"):settings.Directory;rec.Camera=settings.Camera;rec.Resolution=settings.Resolution;
  rec.Engine=FindTool("ffmpeg.exe",settings.Engine);rec.Probe=FindTool("ffprobe.exe",Path.Combine(Path.GetDirectoryName(rec.Engine)??"","ffprobe.exe"));
  settings.Directory=rec.DirectoryPath;settings.Engine=rec.Engine;rec.Configure(settings);
  preview=new PreviewWindow(rec,async action=>{await DesktopCommand(action);}){Left=Math.Clamp(settings.Left,SystemParameters.VirtualScreenLeft,SystemParameters.VirtualScreenLeft+Math.Max(0,SystemParameters.VirtualScreenWidth-360)),Top=Math.Clamp(settings.Top,SystemParameters.VirtualScreenTop,SystemParameters.VirtualScreenTop+Math.Max(0,SystemParameters.VirtualScreenHeight-190)),Width=Math.Max(360,settings.Width),Height=Math.Max(190,settings.Height)};
  preview.Configure(settings);sounds=new VideoSounds(root);
  rec.Cue+=kind=>Dispatcher.InvokeAsync(()=>{if(!UiTest.Active)sounds.Play(kind,settings);});
  SourceInitialized+=(_,_)=>{hotkeys=new VideoHotkeys(new System.Windows.Interop.WindowInteropHelper(this).Handle,action=>{_=HotkeyAction(action);});try{using var plan=hotkeys.Prepare(settings);plan.Commit();}catch(Exception ex){hotkeys.ReportStartupFailure(ex.Message);status.Text=hotkeys.Status;}};
  // C 风格外壳：品牌只在网页侧栏出现（「扫码入库」），原生层只留一条细工具栏 + 状态行。
  var panel=new DockPanel();var (top,bar)=ShellChrome.Build(status);DockPanel.SetDock(top,Dock.Top);panel.Children.Add(top);
  Add(bar,"显示监控浮窗",()=>{preview.Show();return Task.CompletedTask;},Theme.Primary);Add(bar,"结束本单录像",()=>rec.StopAsync());Add(bar,"继续本单新一段",async()=>{if(!enabled.IsChecked.GetValueOrDefault())throw new Exception("请先启用自动录像");if(currentTracking=="")throw new Exception("请先在业务界面确认物流单，或使用设置里的测试录像");await DesktopCommand("continue");});Add(bar,"打开录像目录",()=>{System.IO.Directory.CreateDirectory(rec.DirectoryPath);Process.Start(new ProcessStartInfo(rec.DirectoryPath){UseShellExecute=true});return Task.CompletedTask;},Theme.Ghost);
  exportContent=ExportPanel();libraryContent=LibraryPanel();panel.Children.Add(web);Content=panel;
  Loaded+=async(_,_)=>await Safe(Start);Closing+=OnClosing;
  timer.Tick+=async(_,_)=>{if(ticking||closing)return;ticking=true;try{await rec.HealthAsync();if(testDeadline!=null&&DateTimeOffset.Now>=testDeadline&&rec.Active&&rec.Current?.Id==testSegmentId){await rec.StopAsync("30秒测试完成并验证文件");testDeadline=null;}await Poll();await CheckCleanupReminder();await PublishVideo();status.Text=$"{rec.Status}　物流：{rec.Current?.Tracking??"—"}　业务服务：{(backend.Alive?"运行中":"已停止，停止操作并查看日志")}　{(enabled.IsChecked==true?"自动录像已启用":"自动录像关闭")}　{rec.Warning}　{cleanupHint}　{settingsLoadWarning}";}catch(Exception ex){status.Text="异常："+ex.Message;}finally{ticking=false;}};
 }
 static string FindTool(string name,string preferred)=>Dependencies.Resolve(name,preferred);
 Button Add(Panel p,string text,Func<Task> fn,string level=Theme.Secondary){var b=ShellChrome.Action(text,level);b.Click+=async(_,_)=>{b.IsEnabled=false;try{await Safe(fn);}finally{b.IsEnabled=true;}};p.Children.Add(b);return b;}
 async Task Safe(Func<Task> f){try{await f();}catch(Exception ex){status.Text=ex.Message;MessageBox.Show(this,ex.Message,"操作未完成",MessageBoxButton.OK,MessageBoxImage.Warning);}}
 async Task Start(){
  var data=Path.Combine(root,"data");if(UiTest.Active)await backend.AttachForAcceptance(UiTest.Url,UiTest.Token,UiTest.Pid);else await backend.Start(AppContext.BaseDirectory,data);
  var env=await CoreWebView2Environment.CreateAsync(AppPaths.BrowserFolder,Path.Combine(root,"webview-profile"));webEnvironment.TrySetResult(env);await web.EnsureCoreWebView2Async(env);
  web.CoreWebView2.WebMessageReceived+=ReceiveCommand;
  if(UiTest.Active){
   web.CoreWebView2.AddWebResourceRequestedFilter("*",CoreWebView2WebResourceContext.All);
   web.CoreWebView2.WebResourceRequested+=(_,e)=>{
    if(Uri.TryCreate(e.Request.Uri,UriKind.Absolute,out var uri)&&uri.Scheme is "http" or "https" && !uri.IsLoopback){
     File.AppendAllText(Path.Combine(root,"blocked-test-network.log"),e.Request.Uri+"\n");
     e.Response=web.CoreWebView2.Environment.CreateWebResourceResponse(new MemoryStream(),403,"Local acceptance only","");
    }
   };
  }
  web.CoreWebView2.Settings.AreDevToolsEnabled=false;web.CoreWebView2.Settings.IsStatusBarEnabled=false;
  web.CoreWebView2.PermissionRequested+=(_,e)=>{if(UiTest.Active)File.AppendAllText(Path.Combine(UiTest.Output,"permissions.log"),DateTimeOffset.Now+" "+e.PermissionKind+" "+e.Uri+"\n");e.State=CoreWebView2PermissionState.Deny;};
  web.CoreWebView2.NavigationStarting+=async(_,e)=>{if(!e.Uri.StartsWith(backend.Url+"/",StringComparison.OrdinalIgnoreCase)&&e.Uri!=backend.Url&&e.Uri!="about:blank")e.Cancel=true;else if(rec.Active){currentTracking="";currentKey="";await rec.StopAsync("切换业务页面，重新确认物流后续录");}};
  web.CoreWebView2.NewWindowRequested+=(_,e)=>{e.Handled=true;if(e.Uri.StartsWith(backend.Url+"/"))web.CoreWebView2.Navigate(e.Uri);};
  await web.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync("window.__nativeVideo="+JsonSerializer.Serialize(new{enabled=enabled.IsChecked==true,ready=false,key=""})+";");
  if(UiTest.Active)await web.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync("window.__uiErrors=[];addEventListener('error',e=>__uiErrors.push(e.message));addEventListener('unhandledrejection',e=>__uiErrors.push(String(e.reason))); window.__uiFetch=[];const diagnosticFetch=window.fetch.bind(window);window.fetch=async function(url,options){const row={url:String(url)};window.__uiFetch.push(row);try{const r=await diagnosticFetch(url,options);row.status=r.status;if(String(url).startsWith('/api/state'))row.data=await r.clone().text();return r;}catch(e){row.error=String(e);throw e;}}; ");
  rec.Changed+=()=>Dispatcher.InvokeAsync(async()=>await PublishVideo());
  web.CoreWebView2.ProcessFailed+=(_,_)=>status.Text="业务界面异常。录像不会随页面销毁；请先结束录像，再重开软件，核对原单。";
  web.CoreWebView2.Navigate(backend.Url+"/");
  if(settings.ShowPreviewOnStartup&&!UiTest.Active)preview.Show();
  timer.Start();if(UiTest.Active)_=UiTest.HttpOnly?AcceptanceHttpUi():UiTest.SettingsOnly?AcceptanceSettingsUi():AcceptanceUi();await PublishVideo();status.Text=settingsLoadFailed?settingsLoadWarning:!settings.DiskConfigured?"自动录像设置已恢复；请先在视频设置保存磁盘预警和停止GB，未配置不能开始录像。":enabled.IsChecked==true?"自动录像已启用，等待确认物流单号。":"自动录像已关闭，已保留本机设置。";
 }
 async Task PublishVideo(){if(web.CoreWebView2==null)return;var payload=JsonSerializer.Serialize(new{enabled=enabled.IsChecked==true,ready=rec.Ready,active=rec.Active,key=rec.Current?.BatchKey??"",tracking=rec.Current?.Tracking??"",test=rec.Current?.BatchKey.StartsWith("TEST-",StringComparison.Ordinal)==true,status=rec.Status,warning=rec.Warning,diskConfigured=settings.DiskConfigured,archiveHint,cleanupHint,settingsWarning=settingsLoadWarning,effectiveMode=rec.Current?.EffectiveMode??"",hotkeyStatus=hotkeys?.Status??"未注册"});await web.CoreWebView2.ExecuteScriptAsync("window.__nativeVideo="+payload+";window.dispatchEvent(new CustomEvent('native:video',{detail:window.__nativeVideo}));");}
 async Task Poll(){if(!backend.Alive){if(rec.Active)await rec.StopAsync("业务服务退出");return;}await eventsGate.WaitAsync();try{
  using var d=await backend.Get("/api/native/events?after="+backend.Cursor);failed=0;
  foreach(var e in d.RootElement.GetProperty("events").EnumerateArray()){
   backend.Cursor=e.GetProperty("id").GetInt64();var kind=e.GetProperty("kind").GetString();using var p=JsonDocument.Parse(e.GetProperty("payload").GetString()!);
   if(kind=="stop"){currentTracking="";currentKey="";await rec.StopAsync("结单/退出批次/切换模块");}
   else if(kind=="tracking"){
    currentTracking=p.RootElement.GetProperty("tracking_no").GetString()!;currentKey=p.RootElement.GetProperty("source").GetString()+":"+p.RootElement.GetProperty("session_id")+":"+p.RootElement.GetProperty("id");
    VideoContext context;
    if(p.RootElement.TryGetProperty("context",out var c))context=c.Deserialize<VideoContext>(ArchiveLayout.Json)!;
    else{using var lookup=await backend.Get("/api/native/context?key="+Uri.EscapeDataString(currentKey));context=lookup.RootElement.GetProperty("context").Deserialize<VideoContext>(ArchiveLayout.Json)!;}
    Library.Associate(context);
    if(enabled.IsChecked==true){if(UiTest.SettingsOnly)throw new InvalidOperationException("Settings-only acceptance forbids camera access");if(DateTimeOffset.Now-DateTimeOffset.Parse(e.GetProperty("at").GetString()!)>TimeSpan.FromSeconds(30)){await rec.StopAsync("物流事件过期，请重新扫描确认");status.Text="物流事件延迟超过30秒，未补录过去画面，请重新扫描确认";continue;}await rec.StartAsync(currentTracking,currentKey,context);}
   }
   else if(kind=="item"){
    var context=p.RootElement.GetProperty("context").Deserialize<VideoContext>(ArchiveLayout.Json)!;
    if(rec.Current?.BatchKey==context.Key){await rec.UpdateContextAsync(context);preview.ProductConfirmed();}
   }
   else if(kind=="association"){
    foreach(var c in p.RootElement.GetProperty("contexts").EnumerateArray()){
     var context=c.Deserialize<VideoContext>(ArchiveLayout.Json)!;Library.Associate(context);
     if(rec.Current?.BatchKey==context.Key)await rec.UpdateContextAsync(context);
    }
    archiveHint="后补采购关联已更新；旧录像原位保留，请停录后在录像索引预览并确认归档。";
   }
  }
 }catch{failed++;if(failed>=3&&rec.Active)await rec.StopAsync("业务事件通道失联，防止错绑物流");throw;}finally{eventsGate.Release();}}
 UIElement ExportPanel(){var p=new DockPanel{Margin=new Thickness(16),Background=Palette.Bg};var controls=new StackPanel();DockPanel.SetDock(controls,Dock.Top);p.Children.Add(controls);controls.Children.Add(new TextBlock{Text="组合模糊筛选｜时间格式 yyyy-MM-dd HH:mm:ss（本地时间），留空不限；旧6列模板在原导出页。",TextWrapping=TextWrapping.Wrap,Margin=new Thickness(4)});var f=new WrapPanel();controls.Children.Add(f);
 foreach(var x in new[]{("start","开始时间"),("end","结束时间"),("po","采购单包含"),("outer","外部单号包含"),("tracking","物流单号包含"),("sku","商品编码包含")}){var v=new StackPanel{Margin=new Thickness(4)};v.Children.Add(new TextBlock{Text=x.Item2});var t=new TextBox{Width=190,Padding=new Thickness(6)};filters[x.Item1]=t;v.Children.Add(t);f.Children.Add(v);}
 exportMode.Items.Add("逐次扫码/修改流水");exportMode.Items.Add("商品实收汇总");exportMode.SelectedIndex=0;f.Children.Add(exportMode);
 Add(f,"查询预览",LoadExport,Theme.Primary);Add(f,"按当前条件导出CSV",async()=>{await LoadExport();if(exportResult==null)return;var save=new Microsoft.Win32.SaveFileDialog{Filter="CSV|*.csv",FileName="收货精细导出_"+DateTime.Now.ToString("yyyyMMdd_HHmmss")+".csv"};if(save.ShowDialog(this)!=true)return;var sb=new StringBuilder();string Cell(JsonElement c){var s=c.ValueKind==JsonValueKind.Null?"":c.ToString();if(c.ValueKind==JsonValueKind.String && s.Length>0 && "=+-@\t\r".Contains(s[0]))s="'"+s;return "\""+s.Replace("\"","\"\"")+"\"";}var j=exportResult.Value;sb.AppendLine(string.Join(",",j.GetProperty("columns").EnumerateArray().Select(Cell)));foreach(var row in j.GetProperty("rows").EnumerateArray())sb.AppendLine(string.Join(",",row.EnumerateArray().Select(Cell)));await System.IO.File.WriteAllTextAsync(save.FileName,sb.ToString(),new UTF8Encoding(true));status.Text="已导出 "+j.GetProperty("count")+" 行："+save.FileName;});
 controls.Children.Add(new TextBlock{Text="时间按确认/修改时间筛选。汇总显示当前累计实收，不是时间范围内新增数量。流水保留删除与修改；旧数据没有的时间留空。",Margin=new Thickness(4,10,4,10),TextWrapping=TextWrapping.Wrap});p.Children.Add(exportGrid);return p;}
 async Task LoadExport(){string q="?mode="+(exportMode.SelectedIndex==0?"events":"summary");foreach(var f in filters){var value=f.Value.Text.Trim();if(value!=""&&f.Key is "start" or "end"){if(!DateTime.TryParseExact(value,"yyyy-MM-dd HH:mm:ss",null,System.Globalization.DateTimeStyles.None,out var dt))throw new Exception("时间格式应为 yyyy-MM-dd HH:mm:ss");value=new DateTimeOffset(dt).ToString("O");}q+="&"+f.Key+"="+Uri.EscapeDataString(value);}using var d=await backend.Get("/api/native/export"+q);exportResult=d.RootElement.Clone();var table=new DataTable();foreach(var c in d.RootElement.GetProperty("columns").EnumerateArray())table.Columns.Add(c.GetString()!);foreach(var r in d.RootElement.GetProperty("rows").EnumerateArray().Take(1000))table.Rows.Add(r.EnumerateArray().Select(c=>(object)c.ToString()).ToArray());exportGrid.ItemsSource=table.DefaultView;status.Text="命中 "+d.RootElement.GetProperty("count")+" 行，预览最多1000行；导出包含全部命中行。";}
 UIElement LibraryPanel()=>VideoLibraryPanel();
 async Task<bool> HasPending(bool includeSettings=true){if(web.CoreWebView2==null)return false;try{return await web.CoreWebView2.ExecuteScriptAsync("Boolean(document.querySelector('#pending-bar:not([hidden]),#pending:not([hidden]),#editor[open],.qty-saving,.qty-save-error,.qty-unsaved') || window.ReceivingApp?.isBusy() || window.ReceivingDesk?.blocked()"+(includeSettings?" || window.SettingsWorkspace?.hasUnsaved()":"")+")")=="true";}catch{return true;}}
 void SaveSettings(){if(settingsLoadFailed)return;settings.Camera=rec.Camera;settings.Directory=rec.DirectoryPath;settings.Engine=rec.Engine;settings.Resolution=rec.Resolution;settings.Left=preview.Left;settings.Top=preview.Top;settings.Width=preview.Width;settings.Height=preview.RestoreHeight;WriteSettings(settings);}
 async void OnClosing(object? sender,System.ComponentModel.CancelEventArgs e){if(closing)return;e.Cancel=true;if(await HasPending()){MessageBox.Show(this,"请先保存或取消待录入商品 / 设置草稿，等待保存完成后再退出。");return;}if(MessageBox.Show(this,"退出将结束并保存录像。请确认没有尚未保存的数量或正在提交的入库操作。是否退出？","安全退出",MessageBoxButton.YesNo,MessageBoxImage.Question)!=MessageBoxResult.Yes)return;await ShutdownSafely();}
 // Shared by the window close button and applyUpdate. beforeClose runs after recorder and backend have stopped cleanly.
 async Task<bool> ShutdownSafely(Action? beforeClose=null){closing=true;IsEnabled=false;timer.Stop();try{await eventsGate.WaitAsync();try{await rec.DisposeAsync();await backend.DisposeAsync();SaveSettings();}finally{eventsGate.Release();}beforeClose?.Invoke();hotkeys?.Dispose();sounds?.Dispose();preview.ExitAllowed=true;preview.Close();web.Dispose();Close();return true;}catch(Exception ex){closing=false;IsEnabled=true;MessageBox.Show(this,"退出未完成："+ex.Message);return false;}}
}
public static class Program {
 [STAThread]public static void Main(string[] args){
  if(args.Contains("--dependency-check")){var check=Dependencies.Check().GetAwaiter().GetResult();File.WriteAllText(args[Array.IndexOf(args,"--dependency-check")+1],JsonSerializer.Serialize(new{check.WebView,check.Video,check.Engine,check.Detail,check.Ready,BrowserFolder=AppPaths.BrowserFolder,AppPaths.Installed}));Environment.ExitCode=check.Ready?0:1;return;}
  // Standalone 录像查询 UI acceptance: no backend, camera or business window (run by CI).
  // Standalone 录像浮窗 V2 UI acceptance: synthetic states and frames, no camera/backend (run by CI).
  if(args.Contains("--float-ui-test")){Environment.ExitCode=FloatUiAcceptance.Run(args);return;}
  if(args.Contains("--library-ui-test")){Environment.ExitCode=LibraryUiAcceptance.Run(args);return;}
  if(args.Contains("--ui-self-test"))UiTest.Configure(args);
  if(args.Contains("--backend-self-test")){BackendAcceptance.Run(args).GetAwaiter().GetResult();return;}
  if(args.Contains("--self-test")){try{Acceptance.Run(args).GetAwaiter().GetResult();}catch(Exception e){System.IO.File.WriteAllText(Path.Combine(AppContext.BaseDirectory,"self-test-error.txt"),e.ToString());Environment.ExitCode=1;}return;}
  // Restore relaunch: wait (≤30s) for the previous instance to exit before taking the single-instance mutexes.
  var restartAt=Array.IndexOf(args,"--restart-after");
  if(restartAt>=0&&restartAt+1<args.Length&&int.TryParse(args[restartAt+1],out var previousPid)){try{using var previous=Process.GetProcessById(previousPid);previous.WaitForExit(30000);}catch(ArgumentException){}catch(InvalidOperationException){}}
  using var installedMutex=new Mutex(AppPaths.Installed,"Local\\ReceivingAssistantInstalled",out var installedFirst);if(AppPaths.Installed&&!installedFirst){MessageBox.Show("已安装的小助手正在运行，请勿重复启动。");return;}
  using var mutex=new Mutex(true,"Local\\ReceivingNativeTrial_"+Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(AppPaths.DataRoot)))[..16],out var first);if(!first){MessageBox.Show("此数据目录已有一个程序在运行，请勿重复启动。");return;}
  var app=new Application{ShutdownMode=ShutdownMode.OnMainWindowClose};app.DispatcherUnhandledException+=(_,e)=>{try{System.IO.File.AppendAllText(Path.Combine(AppPaths.DataRoot,"native-errors.log"),DateTimeOffset.Now+" "+e.Exception+"\n");}catch{}MessageBox.Show("发生异常，请先核实保存状态："+e.Exception.Message);e.Handled=true;};Theme.Apply();if(!UiTest.Active){app.ShutdownMode=ShutdownMode.OnExplicitShutdown;var setup=new DependencyWindow();setup.ShowDialog();if(!setup.Accepted){app.Shutdown();return;}}app.ShutdownMode=ShutdownMode.OnMainWindowClose;var main=new MainWindow();app.MainWindow=main;app.Run(main);
 }
}

// 主窗口外壳（方向 C）：白色细工具栏 + 状态行，不再重复网页侧栏的品牌标题。
// Shared with the library acceptance so CI can screenshot the shell without a backend.
public static class ShellChrome {
 public const string Brand="扫码入库",WindowTitle="扫码入库 · 聚水潭收货助手 4.1.1";
 public static (Border Top,WrapPanel Bar) Build(TextBlock status){
  var bar=new WrapPanel{VerticalAlignment=VerticalAlignment.Center};
  status.FontWeight=FontWeights.SemiBold;status.TextWrapping=TextWrapping.Wrap;status.Foreground=Palette.Text2;status.VerticalAlignment=VerticalAlignment.Center;
  var dot=new System.Windows.Shapes.Ellipse{Width=8,Height=8,Fill=Palette.Brand,Margin=new Thickness(0,0,8,0),VerticalAlignment=VerticalAlignment.Center};
  var line=new DockPanel{Margin=new Thickness(0,8,0,0)};DockPanel.SetDock(dot,Dock.Left);line.Children.Add(dot);line.Children.Add(status);
  var stateBox=new Border{Background=Palette.Surface2,BorderBrush=Palette.Line,BorderThickness=new Thickness(1),CornerRadius=new CornerRadius(Palette.RadiusSm+2),Padding=new Thickness(12,7,12,7),Child=line,Margin=new Thickness(0,8,0,0)};
  line.Margin=new Thickness(0);
  var stack=new StackPanel();stack.Children.Add(bar);stack.Children.Add(stateBox);
  var top=new Border{Background=Palette.Surface,BorderBrush=Palette.Line,BorderThickness=new Thickness(0,0,0,1),Padding=new Thickness(14,10,14,10),Child=stack};
  return (top,bar);
 }
 public static Button Action(string text,string level=Theme.Secondary)=>new Button{Content=text,Margin=new Thickness(0,0,8,0),FontFamily=Palette.Font,FontSize=13}.Level(level);
}
