using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Web.WebView2.Core;
namespace Receiving.Native;

public record DependencyState(bool WebView, bool Video, string Engine, string Detail) { public bool Ready => WebView && Video; }
public static class Dependencies {
 public static string Resolve(string name, string preferred="") {
  var bundled=Path.Combine(AppContext.BaseDirectory,"runtime","ffmpeg",name);if(File.Exists(bundled))return bundled;
  if(AppPaths.Installed)return bundled;
  var shared=Path.Combine(AppPaths.SharedFfmpegFolder,name);if(File.Exists(shared))return shared;
  if(File.Exists(preferred))return Path.GetFullPath(preferred);
  var dirs=new List<string>{Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Microsoft","WinGet","Links")};
  foreach(var target in new[]{EnvironmentVariableTarget.Process,EnvironmentVariableTarget.User,EnvironmentVariableTarget.Machine})
   dirs.AddRange((Environment.GetEnvironmentVariable("PATH",target)??"").Split(';'));
  foreach(var dir in dirs)try{var p=Path.Combine(Environment.ExpandEnvironmentVariables(dir.Trim().Trim('"')),name);if(Path.IsPathFullyQualified(p)&&File.Exists(p))return p;}catch{}
  return name;
 }
 public static async Task<DependencyState> Check() {
  bool web=false;string detail="";try{web=!string.IsNullOrWhiteSpace(CoreWebView2Environment.GetAvailableBrowserVersionString(AppPaths.BrowserFolder));}catch(Exception e){detail="WebView2："+e.Message+"\n";}
  if(AppPaths.Installed&&AppPaths.BrowserFolder==null){web=false;detail+="内置WebView2目录缺失，请重新运行完整安装包修复。\n";}
  string preferred="";try{var p=Path.Combine(AppPaths.DataRoot,"native-settings.json");if(File.Exists(p))preferred=JsonSerializer.Deserialize<UserSettings>(File.ReadAllText(p))?.Engine??"";}catch{}
  string engine=Resolve("ffmpeg.exe",preferred),probe=Resolve("ffprobe.exe",Path.Combine(Path.GetDirectoryName(engine)??"","ffprobe.exe"));bool video=false;
  try{
   var capabilities=await VideoCapabilities.Check(engine,probe,(exe,args)=>Recorder.Run(exe,args,8));
   video=capabilities.Ready;
   if(!video)detail+=capabilities.Detail+"\n";
  }catch(Exception e){detail+="录像组件："+e.Message+"\n";}
  return new(web,video,engine,detail);
 }
 public static async Task<int> Install(string id,Action<string> log) {
  if(id is not ("Gyan.FFmpeg" or "Microsoft.EdgeWebView2Runtime"))throw new ArgumentException("不允许的组件");
  var args=new List<string>{"install","--id",id,"--exact","--source","winget","--accept-package-agreements","--accept-source-agreements","--disable-interactivity","--silent"};
  if(id=="Gyan.FFmpeg")args.AddRange(["--version","8.1.2"]);
  var info=Recorder.StartInfo(Resolve("winget.exe"),args);
  using var p=Process.Start(info)??throw new Exception("无法启动Windows包管理器");
  async Task Drain(StreamReader reader){while(await reader.ReadLineAsync() is string line)log(line);}
  await Task.WhenAll(Drain(p.StandardOutput),Drain(p.StandardError),p.WaitForExitAsync());return p.ExitCode;
 }
}

public sealed class DependencyWindow:Window {
 readonly TextBlock state=new(){TextWrapping=TextWrapping.Wrap,Margin=new Thickness(0,12,0,12)};
 readonly TextBox output=new(){IsReadOnly=true,AcceptsReturn=true,TextWrapping=TextWrapping.Wrap,VerticalScrollBarVisibility=ScrollBarVisibility.Auto,Height=230,FontFamily=Palette.Mono,FontSize=12};
 readonly Button install=new(){Content="一键安装缺少的依赖",Margin=new Thickness(0,0,8,0)},retry=new(){Content="重新检查",Margin=new Thickness(0,0,8,0)},enter=new Button{Content="进入扫码入库",Margin=new Thickness(0,0,8,0),IsEnabled=false}.Level(Theme.Primary);
 DependencyState? result;bool busy;public bool Accepted{get;private set;}
 public DependencyWindow(){Title="扫码入库 · 环境就绪检查";Theme.Dialog(this);Width=750;Height=560;WindowStartupLocation=WindowStartupLocation.CenterScreen;
  var panel=new StackPanel();Content=new Border{Margin=new Thickness(18),Child=Theme.Card(panel,22)};
  panel.Children.Add(new TextBlock{Text="自动检查 WebView2 与录像组件",FontSize=22,FontWeight=FontWeights.Bold,Foreground=Palette.Text});
  panel.Children.Add(new TextBlock{Text=AppPaths.Installed?"离线完整版：检查软件自带的浏览器与录像组件，无需下载或系统安装。若组件损坏，请重新运行完整安装包修复。业务数据与录像保留在独立目录。":"仅在缺失或不可用时安装，不修改收货数据库。断网电脑可先运行「运行依赖安装程序」（收货小助手-版本-运行依赖安装程序.exe），装好后点重新检查即可，无需联网。一键安装需要联网，可能显示Windows权限确认。点击一键安装即同意由Windows包管理器下载安装所缺组件及其许可条款（Microsoft WebView2 / Gyan FFmpeg）。",TextWrapping=TextWrapping.Wrap,Foreground=Palette.Text2,Margin=new Thickness(0,12,0,0)});
  panel.Children.Add(state);panel.Children.Add(output);var buttons=new WrapPanel{Margin=new Thickness(0,12,0,0)};panel.Children.Add(buttons);buttons.Children.Add(install);buttons.Children.Add(retry);buttons.Children.Add(enter);
  var store=new Button{Content="缺少winget？打开微软应用安装程序",Margin=new Thickness(0,0,8,0)}.Level(Theme.Ghost);if(!AppPaths.Installed)buttons.Children.Add(store);else install.Visibility=Visibility.Collapsed;store.Click+=(_,_)=>{try{Process.Start(new ProcessStartInfo("ms-windows-store://pdp/?ProductId=9NBLGGH4NNS1"){UseShellExecute=true});}catch(Exception ex){Log(ex.Message+"；请在Microsoft Store搜索‘应用安装程序’。");}};
  install.Click+=async(_,_)=>await Run(true);retry.Click+=async(_,_)=>await Run(false);enter.Click+=(_,_)=>{Accepted=true;Close();};Loaded+=async(_,_)=>await Run(false);
  Closing+=(_,e)=>{if(busy){e.Cancel=true;Log("正在检查或安装，请等待结束，不要中断系统安装进程。");}};
 }
 void Log(string value){Dispatcher.Invoke(()=>{output.AppendText(value+Environment.NewLine);output.ScrollToEnd();});}
 async Task Run(bool performInstall){if(busy)return;busy=true;install.IsEnabled=retry.IsEnabled=enter.IsEnabled=false;
  try{state.Text="正在检查，请稍候……";result=await Dependencies.Check();
   if(performInstall&&AppPaths.Installed)throw new IOException("离线完整版的内置组件缺失或损坏，请重新运行完整安装包修复，不需要联网安装系统依赖。");
   if(performInstall&&!result.Ready){
    foreach(var id in new[]{result.WebView?null:"Microsoft.EdgeWebView2Runtime",result.Video?null:"Gyan.FFmpeg"})if(id!=null){Log("开始安装："+id);int code=await Dependencies.Install(id,Log);Log("安装进程退出码："+code+"；最终以复检结果为准。");}
    result=await Dependencies.Check();
   }
   state.Text=$"WebView2：{(result.WebView?"可用":"缺少/不可用")}    FFmpeg + ffprobe：{(result.Video?"可用":"缺少/不可用")}\n"+(result.Ready?"环境检查通过，可以进入小助手。":(AppPaths.Installed?"请安装依赖后重新检查；无法联网时可手动安装，随后重试。":"请安装依赖后重新检查；断网电脑可先运行「运行依赖安装程序」，随后重试。"));
   if(!string.IsNullOrEmpty(result.Detail))Log(result.Detail);
   if(result.Ready){enter.IsEnabled=true;if(!performInstall){Accepted=true;busy=false;Close();}}
  }catch(Exception ex){state.Text="未完成环境配置，请按提示处理后重试。";Log(ex.Message+"\n若提示找不到winget，请安装/更新微软‘应用安装程序’，然后重新启动小助手。不要关闭系统安全防护。");}
  finally{busy=false;install.IsEnabled=result?.Ready!=true;retry.IsEnabled=true;enter.IsEnabled=result?.Ready==true;}
 }
}
