using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Microsoft.Web.WebView2.Core;
namespace Receiving.Native;

// Shared helpers still used by the HTTP/UI smoke acceptance (HttpUiAcceptance.cs).
public sealed partial class MainWindow {
 delegate bool LibraryEnumWindow(IntPtr hwnd,IntPtr arg);
 [DllImport("user32.dll")]static extern bool EnumWindows(LibraryEnumWindow callback,IntPtr arg);
 [DllImport("user32.dll")]static extern bool EnumChildWindows(IntPtr parent,LibraryEnumWindow callback,IntPtr arg);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)]static extern int GetWindowText(IntPtr hwnd,StringBuilder text,int length);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)]static extern int GetClassName(IntPtr hwnd,StringBuilder text,int length);
 [DllImport("user32.dll")]static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);
 [DllImport("user32.dll")]static extern IntPtr GetDlgItem(IntPtr hwnd,int id);
 [DllImport("user32.dll")]static extern IntPtr SendMessage(IntPtr hwnd,uint msg,IntPtr w,IntPtr l);
 static string LibraryText(IntPtr hwnd){var text=new StringBuilder(8192);GetWindowText(hwnd,text,text.Capacity);return text.ToString();}
 static string LibraryHash(string file)=>Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(file)));
 static void InvokeLibraryButton(Button b)=>((IInvokeProvider)new ButtonAutomationPeer(b).GetPattern(PatternInterface.Invoke)).Invoke();
 VideoSegment SeedLibrary(string name,VideoContext context,int age,bool protect=false,string state="saved"){
  var at=DateTimeOffset.Now.AddDays(-age);context=context.Copy();context.Tracking=name;
  var s=ArchiveLayout.Reserve(rec.DirectoryPath,context,context.Key,at);
  File.Copy(Path.Combine(root,"synthetic.mp4"),s.File,false);File.WriteAllText(s.File+".log","ISOLATED LIBRARY UI FIXTURE");
  var photo=Path.GetFileName(s.File)+"_fixture.png";File.Copy(Path.Combine(root,"synthetic.png"),Path.Combine(Path.GetDirectoryName(s.File)!,photo),false);
  s.State=state;s.FirstFrameAt=at;s.EndAt=at.AddSeconds(1);s.ExitCode=state=="saved"?0:1;s.Duration=1;s.Protected=protect;s.Photos.Add(photo);ArchiveLayout.Write(rec.DirectoryPath,s);return s;
 }
}

// Standalone 录像查询 acceptance (CI): Receiving.Native.exe --library-ui-test --output <fresh dir> [--ffmpeg <ffmpeg.exe>]
// Real VideoLibraryView, real VideoLibrary store on freshly generated fixture media, real WPF dialogs.
// No backend, no camera, no business window. Result: <output>/library-ui-result.json (+ screenshots).
public static class LibraryUiAcceptance {
 public static int Run(string[] args) {
  string Arg(string key){var i=Array.IndexOf(args,key);return i>=0&&i+1<args.Length&&!args[i+1].StartsWith("--")?args[i+1]:"";}
  var requested=Arg("--output");if(requested==""){Console.Error.WriteLine("--output is required");return 2;}
  var output=Path.GetFullPath(requested);
  if(Directory.Exists(output)&&Directory.EnumerateFileSystemEntries(output).Any()){Console.Error.WriteLine("--output must be a new or empty directory");return 2;}
  Directory.CreateDirectory(output);
  var ffmpeg=Arg("--ffmpeg");if(ffmpeg=="")ffmpeg=Dependencies.Resolve("ffmpeg.exe");
  var code=1;var app=new Application{ShutdownMode=ShutdownMode.OnExplicitShutdown};Theme.Apply();
  var runner=new LibraryAcceptanceRunner(output,ffmpeg);
  app.DispatcherUnhandledException+=(_,e)=>{runner.Unhandled(e.Exception);e.Handled=true;};
  app.Startup+=async(_,_)=>{code=await runner.Execute();app.Shutdown(code);};
  app.Run();return code;
 }
}

