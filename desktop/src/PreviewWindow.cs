using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using System.Windows.Shell;
using System.Windows.Threading;
using System.Runtime.InteropServices;
using System.Windows.Interop;
using Path = System.Windows.Shapes.Path;
namespace Receiving.Native;

// One render frame of the float. Built from Recorder every tick; acceptance can substitute it (FloatUiAcceptance).
public sealed record FloatView(bool Active,bool Ready,double FrameAge,string Status,string Warning,VideoSegment? Current,TimeSpan? Elapsed){
 public static FloatView From(Recorder r){var c=r.Current;return new(r.Active,r.Ready,r.FrameAge,r.Status,r.Warning,c,r.Active&&c?.FirstFrameAt!=null?DateTimeOffset.Now-c.FirstFrameAt.Value:null);}
 public bool Fault=>!Active&&(Current?.State=="interrupted"||Status.StartsWith("录像异常")||Status.StartsWith("录像已停")||Status.StartsWith("未就绪")||Status.StartsWith("已达到磁盘停止"));
 public static string FaultTag(string status)=>status.Contains("无新画面")?"摄像头断开":status.Contains("进程已退出")?"录像进程退出":status.Contains("未增长")?"文件未增长":status.Contains("叠字")?"叠字失败":status.Contains("磁盘")?"磁盘":status.StartsWith("未就绪")?"未就绪":"异常";
}

