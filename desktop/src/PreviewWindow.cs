using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using System.Runtime.InteropServices;
using System.Windows.Interop;
namespace Receiving.Native;
public sealed class PreviewWindow:Window {
 readonly Recorder rec;readonly Image image=new(){Stretch=Stretch.Uniform};readonly TextBlock info=new(){TextWrapping=TextWrapping.Wrap,Margin=new Thickness(8),FontSize=12};
 readonly WriteableBitmap bitmap=new(480,270,96,96,PixelFormats.Bgr24,null);readonly Border viewport=new(){ClipToBounds=true,Background=Brushes.Black};
 readonly ContextMenu menu=new();readonly Dictionary<string,MenuItem> items=new();readonly Func<string,Task>? command;
 readonly DispatcherTimer timer=new(){Interval=TimeSpan.FromMilliseconds(200)};UserSettings options=new();byte[]? latest;bool compact,manualZoom;DateTimeOffset zoomUntil;
 public bool ExitAllowed{get;set;}
 [DllImport("user32.dll",EntryPoint="GetWindowLongW")]static extern int GetStyle(IntPtr h,int n);
 [DllImport("user32.dll",EntryPoint="SetWindowLongW")]static extern int SetStyle(IntPtr h,int n,int v);
 public PreviewWindow(Recorder r,Func<string,Task>? action=null){
  rec=r;command=action;Title="视频举证 · 关闭仅隐藏";Width=420;Height=370;MinWidth=360;MinHeight=200;ShowActivated=false;Topmost=true;Background=Brushes.White;
  SourceInitialized+=(_,_)=>{var h=new WindowInteropHelper(this).Handle;SetStyle(h,-20,GetStyle(h,-20)|0x08000000);};
  var root=new DockPanel();var bar=new DockPanel{Margin=new Thickness(6)};DockPanel.SetDock(bar,Dock.Bottom);root.Children.Add(bar);
  var stop=new Button{Content="结束本单录像",Padding=new Thickness(14,6,14,6),Margin=new Thickness(3),HorizontalAlignment=HorizontalAlignment.Left};stop.Click+=async(_,_)=>await Safe(()=>rec.StopAsync());DockPanel.SetDock(stop,Dock.Left);bar.Children.Add(stop);
  var more=new Button{Content="更多 ▾",Padding=new Thickness(12,6,12,6),Margin=new Thickness(3),HorizontalAlignment=HorizontalAlignment.Right};bar.Children.Add(more);
  more.Click+=(_,_)=>{RefreshMenu();menu.PlacementTarget=more;menu.Placement=PlacementMode.Top;menu.IsOpen=true;};
  AddMenu("continue","继续本单新一段",()=>Invoke("continue"));
  AddMenu("photo","拍照举证（JPEG）",Photo);
  AddMenu("note","本单备注（最多300字）",Note);
  AddMenu("protect","售后保护",async()=>{if(rec.Current!=null)await rec.SetEvidenceAsync(rec.Current.Note,!rec.Current.Protected);});items["protect"].IsCheckable=true;
  AddMenu("zoom","局部放大预览 / 恢复",()=>{manualZoom=!manualZoom;return Task.CompletedTask;});items["zoom"].IsCheckable=true;
  menu.Items.Add(new Separator());
  AddMenu("collapse","收起 / 展开画面",()=>{compact=!compact;viewport.Visibility=compact?Visibility.Collapsed:Visibility.Visible;Height=compact?210:370;return Task.CompletedTask;});
  AddMenu("pin","窗口置顶",()=>{Topmost=!Topmost;return Task.CompletedTask;});items["pin"].IsCheckable=true;
  AddMenu("directory","打开本单目录",()=>Invoke("currentDirectory"));
  AddMenu("library","录像检索 / 备注 / 保护 / 归档",()=>Invoke("library"));
  AddMenu("diagnostics","复制录像诊断",()=>Invoke("diagnostics"));
  AddMenu("hide","隐藏浮窗（不停止录像）",()=>{Hide();return Task.CompletedTask;});
  DockPanel.SetDock(info,Dock.Bottom);root.Children.Add(info);image.Source=bitmap;image.RenderTransformOrigin=new Point(.5,.5);viewport.Child=image;root.Children.Add(viewport);Content=root;
  r.Frame+=OnFrame;
  timer.Tick+=(_,_)=>{
   var data=Interlocked.Exchange(ref latest,null);if(data!=null)bitmap.WritePixels(new Int32Rect(0,0,480,270),data,480*3,0);
   var c=r.Current;var elapsed=r.Active&&c?.FirstFrameAt!=null?(DateTimeOffset.Now-c.FirstFrameAt.Value).ToString(@"hh\:mm\:ss"):"";
   info.Text=$"{r.Status}  {(r.Active?"●":"○")} {elapsed}\n物流：{c?.Tracking??"未选择"}\n"+(r.Warning!=""?r.Warning:r.Active?(r.FrameAge<3?"画面更新正常":"画面滞后，检查相机"):"隐藏不停止录像；结束不结单、不入库");
   info.Foreground=r.Warning!=""?Brushes.DarkOrange:r.Ready&&r.FrameAge<3?Brushes.DarkGreen:Brushes.DarkRed;stop.IsEnabled=r.Active;
   var zoom=options.ZoomEnabled&&(manualZoom||DateTimeOffset.Now<zoomUntil);image.RenderTransform=new ScaleTransform(zoom?2:1,zoom?2:1);
  };timer.Start();
  Closing+=(_,e)=>{if(!ExitAllowed){e.Cancel=true;Hide();}else{timer.Stop();r.Frame-=OnFrame;}};
 }
 void OnFrame(byte[] data)=>Interlocked.Exchange(ref latest,data);
 void AddMenu(string id,string title,Func<Task> action){var item=new MenuItem{Header=title};item.Click+=async(_,_)=>await Safe(action);menu.Items.Add(item);items[id]=item;}
 async Task Safe(Func<Task> fn){try{await fn();}catch(Exception ex){info.Text=ex.Message;MessageBox.Show(this,ex.Message,"操作未完成",MessageBoxButton.OK,MessageBoxImage.Warning);}}
 Task Invoke(string action)=>command?.Invoke(action)??Task.CompletedTask;
 public void Configure(UserSettings settings){options=settings.Copy();if(!options.ZoomEnabled){manualZoom=false;zoomUntil=default;}RefreshMenu();}
 public void ProductConfirmed(){if(options.ZoomEnabled)zoomUntil=DateTimeOffset.Now.AddSeconds(3);}
 void RefreshMenu(){
  foreach(var (id,enabled) in new[]{("photo",options.PhotoEnabled),("note",options.NotesEnabled),("protect",options.ProtectionEnabled),("zoom",options.ZoomEnabled)})items[id].Visibility=enabled?Visibility.Visible:Visibility.Collapsed;
  items["photo"].IsEnabled=rec.Ready&&rec.FrameAge<3;items["note"].IsEnabled=rec.Current!=null;items["protect"].IsEnabled=rec.Current!=null;
  items["protect"].IsChecked=rec.Current?.Protected==true;items["pin"].IsChecked=Topmost;items["zoom"].IsChecked=manualZoom;
 }
 async Task Photo(){var encoder=new JpegBitmapEncoder{QualityLevel=92};encoder.Frames.Add(BitmapFrame.Create(bitmap));using var stream=new System.IO.MemoryStream();encoder.Save(stream);var saved=await rec.SavePhotoAsync(stream.ToArray());info.Text="已保存JPEG举证照片："+System.IO.Path.GetFileName(saved);}
 async Task Note(){
  if(!options.NotesEnabled||rec.Current==null)return;var old=rec.Current;
  var value=PromptNote(this,old.Note);if(value==null)return;
  if(rec.Current?.Id!=old.Id)throw new InvalidOperationException("录像已切换，请为当前物流重新填写备注");
  await rec.SetEvidenceAsync(value,old.Protected);
 }
 public static string? PromptNote(Window owner,string value){
  var dialog=new Window{Title="本段录像备注 · 最多300字",Owner=owner,Width=470,Height=280,WindowStartupLocation=WindowStartupLocation.CenterOwner,ResizeMode=ResizeMode.NoResize};
  var panel=new DockPanel{Margin=new Thickness(16)};var row=new StackPanel{Orientation=Orientation.Horizontal,HorizontalAlignment=HorizontalAlignment.Right};DockPanel.SetDock(row,Dock.Bottom);panel.Children.Add(row);
  var text=new TextBox{Text=value,MaxLength=300,AcceptsReturn=true,TextWrapping=TextWrapping.Wrap,VerticalScrollBarVisibility=ScrollBarVisibility.Auto};panel.Children.Add(text);
  var save=new Button{Content="保存备注",Padding=new Thickness(14,8,14,8),Margin=new Thickness(6)};save.Click+=(_,_)=>dialog.DialogResult=true;row.Children.Add(save);
  var cancel=new Button{Content="取消",Padding=new Thickness(14,8,14,8),Margin=new Thickness(6),IsCancel=true};row.Children.Add(cancel);dialog.Content=panel;
  return dialog.ShowDialog()==true?text.Text:null;
 }
}