sealed class LibraryAcceptanceRunner : IVideoLibraryHost {
 readonly string output,ffmpeg,recordings;readonly VideoLibrary store;
 readonly List<string> checks=new();readonly Dictionary<string,object?> soft=new();readonly List<object> dialogs=new();readonly List<string> unhandled=new();
 string? activeId;bool recording;Task<CoreWebView2Environment>? env;Window window=null!;VideoLibraryView view=null!;
 VideoSegment today1=null!;int associationRefreshes,afterChanges,afterDeletes;
 public LibraryAcceptanceRunner(string output,string ffmpeg){this.output=output;this.ffmpeg=ffmpeg;recordings=Path.Combine(output,"recordings");store=new VideoLibrary(recordings);}
 public void Unhandled(Exception ex){lock(unhandled)unhandled.Add(ex.ToString());}
 // ---- host
 public VideoLibrary Store=>store;public string? ActiveVideoId=>activeId;public bool Recording=>recording;
 public bool NotesEnabled=>true;public bool ProtectionEnabled=>true;public int CleanupDays=>180;
 public string Ffmpeg=>ffmpeg;public string PlayerFolder=>Path.Combine(output,"library-player");
 public Task<CoreWebView2Environment> WebEnvironment()=>env??=CoreWebView2Environment.CreateAsync(AppPaths.BrowserFolder,Path.Combine(output,"webview-profile"));
 public Task<int> RefreshAssociations(IReadOnlyList<LibraryItem> items){associationRefreshes++;return Task.FromResult(0);}
 public Task RefreshContext(LibraryItem item){
  if(item.Segment.Context.Source=="无采购单"){var c=item.Segment.Context.Copy();c.Purchases=new(){new("PO-ARCH","EXT-ARCH")};store.Associate(c);}
  return Task.CompletedTask;
 }
 // 快递单登记 KD-ONLY-001 没有录像；LIB-TODAY-A 的登记有录像（应被录像列表覆盖，不重复列出）
 public Task<IReadOnlyList<RegistrationHit>> FindRegistrations(string query)=>Task.FromResult<IReadOnlyList<RegistrationHit>>(new[]{
  new RegistrationHit("快递单:31:7","快递单","快递单登记",31,7,"KD-ONLY-001","KD20261029-0001","","到付件","2026-10-29 09:15:00","closed",0),
  new RegistrationHit(today1.Context.Source+":"+today1.Context.SessionId+":"+today1.Context.BatchId,today1.Context.Source,"采购单收货",today1.Context.SessionId,today1.Context.BatchId,today1.Tracking,"","PO-LIB-1","","","closed",1)}
  .Where(r=>r.Tracking.Contains(query,StringComparison.OrdinalIgnoreCase)).ToList());
 public Task<HashSet<string>> FindByProduct(string query)=>Task.FromResult(query=="SKU-CUE-01"?new HashSet<string>{VideoLibraryView.ProductKey(today1.Tracking,today1.Context.SessionId,today1.Context.Source)}:new HashSet<string>());
 public Task<IReadOnlyList<ScanCue>> LoadCues(LibraryItem item)=>Task.FromResult<IReadOnlyList<ScanCue>>(item.Id==today1.Id
  ?new[]{new ScanCue(1.5,item.At.AddSeconds(1.5),"SKU-CUE-01","验收商品A","6901234567890","+1"),new ScanCue(3,item.At.AddSeconds(3),"SKU-CUE-02","验收商品B","6909876543210","+2")}
  :Array.Empty<ScanCue>());
 public Task<T> Exclusive<T>(Func<string?,Task<T>> action)=>action(activeId);
 public Task AfterChange(LibraryItem item,string? movedTo=null){afterChanges++;return Task.CompletedTask;}
 public Task AfterDelete(){afterDeletes++;return Task.CompletedTask;}

