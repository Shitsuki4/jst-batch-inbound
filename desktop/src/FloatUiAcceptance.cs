using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shell;
namespace Receiving.Native;

// Standalone 录像浮窗 V2 acceptance (CI): Receiving.Native.exe --float-ui-test --output <fresh dir>
// Real PreviewWindow on a real (idle) Recorder; recorder states are substituted through FloatView, frames are synthetic.
// No camera, backend or business window. Result: <output>/float-ui-result.json + one PNG per state.
public static class FloatUiAcceptance {
 [DllImport("user32.dll",EntryPoint="GetWindowLongW")]static extern int GetStyle(IntPtr h,int n);
 public static int Run(string[] args){
  string Arg(string key){var i=Array.IndexOf(args,key);return i>=0&&i+1<args.Length&&!args[i+1].StartsWith("--")?args[i+1]:"";}
  var requested=Arg("--output");if(requested==""){Console.Error.WriteLine("--output is required");return 2;}
  var output=Path.GetFullPath(requested);
  if(Directory.Exists(output)&&Directory.EnumerateFileSystemEntries(output).Any()){Console.Error.WriteLine("--output must be a new or empty directory");return 2;}
  Directory.CreateDirectory(output);
  var code=1;var app=new Application{ShutdownMode=ShutdownMode.OnExplicitShutdown};Theme.Apply();
  var checks=new List<string>();var shots=new List<string>();var unhandled=new List<string>();var invoked=new List<string>();Exception? error=null;
  app.DispatcherUnhandledException+=(_,e)=>{lock(unhandled)unhandled.Add(e.Exception.ToString());e.Handled=true;};
  app.Startup+=async(_,_)=>{
   PreviewWindow? w=null;var failDiagnostics=false;
   try{
    void Check(bool ok,string what){if(!ok)throw new Exception("浮窗验收失败："+what);checks.Add(what);}
    async Task Settle(int ms=250){w!.Tick();await Task.Delay(ms);w.Tick();w.UpdateLayout();}
    void Click(Button b)=>b.RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
    T P<T>(string name)where T:FrameworkElement=>w!.Part<T>(name);
    bool Shown(string name)=>w!.Part<FrameworkElement>(name).IsVisible;
    string Text(string name)=>P<TextBlock>(name).Text;
    void Shot(string name){
     var el=(FrameworkElement)w!.Content;var dpi=VisualTreeHelper.GetDpi(el);
     var bmp=new RenderTargetBitmap(Math.Max(1,(int)Math.Ceiling(el.ActualWidth*dpi.DpiScaleX)),Math.Max(1,(int)Math.Ceiling(el.ActualHeight*dpi.DpiScaleY)),dpi.PixelsPerInchX,dpi.PixelsPerInchY,PixelFormats.Pbgra32);
     bmp.Render(el);var png=new PngBitmapEncoder();png.Frames.Add(BitmapFrame.Create(bmp));using(var f=File.Create(Path.Combine(output,name)))png.Save(f);shots.Add(name);
    }
    foreach(var icon in PreviewWindow.IconNames)PreviewWindow.Glyph(icon,16);checks.Add("全部线性图标几何可解析");
    var rec=new Recorder();
    w=new PreviewWindow(rec,a=>{invoked.Add(a);if(a=="diagnostics"&&failDiagnostics)throw new InvalidOperationException("验收：模拟操作失败");return Task.CompletedTask;}){Left=80,Top=80};
    w.Configure(new UserSettings{PhotoEnabled=true,NotesEnabled=true,ProtectionEnabled=true,ZoomEnabled=true,HotkeysEnabled=true,HotkeyStop="Ctrl+Alt+S",HotkeyContinue="Ctrl+Alt+C",HotkeyPreview="Ctrl+Alt+V"});
    Check(w.WindowStyle==WindowStyle.None&&WindowChrome.GetWindowChrome(w)!=null&&w.ResizeMode==ResizeMode.CanResize,"无系统标题栏，WindowChrome 可拖动/缩放");
    Check(w.Topmost&&!w.ShowActivated&&w.MinWidth==360&&w.Width==432&&w.Height==388,"默认 432×388、置顶、不激活、最小宽 360");
    w.Show();await Task.Delay(500);
    var style=GetStyle(new WindowInteropHelper(w).Handle,-20);Check((style&0x08000000)!=0,"保留 WS_EX_NOACTIVATE，不抢扫码焦点");
    // synthetic 480×270 BGR frame: warehouse-ish gradient with a box
    var frame=new byte[480*270*3];for(int y=0;y<270;y++)for(int x=0;x<480;x++){var i=(y*480+x)*3;var box=x>170&&x<330&&y>130&&y<230;frame[i]=(byte)(box?60:90+y/4);frame[i+1]=(byte)(box?120:85+y/5);frame[i+2]=(byte)(box?190:80+x/8);}
    w.TestFrame(frame);
    var ctx=new VideoContext{Tracking="JDVA28834455102",Purchases=new(){new("CG20261020-0187","WB-3391-A")}};
    var live=new VideoSegment{Tracking="JDVA28834455102",State="recording",Context=ctx,Protected=true,FirstFrameAt=DateTimeOffset.Now.AddSeconds(-767)};
    var recording=new FloatView(true,true,0.2,"正在录制 · 1280x720 / 30fps","",live,TimeSpan.FromSeconds(767));
    // 1 录像中
    w.TestView=()=>recording;await Settle();
    Check(Text("FloatState")=="录像中"&&Text("FloatTime")=="00:12:47"&&Text("FloatChipText")=="画面正常","顶栏：录像中 + 时长 + 画面正常");
    Check(Text("FloatTracking")=="JDVA28834455102"&&Text("FloatPurchase").Contains("CG20261020-0187")&&Text("FloatPurchase").Contains("外部 WB-3391-A"),"信息区显示物流、采购单与外部单号");
    Check(Shown("FloatStop")&&!Shown("FloatContinue")&&P<Button>("FloatStop").IsEnabled,"录像中主操作为结束本单录像");
    Check(Shown("FloatPhoto")&&P<Button>("FloatPhoto").IsEnabled&&Shown("FloatNote")&&Shown("FloatProtect"),"拍照 / 备注 / 保护直达");
    Check(((P<Button>("FloatProtect").Content as StackPanel)?.Children.OfType<TextBlock>().FirstOrDefault()?.Text)=="已保护","售后保护状态可见（已保护）");
    Check(!Shown("FloatCenter")&&!Shown("FloatBanner")&&Shown("FloatViewport"),"录像中无遮罩、无横幅");
    var vp=P<Grid>("FloatViewport");Check(vp.ActualWidth>400&&vp.ActualHeight>200,"实时画面区域 16:9 级别尺寸");
    Shot("float-1-recording.png");
    w.ProductConfirmed();w.ShowToast("已保存举证照片 094218_a3f9c1.jpg");await Settle();
    Check(Shown("FloatZoom")&&Shown("FloatToast"),"确认商品自动放大角标 + 拍照成功提示可见");
    Shot("float-2-zoom-toast.png");
    // 2 未录像 / 已保存
    w.ClearTransient();
    var saved=new VideoSegment{Tracking="JDVA28834455102",State="saved",Context=ctx,Duration=1085};
    w.TestView=()=>new FloatView(false,false,double.PositiveInfinity,"已保存；未录制","",saved,null);await Settle();
    Check(Text("FloatState")=="未录像"&&Text("FloatChipText")=="上段已保存 00:18:05"&&Text("FloatTrackingNote")=="上一段","未录像：上段已保存 + 上一段单号");
    Check(Shown("FloatContinue")&&!Shown("FloatStop")&&!P<Button>("FloatPhoto").IsEnabled,"未录像主操作为继续本单新一段，拍照禁用");
    Check(Shown("FloatCenter")&&Text("FloatCenterMessage").Contains("扫描下一个物流单")&&!Shown("FloatCenterActions"),"未录像画面提示扫描下一单");
    Shot("float-3-idle.png");
    Click(P<Button>("FloatContinue"));await Task.Delay(150);Check(invoked.Contains("continue"),"继续本单新一段走 continue 命令");
    // 3 磁盘不足（仍在录）
    w.TestView=()=>recording with{Warning="磁盘剩余 8.60 GB，低于预警阈值，请手动整理（不会自动删除）"};await Settle();
    Check(Text("FloatChipText")=="磁盘不足"&&Shown("FloatBanner")&&Text("FloatBannerText").Contains("8.60 GB"),"磁盘预警：胶囊 + 琥珀横幅");
    Shot("float-4-disk.png");
    Click(P<Button>("FloatBannerAction"));await Task.Delay(150);Check(invoked.Contains("library"),"横幅「去整理」打开录像检索");
    // 4 异常 · 摄像头断开
    var broken=new VideoSegment{Tracking="JDVA28834455102",State="interrupted",Context=ctx};
    w.TestView=()=>new FloatView(false,false,double.PositiveInfinity,"录像异常：连续8秒无新画面，请检查相机并复制录像诊断","",broken,null);await Settle();
    Check(Text("FloatState")=="录像已停止"&&Text("FloatChipText")=="摄像头断开"&&Shown("FloatCenter")&&Shown("FloatCenterActions"),"异常：红色状态 + 画面内诊断/继续按钮");
    Shot("float-5-error.png");
    Click(P<Button>("FloatCenterDiagnostics"));await Task.Delay(150);Check(invoked.Contains("diagnostics"),"异常卡片直接复制录像诊断");
    // 5 操作失败：浮窗内横幅，不弹模态框（若弹 MessageBox，这里会卡住直到 CI 超时）
    failDiagnostics=true;Click(P<Button>("FloatCenterDiagnostics"));await Settle();
    Check(Shown("FloatBanner")&&Text("FloatBannerText").Contains("模拟操作失败"),"操作失败显示在浮窗横幅，不弹 MessageBox");
    Shot("float-6-action-error.png");failDiagnostics=false;
    // 6 更多菜单
    w.ClearTransient();
    w.TestView=()=>recording;await Settle();
    var expected=new[]{"continue","stop","photo","note","protect","zoom","collapse","pin","hide","directory","library","diagnostics"};
    Check(expected.All(w.MenuItems.ContainsKey),"「⋯」菜单保留全部现有项（+结束本单录像）");
    Click(P<Button>("FloatMore"));await Task.Delay(300);var menuOpen=w.MenuItems["pin"].Parent is ContextMenu m&&m.IsOpen;
    Check(menuOpen&&w.MenuItems["pin"].IsChecked&&w.MenuItems["protect"].IsChecked&&w.MenuItems["stop"].InputGestureText=="Ctrl+Alt+S","菜单打开，置顶/保护勾选与快捷键提示正确");
    ((ContextMenu)w.MenuItems["pin"].Parent).IsOpen=false;await Task.Delay(150);
    // 7 置顶 / 收起 / 缩放 / 隐藏
    Click(P<Button>("FloatPin"));Check(!w.Topmost,"顶栏取消置顶");Click(P<Button>("FloatPin"));Check(w.Topmost,"顶栏恢复置顶");
    Click(P<Button>("FloatCollapse"));await Settle(400);
    Check(w.Compact&&!Shown("FloatViewport")&&w.ActualHeight<200&&Math.Abs(w.RestoreHeight-388)<1,"收起画面：只留状态/单号/工具栏，保存尺寸仍为展开高度");
    Shot("float-7-collapsed.png");
    Click(P<Button>("FloatCollapse"));await Settle(400);Check(!w.Compact&&Shown("FloatViewport")&&Math.Abs(w.ActualHeight-388)<2,"展开恢复原高度");
    w.Width=660;w.Height=480;await Settle(300);Check(w.ActualWidth>=650&&P<Grid>("FloatViewport").ActualWidth>=640,"缩放后画面随宽度放大");
    w.Width=432;w.Height=388;await Settle(200);
    Click(P<Button>("FloatHide"));await Task.Delay(150);Check(!w.IsVisible,"隐藏按钮隐藏浮窗");w.Show();await Task.Delay(200);
    w.Close();await Task.Delay(150);Check(!w.IsVisible,"关闭只隐藏（未允许退出）");w.Show();await Task.Delay(200);
    // 8 设置关闭拍照/备注/保护
    w.Configure(new UserSettings{PhotoEnabled=false,NotesEnabled=false,ProtectionEnabled=false,ZoomEnabled=false});await Settle();
    Check(!Shown("FloatPhoto")&&!Shown("FloatNote")&&!Shown("FloatProtect")&&!Shown("FloatZoom")&&w.MenuItems["photo"].Visibility==Visibility.Collapsed,"设置关闭时隐藏拍照/备注/保护/放大");
    w.TestView=null;await Settle();Check(Text("FloatState")=="未录像"&&Text("FloatTracking")=="未选择","真实空闲 Recorder：未录像 / 未选择");
    Shot("float-8-real-idle.png");
    if(unhandled.Count>0)throw new Exception("UI 未处理异常："+unhandled[0]);
   }catch(Exception ex){error=ex;try{if(w!=null&&w.IsVisible)
     {var el=(FrameworkElement)w.Content;var bmp=new RenderTargetBitmap(Math.Max(1,(int)el.ActualWidth),Math.Max(1,(int)el.ActualHeight),96,96,PixelFormats.Pbgra32);bmp.Render(el);var png=new PngBitmapEncoder();png.Frames.Add(BitmapFrame.Create(bmp));using var f=File.Create(Path.Combine(output,"float-failure.png"));png.Save(f);}}catch{}}
   finally{
    try{if(w!=null){w.ExitAllowed=true;w.Close();}}catch{}
    code=error==null&&unhandled.Count==0?0:1;
    File.WriteAllText(Path.Combine(output,"float-ui-result.json"),JsonSerializer.Serialize(new{ok=code==0,checks,shots,invoked,unhandled,error=error?.ToString()},new JsonSerializerOptions{WriteIndented=true,Encoder=System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping}));
    app.Shutdown(code);
   }
  };
  app.Run();return code;
 }
}
