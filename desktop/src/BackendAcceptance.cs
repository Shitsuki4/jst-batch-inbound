using System.Text.Json;
using System.Diagnostics;
namespace Receiving.Native;
public static class BackendAcceptance {
 public static async Task Run(string[] args){var root=Path.GetFullPath(args[Array.IndexOf(args,"--root")+1]);var output=Path.GetFullPath(args[Array.IndexOf(args,"--output")+1]);Directory.CreateDirectory(output);var checks=new List<string>();var b=new Backend();var reuse=new Backend();Exception? error=null;try{
  await b.Start(root,Path.Combine(output,"isolated-data"));using var health=await b.Get("/api/native/health");checks.Add("打包后端启动及授权健康检查");
  using var events=await b.Get("/api/native/events?after=0");using var export=await b.Get("/api/native/export?mode=events");if(export.RootElement.GetProperty("count").GetInt32()!=0)throw new Exception("新数据不是空库");checks.Add("独立空库及三种业务导出初始化");
  await reuse.Start(root,Path.Combine(output,"isolated-data"));if(b.ProcessId!=reuse.ProcessId)throw new Exception("未复用同一服务");checks.Add("同目录握手复用、不重复启动服务");
  await b.DisposeAsync();for(int i=0;i<60&&reuse.Alive;i++)await Task.Delay(100);if(reuse.Alive)throw new Exception("安全退出未完成");checks.Add("安全关闭服务与HTTP端口释放");
  await reuse.DisposeAsync();var again=new Backend();await again.Start(root,Path.Combine(output,"isolated-data"));checks.Add("关闭后重新启动、释放数据锁");await again.DisposeAsync();
 }catch(Exception ex){error=ex;}finally{try{await reuse.DisposeAsync();}catch{}await File.WriteAllTextAsync(Path.Combine(output,"backend-acceptance.json"),JsonSerializer.Serialize(new{ok=error==null,checks,error=error?.ToString()},Recorder.JsonOptions));Environment.ExitCode=error==null?0:1;}}
}