 // ---- helpers
 void Check(bool pass,string message){if(!pass)throw new Exception("失败："+message);checks.Add(message);}
 static IEnumerable<T> Find<T>(DependencyObject root)where T:DependencyObject{for(int i=0;i<VisualTreeHelper.GetChildrenCount(root);i++){var c=VisualTreeHelper.GetChild(root,i);if(c is T t)yield return t;foreach(var d in Find<T>(c))yield return d;}}
 static void Invoke(Button b)=>((IInvokeProvider)new ButtonAutomationPeer(b).GetPattern(PatternInterface.Invoke)).Invoke();
 static void Toggle(CheckBox c)=>((IToggleProvider)new CheckBoxAutomationPeer(c).GetPattern(PatternInterface.Toggle)).Toggle();
 static string Texts(Window w)=>string.Join(" | ",Find<TextBlock>(w).Select(t=>t.Text).Concat(Find<TextBox>(w).Select(t=>t.Text)).Where(t=>!string.IsNullOrWhiteSpace(t)));
 static async Task Until(Func<bool> check,int seconds=30,string what="条件"){var sw=Stopwatch.StartNew();while(sw.Elapsed.TotalSeconds<seconds){if(check())return;await Task.Delay(80);}throw new TimeoutException("等待超时："+what);}
 Button ViewButton(string label)=>Find<Button>(window).Single(b=>Equals(b.Content,label));
 async Task Click(string label,string? dialogTitle=null,Action<Window>? answer=null){
  var button=ViewButton(label);var handled=false;Exception? problem=null;var seen=new HashSet<Window>();
  var timer=new DispatcherTimer(DispatcherPriority.Send){Interval=TimeSpan.FromMilliseconds(60)};
  timer.Tick+=(_,_)=>{
   foreach(var w in Application.Current.Windows.OfType<Window>().Where(w=>!ReferenceEquals(w,window)&&w.IsVisible&&w.IsLoaded).ToList()){
    if(!seen.Add(w))continue;
    var record=new{title=w.Title,text=Texts(w),expected=dialogTitle};dialogs.Add(record);
    if(dialogTitle!=null&&!handled&&w.Title==dialogTitle){handled=true;try{answer!(w);}catch(Exception ex){problem=ex;try{w.DialogResult=false;}catch{w.Close();}}}
    else{problem??=new Exception("意外弹窗："+w.Title+"："+record.text);try{w.DialogResult=false;}catch{w.Close();}}
   }
  };
  timer.Start();
  try{Invoke(button);await Task.Delay(200);await Until(()=>problem!=null||(!view.Busy&&!view.Loading&&(dialogTitle==null||handled)),45,"按钮「"+label+"」");}
  finally{timer.Stop();}
  if(problem!=null)throw new Exception("按钮「"+label+"」："+problem.Message,problem);
  if(dialogTitle!=null&&!handled)throw new Exception("未出现预期弹窗："+dialogTitle);
 }
 static void Press(Window w,Func<string,bool> label)=>Invoke(Find<Button>(w).First(b=>b.Content is string s&&label(s)));
 LibraryItem Item(VideoSegment s)=>store.List().Single(i=>i.Id==s.Id);
 bool Gone(VideoSegment s)=>!File.Exists(s.File)&&!File.Exists(s.File+".json")&&!File.Exists(s.File+".log")&&s.Photos.All(n=>!File.Exists(Path.Combine(Path.GetDirectoryName(s.File)!,n)));
 HashSet<string> AssociationKeys(){var f=Path.Combine(recordings,"video-associations.json");if(!File.Exists(f))return new();using var d=JsonDocument.Parse(File.ReadAllText(f));return d.RootElement.EnumerateObject().Select(p=>p.Name).ToHashSet();}
 void ShotOf(Window w,string name){try{w.UpdateLayout();var image=new RenderTargetBitmap(Math.Max(1,(int)w.ActualWidth),Math.Max(1,(int)w.ActualHeight),96,96,PixelFormats.Pbgra32);image.Render(w);var enc=new PngBitmapEncoder();enc.Frames.Add(BitmapFrame.Create(image));using var f=File.Create(Path.Combine(output,name));enc.Save(f);soft["shot:"+name]=true;}catch(Exception ex){soft["shot:"+name]=ex.Message;}}
 // C 风格主窗口外壳（无后端）：同一 ShellChrome 构建的工具栏 + 状态行，业务区用占位卡片代替 WebView2。Screenshot only (soft), not a check.
 async Task ShellShot(){
  var status=new TextBlock{Text="自动录像已启用，等待确认物流单号。　物流：—　业务服务：运行中"};var (top,bar)=ShellChrome.Build(status);
  foreach(var (text,level) in new[]{("显示监控浮窗",Theme.Primary),("结束本单录像",Theme.Secondary),("继续本单新一段",Theme.Secondary),("打开录像目录",Theme.Ghost)})bar.Children.Add(ShellChrome.Action(text,level));
  var dock=new DockPanel();DockPanel.SetDock(top,Dock.Top);dock.Children.Add(top);
  dock.Children.Add(new Border{Margin=new Thickness(16),Child=Theme.Card(new TextBlock{Text="业务界面（WebView2 · 网页 C 风格，侧栏品牌「"+ShellChrome.Brand+"」）",Foreground=Palette.Text3,HorizontalAlignment=HorizontalAlignment.Center,VerticalAlignment=VerticalAlignment.Center},24)});
  var w=Theme.Dialog(new Window{Title=ShellChrome.WindowTitle,Width=1280,Height=720,Content=dock,ShowActivated=false,WindowStartupLocation=WindowStartupLocation.CenterScreen});
  try{w.Show();await Task.Delay(300);ShotOf(w,"shell-chrome.png");}finally{w.Close();}
 }
 void Shot(string name){window.UpdateLayout();var image=new RenderTargetBitmap(Math.Max(1,(int)window.ActualWidth),Math.Max(1,(int)window.ActualHeight),96,96,PixelFormats.Pbgra32);image.Render(window);var enc=new PngBitmapEncoder();enc.Frames.Add(BitmapFrame.Create(image));using var f=File.Create(Path.Combine(output,name));enc.Save(f);}
 async Task Select(params VideoSegment[] segments){await Until(()=>!view.Loading,30,"列表加载");var ids=segments.Select(s=>s.Id).ToHashSet();view.Grid.SelectRows(view.Rows.Where(r=>ids.Contains(r.Id)));view.Grid.UpdateLayout();Check(view.SelectedRows.Count==segments.Length,"选中 "+string.Join(",",segments.Select(s=>s.Tracking)));}
 async Task Search(string text,bool enter=true){view.Query.Text=text;await view.SearchAsync(enter);await Task.Delay(50);}

 VideoSegment Seed(string tracking,VideoContext context,DateTimeOffset at,string synthetic,string png,bool protect=false,string state="saved",byte[]? tiny=null){
  context=context.Copy();context.Tracking=tracking;
  var s=ArchiveLayout.Reserve(recordings,context,context.Key,at);
  if(tiny!=null)File.WriteAllBytes(s.File,tiny);else File.Copy(synthetic,s.File,true);
  File.WriteAllText(s.File+".log","LIBRARY UI FIXTURE");
  if(tiny==null){var photo=Path.GetFileName(s.File)+"_fixture.png";File.Copy(png,Path.Combine(Path.GetDirectoryName(s.File)!,photo),false);s.Photos.Add(photo);}
  s.State=state;s.FirstFrameAt=at;s.EndAt=state=="recording"?null:at.AddSeconds(6);s.ExitCode=state=="saved"?0:state=="recording"?null:1;s.Duration=state=="saved"?6:0;s.Protected=protect;s.EffectiveMode="640x360 / H264 / 15fps (fixture)";
  if(state=="interrupted")s.Reason="验收：模拟中断";
  ArchiveLayout.Write(recordings,s);return s;
 }
 static VideoContext Ctx(string source,long session,long batch,params (string Po,string Outer)[] purchases)=>new(){Source=source,SessionId=session,BatchId=batch,Purchases=purchases.Select(p=>new PurchaseLink(p.Po,p.Outer)).ToList()};