// 录像浮窗 V2「标准预览卡」：顶栏状态 + 16:9 实时画面 + 单号信息 + 工具栏。
// 保留：不抢焦点（WS_EX_NOACTIVATE）、默认置顶、关闭=隐藏、可拖动/缩放、位置尺寸由 Program 保存。
public sealed class PreviewWindow:Window {
 readonly Recorder rec;readonly Func<string,Task>? command;
 readonly WriteableBitmap bitmap=new(480,270,96,96,PixelFormats.Bgr24,null);readonly Image image=new(){Stretch=Stretch.Uniform};
 readonly Grid viewport=new(){ClipToBounds=true,Background=Palette.Stage};
 readonly ContextMenu menu=new();readonly Dictionary<string,MenuItem> items=new();readonly Dictionary<string,FrameworkElement> parts=new();
 readonly DispatcherTimer timer=new(){Interval=TimeSpan.FromMilliseconds(200)};
 UserSettings options=new();byte[]? latest;bool compact,manualZoom,hasFrame,pulsing;DateTimeOffset zoomUntil,toastUntil,errorUntil;string errorText="",toastText="";double expandedHeight=388;bool? shownProtected;string centerIconName="-",bannerKind="";
 readonly Ellipse dot=new(){Width=9,Height=9,VerticalAlignment=VerticalAlignment.Center};
 readonly TextBlock stateText=new(){FontWeight=FontWeights.Bold,FontSize=14,VerticalAlignment=VerticalAlignment.Center,Margin=new Thickness(7,0,0,0)};
 readonly TextBlock timeText=new(){FontFamily=Palette.Mono,FontWeight=FontWeights.SemiBold,FontSize=14,VerticalAlignment=VerticalAlignment.Center,Margin=new Thickness(8,0,0,0)};
 readonly Border chip=new(){CornerRadius=new CornerRadius(6),Padding=new Thickness(7,2,7,2),Margin=new Thickness(8,0,0,0),VerticalAlignment=VerticalAlignment.Center};readonly TextBlock chipText=new(){FontSize=11.5,FontWeight=FontWeights.SemiBold};
 readonly Border zoomChip=new(),toast=new(),center=new(),banner=new();
 readonly TextBlock zoomText=new(){Foreground=Palette.OnDark,FontSize=12,FontWeight=FontWeights.SemiBold,VerticalAlignment=VerticalAlignment.Center};
 readonly TextBlock toastLabel=new(){Foreground=Palette.OnDark,FontSize=12,VerticalAlignment=VerticalAlignment.Center,TextTrimming=TextTrimming.CharacterEllipsis};
 readonly ContentControl centerIcon=new(){Foreground=Palette.OnDark,HorizontalAlignment=HorizontalAlignment.Center,Focusable=false};
 readonly TextBlock centerMsg=new(){Foreground=Palette.OnDark,FontSize=14,FontWeight=FontWeights.Bold,TextAlignment=TextAlignment.Center,TextWrapping=TextWrapping.Wrap,Margin=new Thickness(16,10,16,0)};
 readonly TextBlock centerSub=new(){Foreground=Palette.OnDark2,FontSize=12,TextAlignment=TextAlignment.Center,TextWrapping=TextWrapping.Wrap,Margin=new Thickness(24,6,24,0),MaxWidth=360,LineHeight=18};
 readonly StackPanel centerRow=new(){Orientation=Orientation.Horizontal,HorizontalAlignment=HorizontalAlignment.Center,Margin=new Thickness(0,12,0,0)};
 readonly ContentControl bannerIcon=new(){Focusable=false,VerticalAlignment=VerticalAlignment.Center};readonly TextBlock bannerText=new(){FontSize=12.5,TextWrapping=TextWrapping.Wrap,VerticalAlignment=VerticalAlignment.Center,Margin=new Thickness(8,0,8,0)};
 readonly Button bannerAction;string bannerCommand="";
 readonly TextBlock trackingText=new(){FontFamily=Palette.Mono,FontWeight=FontWeights.SemiBold,FontSize=14,Foreground=Palette.Text,VerticalAlignment=VerticalAlignment.Center,TextTrimming=TextTrimming.CharacterEllipsis};
 readonly TextBlock trackingNote=new(){FontSize=12,Foreground=Palette.Text3,Margin=new Thickness(6,0,0,0),VerticalAlignment=VerticalAlignment.Center};
 readonly TextBlock purchaseText=new(){FontFamily=Palette.Mono,FontSize=12.5,Foreground=Palette.Text2,TextTrimming=TextTrimming.CharacterEllipsis};
 readonly Button stopButton,continueButton,photoButton,noteButton,protectButton,moreButton,pinButton,collapseButton,hideButton,centerDiagnostics,centerContinue;
 public bool ExitAllowed{get;set;}
 /// <summary>Height to persist: the expanded height even while the picture is collapsed.</summary>
 public double RestoreHeight=>compact?expandedHeight:Height;
 [DllImport("user32.dll",EntryPoint="GetWindowLongW")]static extern int GetStyle(IntPtr h,int n);
 [DllImport("user32.dll",EntryPoint="SetWindowLongW")]static extern int SetStyle(IntPtr h,int n,int v);
 [DllImport("dwmapi.dll")]static extern int DwmSetWindowAttribute(IntPtr h,int attribute,ref int value,int size);
 public PreviewWindow(Recorder r,Func<string,Task>? action=null){
  rec=r;command=action;Title="视频举证 · 录像浮窗（关闭仅隐藏）";Width=432;Height=388;MinWidth=360;MinHeight=140;ShowActivated=false;Topmost=true;
  WindowStyle=WindowStyle.None;ResizeMode=ResizeMode.CanResize;Background=Palette.Surface;Foreground=Palette.Text;FontFamily=Palette.Font;FontSize=12;UseLayoutRounding=true;SnapsToDevicePixels=true;
  TextOptions.SetTextFormattingMode(this,TextFormattingMode.Display);
  WindowChrome.SetWindowChrome(this,new WindowChrome{CaptionHeight=42,ResizeBorderThickness=new Thickness(6),CornerRadius=new CornerRadius(Palette.Radius),GlassFrameThickness=new Thickness(0),UseAeroCaptionButtons=false});
  SourceInitialized+=(_,_)=>{var h=new WindowInteropHelper(this).Handle;SetStyle(h,-20,GetStyle(h,-20)|0x08000000);
   // Windows 11 rounded corners (DWMWA_WINDOW_CORNER_PREFERENCE=33, ROUND=2); harmless no-op on Windows 10.
   try{var round=2;DwmSetWindowAttribute(h,33,ref round,sizeof(int));}catch{}};
  // ---- top bar: grip · ● 状态 时长 · 健康胶囊 ······ 置顶 收起 隐藏
  var bar=new DockPanel{Height=42,LastChildFill=true,Background=Brushes.Transparent};
  var right=new StackPanel{Orientation=Orientation.Horizontal,VerticalAlignment=VerticalAlignment.Center,Margin=new Thickness(0,0,6,0)};DockPanel.SetDock(right,Dock.Right);bar.Children.Add(right);
  pinButton=Reg("FloatPin",IconButton("pin","窗口置顶",()=>{Topmost=!Topmost;return Task.CompletedTask;}));
  collapseButton=Reg("FloatCollapse",IconButton("collapse","收起 / 展开画面",()=>{ToggleCompact();return Task.CompletedTask;}));
  hideButton=Reg("FloatHide",IconButton("hide","隐藏浮窗（不停止录像）",()=>{Hide();return Task.CompletedTask;}));
  foreach(var b in new[]{pinButton,collapseButton,hideButton}){WindowChrome.SetIsHitTestVisibleInChrome(b,true);right.Children.Add(b);}
  var left=new StackPanel{Orientation=Orientation.Horizontal,VerticalAlignment=VerticalAlignment.Center,Margin=new Thickness(8,0,0,0)};
  var grip=Glyph("grip",16);grip.Margin=new Thickness(0,0,6,0);((Control)grip).Foreground=Palette.Grip;left.Children.Add(grip);
  left.Children.Add(Reg("FloatDot",dot));left.Children.Add(Reg("FloatState",stateText));left.Children.Add(Reg("FloatTime",timeText));chip.Child=Reg("FloatChipText",chipText);left.Children.Add(Reg("FloatChip",chip));
  bar.Children.Add(left);
  var barBox=new Border{BorderBrush=Palette.Line,BorderThickness=new Thickness(0,0,0,1),Child=bar,ToolTip="拖动顶栏移动浮窗"};
  // ---- 16:9 picture with corner overlays
  image.Source=bitmap;image.RenderTransformOrigin=new Point(.5,.5);viewport.Children.Add(image);Reg("FloatViewport",viewport);
  zoomChip.Background=Palette.Glass;zoomChip.CornerRadius=new CornerRadius(Palette.RadiusSm);zoomChip.Padding=new Thickness(8,3,8,3);zoomChip.Margin=new Thickness(8);zoomChip.HorizontalAlignment=HorizontalAlignment.Right;zoomChip.VerticalAlignment=VerticalAlignment.Top;
  zoomChip.Child=Row(Glyph("zoom",13,Palette.OnDark),zoomText);zoomChip.Visibility=Visibility.Collapsed;viewport.Children.Add(Reg("FloatZoom",zoomChip));
  toast.Background=Palette.Glass;toast.CornerRadius=new CornerRadius(Palette.RadiusSm);toast.Padding=new Thickness(9,4,10,4);toast.Margin=new Thickness(8);toast.HorizontalAlignment=HorizontalAlignment.Left;toast.VerticalAlignment=VerticalAlignment.Bottom;
  toast.Child=Row(Glyph("check",13,Palette.OnDark),toastLabel);toast.Visibility=Visibility.Collapsed;viewport.Children.Add(Reg("FloatToast",toast));
  centerDiagnostics=Reg("FloatCenterDiagnostics",TextButton("copy","复制录像诊断",ButtonKind.OnDark,()=>Invoke("diagnostics")));
  centerContinue=Reg("FloatCenterContinue",TextButton("play","继续本单新一段",ButtonKind.Primary,()=>Invoke("continue")));centerContinue.Margin=new Thickness(8,0,0,0);
  centerRow.Children.Add(centerDiagnostics);centerRow.Children.Add(centerContinue);
  var centerStack=new StackPanel{VerticalAlignment=VerticalAlignment.Center};centerStack.Children.Add(centerIcon);centerStack.Children.Add(Reg("FloatCenterMessage",centerMsg));centerStack.Children.Add(Reg("FloatCenterSub",centerSub));centerStack.Children.Add(Reg("FloatCenterActions",centerRow));
  center.Background=Palette.StageScrim;center.Child=centerStack;center.Visibility=Visibility.Collapsed;viewport.Children.Add(Reg("FloatCenter",center));
  // ---- inline banner (disk warning / failed action) — replaces the old modal MessageBox
  bannerAction=Reg("FloatBannerAction",LinkButton("",()=>bannerCommand==""?Task.CompletedTask:Invoke(bannerCommand)));
  var bannerDock=new DockPanel();DockPanel.SetDock(bannerIcon,Dock.Left);bannerDock.Children.Add(bannerIcon);DockPanel.SetDock(bannerAction,Dock.Right);bannerDock.Children.Add(bannerAction);bannerDock.Children.Add(Reg("FloatBannerText",bannerText));
  banner.Padding=new Thickness(12,7,10,7);banner.Child=bannerDock;banner.Visibility=Visibility.Collapsed;Reg("FloatBanner",banner);
  // ---- 物流 / 采购
  var info=new Grid{Margin=new Thickness(14,9,14,2)};info.ColumnDefinitions.Add(new ColumnDefinition{Width=new GridLength(44)});info.ColumnDefinitions.Add(new ColumnDefinition());info.RowDefinitions.Add(new RowDefinition());info.RowDefinitions.Add(new RowDefinition());
  var l1=Label("物流");var l2=Label("采购");Grid.SetRow(l2,1);var trackRow=Row(Reg("FloatTracking",trackingText),Reg("FloatTrackingNote",trackingNote));trackRow.Margin=new Thickness(0);Grid.SetColumn(trackRow,1);
  Grid.SetColumn(purchaseText,1);Grid.SetRow(purchaseText,1);purchaseText.Margin=new Thickness(0,1,0,0);Reg("FloatPurchase",purchaseText);
  foreach(var e in new UIElement[]{l1,l2,trackRow,purchaseText})info.Children.Add(e);
  // ---- tools: 主操作 · 拍照 · 备注 · 保护 ······ ⋯
  var tools=new DockPanel{Margin=new Thickness(10,6,10,10),LastChildFill=false};
  stopButton=Reg("FloatStop",TextButton("stop","结束本单录像",ButtonKind.Outline,()=>rec.StopAsync()));
  continueButton=Reg("FloatContinue",TextButton("play","继续本单新一段",ButtonKind.Primary,()=>Invoke("continue")));
  photoButton=Reg("FloatPhoto",TextButton("camera","拍照",ButtonKind.Ghost,Photo));noteButton=Reg("FloatNote",TextButton("edit","备注",ButtonKind.Ghost,Note));
  protectButton=Reg("FloatProtect",TextButton("shield","保护",ButtonKind.Ghost,ToggleProtect));
  moreButton=Reg("FloatMore",IconButton("more","更多",()=>{RefreshMenu();menu.PlacementTarget=moreButton;menu.Placement=PlacementMode.Top;menu.IsOpen=true;return Task.CompletedTask;},18));
  DockPanel.SetDock(moreButton,Dock.Right);tools.Children.Add(moreButton);
  foreach(var b in new[]{stopButton,continueButton,photoButton,noteButton,protectButton}){DockPanel.SetDock(b,Dock.Left);b.Margin=new Thickness(0,0,6,0);tools.Children.Add(b);}
  var root=new Grid();foreach(var h in new[]{GridLength.Auto,new GridLength(1,GridUnitType.Star),GridLength.Auto,GridLength.Auto,GridLength.Auto})root.RowDefinitions.Add(new RowDefinition{Height=h});
  UIElement[] rows={barBox,viewport,banner,info,tools};for(int i=0;i<rows.Length;i++){Grid.SetRow(rows[i],i);root.Children.Add(rows[i]);}
  Content=new Border{BorderBrush=Palette.LineStrong,BorderThickness=new Thickness(1),Background=Palette.Surface,Child=root};
  // ---- 「⋯」更多菜单：全部现有项保留，按 本单录像 / 浮窗 / 资料与排障 分组
  menu.FontFamily=Palette.Font;menu.FontSize=13;
  MenuHeader("本单录像");
  AddMenu("continue","继续本单新一段",()=>Invoke("continue"));
  AddMenu("stop","结束本单录像",()=>rec.StopAsync());
  AddMenu("photo","拍照举证（JPEG）",Photo);
  AddMenu("note","本单备注（最多300字）",Note);
  AddMenu("protect","售后保护",ToggleProtect);items["protect"].IsCheckable=true;
  AddMenu("zoom","局部放大预览 / 恢复",()=>{manualZoom=!manualZoom;Render(Snapshot());return Task.CompletedTask;});items["zoom"].IsCheckable=true;
  menu.Items.Add(new Separator());MenuHeader("浮窗");
  AddMenu("collapse","收起 / 展开画面",()=>{ToggleCompact();return Task.CompletedTask;});
  AddMenu("pin","窗口置顶",()=>{Topmost=!Topmost;return Task.CompletedTask;});items["pin"].IsCheckable=true;
  AddMenu("hide","隐藏浮窗（不停止录像）",()=>{Hide();return Task.CompletedTask;});
  menu.Items.Add(new Separator());MenuHeader("资料与排障");
  AddMenu("directory","打开本单目录",()=>Invoke("currentDirectory"));
  AddMenu("library","录像检索 / 备注 / 保护 / 归档",()=>Invoke("library"));
  AddMenu("diagnostics","复制录像诊断",()=>Invoke("diagnostics"));
  r.Frame+=OnFrame;
  timer.Tick+=(_,_)=>Tick();timer.Start();
  Closing+=(_,e)=>{if(!ExitAllowed){e.Cancel=true;Hide();}else{timer.Stop();r.Frame-=OnFrame;}};
  Render(Snapshot());
 }
 // ---- acceptance hooks (same assembly only)
 internal Func<FloatView>? TestView{get;set;}
 internal void TestFrame(byte[] bgr480x270)=>Interlocked.Exchange(ref latest,bgr480x270);
 internal T Part<T>(string name)where T:FrameworkElement=>(T)parts[name];
 internal IReadOnlyDictionary<string,MenuItem> MenuItems=>items;
 internal bool Compact=>compact;
 internal void ClearTransient(){zoomUntil=default;toastUntil=default;errorUntil=default;manualZoom=false;Render(Snapshot());}
 internal void ShowToast(string text){toastText=text;toastUntil=DateTimeOffset.Now.AddSeconds(4);Render(Snapshot());}
 internal void Tick(){
  var data=Interlocked.Exchange(ref latest,null);if(data!=null){bitmap.WritePixels(new Int32Rect(0,0,480,270),data,480*3,0);hasFrame=true;}
  Render(Snapshot());
 }
 FloatView Snapshot()=>TestView?.Invoke()??FloatView.From(rec);
 void OnFrame(byte[] data)=>Interlocked.Exchange(ref latest,data);
 T Reg<T>(string name,T element)where T:FrameworkElement{element.Name=name;parts[name]=element;return element;}

