using System.Runtime.InteropServices;
using System.Windows.Interop;
namespace Receiving.Native;

// Register only the user's named combinations. No keyboard hook, raw input or scanner capture.
public sealed class VideoHotkeys : IDisposable {
 readonly IntPtr handle;readonly HwndSource source;readonly Action<string> invoke;int nextId=0x4100;
 Dictionary<string,Binding> active=new(StringComparer.OrdinalIgnoreCase);
 internal sealed record Binding(int Id,HotkeySpec Spec,string Action);
 [DllImport("user32.dll",SetLastError=true)]static extern bool RegisterHotKey(IntPtr window,int id,uint modifiers,uint key);
 [DllImport("user32.dll",SetLastError=true)]static extern bool UnregisterHotKey(IntPtr window,int id);
 public bool Registered=>active.Count>0;
 public string Status {get;private set;}="全局快捷键未启用";
 public VideoHotkeys(IntPtr window,Action<string> handler){handle=window;invoke=handler;source=HwndSource.FromHwnd(handle)??throw new InvalidOperationException("主窗口尚未就绪");source.AddHook(Hook);}
 IntPtr Hook(IntPtr hwnd,int msg,IntPtr w,IntPtr l,ref bool handled){if(msg==0x0312){var binding=active.Values.FirstOrDefault(b=>b.Id==w.ToInt32());if(binding!=null){handled=true;invoke(binding.Action);}}return IntPtr.Zero;}
 public Registration Prepare(UserSettings settings){
  var desired=new Dictionary<string,Binding>(StringComparer.OrdinalIgnoreCase);var added=new List<Binding>();
  try{
   if(settings.HotkeysEnabled)foreach(var (text,action) in new[]{(settings.HotkeyPreview,"togglePreview"),(settings.HotkeyStop,"stop"),(settings.HotkeyContinue,"continue")}){
    if(string.IsNullOrWhiteSpace(text))continue;var spec=HotkeySpec.Parse(text);
    if(desired.ContainsKey(spec.Text))throw new ArgumentException("两个动作使用相同全局快捷键");
    if(active.TryGetValue(spec.Text,out var old))desired.Add(spec.Text,old with{Action=action});
    else{
     if(nextId>=0xBFFF)throw new InvalidOperationException("快捷键配置次数过多，请重新启动软件");
     var binding=new Binding(nextId++,spec,action);
     if(!RegisterHotKey(handle,binding.Id,spec.Modifiers|0x4000,spec.Key))throw new InvalidOperationException($"快捷键 {spec.Text} 注册失败（冲突或Windows保留，错误 {Marshal.GetLastWin32Error()}）。旧快捷键和原设置保持不变，请换一个组合键。");
     added.Add(binding);desired.Add(spec.Text,binding);
    }
   }
   return new Registration(this,desired,added);
  }catch{foreach(var binding in added)UnregisterHotKey(handle,binding.Id);throw;}
 }
 public void ReportStartupFailure(string message){Status="全局快捷键未注册成功："+message;}
 public sealed class Registration : IDisposable {
  readonly VideoHotkeys owner;readonly Dictionary<string,Binding> desired;readonly List<Binding> added;bool finished;
  internal Registration(VideoHotkeys owner,Dictionary<string,Binding> desired,List<Binding> added){this.owner=owner;this.desired=desired;this.added=added;}
  public void Commit(){if(finished)throw new InvalidOperationException("快捷键事务已结束");foreach(var pair in owner.active)if(!desired.ContainsKey(pair.Key))UnregisterHotKey(owner.handle,pair.Value.Id);owner.active=desired;owner.Status=desired.Count==0?"全局快捷键已停用":"已注册："+string.Join("；",desired.Keys);finished=true;}
  public void Dispose(){if(!finished){foreach(var b in added)UnregisterHotKey(owner.handle,b.Id);finished=true;}}
 }
 public void Dispose(){foreach(var b in active.Values)UnregisterHotKey(handle,b.Id);active.Clear();source.RemoveHook(Hook);Status="全局快捷键已释放";}
}