 public async Task<int> Execute(){
  Exception? error=null;var timings=new Dictionary<string,double>();
  try{
   // ---- fixture media (same container flags as Recorder: fragmented MP4, H.264, no audio)
   Check(Path.IsPathFullyQualified(ffmpeg)&&File.Exists(ffmpeg),"找到 FFmpeg："+ffmpeg);
   var media=Path.Combine(output,"fixture");Directory.CreateDirectory(media);
   var synthetic=Path.Combine(media,"synthetic.mp4");var png=Path.Combine(media,"synthetic.png");
   var gen=await Recorder.Run(ffmpeg,new[]{"-hide_banner","-loglevel","error","-n","-f","lavfi","-i","testsrc2=size=640x360:rate=15","-t","6","-an","-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p","-g","15","-movflags","+frag_keyframe+empty_moov+default_base_moof","-f","mp4",synthetic},120);
   Check(gen.Code==0&&new FileInfo(synthetic).Length>0,"生成与录像同格式的 fMP4 测试视频 "+gen.Err.Trim());
   var pic=await Recorder.Run(ffmpeg,new[]{"-hide_banner","-loglevel","error","-n","-f","lavfi","-i","color=c=teal:s=64x36","-frames:v","1",png},60);
   Check(pic.Code==0&&File.Exists(png),"生成测试照片");
   var today=new DateTimeOffset(DateTime.Today.AddMinutes(1));
   today1=Seed("LIB-TODAY-A",Ctx("普通",101,1,("PO-LIB-1","EXT-LIB-1")),today,synthetic,png);
   var today2=Seed("LIB-TODAY-B",Ctx("普通",202,1,("PO-LIB-2","EXT-LIB-2")),today.AddMinutes(1),synthetic,png);
   var todayProt=Seed("LIB-TODAY-PROT",Ctx("普通",404,1,("PO-LIB-4","EXT-LIB-4")),today.AddMinutes(2),synthetic,png,protect:true);
   var live=Seed("LIB-TODAY-LIVE",Ctx("普通",505,1,("PO-LIB-5","EXT-LIB-5")),today.AddMinutes(3),synthetic,png,state:"recording");activeId=live.Id;
   var missing=Seed("LIB-TODAY-MISSING",Ctx("普通",606,1,("PO-LIB-6","EXT-LIB-6")),today.AddMinutes(4),synthetic,png);File.Delete(missing.File);
   var week=Seed("LIB-WEEK-3",Ctx("普通",707,1,("PO-LIB-7","EXT-LIB-7")),DateTimeOffset.Now.AddDays(-3),synthetic,png);
   var arch=Seed("LIB-ARCH",Ctx("无采购单",303,1),DateTimeOffset.Now.AddDays(-4),synthetic,png);
   var old=Seed("LIB-OLD-200",Ctx("普通",101,2,("PO-LIB-1","EXT-LIB-1")),DateTimeOffset.Now.AddDays(-200),synthetic,png);
   var oldProt=Seed("LIB-OLD-PROT",Ctx("普通",808,1,("PO-LIB-8","EXT-LIB-8")),DateTimeOffset.Now.AddDays(-210),synthetic,png,protect:true);
   var oldFailed=Seed("LIB-OLD-FAILED",Ctx("普通",809,1,("PO-LIB-9","EXT-LIB-9")),DateTimeOffset.Now.AddDays(-220),synthetic,png,state:"interrupted");
   foreach(var s in new[]{today1,today2,old})store.Associate(s.Context);
   var sw=Stopwatch.StartNew();const int bulk=1000;
   for(int i=0;i<bulk;i++)Seed($"BULK-{i:0000}",Ctx("普通",9000,i+1,("PO-BULK","EXT-BULK")),DateTimeOffset.Now.AddDays(-40).AddSeconds(i),synthetic,png,tiny:new byte[]{0,0,0,24,1,2,3,4});
   timings["seed_bulk_seconds"]=sw.Elapsed.TotalSeconds;
   var originals=new[]{todayProt,live,week,oldProt,oldFailed}.ToDictionary(s=>s.Id,s=>Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(s.File))));
   Check(AssociationKeys().SetEquals(new[]{"普通:101","普通:202"}),"关联索引初始包含 101 / 202");

