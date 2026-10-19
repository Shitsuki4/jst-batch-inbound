using System.Net;
using System.Net.Sockets;
using System.Net.Http;
using System.Diagnostics;
using System.Text.Json;
namespace Receiving.Native;
public sealed class Backend : IAsyncDisposable {
 const int PortInUseExitCode=75,MaxPortAttempts=5;
 Process? proc;readonly HttpClient client=new(){Timeout=TimeSpan.FromSeconds(20)}; public string Url{get;private set;}=""; public string Token{get;private set;}=Guid.NewGuid().ToString("N");
 public int? ProcessId=>proc?.Id;public bool Alive=>proc!=null&&!proc.HasExited;public long Cursor{get;set;}
 internal async Task AttachForAcceptance(string url,string token,int pid){if(!url.StartsWith("http://127.0.0.1:"))throw new Exception("仅允许本机测试fixture");Url=url;Token=token;proc=Process.GetProcessById(pid);client.DefaultRequestHeaders.Add("X-Native-Token",token);using var h=await Get("/api/native/health");Cursor=h.RootElement.GetProperty("last").GetInt64();}
 public async Task Start(string root,string data){
  var connection=Path.Combine(data,"native-connection.json");
  if(System.IO.File.Exists(connection)){
   try{using var saved=JsonDocument.Parse(await System.IO.File.ReadAllTextAsync(connection));var j=saved.RootElement;var candidate=Process.GetProcessById(j.GetProperty("pid").GetInt32());
    if(!candidate.HasExited){Url="http://127.0.0.1:"+j.GetProperty("port").GetInt32();Token=j.GetProperty("token").GetString()!;client.DefaultRequestHeaders.Remove("X-Native-Token");client.DefaultRequestHeaders.Add("X-Native-Token",Token);using var healthy=await Get("/api/native/health");Cursor=healthy.RootElement.GetProperty("last").GetInt64();proc=candidate;return;}
   }catch{}Token=Guid.NewGuid().ToString("N");
  }
  var exe=Path.Combine(root,"backend","Receiving.Service.exe");
  client.DefaultRequestHeaders.Remove("X-Native-Token");client.DefaultRequestHeaders.Add("X-Native-Token",Token);
  // The backend exits with PortInUseExitCode when it cannot bind the port we picked (another process grabbed it
  // between our probe and its bind). Pick a fresh port and relaunch instead of polling a port nobody will answer on.
  for(int attempt=1;;attempt++){
   var port=PickFreePort();Url=$"http://127.0.0.1:{port}";
   var p=new ProcessStartInfo(exe){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true,WorkingDirectory=Path.GetDirectoryName(exe)!};
   p.Environment["JST_DB_DIR"]=data;p.Environment["JST_NO_BROWSER"]="1";p.Environment["JST_NATIVE_PORT"]=port.ToString();p.Environment["JST_NATIVE_TOKEN"]=Token;
   proc=Process.Start(p)??throw new Exception("业务服务未启动");proc.OutputDataReceived+=(_,_)=>{};proc.ErrorDataReceived+=(_,_)=>{};proc.BeginOutputReadLine();proc.BeginErrorReadLine();
   var portBusy=false;
   for(int i=0;i<100;i++){
    if(proc.HasExited){
     if(proc.ExitCode==PortInUseExitCode){if(attempt<MaxPortAttempts){portBusy=true;break;}throw new Exception($"业务服务端口连续{MaxPortAttempts}次被占用，无法启动。请关闭占用本机端口的程序后重试；不要重复入库");}
     throw new Exception("业务服务启动失败（可能上次任务仍在安全收尾），请查看data/logs；不要重复入库");
    }
    try{using var doc=await Get("/api/native/health");Cursor=doc.RootElement.GetProperty("last").GetInt64();return;}catch{await Task.Delay(200);}
   }
   if(!portBusy)throw new TimeoutException("业务服务未就绪");
   proc.Dispose();proc=null;
  }
 }
 static int PickFreePort(){var listener=new TcpListener(IPAddress.Loopback,0);listener.Start();try{return ((IPEndPoint)listener.LocalEndpoint).Port;}finally{listener.Stop();}}
 public async Task<JsonDocument> Get(string route){var text=await client.GetStringAsync(Url+route);var d=JsonDocument.Parse(text);if(!d.RootElement.GetProperty("ok").GetBoolean())throw new Exception(d.RootElement.GetProperty("msg").GetString());return d;}
 public async ValueTask DisposeAsync(){if(Alive){try{await client.PostAsync(Url+"/api/native/shutdown",new StringContent("{}"));await proc!.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(20));}catch{ // Do not kill uncertain in-flight ERP write.
 try{System.IO.File.WriteAllText(Path.Combine(AppPaths.DataRoot,"service-exit-warning.txt"),"服务退出未确认。请核实原入库单，不要重复提交。");}catch{}
 }}client.Dispose();proc?.Dispose();}
}