 void Render(FloatView v){
  var c=v.Current;var fault=v.Fault;var now=DateTimeOffset.Now;
  // status line
  if(v.Ready){dot.Fill=Palette.Rec;stateText.Text="录像中";stateText.Foreground=Palette.Text;}
  else if(v.Active){dot.Fill=Palette.Warn;stateText.Text=v.Status.Contains("保存")?"正在保存":"相机启动中";stateText.Foreground=Palette.Text;}
  else if(fault){dot.Fill=Palette.Danger;stateText.Text="录像已停止";stateText.Foreground=Palette.Danger;}
  else{dot.Fill=Palette.Idle;stateText.Text="未录像";stateText.Foreground=Palette.Text;}
  Pulse(v.Ready);
  timeText.Text=v.Elapsed is {} e?e.ToString(@"hh\:mm\:ss"):"";timeText.Visibility=timeText.Text==""?Visibility.Collapsed:Visibility.Visible;
  if(v.Active&&v.Warning!="")Chip("磁盘不足","warn");
  else if(v.Ready)Chip(v.FrameAge<3?"画面正常":"画面滞后",v.FrameAge<3?"ok":"warn");
  else if(!v.Active&&fault)Chip(FloatView.FaultTag(v.Status),"err");
  else if(!v.Active&&c?.State=="saved")Chip("上段已保存 "+TimeSpan.FromSeconds(Math.Max(0,c.Duration)).ToString(@"hh\:mm\:ss"),"ok");
  else Chip("","");
  // picture overlays
  if(fault){
   SetCenter("cameraOff",v.Status,c?.State=="saved"?"本段已保存。处理后点「继续本单新一段」。":"本段已标记为中断，文件保留。重新插好摄像头或处理后继续。",true);
  }else if(!v.Active){
   SetCenter("scan","扫描下一个物流单即自动开始录像",(hasFrame&&c!=null?"画面为上一段最后一帧 · ":"")+"隐藏不停止录像；结束不结单、不入库",false);
  }else if(!v.Ready){SetCenter("",v.Status,"",false);}
  else center.Visibility=Visibility.Collapsed;
  var zoom=options.ZoomEnabled&&(manualZoom||now<zoomUntil);image.RenderTransform=new ScaleTransform(zoom?2:1,zoom?2:1);
  zoomChip.Visibility=zoom?Visibility.Visible:Visibility.Collapsed;zoomText.Text=manualZoom?"局部放大 ×2":"局部放大 ×2 · 3 秒";
  toast.Visibility=now<toastUntil?Visibility.Visible:Visibility.Collapsed;toastLabel.Text=toastText;
  // banner: failed action first, then disk warning
  if(now<errorUntil)Banner("err",errorText,"复制诊断","diagnostics");
  else if(v.Warning!="")Banner("warn",v.Warning,"去整理","library");
  else banner.Visibility=Visibility.Collapsed;
  // info
  trackingText.Text=string.IsNullOrWhiteSpace(c?.Tracking)?"未选择":c!.Tracking;trackingNote.Text=!v.Active&&c!=null?"上一段":"";
  var purchases=c?.Context?.Purchases??new();
  purchaseText.Text=purchases.Count==0?"—":string.Join("；",purchases.Take(2).Select(p=>p.Po+(string.IsNullOrWhiteSpace(p.Outer)?"":" · 外部 "+p.Outer)))+(purchases.Count>2?$" 等{purchases.Count}单":"");
  purchaseText.ToolTip=purchases.Count==0?null:string.Join("\n",purchases.Select(p=>p.Po+"  外部 "+p.Outer));
  // tools
  stopButton.Visibility=v.Active?Visibility.Visible:Visibility.Collapsed;stopButton.IsEnabled=v.Active;continueButton.Visibility=v.Active?Visibility.Collapsed:Visibility.Visible;
  photoButton.Visibility=options.PhotoEnabled?Visibility.Visible:Visibility.Collapsed;photoButton.IsEnabled=v.Ready&&v.FrameAge<3;
  noteButton.Visibility=options.NotesEnabled?Visibility.Visible:Visibility.Collapsed;noteButton.IsEnabled=c!=null;
  protectButton.Visibility=options.ProtectionEnabled?Visibility.Visible:Visibility.Collapsed;protectButton.IsEnabled=c!=null;
  var isProtected=c?.Protected==true;if(shownProtected!=isProtected){shownProtected=isProtected;protectButton.Content=ButtonContent(isProtected?"shieldOk":"shield",isProtected?"已保护":"保护");protectButton.Foreground=isProtected?Palette.Brand:Palette.Text2;}
  SetOn(pinButton,Topmost);SetOn(moreButton,menu.IsOpen);
 }
 void Chip(string text,string kind){
  chip.Visibility=text==""?Visibility.Collapsed:Visibility.Visible;chipText.Text=text;
  if(kind=="ok"){chip.Background=Palette.OkSoft;chipText.Foreground=Palette.Ok;}
  else if(kind=="warn"){chip.Background=Palette.WarnSoft;chipText.Foreground=Palette.WarnText;}
  else if(kind=="err"){chip.Background=Palette.DangerSoft;chipText.Foreground=Palette.DangerText;}
  else{chip.Background=Palette.Surface2;chipText.Foreground=Palette.Text2;}
 }
 void SetCenter(string icon,string message,string sub,bool actions){
  center.Visibility=Visibility.Visible;if(icon!=centerIconName){centerIconName=icon;centerIcon.Content=icon==""?null:Glyph(icon,30,Palette.OnDark);}centerIcon.Visibility=icon==""?Visibility.Collapsed:Visibility.Visible;
  centerMsg.Text=message;centerSub.Text=sub;centerSub.Visibility=sub==""?Visibility.Collapsed:Visibility.Visible;centerRow.Visibility=actions?Visibility.Visible:Visibility.Collapsed;
  center.Background=actions?Palette.Glass:Palette.StageScrim;
 }
 void Banner(string kind,string text,string action,string cmd){
  banner.Visibility=Visibility.Visible;bannerText.Text=text;bannerCommand=cmd;((TextBlock)bannerAction.Content).Text=action;
  banner.Background=kind=="err"?Palette.DangerSoft:Palette.WarnSoft;bannerText.Foreground=kind=="err"?Palette.DangerText:Palette.WarnText;
  if(kind!=bannerKind){bannerKind=kind;bannerIcon.Content=Glyph("alert",15,kind=="err"?Palette.Danger:Palette.Warn);}
 }
 void Pulse(bool on){
  if(on==pulsing)return;pulsing=on;
  if(on)dot.BeginAnimation(OpacityProperty,new DoubleAnimation(1,.35,TimeSpan.FromMilliseconds(700)){AutoReverse=true,RepeatBehavior=RepeatBehavior.Forever});
  else{dot.BeginAnimation(OpacityProperty,null);dot.Opacity=1;}
 }
 void ToggleCompact(){
  compact=!compact;
  if(compact){expandedHeight=Height;viewport.Visibility=Visibility.Collapsed;SizeToContent=SizeToContent.Height;}
  else{SizeToContent=SizeToContent.Manual;viewport.Visibility=Visibility.Visible;Height=Math.Max(MinHeight,expandedHeight);}
  collapseButton.Content=Glyph(compact?"expand":"collapse",16);
 }
 Task ToggleProtect()=>rec.Current!=null?rec.SetEvidenceAsync(rec.Current.Note,!rec.Current.Protected):Task.CompletedTask;
 void MenuHeader(string text)=>menu.Items.Add(new MenuItem{Header=text,IsEnabled=false,FontSize=11.5,Foreground=Palette.Text3,Focusable=false});
 void AddMenu(string id,string title,Func<Task> action){var item=new MenuItem{Header=title};item.Click+=async(_,_)=>await Safe(action);menu.Items.Add(item);items[id]=item;}
 async Task Safe(Func<Task> fn){try{await fn();}catch(Exception ex){ShowError(ex.Message);}}
 // Failures stay inside the float (no modal MessageBox over a topmost window that would block scanning).
 void ShowError(string message){errorText=message;errorUntil=DateTimeOffset.Now.AddSeconds(10);Render(Snapshot());}
 Task Invoke(string action)=>command?.Invoke(action)??Task.CompletedTask;
 public void Configure(UserSettings settings){options=settings.Copy();if(!options.ZoomEnabled){manualZoom=false;zoomUntil=default;}RefreshMenu();Render(Snapshot());}
 public void ProductConfirmed(){if(options.ZoomEnabled)zoomUntil=DateTimeOffset.Now.AddSeconds(3);}
 void RefreshMenu(){
  var v=Snapshot();
  foreach(var (id,enabled) in new[]{("photo",options.PhotoEnabled),("note",options.NotesEnabled),("protect",options.ProtectionEnabled),("zoom",options.ZoomEnabled)})items[id].Visibility=enabled?Visibility.Visible:Visibility.Collapsed;
  items["photo"].IsEnabled=v.Ready&&v.FrameAge<3;items["note"].IsEnabled=v.Current!=null;items["protect"].IsEnabled=v.Current!=null;items["stop"].IsEnabled=v.Active;
  items["protect"].IsChecked=v.Current?.Protected==true;items["pin"].IsChecked=Topmost;items["zoom"].IsChecked=manualZoom;
  var hk=options.HotkeysEnabled;items["continue"].InputGestureText=hk?options.HotkeyContinue:"";items["stop"].InputGestureText=hk?options.HotkeyStop:"";items["hide"].InputGestureText=hk?options.HotkeyPreview:"";
 }
 async Task Photo(){var encoder=new JpegBitmapEncoder{QualityLevel=92};encoder.Frames.Add(BitmapFrame.Create(bitmap));using var stream=new System.IO.MemoryStream();encoder.Save(stream);var saved=await rec.SavePhotoAsync(stream.ToArray());ShowToast("已保存举证照片 "+System.IO.Path.GetFileName(saved));}
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