   // ---- open: today's recordings, autofocus search
   view=new VideoLibraryView(this);
   window=new Window{Title="录像查询 · 验收",Background=Palette.Bg,Width=1280,Height=820,Content=view,ShowActivated=true,WindowStartupLocation=WindowStartupLocation.CenterScreen,FontFamily=new FontFamily("Microsoft YaHei UI"),FontSize=14};
   var loaded=new TaskCompletionSource();window.Loaded+=(_,_)=>loaded.TrySetResult();window.Show();await loaded.Task.WaitAsync(TimeSpan.FromSeconds(30));
   sw.Restart();await view.OpenAsync();timings["open_seconds"]=sw.Elapsed.TotalSeconds;
   Check(view.Preset==LibraryPreset.Today,"默认打开今天");
   Check(view.Rows.Select(r=>r.Id).ToHashSet().SetEquals(new[]{today1.Id,today2.Id,todayProt.Id,live.Id,missing.Id}),"打开即显示今天的 5 段录像（含在录、保护、文件缺失）");
   Check(view.AllItems.Count==10+bulk,"后台读取全部索引 "+view.AllItems.Count);
   Check(associationRefreshes>=1,"读取后在后台刷新采购关联");
   await Task.Delay(300);soft["search_box_focused"]=view.Query.IsKeyboardFocused||FocusManager.GetFocusedElement(window)==view.Query;
   Check(view.Summary.Text.Contains("共 5 段"),"结果数与总大小："+view.Summary.Text);
   var liveRow=view.Rows.Single(r=>r.Id==live.Id);var missingRow=view.Rows.Single(r=>r.Id==missing.Id);
   Check(liveRow.Status=="在录"&&view.Rows.Single(r=>r.Id==today1.Id).Status=="已完成"&&missingRow.SizeText=="文件缺失","状态着色字段：在录 / 已完成 / 文件缺失");
   Shot("library-open.png");

   // ---- search
   await Search("EXT-LIB-1");Check(view.Rows.Count==1&&view.Rows[0].Id==today1.Id,"外部单号搜索命中");
   await Search("PO-LIB-2");Check(view.Rows.Count==1&&view.Rows[0].Id==today2.Id,"采购单号搜索命中");
   await Search("LIB-WEEK-3");Check(view.Preset==LibraryPreset.All&&view.Rows.Count==1&&view.Rows[0].Id==week.Id,"扫码不在今天：自动切换到全部日期并命中");
   await Search("SKU-CUE-01");Check(view.Rows.Count==1&&view.Rows[0].Id==today1.Id,"商品编码搜索（业务扫码流水）命中");
   await Search("NO-SUCH-CODE");Check(view.Rows.Count==0&&view.RegistrationRows.Count==0&&view.Registrations.Visibility!=Visibility.Visible,"无结果时为空列表");
   await Search("KD-ONLY-001");Check(view.Rows.Count==0&&view.RegistrationRows.Count==1&&view.Registrations.Visibility==Visibility.Visible&&view.Registrations.Text.Contains("KD-ONLY-001")&&view.Registrations.Text.Contains("快递单登记"),"没有录像的快递单登记单号也能搜到："+view.Registrations.Text.Replace("\n"," / "));
   Shot("library-registration-only.png");
   await Search("LIB-TODAY-A");Check(view.Rows.Any(r=>r.Id==today1.Id)&&view.RegistrationRows.Count==0,"有录像的登记只出现在录像列表里，不重复列出");
   view.Query.Clear();await view.SearchAsync(false);
   view.StatusBox.SelectedItem="售后保护";Check(view.Rows.Count==2&&view.Rows.All(r=>r.Item.Protected),"状态筛选：售后保护");
   view.StatusBox.SelectedItem="在录";Check(view.Rows.Count==1&&view.Rows[0].Id==live.Id,"状态筛选：在录");
   view.StatusBox.SelectedItem="异常";Check(view.Rows.Any(r=>r.Id==oldFailed.Id)&&view.Rows.All(r=>r.Status=="异常"),"状态筛选：异常");
   view.StatusBox.SelectedItem="全部";
   view.SetPreset(LibraryPreset.Last7);Check(view.Rows.Any(r=>r.Id==week.Id)&&view.Rows.All(r=>r.Item.At>=DateTimeOffset.Now.Date.AddDays(-6)),"近7天");
   view.SetPreset(LibraryPreset.Last30);Check(view.Rows.Any(r=>r.Id==arch.Id)&&view.Rows.All(r=>r.Item.At>=DateTimeOffset.Now.Date.AddDays(-29)),"近30天");
   view.From.SelectedDate=DateTime.Today.AddDays(-41);view.To.SelectedDate=DateTime.Today.AddDays(-39);view.SetPreset(LibraryPreset.Custom);
   Check(view.Rows.Count==bulk,"自定义日期范围 "+view.Rows.Count);

