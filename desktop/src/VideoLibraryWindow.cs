using System.Diagnostics;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Input;
namespace Receiving.Native;
public sealed class VideoLibraryRow {
 public LibraryItem Item {get;init;}=null!;public bool Checked{get;set;}
 public string Tracking=>Item.Tracking;public string Purchase=>Item.Purchase;public string Outer=>Item.Outer;
 public string Time=>Item.At.ToString("yyyy-MM-dd HH:mm:ss");public string State=>Item.State;public string Archive=>Item.ArchiveStatus;
 public string Protection=>Item.Protected?"售后保护":"—";public string Note=>Item.Note;public string Size=>$"{Item.Bytes/1048576.0:0.0} MB";
}
public sealed partial class MainWindow {
 readonly DataGrid libraryGrid=new(){AutoGenerateColumns=false,CanUserAddRows=false,SelectionMode=DataGridSelectionMode.Single};
 readonly TextBox libraryQuery=new(){Width=320,Padding=new Thickness(6)};
 readonly DatePicker libraryFrom=new(){Width=142},libraryTo=new(){Width=142};
 readonly ComboBox cleanupPeriod=new(){Width=190};readonly TextBlock libraryNotice=new(){TextWrapping=TextWrapping.Wrap,Margin=new Thickness(6)};
 List<VideoLibraryRow> libraryRows=new();bool cleanupView,libraryLoading;int previewCleanupDays;
 UIElement VideoLibraryPanel(){
  var panel=new DockPanel{Margin=new Thickness(14)};var top=new StackPanel();DockPanel.SetDock(top,Dock.Top);panel.Children.Add(top);
  top.Children.Add(new TextBlock{Text="单份录像，多采购关联检索。旧文件不会自动搬动；清理必须预览、勾选并再次确认。在录、异常、售后保护片段不进入普通清理候选。",TextWrapping=TextWrapping.Wrap,Margin=new Thickness(4)});
  var search=new WrapPanel();top.Children.Add(search);search.Children.Add(new TextBlock{Text="采购 / 外部 / 物流 / 商品 / 备注：",VerticalAlignment=VerticalAlignment.Center});search.Children.Add(libraryQuery);
  search.Children.Add(new TextBlock{Text=" 开始日期 ",VerticalAlignment=VerticalAlignment.Center});search.Children.Add(libraryFrom);search.Children.Add(new TextBlock{Text=" 至 ",VerticalAlignment=VerticalAlignment.Center});search.Children.Add(libraryTo);
  Add(search,"查询 / 刷新",()=>LoadLibrary());libraryQuery.KeyDown+=async(_,e)=>{if(e.Key==Key.Enter)await Safe(()=>LoadLibrary());};
  var actions=new WrapPanel();top.Children.Add(actions);
  Add(actions,"播放所选录像",()=>{var item=SelectedVideo();if(!File.Exists(ArchiveLayout.Checked(rec.DirectoryPath,item.File)))throw new IOException("录像文件不存在");Process.Start(new ProcessStartInfo(item.File){UseShellExecute=true});return Task.CompletedTask;});
  Add(actions,"打开所选目录",()=>{var folder=Path.GetDirectoryName(SelectedVideo().File)!;Process.Start(new ProcessStartInfo(ArchiveLayout.Checked(rec.DirectoryPath,folder)){UseShellExecute=true});return Task.CompletedTask;});
  Add(actions,"本段备注",EditLibraryNote);Add(actions,"售后保护 / 解除",ToggleLibraryProtection);Add(actions,"预览后补采购归档",ArchiveLibraryVideo);
  var cleanup=new WrapPanel();top.Children.Add(cleanup);
  cleanupPeriod.ItemsSource=new[]{new CleanupChoice(0,"不按天数提醒（不删除）"),new CleanupChoice(90,"90天以上候选"),new CleanupChoice(180,"180天以上候选")};cleanupPeriod.SelectedValuePath="Days";cleanupPeriod.DisplayMemberPath="Name";cleanupPeriod.SelectedValue=settings.CleanupDays;cleanup.Children.Add(cleanupPeriod);
  Add(cleanup,"预览清理候选",()=>LoadLibrary(true));Add(cleanup,"删除已勾选候选",DeleteLibrarySelection);
  DockPanel.SetDock(libraryNotice,Dock.Bottom);panel.Children.Add(libraryNotice);
  var checkbox=new FrameworkElementFactory(typeof(CheckBox));checkbox.SetBinding(CheckBox.IsCheckedProperty,new Binding("Checked"){Mode=BindingMode.TwoWay,UpdateSourceTrigger=UpdateSourceTrigger.PropertyChanged});checkbox.SetValue(FrameworkElement.HorizontalAlignmentProperty,HorizontalAlignment.Center);
  libraryGrid.Columns.Add(new DataGridTemplateColumn{Header="勾选",Width=50,CellTemplate=new DataTemplate{VisualTree=checkbox}});
  foreach(var (key,title,width) in new[]{("Time","开始时间",160),("Tracking","物流",160),("Purchase","采购",170),("Outer","外部单号",170),("State","状态",95),("Size","大小",90),("Protection","保护",90),("Archive","归档",165),("Note","备注",200)})libraryGrid.Columns.Add(new DataGridTextColumn{Header=title,Binding=new Binding(key),Width=width,IsReadOnly=true});
  panel.Children.Add(libraryGrid);return panel;
 }
 sealed record CleanupChoice(int Days,string Name);
 LibraryItem SelectedVideo()=>libraryGrid.SelectedItem is VideoLibraryRow row?row.Item:throw new InvalidOperationException("请先选择一条录像");
 string? ActiveVideoId=>rec.Active?rec.Current?.Id:null;
 async Task LoadLibrary(bool candidates=false){
  if(libraryLoading)return;libraryLoading=true;libraryNotice.Text="正在读取本机录像索引…";
  try{
   var store=Library;var all=await Task.Run(()=>store.List());
   var keys=all.Select(i=>i.Segment.Context).Where(c=>c.SessionId>0&&c.BatchId>0&&c.Source is "普通" or "无采购单" or "联合").Select(c=>c.Key).Distinct().ToArray();
   foreach(var batch in keys.Chunk(100)){
    using var response=await backend.Get("/api/native/contexts?keys="+Uri.EscapeDataString(string.Join('|',batch)));
    foreach(var c in response.RootElement.GetProperty("contexts").EnumerateArray())store.Associate(c.Deserialize<VideoContext>(ArchiveLayout.Json)!);
   }
   var query=libraryQuery.Text.Trim();var items=await Task.Run(()=>store.List(query));
   var from=libraryFrom.SelectedDate;var to=libraryTo.SelectedDate;if(from!=null&&to!=null&&from>to)throw new ArgumentException("开始日期不能晚于结束日期");
   items=items.Where(i=>(from==null||i.At.LocalDateTime.Date>=from)&&(to==null||i.At.LocalDateTime.Date<=to)).ToList();
   cleanupView=candidates;previewCleanupDays=(int?)cleanupPeriod.SelectedValue??settings.CleanupDays;
   if(candidates){if(previewCleanupDays==0)throw new InvalidOperationException("当前是不按天数提醒；如需手动清理，请先明确选择90天或180天候选，再预览勾选。没有删除任何文件。");items=items.Where(i=>VideoLibrary.CleanupEligible(i,previewCleanupDays,DateTimeOffset.Now,ActiveVideoId)).ToList();}
   libraryRows=items.Select(i=>new VideoLibraryRow{Item=i}).ToList();libraryGrid.ItemsSource=libraryRows;
   libraryNotice.Text=$"共 {items.Count} 段，{items.Sum(i=>i.Bytes)/1073741824.0:0.00} GB；{items.Count(i=>i.PendingArchive)} 段后补采购待确认归档。"+(candidates?"当前仅列清理候选，默认全部未勾选；点击删除仍须再次确认。":"当前为查询模式，不能直接批量删除。")+(store.Warnings.Count>0?"\n索引警告："+string.Join("；",store.Warnings.Take(3)):"");
  }catch(Exception ex){libraryNotice.Text="查询未完成："+ex.Message;cleanupView=false;throw;}finally{libraryLoading=false;}
 }
 async Task RefreshCurrentMetadata(LibraryItem item){if(!rec.Active&&rec.Current?.Id==item.Id)await rec.RefreshMetadataAsync(item.File);}
 async Task EditLibraryNote(){
  if(!settings.NotesEnabled)throw new InvalidOperationException("请在设置 → 视频举证中开启备注功能");var item=SelectedVideo();var note=PreviewWindow.PromptNote(libraryWindow??this,item.Note);if(note==null)return;
  await eventsGate.WaitAsync();try{Library.Edit(item,note,item.Protected,ActiveVideoId);await RefreshCurrentMetadata(item);}finally{eventsGate.Release();}await LoadLibrary(cleanupView);
 }
 async Task ToggleLibraryProtection(){
  if(!settings.ProtectionEnabled)throw new InvalidOperationException("请在设置 → 视频举证中开启售后保护功能（已有保护不会因关闭功能而解除）");var item=SelectedVideo();
  if(item.Protected&&MessageBox.Show(libraryWindow??this,"解除此段录像的售后保护？解除后可进入普通清理候选。\n"+item.Tracking,"确认解除保护",MessageBoxButton.YesNo,MessageBoxImage.Question,MessageBoxResult.No)!=MessageBoxResult.Yes)return;
  await eventsGate.WaitAsync();try{Library.Edit(item,item.Note,!item.Protected,ActiveVideoId);await RefreshCurrentMetadata(item);}finally{eventsGate.Release();}await LoadLibrary(cleanupView);
 }
 async Task ArchiveLibraryVideo(){
  if(rec.Active)throw new InvalidOperationException("请先结束录像，再预览并确认移动归档旧录像");var shown=SelectedVideo();
  using(var response=await backend.Get("/api/native/context?key="+Uri.EscapeDataString(shown.Segment.BatchKey)))Library.Associate(response.RootElement.GetProperty("context").Deserialize<VideoContext>(ArchiveLayout.Json)!);
  var item=Library.List().Single(i=>i.Id==shown.Id);if(!item.PendingArchive)throw new InvalidOperationException("此段没有待归档的后补采购关联；不会静默搬动其他历史录像");
  var target=Library.ArchiveTarget(item);
  if(MessageBox.Show(libraryWindow??this,"仅移动以下已停录片段及它的索引、日志和照片，视频仍只保存一份。\n\n原路径：\n"+item.File+"\n\n新路径：\n"+target+"\n\n确认归档？","确认后补采购归档",MessageBoxButton.YesNo,MessageBoxImage.Question,MessageBoxResult.No)!=MessageBoxResult.Yes)return;
  await eventsGate.WaitAsync();try{
   if(rec.Active)throw new InvalidOperationException("确认期间又开始了录像，归档已取消，请停录后重新预览");
   var store=Library;var activeId=ActiveVideoId;var moved=await Task.Run(()=>store.ArchiveConfirmed(item,target,activeId,true));
   if(rec.Current?.Id==item.Id)await rec.RefreshMetadataAsync(moved);archiveHint="用户确认归档完成，旧录像未复制成多份。";
  }finally{eventsGate.Release();}await LoadLibrary();
 }
 async Task DeleteLibrarySelection(){
  libraryGrid.CommitEdit(DataGridEditingUnit.Cell,true);libraryGrid.CommitEdit(DataGridEditingUnit.Row,true);
  if(!cleanupView)throw new InvalidOperationException("请先点击预览清理候选，再逐段勾选；查询结果不会直接删除");
  var selected=libraryRows.Where(r=>r.Checked).Select(r=>r.Item).ToArray();if(selected.Length==0)throw new InvalidOperationException("请勾选需要删除的候选片段，默认不全选");
  var text=$"永久删除已勾选的 {selected.Length} 段（{selected.Sum(i=>i.Bytes)/1073741824.0:0.00} GB）及各自照片、日志和索引？不会删除未勾选录像或目录。\n\n"+string.Join("\n",selected.Take(8).Select(i=>i.Tracking+" · "+i.At.ToString("yyyy-MM-dd")))+(selected.Length>8?"\n…其余见已勾选列表":"");
  if(MessageBox.Show(libraryWindow??this,text,"确认永久删除已勾选录像",MessageBoxButton.YesNo,MessageBoxImage.Warning,MessageBoxResult.No)!=MessageBoxResult.Yes)return;
  (int Count,List<string> Errors) result;
  await eventsGate.WaitAsync();try{if(rec.Active)throw new InvalidOperationException("正在录像，清理已取消；请停录后重新确认");var store=Library;var activeId=ActiveVideoId;result=await Task.Run(()=>store.DeleteConfirmed(selected,previewCleanupDays,DateTimeOffset.Now,activeId,true));if(rec.Current!=null&&!File.Exists(rec.Current.File))await rec.RefreshMetadataAsync(rec.Current.File);}finally{eventsGate.Release();}
  await LoadLibrary(true);libraryNotice.Text=$"已删除 {result.Count} 段。"+(result.Errors.Count==0?"未勾选内容保持不变。":"未完成："+string.Join("；",result.Errors));
 }
}