 // ---- building blocks
 enum ButtonKind{Primary,Outline,Ghost,Icon,OnDark,Link}
 static ControlTemplate? template;
 static ControlTemplate ButtonTemplate()=>template??=(ControlTemplate)XamlReader.Parse("""
<ControlTemplate TargetType="Button" xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
 <Border x:Name="box" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="8" SnapsToDevicePixels="True">
  <Grid><Border x:Name="hover" CornerRadius="7" Background="#1A1C1D2B" Opacity="0"/><ContentPresenter Margin="{TemplateBinding Padding}" HorizontalAlignment="Center" VerticalAlignment="Center"/></Grid>
 </Border>
 <ControlTemplate.Triggers>
  <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="hover" Property="Opacity" Value="0.6"/></Trigger>
  <Trigger Property="IsPressed" Value="True"><Setter TargetName="hover" Property="Opacity" Value="1"/></Trigger>
  <Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="0.45"/></Trigger>
 </ControlTemplate.Triggers>
</ControlTemplate>
""");
 Button Base(ButtonKind kind,Func<Task> action){
  var b=new Button{Template=ButtonTemplate(),Focusable=false,Cursor=Cursors.Hand,FontFamily=Palette.Font,FontSize=13,BorderThickness=new Thickness(0),Background=Brushes.Transparent,Foreground=Palette.Text2,Padding=new Thickness(9,6,9,6),VerticalAlignment=VerticalAlignment.Center};
  switch(kind){
   case ButtonKind.Primary:b.Background=Palette.Brand;b.Foreground=Palette.OnDark;b.Padding=new Thickness(12,6,12,6);b.FontWeight=FontWeights.SemiBold;break;
   case ButtonKind.Outline:b.Background=Palette.Surface;b.Foreground=Palette.Text;b.BorderBrush=Palette.LineStrong;b.BorderThickness=new Thickness(1);b.Padding=new Thickness(11,5,11,5);b.FontWeight=FontWeights.SemiBold;break;
   case ButtonKind.OnDark:b.Background=Brushes.Transparent;b.Foreground=Palette.OnDark;b.BorderBrush=Palette.OnDarkLine;b.BorderThickness=new Thickness(1);b.Padding=new Thickness(11,5,11,5);b.FontWeight=FontWeights.SemiBold;break;
   case ButtonKind.Icon:b.Padding=new Thickness(6);b.Width=30;b.Height=30;break;
   case ButtonKind.Link:b.Foreground=Palette.Brand;b.Padding=new Thickness(4,2,4,2);b.FontWeight=FontWeights.SemiBold;b.FontSize=12.5;break;
  }
  b.Click+=async(_,_)=>await Safe(action);return b;
 }
 Button TextButton(string icon,string text,ButtonKind kind,Func<Task> action){var b=Base(kind,action);b.Content=ButtonContent(icon,text);AutomationProperties.SetName(b,text);return b;}
 Button LinkButton(string text,Func<Task> action){var b=Base(ButtonKind.Link,action);b.Content=new TextBlock{Text=text};return b;}
 Button IconButton(string icon,string tip,Func<Task> action,double size=16){var b=Base(ButtonKind.Icon,action);b.Content=Glyph(icon,size);b.ToolTip=tip;AutomationProperties.SetName(b,tip);return b;}
 static void SetOn(Button b,bool on){b.Background=on?Palette.BrandSoft:Brushes.Transparent;b.Foreground=on?Palette.Brand:Palette.Text2;}
 static StackPanel ButtonContent(string icon,string text){var row=new StackPanel{Orientation=Orientation.Horizontal};var i=Glyph(icon,15);i.Margin=new Thickness(0,0,5,0);row.Children.Add(i);row.Children.Add(new TextBlock{Text=text,VerticalAlignment=VerticalAlignment.Center});return row;}
 static StackPanel Row(FrameworkElement a,FrameworkElement b){a.Margin=new Thickness(0,0,6,0);var row=new StackPanel{Orientation=Orientation.Horizontal};row.Children.Add(a);row.Children.Add(b);return row;}
 static TextBlock Label(string text)=>new(){Text=text,FontSize=12,Foreground=Palette.Text3,VerticalAlignment=VerticalAlignment.Center};