   // ---- virtualization / no UI freeze with 1000+ rows
   view.SetPreset(LibraryPreset.All);var maxGap=0.0;var last=Stopwatch.GetTimestamp();
   var ping=new DispatcherTimer(DispatcherPriority.Normal){Interval=TimeSpan.FromMilliseconds(30)};ping.Tick+=(_,_)=>{var now=Stopwatch.GetTimestamp();maxGap=Math.Max(maxGap,Stopwatch.GetElapsedTime(last,now).TotalMilliseconds);last=now;};
   ping.Start();sw.Restart();await view.ReloadAsync();timings["reload_all_seconds"]=sw.Elapsed.TotalSeconds;ping.Stop();timings["max_ui_gap_ms"]=maxGap;
   view.Grid.UpdateLayout();var realized=Find<DataGridRow>(view.Grid).Count();timings["realized_rows"]=realized;
   foreach(var col in view.Grid.Columns.Skip(1))Check(col.ActualWidth>=55,"列宽不被压缩："+col.Header+" "+col.ActualWidth.ToString("0"));
   Check(view.Rows.Count==10+bulk&&view.Grid.EnableRowVirtualization&&VirtualizingPanel.GetIsVirtualizing(view.Grid)&&realized<200,$"{view.Rows.Count} 行只实例化 {realized} 个行控件（虚拟化开启）");
   Check(maxGap<1500,$"后台读取 {view.Rows.Count} 段时界面最大停顿 {maxGap:0} ms");
   var cv=(ListCollectionView)CollectionViewSource.GetDefaultView(view.Grid.ItemsSource);
   foreach(var col in view.Grid.Columns.Skip(1))Check(col.CanUserSort&&!string.IsNullOrEmpty(col.SortMemberPath),"可排序列："+col.Header);
   cv.SortDescriptions.Add(new SortDescription(nameof(VideoLibraryRow.Bytes),ListSortDirection.Descending));Check(((VideoLibraryRow)cv.GetItemAt(0)).Bytes==view.Rows.Max(r=>r.Bytes),"按大小排序");cv.SortDescriptions.Clear();
   window.Width=1024;window.UpdateLayout();Check(view.Grid.ActualWidth>=300&&view.Player.ActualWidth>=300,$"1024 宽度下列表 {view.Grid.ActualWidth:0}px / 播放器 {view.Player.ActualWidth:0}px 可用");Shot("library-1024.png");window.Width=1280;window.UpdateLayout();

   // ---- select all / multi-select
   view.SetPreset(LibraryPreset.Today);await Task.Delay(50);view.Grid.UpdateLayout();
   Check(view.Grid.SelectionMode==DataGridSelectionMode.Extended,"表格支持 Ctrl / Shift 多选");
   Toggle(view.HeaderCheck);await Task.Delay(50);view.Grid.UpdateLayout();
   Check(view.Grid.SelectedItems.Count==view.Rows.Count&&view.HeaderCheck.IsChecked==true,"表头复选框全选当前列表");
   var firstRow=(DataGridRow)view.Grid.ItemContainerGenerator.ContainerFromIndex(0);var rowBox=Find<CheckBox>(firstRow).Single();
   Check(rowBox.IsChecked==true,"全选后行复选框同步勾选");
   Toggle(view.HeaderCheck);await Task.Delay(50);Check(view.Grid.SelectedItems.Count==0&&view.HeaderCheck.IsChecked==false,"表头复选框取消全选");
   view.Grid.UpdateLayout();Toggle(Find<CheckBox>((DataGridRow)view.Grid.ItemContainerGenerator.ContainerFromIndex(0)).Single());Toggle(Find<CheckBox>((DataGridRow)view.Grid.ItemContainerGenerator.ContainerFromIndex(1)).Single());await Task.Delay(50);
   Check(view.Grid.SelectedItems.Count==2&&view.HeaderCheck.IsChecked==null,"逐行勾选可多选，表头显示部分选中");
   Toggle(view.HeaderCheck);await Task.Delay(50);Check(view.Grid.SelectedItems.Count==view.Rows.Count,"部分选中时点表头 = 全选");
   view.Grid.UnselectAll();
   await Click("删除所选","操作未完成",w=>Press(w,s=>s=="确定"));Check(Gone(today1)==false,"未选择时删除给出提示，不删除任何文件");

