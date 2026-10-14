using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
namespace Receiving.Native;

// Opt-in, no-camera UI acceptance. All file operations target freshly seeded fixture media.
public sealed partial class MainWindow {
 delegate bool LibraryEnumWindow(IntPtr hwnd,IntPtr arg);
 [DllImport("user32.dll")]static extern bool EnumThreadWindows(uint thread,LibraryEnumWindow callback,IntPtr arg);
 [DllImport("user32.dll")]static extern bool EnumWindows(LibraryEnumWindow callback,IntPtr arg);
 [DllImport("user32.dll")]static extern bool EnumChildWindows(IntPtr parent,LibraryEnumWindow callback,IntPtr arg);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)]static extern int GetWindowText(IntPtr hwnd,StringBuilder text,int length);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)]static extern int GetClassName(IntPtr hwnd,StringBuilder text,int length);
 [DllImport("user32.dll")]static extern IntPtr SetActiveWindow(IntPtr hwnd);
 [DllImport("user32.dll")]static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);
 [DllImport("user32.dll")]static extern IntPtr GetDlgItem(IntPtr hwnd,int id);
 [DllImport("user32.dll")]static extern int GetDlgCtrlID(IntPtr hwnd);
 [DllImport("user32.dll")]static extern IntPtr SendMessage(IntPtr hwnd,uint msg,IntPtr w,IntPtr l);
 [DllImport("kernel32.dll")]static extern uint GetCurrentThreadId();
 readonly List<object> libraryDialogs=new();
 static string LibraryText(IntPtr hwnd){var text=new StringBuilder(8192);GetWindowText(hwnd,text,text.Capacity);return text.ToString();}
 static string LibraryHash(string file)=>Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(file)));
 static void InvokeLibraryButton(Button b)=>((IInvokeProvider)new ButtonAutomationPeer(b).GetPattern(PatternInterface.Invoke)).Invoke();
 async Task LibraryClick(string label,string? dialog=null,string expected="",int answer=0,string? note=null){
  var button=Children<Button>(libraryWindow!).Single(b=>Equals(b.Content,label));
  bool answered=false;Exception? modalError=null;var observed=new HashSet<string>();
  var watch=new DispatcherTimer(DispatcherPriority.Send){Interval=TimeSpan.FromMilliseconds(70)};
  watch.Tick+=(_,_)=>{
   if(modalError!=null)return;
   try{
    if(dialog=="note"){
     if(answered)return;
     var win=Application.Current.Windows.OfType<Window>().FirstOrDefault(w=>w.Owner==libraryWindow&&w.Title=="本段录像备注 · 最多300字");
     if(win==null)return;var box=Children<TextBox>(win).Single();SettingsAssert(box.MaxLength==300,"备注长度上限");
     if(note!=null)box.Text=note;answered=true;libraryDialogs.Add(new{title=win.Title,action=answer==1?"保存备注":"取消",value=box.Text});
     InvokeLibraryButton(Children<Button>(win).Single(b=>Equals(b.Content,answer==1?"保存备注":"取消")));return;
    }
    if(dialog==null)return;
    EnumWindows((hwnd,_)=>{
     GetWindowThreadProcessId(hwnd,out var pid);if(pid!=Environment.ProcessId)return true;
     var title=LibraryText(hwnd);var kind=new StringBuilder(256);GetClassName(hwnd,kind,kind.Capacity);
     if(kind.ToString()!="#32770")return true;
     var parts=new List<string>();var buttons=new List<IntPtr>();
     EnumChildWindows(hwnd,(child,_)=>{parts.Add(LibraryText(child));var cls=new StringBuilder(256);GetClassName(child,cls,cls.Capacity);if(cls.ToString()=="Button")buttons.Add(child);return true;},IntPtr.Zero);
     var text=string.Join("\n",parts);
     int defaultButton=(int)((long)SendMessage(hwnd,0x400,IntPtr.Zero,IntPtr.Zero)&0xffff);
     if(!observed.Add(title+"\n"+text))return true;
     libraryDialogs.Add(new{title,text,expectedTitle=dialog,answer,defaultButton,buttons=buttons.Select(b=>new{id=GetDlgCtrlID(b),text=LibraryText(b)}).ToArray()});
     try{
      SettingsAssert(!answered,"已处理预期弹窗后又出现："+title+"；"+text);
      SettingsAssert(title==dialog,"确认框标题不符："+title);
      SettingsAssert(text.Contains(expected,StringComparison.Ordinal),"确认框内容不符："+text);
      if(answer is 6 or 7)SettingsAssert(defaultButton==7,"危险操作默认必须为否");
      var target=GetDlgItem(hwnd,answer);
      if(target==IntPtr.Zero)target=buttons.FirstOrDefault(b=>GetDlgCtrlID(b)==answer);
      // Some Windows OK-only message boxes expose the sole OK control under IDCANCEL.
      if(target==IntPtr.Zero&&answer==1&&dialog=="操作未完成"&&buttons.Count==1&&LibraryText(buttons[0]) is "确定" or "OK" or "&OK")target=buttons[0];
      SettingsAssert(target!=IntPtr.Zero,"确认按钮不存在");
      answered=true;SetActiveWindow(hwnd);SendMessage(target,0xF5,IntPtr.Zero,IntPtr.Zero);
     }catch(Exception ex){
      modalError=ex;
      // Never confirm an unexpected destructive dialog. Dismiss only No/Cancel or OK-only.
      var safe=GetDlgItem(hwnd,7);if(safe==IntPtr.Zero)safe=GetDlgItem(hwnd,2);
      if(safe==IntPtr.Zero&&GetDlgItem(hwnd,6)==IntPtr.Zero)safe=GetDlgItem(hwnd,1);
      if(safe!=IntPtr.Zero){SetActiveWindow(hwnd);SendMessage(safe,0xF5,IntPtr.Zero,IntPtr.Zero);}
     }
     return false;
    },IntPtr.Zero);
   }catch(Exception ex){modalError=ex;}
  };
  if(dialog!=null)watch.Start();
  try{InvokeLibraryButton(button);await Task.Delay(220);
   try{await Until(()=>modalError!=null||(button.IsEnabled&&!libraryLoading),25);}
   catch(Exception ex){
    var windows=new List<string>();EnumWindows((hwnd,_)=>{GetWindowThreadProcessId(hwnd,out var pid);if(pid==Environment.ProcessId)windows.Add(LibraryText(hwnd));return true;},IntPtr.Zero);
    throw new Exception($"录像库按钮等待失败：{label}；预期弹窗={dialog}；已应答={answered}；已观察={string.Join(" | ",observed)}；同进程窗口={string.Join(" | ",windows)}；状态={status.Text}",modalError??ex);
   }
   if(modalError!=null)throw modalError;SettingsAssert(dialog==null||answered,"预期对话框未处理："+dialog);
  }finally{watch.Stop();}
 }
 void LibraryShot(string name){
  var w=libraryWindow!;w.UpdateLayout();var image=new RenderTargetBitmap((int)w.ActualWidth,(int)w.ActualHeight,96,96,PixelFormats.Pbgra32);image.Render(w);
  var encoder=new PngBitmapEncoder();encoder.Frames.Add(BitmapFrame.Create(image));using var f=File.Create(Path.Combine(root,name));encoder.Save(f);
 }
 VideoSegment SeedLibrary(string name,VideoContext context,int age,bool protect=false,string state="saved"){
  var at=DateTimeOffset.Now.AddDays(-age);context=context.Copy();context.Tracking=name;
  var s=ArchiveLayout.Reserve(rec.DirectoryPath,context,context.Key,at);
  File.Copy(Path.Combine(root,"synthetic.mp4"),s.File,false);File.WriteAllText(s.File+".log","ISOLATED LIBRARY UI FIXTURE");
  var photo=Path.GetFileName(s.File)+"_fixture.png";File.Copy(Path.Combine(root,"synthetic.png"),Path.Combine(Path.GetDirectoryName(s.File)!,photo),false);
  s.State=state;s.FirstFrameAt=at;s.EndAt=at.AddSeconds(1);s.ExitCode=state=="saved"?0:1;s.Duration=1;s.Protected=protect;s.Photos.Add(photo);ArchiveLayout.Write(rec.DirectoryPath,s);return s;
 }
 async Task AcceptanceLibraryUi(){
  var checks=new List<string>();Exception? error=null;var originals=new Dictionary<string,string>();
  void Check(bool pass,string message){SettingsAssert(pass,message);checks.Add(message);}
  async Task Select(string id){await Until(()=>!libraryLoading);libraryGrid.SelectedItem=libraryRows.Single(r=>r.Item.Id==id);libraryGrid.ScrollIntoView(libraryGrid.SelectedItem);libraryGrid.UpdateLayout();}
  try{
   Check(UiTest.LibraryOnly&&UiTest.SettingsOnly,"录像库专项强制无相机模式");
   using var fixture=JsonDocument.Parse(File.ReadAllText(Path.Combine(root,"library-fixture.json")));
   Check(Path.GetFullPath(fixture.RootElement.GetProperty("output").GetString()!)==Path.GetFullPath(root),"隔离输出目录与fixture精确匹配");
   var linked=fixture.RootElement.GetProperty("context").Deserialize<VideoContext>(ArchiveLayout.Json)!;
   await WaitJs("Boolean(window.NativeDesk?.available && window.receivingSettingsLoaded)",60);
   Check(!rec.Active&&rec.Current==null&&enabled.IsChecked!=true,"宿主就绪且自动录像关闭");
   settings.Directory=Path.Combine(root,"library-recordings");settings.NotesEnabled=true;settings.ProtectionEnabled=true;settings.CleanupDays=0;rec.Configure(settings);
   Check(!Directory.Exists(rec.DirectoryPath),"仅在全新录像fixture目录中创建测试片段");
   var unlinked=linked.Copy();unlinked.Purchases.Clear();
   var pending=SeedLibrary(linked.Tracking,unlinked,220,true);
   VideoContext Plain()=>new(){Source="测试",LastSku="UI-SKU"};
   var delete=SeedLibrary("UI-DELETE-OLD",Plain(),200);var keep=SeedLibrary("UI-KEEP-UNCHECKED",Plain(),210);
   var protect=SeedLibrary("UI-PROTECTED",Plain(),220,true);var failed=SeedLibrary("UI-FAILED",Plain(),230,false,"interrupted");var recent=SeedLibrary("UI-RECENT",Plain(),1);
   foreach(var p in Directory.GetFiles(rec.DirectoryPath,"*",SearchOption.AllDirectories))originals[p]=LibraryHash(p);
   await Js("NativeDesk.call('library')");await Until(()=>libraryWindow?.IsVisible==true&&!libraryLoading&&libraryRows.Count==6);
   Check(Library.List().Single(i=>i.Id==pending.Id).PendingArchive&&File.Exists(pending.File),"真实桥打开录像库并关联后补采购，查询不会自动搬动视频");
   Check(LibraryHash(pending.File)==originals[pending.File],"关联检索不改变录像内容");
   libraryQuery.Text=linked.Purchases[0].Po;await LibraryClick("查询 / 刷新");Check(libraryRows.Count==1&&libraryRows[0].Item.Id==pending.Id,"采购号实际查询命中关联录像");
   libraryQuery.Text=linked.Purchases[0].Outer;await LibraryClick("查询 / 刷新");Check(libraryRows.Count==1,"外部号实际查询命中同一份录像");
   libraryQuery.Clear();await LibraryClick("查询 / 刷新");await Select(recent.Id);
   await LibraryClick("本段备注","note",answer:0,note:"不应保存");Check(Library.List().Single(i=>i.Id==recent.Id).Note=="","取消备注不落盘");
   await LibraryClick("本段备注","note",answer:1,note:"UI验收备注-中文");Check(Library.List().Single(i=>i.Id==recent.Id).Note=="UI验收备注-中文","真实备注窗口保存并回读中文");
   libraryQuery.Text="UI验收备注-中文";await LibraryClick("查询 / 刷新");Check(libraryRows.Count==1&&libraryRows[0].Item.Id==recent.Id,"备注关键词可检索");
   libraryQuery.Clear();await LibraryClick("查询 / 刷新");await Select(keep.Id);
   await LibraryClick("售后保护 / 解除");Check(Library.List().Single(i=>i.Id==keep.Id).Protected,"实际按钮开启售后保护并持久化");
   await Select(keep.Id);await LibraryClick("售后保护 / 解除","确认解除保护",keep.Tracking,7);Check(Library.List().Single(i=>i.Id==keep.Id).Protected,"取消解除保护保持原状态，默认否");
   await Select(keep.Id);await LibraryClick("售后保护 / 解除","确认解除保护",keep.Tracking,6);Check(!Library.List().Single(i=>i.Id==keep.Id).Protected,"明确确认后才解除保护");
   await Select(pending.Id);var target=Library.ArchiveTarget(Library.List().Single(i=>i.Id==pending.Id));
   await LibraryClick("预览后补采购归档","确认后补采购归档",target,7);Check(File.Exists(pending.File)&&!File.Exists(target),"归档预览显示真实目标，取消不搬动");
   await Select(pending.Id);await LibraryClick("预览后补采购归档","确认后补采购归档",target,6);
   var moved=Library.List().Single(i=>i.Id==pending.Id);
   Check(moved.File==target&&!moved.PendingArchive&&!File.Exists(pending.File)&&LibraryHash(target)==originals[pending.File],"确认归档仅移动一份视频，哈希不变");
   Check(!File.Exists(pending.File+".json")&&File.Exists(target+".json")&&LibraryHash(target+".log")==originals[pending.File+".log"]&&moved.Segment.Photos.All(n=>File.Exists(Path.Combine(Path.GetDirectoryName(target)!,n))),"归档同步移动索引、日志、照片且保留原路径审计");
   await LibraryClick("删除已勾选候选","操作未完成","预览清理候选",1);Check(File.Exists(delete.File),"查询模式禁止直接删除");
   await LibraryClick("预览清理候选","操作未完成","明确选择90天或180天",1);Check(File.Exists(delete.File),"不按天数提醒不会隐式启用删除候选");
   cleanupPeriod.SelectedValue=180;await LibraryClick("预览清理候选");
   Check(libraryRows.Count==2&&libraryRows.All(r=>!r.Checked)&&libraryRows.Select(r=>r.Item.Id).ToHashSet().SetEquals(new[]{delete.Id,keep.Id}),"180天候选排除保护/异常/近期片段，默认不勾选");
   await LibraryClick("删除已勾选候选","操作未完成","请勾选",1);Check(File.Exists(delete.File),"未勾选不能删除");
   await Select(delete.Id);var selectedRow=(DataGridRow)libraryGrid.ItemContainerGenerator.ContainerFromItem(libraryGrid.SelectedItem);
   var checkbox=Children<CheckBox>(selectedRow).Single();((IToggleProvider)new CheckBoxAutomationPeer(checkbox).GetPattern(PatternInterface.Toggle)).Toggle();await Task.Delay(100);
   Check(libraryRows.Single(r=>r.Item.Id==delete.Id).Checked&&libraryRows.Count(r=>r.Checked)==1,"实际DataGrid复选框只勾选目标片段");
   LibraryShot("library-cleanup-preview.png");
   await LibraryClick("删除已勾选候选","确认永久删除已勾选录像",delete.Tracking,7);Check(File.Exists(delete.File)&&File.Exists(keep.File),"取消删除保持所有文件，危险操作默认否");
   await LibraryClick("删除已勾选候选","确认永久删除已勾选录像",delete.Tracking,6);
   Check(!File.Exists(delete.File)&&!File.Exists(delete.File+".json")&&!File.Exists(delete.File+".log")&&delete.Photos.All(n=>!File.Exists(Path.Combine(Path.GetDirectoryName(delete.File)!,n))),"确认后仅删除勾选片段及其索引日志照片");
   Check(LibraryHash(keep.File)==originals[keep.File]&&LibraryHash(protect.File)==originals[protect.File]&&LibraryHash(failed.File)==originals[failed.File]&&LibraryHash(recent.File)==originals[recent.File],"未勾选/保护/异常/近期视频字节保持不变");
   Check(Directory.Exists(Path.GetDirectoryName(delete.File))&&libraryNotice.Text.Contains("已删除 1 段"),"只删除文件不递归删除目录，界面显示准确删除数量");
   libraryWindow!.Close();await Js("NativeDesk.call('library')");await Until(()=>libraryWindow?.IsVisible==true&&!libraryLoading);
   Check(Library.List().Count==5&&Library.List().Single(i=>i.Id==recent.Id).Note=="UI验收备注-中文"&&Library.List().Single(i=>i.Id==protect.Id).Protected,"关闭重开库窗口后归档/备注/保护/删除状态一致");
   LibraryShot("library-final.png");Check(!rec.Active&&rec.Current==null&&!preview.IsVisible,"全过程无相机、无自动录制、无预览浮窗");
  }catch(Exception ex){error=ex;try{if(libraryWindow!=null)LibraryShot("library-failure.png");}catch{}}
  finally{
   await File.WriteAllTextAsync(Path.Combine(root,"library-ui-result.json"),JsonSerializer.Serialize(new{ok=error==null,checks,dialogs=libraryDialogs,error=error?.ToString(),no_camera=!rec.Active&&rec.Current==null},Recorder.JsonOptions));
   timer.Stop();closing=true;if(libraryWindow!=null)libraryWindow.Close();await rec.DisposeAsync();await backend.DisposeAsync();hotkeys?.Dispose();sounds?.Dispose();preview.ExitAllowed=true;preview.Close();web.Dispose();Environment.ExitCode=error==null?0:1;Close();
  }
 }
}