 // Line icons in the web icons.js style (24×24, stroke 2, round caps). Filled ones are dot glyphs.
 static readonly Dictionary<string,(string Data,bool Filled)> Icons=new(){
  ["grip"]=("M9 6.5 A1.5 1.5 0 1 0 9 6.49 Z M15 6.5 A1.5 1.5 0 1 0 15 6.49 Z M9 12 A1.5 1.5 0 1 0 9 11.99 Z M15 12 A1.5 1.5 0 1 0 15 11.99 Z M9 17.5 A1.5 1.5 0 1 0 9 17.49 Z M15 17.5 A1.5 1.5 0 1 0 15 17.49 Z",true),
  ["more"]=("M5 12 A1.6 1.6 0 1 0 5 11.99 Z M12 12 A1.6 1.6 0 1 0 12 11.99 Z M19 12 A1.6 1.6 0 1 0 19 11.99 Z",true),
  ["pin"]=("M9 4 H15 M10 4 V10 L7 14 H17 L14 10 V4 M12 14 V20",false),
  ["collapse"]=("M4 14 H10 V20 M20 10 H14 V4 M14 10 L21 3 M3 21 L10 14",false),
  ["expand"]=("M15 3 H21 V9 M9 21 H3 V15 M21 3 L14 10 M3 21 L10 14",false),
  ["hide"]=("M5 12 H19",false),
  ["stop"]=("M7 7 H17 V17 H7 Z",false),
  ["play"]=("M8 5 L19 12 L8 19 Z",false),
  ["camera"]=("M4 8 H8 L10 5 H14 L16 8 H20 V19 H4 Z M12 10.5 A3 3 0 1 0 12 16.5 A3 3 0 1 0 12 10.5 Z",false),
  ["edit"]=("M4 20 H8 L19 9 L15 5 L4 16 Z M13 7 L17 11",false),
  ["shield"]=("M12 3 L19 6 V11 C19 16 15.5 19.5 12 21 C8.5 19.5 5 16 5 11 V6 Z",false),
  ["shieldOk"]=("M12 3 L19 6 V11 C19 16 15.5 19.5 12 21 C8.5 19.5 5 16 5 11 V6 Z M9 12 L11 14 L15 10",false),
  ["zoom"]=("M10.5 4 A6.5 6.5 0 1 0 10.5 17 A6.5 6.5 0 1 0 10.5 4 Z M15.5 15.5 L20 20 M8 10.5 H13 M10.5 8 V13",false),
  ["check"]=("M5 12.5 L10 17 L19 7",false),
  ["alert"]=("M12 4 L21 19 H3 Z M12 10 V14 M12 16.5 V17",false),
  ["scan"]=("M4 8 V5 H7 M17 5 H20 V8 M20 16 V19 H17 M7 19 H4 V16 M8 9 V15 M11 9 V15 M14 9 V15 M16.5 9 V15",false),
  ["copy"]=("M9 9 H20 V20 H9 Z M5 15 H4 V4 H15 V5",false),
  ["cameraOff"]=("M4 8 H8 L10 5 H14 L16 8 H20 V19 H4 Z M12 10.5 A3 3 0 1 0 12 16.5 A3 3 0 1 0 12 10.5 Z M3 3 L21 21",false),
 };
 internal static IEnumerable<string> IconNames=>Icons.Keys;
 // A ContentControl whose Foreground is inherited from the enclosing button (or is given), so icons recolor with state.
 internal static FrameworkElement Glyph(string name,double size,Brush? brush=null){
  var (data,filled)=Icons[name];var path=new Path{Data=Geometry.Parse(data),StrokeThickness=filled?0:2,StrokeLineJoin=PenLineJoin.Round,StrokeStartLineCap=PenLineCap.Round,StrokeEndLineCap=PenLineCap.Round};
  var host=new ContentControl{Width=size,Height=size,Focusable=false,IsTabStop=false,VerticalAlignment=VerticalAlignment.Center};
  path.SetBinding(filled?Shape.FillProperty:Shape.StrokeProperty,new Binding(nameof(Control.Foreground)){Source=host});
  if(brush!=null)host.Foreground=brush;
  host.Content=new Viewbox{Child=new Canvas{Width=24,Height=24,Children={path}}};return host;
 }
}