   // ---- player
   await Select(today1);await Click("播放所选");
   var expectedUrl=LibraryPlayer.VideoUrl(store.Root,today1.File);
   Check(view.Player.Source==expectedUrl&&view.Player.CurrentId==today1.Id,"播放所选：内置播放器载入所选录像 "+expectedUrl);
   try{await Until(()=>view.Player.Opened||view.Player.LastError!="",40,"播放器打开");}catch(TimeoutException){}
   soft["player_error"]=view.Player.LastError;soft["player_duration"]=view.Player.Duration;
   Check(view.Player.Opened&&view.Player.LastError=="","内置播放器打开 fMP4 录像（loadedmetadata，时长 "+view.Player.Duration+" 秒）"+view.Player.LastError);
   await Until(()=>view.Player.CueVersion>0,10,"扫码记录");Check(view.Player.Cues.Items.Count==2,"播放器下方列出该录像的扫码记录");
   view.Player.Cues.SelectedItem=view.Player.Cues.Items[1];await Task.Delay(100);
   Check(view.Player.LastSeek==3&&Math.Abs(view.Player.Seek.Value-3)<0.01,"点击扫码记录跳转到第 3 秒");
   await Until(()=>view.Player.LastSeeked is double t&&Math.Abs(t-3)<1.0,15,"播放器 seeked 事件");
   Check(view.Player.LastSeeked is double sk&&Math.Abs(sk-3)<1.0,"播放器实际跳转到 "+view.Player.LastSeeked+" 秒");
   view.Player.Speed.SelectedIndex=2;await Task.Delay(50);Check(view.Player.Rate==2,"倍速 2x");view.Player.Speed.SelectedIndex=1;
   await view.Player.SeekTo(2);
   var snap=await view.Player.SnapshotTo(Path.Combine(output,"snapshot.png"));var head=File.ReadAllBytes(snap).Take(8).ToArray();
   Check(head.SequenceEqual(new byte[]{0x89,0x50,0x4E,0x47,0x0D,0x0A,0x1A,0x0A}),"截图：FFmpeg 导出当前画面 PNG");
   var clip=await view.Player.ExportClipTo(Path.Combine(output,"clip.mp4"),1,4);Check(new FileInfo(clip).Length>1000,"导出片段：FFmpeg 流复制生成 MP4");
   Check(ClipDialog.Parse("01:05")==65&&ClipDialog.Parse("1:00:00")==3600&&ClipDialog.Parse("12.5")==12.5&&ClipDialog.Parse("x")==null,"片段时间格式解析");
   Shot("library-player.png");
   Invoke(view.Player.FullscreenButton);await Until(()=>view.Fullscreen,5,"全屏");Check(window.WindowStyle==WindowStyle.None&&view.Grid.Visibility!=Visibility.Visible,"全屏：只显示播放器");
   Invoke(view.Player.FullscreenButton);await Until(()=>!view.Fullscreen,5,"退出全屏");Check(window.WindowStyle!=WindowStyle.None&&view.Grid.Visibility==Visibility.Visible,"退出全屏恢复列表");
   // Diagnostics (soft): what this machine's WebView2 decodes, and whether the same pipeline plays a VP9 WebM.
   try{
    soft["webview_codecs"]=await view.Player.DiagnoseAsync();
    var webm=Path.Combine(recordings,"diagnostic-vp9.webm");
    var vp9=await Recorder.Run(ffmpeg,new[]{"-hide_banner","-loglevel","error","-n","-f","lavfi","-i","testsrc2=size=320x180:rate=15","-t","2","-c:v","libvpx-vp9","-b:v","200k",webm},120);
    if(vp9.Code==0){await view.Player.ProbeAsync(webm);try{await Until(()=>view.Player.Opened||view.Player.LastError!="",20,"webm");}catch(TimeoutException){}soft["webm_pipeline_opened"]=view.Player.Opened;soft["webm_pipeline_error"]=view.Player.LastError;}
    else soft["webm_pipeline_error"]="ffmpeg vp9: "+vp9.Err.Trim();
    File.Delete(webm);
   }catch(Exception ex){soft["diagnostics_error"]=ex.Message;}
   await Select(today1);await Click("播放所选");Check(view.Player.CurrentId==today1.Id,"重新载入所选录像");

   // ---- note / protection
   await Select(today1);
   await Click("备注","本段录像备注 · 最多300字",w=>{Find<TextBox>(w).Single().Text="验收备注-中文";Press(w,s=>s=="保存备注");});
   Check(Item(today1).Note=="验收备注-中文"&&afterChanges>0,"备注保存并回读");
   await Search("验收备注");Check(view.Rows.Count==1&&view.Rows[0].Id==today1.Id,"备注可检索");view.Query.Clear();await view.SearchAsync(false);view.SetPreset(LibraryPreset.Today);
   await Select(today1,today2);await Click("备注","操作未完成",w=>Press(w,s=>s=="确定"));Check(true,"多选时备注提示只能单条");
   await Select(today1,today2);await Click("售后保护 / 解除");Check(Item(today1).Protected&&Item(today2).Protected,"批量设为售后保护");
   await Select(today1,today2);await Click("售后保护 / 解除","确认解除售后保护",w=>Press(w,s=>s=="取消"));Check(Item(today1).Protected,"取消解除保持保护");
   await Select(today1,today2);await Click("售后保护 / 解除","确认解除售后保护",w=>Press(w,s=>s=="解除保护"));Check(!Item(today1).Protected&&!Item(today2).Protected,"批量解除售后保护（需确认）");

