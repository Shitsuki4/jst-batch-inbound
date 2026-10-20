using System.Collections.Concurrent;
using System.Diagnostics.Tracing;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
namespace Receiving.Native;

// Explicit opt-in only. No camera API, recording, business write, or library mutation control.
public sealed partial class MainWindow {
 async Task AcceptanceHttpUi(){
  var checks=new List<string>();var rounds=new List<object>();var dialogs=new List<object>();
  var errors=new List<string>();var probes=new ConcurrentQueue<string>();Task? traffic=null;
  using var trace=new HttpUiTrace(Path.Combine(root,"dotnet-network.jsonl"));
  var watch=new DispatcherTimer(DispatcherPriority.Send){Interval=TimeSpan.FromMilliseconds(80)};
  var seen=new HashSet<IntPtr>();
  void Check(bool pass,string message){if(!pass)throw new Exception(message);checks.Add(message);}
  void NoDialog(){if(dialogs.Count>0)throw new Exception("Unexpected native dialog; see report");}
  void Shot(Window w,string name){w.UpdateLayout();var image=new RenderTargetBitmap((int)w.ActualWidth,(int)w.ActualHeight,96,96,PixelFormats.Pbgra32);image.Render(w);var enc=new PngBitmapEncoder();enc.Frames.Add(BitmapFrame.Create(image));using var file=File.Create(Path.Combine(root,name));enc.Save(file);}
  watch.Tick+=(_,_)=>EnumWindows((hwnd,_)=>{
   GetWindowThreadProcessId(hwnd,out var pid);if(pid!=Environment.ProcessId)return true;
   var cls=new StringBuilder(64);GetClassName(hwnd,cls,cls.Capacity);if(cls.ToString()!="#32770"||!seen.Add(hwnd))return true;
   var buttons=new List<IntPtr>();var text=new List<string>();
   EnumChildWindows(hwnd,(child,_)=>{text.Add(LibraryText(child));var c=new StringBuilder(64);GetClassName(child,c,c.Capacity);if(c.ToString()=="Button")buttons.Add(child);return true;},IntPtr.Zero);
   dialogs.Add(new{title=LibraryText(hwnd),text=string.Join("\n",text)});
   // Never approve an unexpected destructive action. Only dismiss No or a sole OK.
   var safe=GetDlgItem(hwnd,7);if(safe==IntPtr.Zero&&buttons.Count==1&&LibraryText(buttons[0]) is "确定" or "OK" or "&OK")safe=buttons[0];
   if(safe!=IntPtr.Zero)SendMessage(safe,0xF5,IntPtr.Zero,IntPtr.Zero);return true;
  },IntPtr.Zero);
  try{
   watch.Start();Check(UiTest.Active&&UiTest.HttpOnly&&UiTest.SettingsOnly,"explicit no-camera HTTP/UI mode");
   using var fixture=JsonDocument.Parse(File.ReadAllText(Path.Combine(root,"http-ui-fixture.json")));
   var f=fixture.RootElement;
   Check(Path.GetFullPath(f.GetProperty("output").GetString()!)==Path.GetFullPath(root)&&f.GetProperty("external_network_forbidden").GetBoolean(),"fresh isolated fixture identity");
   Check(!File.Exists(Path.Combine(root,"native-settings.json")),"no pre-existing user settings");
   await WaitJs("Boolean(window.NativeDesk?.available && window.receivingSettingsLoaded)",60);
   Check(!rec.Active&&rec.Current==null&&enabled.IsChecked!=true&&!preview.IsVisible,"host ready without camera or preview");
   var context=f.GetProperty("context").Deserialize<VideoContext>(ArchiveLayout.Json)!;
   settings.Directory=Path.Combine(root,"http-ui-recordings");settings.CleanupDays=0;rec.Configure(settings);
   Check(!Directory.Exists(rec.DirectoryPath),"new synthetic-media directory only");
   var segment=SeedLibrary(context.Tracking,context,0); // today: the library opens on 今天Library.Associate(context);
   var originals=Directory.GetFiles(rec.DirectoryPath,"*",SearchOption.AllDirectories).ToDictionary(p=>p,LibraryHash);
   File.WriteAllText(Path.Combine(root,"media-hashes-before.json"),JsonSerializer.Serialize(originals,Recorder.JsonOptions));
   async Task Open(string kind){var reply=await Js("NativeDesk.call('"+kind+"')");if(reply.TryGetProperty("error",out var e))throw new Exception(e.ToString());NoDialog();}
   await Open("export");await Open("library");
   Check(exportWindow?.IsVisible==true&&libraryWindow?.IsVisible==true&&libraryView!.Rows.Count==1,"real WebView bridge opens populated native panels");
   var key=Uri.EscapeDataString(context.Key);
   string[] routes=["/api/native/health","/api/native/events?after="+backend.Cursor,"/api/native/export?mode=events","/api/native/contexts?keys="+key];
   traffic=Task.WhenAll(Enumerable.Range(0,4).Select(async worker=>{for(int i=0;i<40;i++){using var response=await backend.Get(routes[(worker+i)%routes.Length]);probes.Enqueue("ok");await Task.Delay(15);}}));
   await web.CoreWebView2.ExecuteScriptAsync("window.__httpSmokeReadDone=false;window.__httpSmokeReadErrors=[];window.__httpSmokeReadCount=0;(async()=>{try{await Promise.all(Array.from({length:4},async()=>{for(let i=0;i<10;i++){const r=await fetch(i%2?'/':'/api/state?sid="+UiTest.Sid+"');if(!r.ok)throw Error('HTTP '+r.status);await r.text();__httpSmokeReadCount++;}}));}catch(e){__httpSmokeReadErrors.push(String(e));}finally{__httpSmokeReadDone=true;}})();");
   for(int i=0;i<40;i++){
    bool miss=i%5==4;exportMode.SelectedIndex=i%2;filters["po"].Text="PO-TEST";filters["outer"].Text="EXT-TEST";filters["sku"].Text="A";filters["tracking"].Text=miss?"NO-SUCH-HTTP-UI":context.Tracking;
    libraryView!.Query.Text=miss?"NO-SUCH-HTTP-UI":i%2==0?context.Tracking:"PO-TEST";
    var exportButton=Children<Button>(exportWindow!).Single(b=>Equals(b.Content,"查询预览"));var libraryButton=Children<Button>(libraryWindow!).Single(b=>Equals(b.Content,"刷新"));
    var previousExport=exportGrid.ItemsSource;var previousLibrary=libraryView.Grid.ItemsSource;
    InvokeLibraryButton(exportButton);InvokeLibraryButton(libraryButton);
    await Until(()=>dialogs.Count>0||(exportButton.IsEnabled&&libraryButton.IsEnabled&&!libraryView.Loading&&!libraryView.Busy&&!ReferenceEquals(previousExport,exportGrid.ItemsSource)&&!ReferenceEquals(previousLibrary,libraryView.Grid.ItemsSource)),30);NoDialog();
    var expected=miss?0:1;var result=exportResult??throw new Exception("Missing export preview");
    if(result.GetProperty("count").GetInt32()!=expected||exportGrid.Items.Count!=expected||libraryView.Rows.Count!=expected)throw new Exception("Wrong preview/library count at round "+i);
    if(!miss){
     var expectedRows=f.GetProperty(i%2==0?"expected_events":"expected_summary").GetProperty("rows");
     if(!JsonElement.DeepEquals(result.GetProperty("rows"),expectedRows)||libraryView.Rows[0].Item.Id!=segment.Id||libraryView.Grid.SelectedItems.Count>1)throw new Exception("Wrong fixture content at round "+i);
    }
    rounds.Add(new{round=i,mode=i%2==0?"events":"summary",miss,export_rows=exportGrid.Items.Count,library_rows=libraryView.Rows.Count});
   }
   await traffic;await WaitJs("window.__httpSmokeReadDone===true",30);
   Check(probes.Count==160,"160 concurrent reads use unchanged host Backend");
   Check(await web.CoreWebView2.ExecuteScriptAsync("__httpSmokeReadCount===40 && __httpSmokeReadErrors.length===0")=="true","40 concurrent WebView reads pass");
   Check(rounds.Count==40,"40 paired real-button export/library rounds: both modes, positive and empty filters");
   libraryView!.Query.Clear();filters["tracking"].Text=context.Tracking;
   for(int i=0;i<4;i++){exportWindow!.Close();libraryWindow!.Close();await Open("export");await Open("library");if(libraryView!.Rows.Count!=1||libraryView.Loading)throw new Exception("Reopen library failed");}
   var finalButton=Children<Button>(exportWindow!).Single(b=>Equals(b.Content,"查询预览"));var last=exportGrid.ItemsSource;InvokeLibraryButton(finalButton);
   await Until(()=>dialogs.Count>0||(finalButton.IsEnabled&&!ReferenceEquals(last,exportGrid.ItemsSource)));NoDialog();
   Check(exportGrid.Items.Count==1&&libraryView!.Rows.Single().Item.Id==segment.Id,"four bridge close/reopen cycles preserve readable content");
   Shot(exportWindow!,"export-final.png");Shot(libraryWindow!,"library-final.png");
   var after=Directory.GetFiles(rec.DirectoryPath,"*",SearchOption.AllDirectories).ToDictionary(p=>p,LibraryHash);
   File.WriteAllText(Path.Combine(root,"media-hashes-after.json"),JsonSerializer.Serialize(after,Recorder.JsonOptions));
   Check(originals.Count==after.Count&&originals.All(p=>after.TryGetValue(p.Key,out var hash)&&hash==p.Value),"all seeded files and association content unchanged");
   Check(await web.CoreWebView2.ExecuteScriptAsync("__uiErrors.length===0")=="true","no WebView script errors");
   Check(dialogs.Count==0&&trace.Failures==0&&failed==0,"no native error dialogs, HTTP/socket failures or poll errors");
   Check(!rec.Active&&rec.Current==null&&enabled.IsChecked!=true&&!preview.IsVisible,"no recording or camera through completion");
  }catch(Exception ex){errors.Add(ex.ToString());}
  finally{
   if(traffic!=null)try{await traffic;}catch(Exception ex){errors.Add("Background reads: "+ex);}
   timer.Stop();closing=true;
   async Task Finish(string name,Func<Task> action){try{await action();}catch(Exception ex){errors.Add(name+": "+ex);}}
   await Finish("native windows",()=>{exportWindow?.Close();libraryWindow?.Close();return Task.CompletedTask;});
   await Finish("recorder",async()=>await rec.DisposeAsync());await Finish("backend",async()=>await backend.DisposeAsync());
   await Finish("UI resources",()=>{hotkeys?.Dispose();sounds?.Dispose();preview.ExitAllowed=true;preview.Close();web.Dispose();return Task.CompletedTask;});watch.Stop();
   var noCamera=!rec.Active&&rec.Current==null&&enabled.IsChecked!=true;
   if(trace.Failures!=0)errors.Add("HTTP/socket trace contains failures");
   var ok=errors.Count==0&&dialogs.Count==0&&noCamera;
   await File.WriteAllTextAsync(Path.Combine(root,"http-ui-result.json"),JsonSerializer.Serialize(new{ok,no_camera=noCamera,checks,rounds,dialogs,errors,background_reads=probes.Count,network_failures=trace.Failures,application_retries_added=false},Recorder.JsonOptions));
   Environment.ExitCode=ok?0:1;Close();
  }
 }
}

// Detailed evidence stays on disk; the runner prints counts only. No request overrides/retries.
sealed class HttpUiTrace:EventListener {
 readonly object gate=new();StreamWriter? writer;int failures;public int Failures=>Volatile.Read(ref failures);
 public HttpUiTrace(string path){writer=new StreamWriter(new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.Read));}
 protected override void OnEventSourceCreated(EventSource source){if(source.Name is "System.Net.Http" or "System.Net.Sockets")EnableEvents(source,EventLevel.Informational);}
 protected override void OnEventWritten(EventWrittenEventArgs e){
  if(writer==null)return;if(e.EventName is "RequestFailed" or "ConnectFailed" or "AcceptFailed")Interlocked.Increment(ref failures);
  var values=new Dictionary<string,object?>();for(int i=0;i<(e.Payload?.Count??0);i++)values[e.PayloadNames?[i]??i.ToString()]=e.Payload![i];
  lock(gate){writer?.WriteLine(JsonSerializer.Serialize(new{utc=DateTimeOffset.UtcNow,source=e.EventSource.Name,name=e.EventName,payload=values}));writer?.Flush();}
 }
 public override void Dispose(){base.Dispose();lock(gate){writer?.Dispose();writer=null;}}
}
