using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Microsoft.Web.WebView2.Core;
namespace Receiving.Native;
public static class UiTest {
 public static bool Active,SettingsOnly,SettingsReload,Hidden,HttpOnly;public static string Url="",Token="",Output="",Camera="";public static int Pid,Sid;
 static string Required(string[] args,string key){var i=Array.IndexOf(args,key);if(i<0||i+1>=args.Length||string.IsNullOrWhiteSpace(args[i+1])||args[i+1].StartsWith("--"))throw new ArgumentException("Missing "+key);return args[i+1];}
 public static void Configure(string[] args){
  HttpOnly=args.Contains("--http-ui-only");if(args.Contains("--library-only"))throw new ArgumentException("录像查询验收已改为独立模式：Receiving.Native.exe --library-ui-test --output <新目录> [--ffmpeg <ffmpeg.exe>]");SettingsOnly=args.Contains("--settings-only")||HttpOnly;SettingsReload=args.Contains("--settings-reload");Hidden=args.Contains("--test-hidden");
  // Old launchers must not silently acquire a camera or reuse their old output.
  if(!SettingsOnly){if(!args.Contains("--allow-camera"))throw new ArgumentException("USB acceptance requires --allow-camera and --camera; use run_usb_ui.py");Camera=Required(args,"--camera");}
  Output=Path.GetFullPath(Required(args,"--output"));using var doc=JsonDocument.Parse(File.ReadAllText(Required(args,"--fixture")));var j=doc.RootElement;
  Url=j.GetProperty("base").GetString()!;if(!Uri.TryCreate(Url,UriKind.Absolute,out var uri)||uri.Scheme!="http"||!uri.IsLoopback)throw new ArgumentException("Acceptance fixture must be loopback HTTP");
  Token=j.GetProperty("token").GetString()!;Pid=j.GetProperty("pid").GetInt32();Sid=j.GetProperty("sid").GetInt32();Directory.CreateDirectory(Output);Active=true;
 }
}
public sealed partial class MainWindow {
 static IEnumerable<T> Children<T>(DependencyObject root)where T:DependencyObject{for(int i=0;i<VisualTreeHelper.GetChildrenCount(root);i++){var c=VisualTreeHelper.GetChild(root,i);if(c is T t)yield return t;foreach(var d in Children<T>(c))yield return d;}}
 async Task Until(Func<bool> check,int seconds=25){for(int i=0;i<seconds*10;i++){if(check())return;await Task.Delay(100);}throw new Exception("等待条件超时");}
 async Task<JsonElement> Js(string code){await web.CoreWebView2.ExecuteScriptAsync("window.__testDone=false;(async()=>{try{window.__testResult=await ("+code+");}catch(e){window.__testResult={error:e.message};}finally{window.__testDone=true;}})();");for(int i=0;i<250;i++){if(await web.CoreWebView2.ExecuteScriptAsync("window.__testDone")=="true"){using var d=JsonDocument.Parse(await web.CoreWebView2.ExecuteScriptAsync("window.__testResult"));return d.RootElement.Clone();}await Task.Delay(100);}throw new Exception("WebView测试请求超时");}
 async Task<JsonElement> Post(string path,object body)=>await Js("fetch("+JsonSerializer.Serialize(path)+",{method:'POST',headers:{'Content-Type':'application/json'},body:"+JsonSerializer.Serialize(JsonSerializer.Serialize(body))+"}).then(r=>r.json())");
 async Task WaitJs(string expression,int seconds=30){for(int i=0;i<seconds*10;i++){if(await web.CoreWebView2.ExecuteScriptAsync(expression)=="true")return;await Task.Delay(100);}throw new Exception("网页条件超时："+expression);}
 async Task CaptureWeb(string name){using var image=File.Create(Path.Combine(UiTest.Output,name));await web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);}
 async Task AcceptanceUi(){var checks=new List<string>();Exception? error=null;try{
  await Task.Delay(1800);if(await web.CoreWebView2.ExecuteScriptAsync("typeof window.fetch")!="\"function\"")throw new Exception("WebView加载失败");checks.Add("WebView2页面加载");
  await WaitJs("Boolean(window.SettingsWorkspace && window.NativeDesk?.available && window.receivingSettingsLoaded)");
  if(!CommandSourceAllowed(backend.Url+"/index.html")||CommandSourceAllowed("https://example.com/index.html")||CommandSourceAllowed(backend.Url+"/api/state"))throw new Exception("桌面桥接来源白名单异常");checks.Add("桌面桥接仅允许本地业务页来源");
  await web.CoreWebView2.ExecuteScriptAsync("ReceivingApp.navigate('settings');SettingsWorkspace.show('video');");
  if(enabled.IsChecked!=true||rec.Active||preview.IsVisible)throw new Exception("验收启动应默认启用开关，但未扫描不得录像；测试不自动显示浮窗");
  if(File.Exists(Path.Combine(root,"native-settings.json")))throw new Exception("USB验收必须使用全新隔离配置目录");
  await WaitJs("!document.getElementById('video-save').disabled");
  await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-enabled').click()");
  await Until(()=>enabled.IsChecked!=true);await WaitJs("!document.getElementById('video-enabled').disabled");
  await SetVideoFields(new{camera=UiTest.Camera,warningGb=4,stopGb=2,overlayTime=true,overlayTracking=true,overlayPurchase=true,overlayOuter=true,overlaySku=true,overlayQuantity=true});
  await SaveVideoUi(true);
  if(settings.WarningGb!=4||settings.StopGb!=2||settings.Camera!=UiTest.Camera)throw new Exception("USB验收隔离配置回读不符");
  checks.Add("仅在全新验收目录通过真实设置页保存指定相机、测试GB阈值和六项叠字");
  await WaitJs("!document.getElementById('video-enabled').disabled");
  await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-enabled').click()");
  await Until(()=>enabled.IsChecked==true);await WaitJs("!document.getElementById('video-enabled').disabled");checks.Add("统一设置页通过受限桥接启用自动录像");
  await web.CoreWebView2.ExecuteScriptAsync("ReceivingApp.navigate('home')");
  await PublishVideo();if(UiTest.Hidden){preview.Opacity=0;preview.ShowInTaskbar=false;}preview.Show();preview.Width=660;preview.Height=480;preview.Left=160;preview.Top=120;await Task.Delay(300);if(preview.ActualWidth<650)throw new Exception("浮窗缩放失败");checks.Add("浮窗显示、位置和尺寸调整");preview.Hide();
  var a=await Post("/api/tracking/scan",new{sid=UiTest.Sid,tracking_no="UI-TEST-A"});if(!a.GetProperty("ok").GetBoolean())throw new Exception(a.ToString());var bid=a.GetProperty("batch").GetProperty("id").GetInt32();
  await Until(()=>rec.Ready&&rec.Current?.Tracking=="UI-TEST-A");var id=rec.Current!.Id;if(preview.IsVisible)throw new Exception("物流启动录像不应自动弹出浮窗");checks.Add("WebView→后端确认物流→轮询→USB录像启动，不自动弹出浮窗");preview.Show();await Task.Delay(3000);
  var item=await Post("/api/item/scan",new{sid=UiTest.Sid,batch_id=bid,code="A",qty=2});if(!item.GetProperty("ok").GetBoolean())throw new Exception(item.ToString());await Task.Delay(600);if(rec.Current!.Id!=id)throw new Exception("商品扫码错误切段");checks.Add("商品扫码落库但不切录像");
  preview.Close();if(preview.IsVisible||!rec.Active)throw new Exception("浮窗隐藏影响录像");preview.Show();checks.Add("浮窗关闭只隐藏，录像继续");
  Children<Button>(preview).First(x=>x.Name=="FloatStop").RaiseEvent(new RoutedEventArgs(Button.ClickEvent));await Until(()=>!rec.Active);if(rec.Current!.State!="saved")throw new Exception("浮窗停止未保存");checks.Add("浮窗结束按钮正常收尾文件");await Task.Delay(500);
  var blocked=await Post("/api/item/scan",new{sid=UiTest.Sid,batch_id=bid,code="A",qty=2});if(!blocked.TryGetProperty("error",out _))throw new Exception("录像停止后扫码未阻止");checks.Add("启用录像但未录制时商品扫码阻止");
  var b=await Post("/api/tracking/scan",new{sid=UiTest.Sid,tracking_no="UI-TEST-B"});await Until(()=>rec.Ready&&rec.Current?.Tracking=="UI-TEST-B");await Task.Delay(3000);
  await web.CoreWebView2.ExecuteScriptAsync("window.dispatchEvent(new CustomEvent('native-receipt',{detail:{kind:'tracking',key:'普通:"+UiTest.Sid+":"+bid+"'}}))");await Until(()=>rec.Ready&&rec.Current?.Tracking=="UI-TEST-A");if(rec.Current!.Id==id)throw new Exception("回扫覆盖旧段");checks.Add("A→B→A队列顺序及不覆盖");await Task.Delay(2500);
  await LoadExport();if(exportResult!.Value.GetProperty("count").GetInt32()!=1)throw new Exception("导出流水数量不符");checks.Add("原生精细导出预览与扫码流水");
  using(var image=File.Create(Path.Combine(UiTest.Output,"business-webview.png")))await web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
  await web.CoreWebView2.ExecuteScriptAsync("ReceivingApp.navigate('settings');SettingsWorkspace.show('video');");
  await WaitJs("document.getElementById('video-save').disabled && document.getElementById('video-test-start').disabled");
  var guard=await Js("NativeDesk.call('testStop').then(()=>({blocked:false}),e=>({blocked:true,message:e.message}))");if(!guard.GetProperty("blocked").GetBoolean()||!rec.Active)throw new Exception("测试停止误停真实收货录像");
  checks.Add("录制中设置/测试按钮锁定，测试停止不能终止实际收货");
  preview.Close();await CaptureWeb("native-settings-ui.png");checks.Add("整合设置页实际WebView渲染，无独立旧设置页");
  await rec.StopAsync("UI验收结束");if(rec.Current!.State!="saved")throw new Exception("末段收尾失败");checks.Add("UI验收末段文件保存");
  // Regression: these buttons run outside the timer. Health ticks must not queue
  // a stale stop while StartAsync is waiting for a USB camera's first frame.
  await WaitJs("!document.getElementById('video-save').disabled");
  await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-save').click()");
  await WaitJs("!SettingsWorkspace.hasUnsaved() && document.getElementById('video-message').textContent.includes('已保存')");checks.Add("统一视频设置通过桥接保存并回读，使用隔离目录");
  await RejectBridge("testStart",new{},"关闭扫码自动录像");if(rec.Active)throw new Exception("自动录像开启时不得启动独立测试");
  await WaitJs("!document.getElementById('video-enabled').disabled");await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-enabled').click()");await Until(()=>enabled.IsChecked!=true);
  await WaitJs("!document.getElementById('video-test-start').disabled");
  await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-test-start').click()");
  await Until(()=>rec.Ready&&rec.Current?.Tracking=="TEST-摄像头验收");
  var manualId=rec.Current!.Id;var started=DateTimeOffset.Now;
  while(DateTimeOffset.Now-started<TimeSpan.FromSeconds(20)){await Task.Delay(500);if(!rec.Ready||rec.Current?.Id!=manualId||rec.FrameAge>3)throw new Exception("30秒测试到时前在健康检查并发下误停");}
  await Until(()=>!rec.Active,35);
  if(rec.Current?.Id!=manualId||rec.Current.State!="saved"||rec.Current.Reason!="30秒测试完成并验证文件"||DateTimeOffset.Now-started<TimeSpan.FromSeconds(27))throw new Exception("测试未按30秒到时自动收尾");
  if(preview.IsVisible)throw new Exception("独立测试不应自动弹出浮窗");checks.Add("真实USB测试连续画面正常，约30秒自动收尾并验证，不自动弹浮窗");
  await WaitJs("!document.getElementById('video-test-stop').disabled");await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-test-stop').click()");
  await Until(()=>!rec.Active);if(rec.Current!.State!="saved")throw new Exception("手动测试未正常保存");
  checks.Add("30秒自动收尾后仍可点击结束测试并验证，结果保持已保存");
  await WaitJs("!document.getElementById('video-enabled').disabled");await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-enabled').click()");await Until(()=>enabled.IsChecked==true);
  await WaitJs("!document.getElementById('video-enabled').disabled");
  await WaitJs("!SettingsWorkspace.hasUnsaved()");await web.CoreWebView2.ExecuteScriptAsync("document.querySelector('[data-settings-pane=video] [data-native-action=continue]').click()");
  await Until(()=>rec.Ready&&rec.Current?.Tracking==currentTracking);await Task.Delay(10000);
  if(!rec.Ready||rec.FrameAge>3)throw new Exception("继续录像在健康检查并发下误停");
  await rec.StopAsync("继续录像验收结束");if(rec.Current!.State!="saved")throw new Exception("继续录像未正常保存");checks.Add("实际点击继续本单新一段，10秒录像及收尾通过");
  if(await web.CoreWebView2.ExecuteScriptAsync("Boolean(document.getElementById('video-diagnostics'))")!="true"||!rec.Diagnostics.Contains("版本：4.0.0"))throw new Exception("诊断入口缺失");checks.Add("诊断入口及录像状态可用（不改用户剪贴板）");
  await web.CoreWebView2.ExecuteScriptAsync("document.querySelector('[data-settings-pane=video] [data-native-action=continue]').click()");await Until(()=>rec.Ready);await Task.Delay(2500);
  var paused=await Post("/api/session/pause-report",new{sid=UiTest.Sid});if(!paused.GetProperty("ok").GetBoolean())throw new Exception(paused.ToString());await Until(()=>!rec.Active);if(rec.Current?.State!="saved")throw new Exception("暂存未收尾录像");checks.Add("成功暂存对单停止录像并保存，不继续占用摄像头");
  var ex=await Js("NativeDesk.call('export')");if(exportWindow==null||!exportWindow.IsVisible)throw new Exception("统一导出入口失败");exportWindow.Close();checks.Add("统一明细导出入口打开秒级导出窗口");
  var lib=await Js("NativeDesk.call('library')");if(libraryWindow==null||!libraryWindow.IsVisible)throw new Exception("录像索引入口失败");libraryWindow.Close();checks.Add("统一录像索引入口正常");

 }catch(Exception ex){error=ex;try{await CaptureWeb("ui-failure.png");await File.WriteAllTextAsync(Path.Combine(UiTest.Output,"ui-failure-state.json"),await web.CoreWebView2.ExecuteScriptAsync("({href:location.href,settings:!!window.SettingsWorkspace,native:window.NativeDesk?.available,loaded:window.receivingSettingsLoaded,errors:window.__uiErrors,fetches:window.__uiFetch,body:document.body.innerText.slice(0,7000)})"));}catch{}}finally{
  timer.Stop();await rec.DisposeAsync();hotkeys?.Dispose();sounds?.Dispose();preview.ExitAllowed=true;preview.Close();web.Dispose();closing=true;
  await File.WriteAllTextAsync(Path.Combine(UiTest.Output,"ui-acceptance.json"),JsonSerializer.Serialize(new{ok=error==null,checks,error=error?.ToString()},Recorder.JsonOptions));Environment.ExitCode=error==null?0:1;Close();
 }}
}