   // ---- multi delete: live always skipped, protected skipped by default, missing media cleans index
   view.Grid.UnselectAll();Toggle(view.HeaderCheck);await Task.Delay(50);Check(view.SelectedRows.Count==5,"今天 5 段全选（表头复选框）");
   await Click("删除所选",DeleteConfirmDialog.DialogTitle,w=>{var d=(DeleteConfirmDialog)w;ShotOf(w,"library-delete-confirm.png");
    Check(d.SummaryText.Text.Contains("将永久删除 3 段")&&d.SummaryText.Text.Contains("1 段视频文件已不存在"),"确认框：数量 / 大小 / 缺失文件 "+d.SummaryText.Text);
    Check(d.Details.Text.Contains("跳过（在录）")&&d.Details.Text.Contains("跳过（售后保护）")&&d.IncludeProtected.IsVisible&&d.IncludeProtected.IsChecked==false,"确认框：列出跳过项，售后保护默认不删");
    Check(d.CancelButton.IsDefault&&!d.ConfirmButton.IsDefault,"危险操作默认按钮为取消");
    Press(w,s=>s=="取消");});
   Check(!Gone(today1)&&!Gone(today2)&&File.Exists(missing.File+".json"),"取消删除不改动任何文件");
   view.Grid.UnselectAll();Toggle(view.HeaderCheck);await Task.Delay(50);Check(view.SelectedRows.Count==5,"重新全选");
   await Click("删除所选",DeleteConfirmDialog.DialogTitle,w=>Press(w,s=>s.StartsWith("确认删除")));
   Check(Gone(today1)&&Gone(today2)&&!File.Exists(missing.File+".json"),"确认后删除所选（含视频、照片、日志、索引；缺失文件只清索引）");
   Check(File.Exists(todayProt.File+".json")&&File.Exists(live.File+".json"),"在录与售后保护片段被跳过");
   Check(view.Rows.Count==2&&view.Rows.All(r=>r.Id==todayProt.Id||r.Id==live.Id)&&view.Notice.Text.Contains("已删除 3 段"),"列表与索引同步刷新："+view.Notice.Text);
   Check(store.List().All(i=>i.Id!=today1.Id&&i.Id!=today2.Id&&i.Id!=missing.Id),"录像索引已更新");
   Check(AssociationKeys().SetEquals(new[]{"普通:101"}),"只被已删录像引用的采购关联(202)已清理，仍被引用的(101)保留");
   Check(view.Player.Source==null&&view.Player.CurrentId==null,"正在播放的录像被删除后播放器清空");
   Check(afterDeletes>=1,"删除后通知宿主刷新当前录像状态");
   view.Grid.UnselectAll();Toggle(view.HeaderCheck);await Task.Delay(50);Check(view.SelectedRows.Count==2,"剩余在录 + 保护两段全选");
   await Click("删除所选",DeleteConfirmDialog.DialogTitle,w=>{var d=(DeleteConfirmDialog)w;
    Check(!d.ConfirmButton.IsEnabled&&d.SummaryText.Text.Contains("没有可删除"),"只剩在录 / 保护时确认按钮不可用");
    Toggle(d.IncludeProtected);Check(d.ConfirmButton.IsEnabled&&d.TargetCount==1,"勾选「同时删除售后保护片段」后可删除保护片段");
    Press(w,s=>s.StartsWith("确认删除"));});
   Check(Gone(todayProt)&&File.Exists(live.File)&&File.Exists(live.File+".json"),"勾选后删除售后保护片段，在录片段始终保留");

   // ---- 后补采购归档 (existing preview flow)
   view.SetPreset(LibraryPreset.All);await Search("LIB-ARCH");await Select(arch);var archTarget="";
   await Click("后补采购归档","确认后补采购归档",w=>{archTarget=Texts(w);Press(w,s=>s=="确认归档");});
   var moved=Item(arch);Check(moved.Archived&&!moved.PendingArchive&&moved.File!=arch.File&&!File.Exists(arch.File)&&archTarget.Contains(moved.File),"后补采购归档：预览原 / 新路径后移动一份");
   view.Query.Clear();await view.SearchAsync(false);view.StatusBox.SelectedItem="已归档";Check(view.Rows.Count==1&&view.Rows[0].Id==arch.Id,"状态筛选：已归档");view.StatusBox.SelectedItem="全部";

   // ---- cleanup old recordings = select candidates, then the same 删除所选 path
   view.SetPreset(LibraryPreset.Today);view.CleanupBox.SelectedIndex=1;
   await Click("选出旧录像");
   Check(view.Preset==LibraryPreset.All&&view.SelectedRows.Count==1&&view.SelectedRows[0].Id==old.Id,"清理旧录像(180天)：只选中超龄、已完成、未保护的片段");
   await Click("删除所选",DeleteConfirmDialog.DialogTitle,w=>Press(w,s=>s.StartsWith("确认删除")));
   Check(Gone(old)&&!File.Exists(old.File+".json"),"旧录像经同一删除路径删除");
   Check(AssociationKeys().Count==0||!AssociationKeys().Contains("普通:101"),"最后引用 101 的录像删除后关联同步清理");
   foreach(var s in new[]{live,week,oldProt,oldFailed})Check(Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(s.File)))==originals[s.Id],"未选中录像字节不变："+s.Tracking);
   await view.ReloadAsync();Check(store.List().Count==view.AllItems.Count&&view.AllItems.Count==bulk+5,"重新读取后索引一致："+view.AllItems.Count);
   Shot("library-final.png");
   await ShellShot();
   lock(unhandled)Check(unhandled.Count==0,"无未处理异常");
  }catch(Exception ex){error=ex;try{if(window!=null)Shot("library-failure.png");}catch{}}
  var ok=error==null;
  try{
   await File.WriteAllTextAsync(Path.Combine(output,"library-ui-result.json"),JsonSerializer.Serialize(new{ok,checks,soft,timings,dialogs,unhandled,error=error?.ToString(),scope="Standalone VideoLibraryView with real VideoLibrary store and WPF dialogs on generated fixture media; no backend, camera or business window. Player media playback depends on the machine's WebView2 runtime and codecs (soft)."},new JsonSerializerOptions{WriteIndented=true,Encoder=System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping}));
  }catch{}
  try{view?.Detach();window?.Close();}catch{}
  return ok?0:1;
 }
}
