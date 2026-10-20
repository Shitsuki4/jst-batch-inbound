using System.Diagnostics;
using System.Text.Json;
using Receiving.Native;
using NetMQ;
using NetMQ.Sockets;
using System.Text.RegularExpressions;

// A deterministic fake video process: no device, audio, network or business data.
if(Environment.GetEnvironmentVariable("SYNTH_FFMPEG") is string ffmpeg&&args.Contains("-i")&&!args.Contains("-list_options"))return await SyntheticChecks.Worker(ffmpeg,args);
if(args.Contains("--settings-export")){await File.WriteAllTextAsync(args[Array.IndexOf(args,"--settings-export")+1],JsonSerializer.Serialize(new UserSettings(),UserSettings.WebJson));return 0;}
if(args.Contains("-list_options")){Console.Error.WriteLine("vcodec=mjpeg min s=1280x720 fps=15 max s=1280x720 fps=30\npixel_format=yuyv422 min s=1280x720 fps=15 max s=1280x720 fps=30");return 1;}
if(args.Contains("-show_entries")) { Console.WriteLine("{\"format\":{\"duration\":\"1.0\"},\"streams\":[{\"codec_type\":\"video\"}]}");return 0; }
if(args.Contains("-i")) {
 var mode=args[Array.IndexOf(args,"-i")+1];
 var output=args[Array.IndexOf(args,"mp4")+1];
 await File.WriteAllTextAsync(output+".args.json",JsonSerializer.Serialize(args));
 await File.WriteAllBytesAsync(output,new byte[1024]);
 var quit=Task.Run(async()=>{while(await Console.In.ReadLineAsync() is string s)if(s=="q")return;});
 using var stopControl=new CancellationTokenSource();
 var endpoint="tcp://127.0.0.1:"+Regex.Match(args[Array.IndexOf(args,"-filter_complex")+1],@"127\.0\.0\.1\\:(\d+)").Groups[1].Value;
 var control=Task.Run(()=>{using var socket=new ResponseSocket();socket.Options.Linger=TimeSpan.Zero;socket.Bind(endpoint);while(!stopControl.IsCancellationRequested&&!quit.IsCompleted){if(socket.TryReceiveFrameString(TimeSpan.FromMilliseconds(100),out _))socket.SendFrame("0 Success");}});
 try{
 // Let watchdog run before the first frame, like a slower USB camera.
 await Task.WhenAny(Task.Delay(1600),quit);
 if(mode.Contains("startup-error")){Console.Error.WriteLine("FAKE: cannot open camera - device busy");return 7;}
 if(quit.IsCompleted)return 0;
 Console.Error.WriteLine("  Stream #0:0: Video: mjpeg, yuvj422p, 1280x720, 30 fps, 30 tbr");
 if(mode=="video=no-first-frame"){await quit;return 0;}
 using var stdout=Console.OpenStandardOutput();var frame=new byte[480*270*3];var clock=Stopwatch.StartNew();
 while(!quit.IsCompleted){
  if(mode.Contains("worker-exit")&&clock.Elapsed.TotalSeconds>1){Console.Error.WriteLine("FAKE: camera read I/O error after first frame");return 9;}
  if(mode!="video=stalled"||clock.Elapsed.TotalSeconds<.5){await stdout.WriteAsync(frame);await stdout.FlushAsync();}
  if(!mode.Contains("disk-stalled")){using var file=new FileStream(output,FileMode.Append,FileAccess.Write,FileShare.ReadWrite);await file.WriteAsync(new byte[128]);}
  await Task.WhenAny(Task.Delay(100),quit);
 }
 return 0;
 }finally{stopControl.Cancel();await control;}
}
var dir=Path.GetFullPath(args[Array.IndexOf(args,"--output")+1]);Directory.CreateDirectory(dir);
var baseline=args.Contains("--baseline");var checks=new List<object>();var path=Environment.ProcessPath!;
void Check(bool condition,string message,object? detail=null){if(!condition)throw new Exception(message);checks.Add(new{name=message,detail});}
async Task Pump(Recorder rec,int seconds){var sw=Stopwatch.StartNew();while(sw.Elapsed.TotalSeconds<seconds){await rec.HealthAsync();await Task.Delay(100);}}
Recorder New(string name)=>new(){Engine=path,Probe=path,Camera=name,DirectoryPath=Path.Combine(dir,name),MinFreeBytes=0,RequireDiskConfiguration=false,CaptureFormat="mjpeg"};
try{
 if(args.Contains("--synthetic")){
  foreach(var check in await SyntheticChecks.Run(dir,Environment.GetEnvironmentVariable("SYNTH_FFMPEG")!,Environment.GetEnvironmentVariable("SYNTH_FFPROBE")!))Check(true,check);
  await File.WriteAllTextAsync(Path.Combine(dir,"result.json"),JsonSerializer.Serialize(new{ok=true,scope="Synthetic lavfi only; production Recorder, no device or business data",checks},Recorder.JsonOptions));
  Console.WriteLine(JsonSerializer.Serialize(new{ok=true,checks},Recorder.JsonOptions));return 0;
 }
 if(!baseline) foreach(var check in CoreChecks.Run(Path.Combine(dir,"unit"))) Check(true,check);
 if(!baseline) foreach(var check in LibraryDeleteChecks.Run(Path.Combine(dir,"library-delete"))) Check(true,check);
 await using(var r=New("delayed")){
  var start=r.StartAsync("TEST-DELAYED","isolated");
  var pump=Pump(r,4);await Task.WhenAll(start,pump);
  var alive=r.Active&&r.Ready;
  if(baseline){Check(!alive,"旧版启动时健康检查误停已复现",new{r.Status,r.Current?.Reason,r.Current?.State});}
  else{
   Check(alive,"延迟首帧与并发健康检查不误停");
   var captureArgs=JsonSerializer.Deserialize<string[]>(await File.ReadAllTextAsync(r.Current!.File+".args.json"))!;
   var timestampOption=Array.IndexOf(captureArgs,"-use_video_device_timestamps");
   Check(timestampOption>=0&&captureArgs[timestampOption+1]=="1","保留DirectShow设备时间戳，不用读取时刻覆盖采集节奏");
   Check(!captureArgs.Contains("-use_wallclock_as_timestamps"),"禁止重新启用造成帧间隔抖动的wallclock覆盖");
   var captureFilter=captureArgs[Array.IndexOf(captureArgs,"-filter_complex")+1];
   Check(!captureArgs.Contains("-r")&&!captureArgs.Contains("-fps_mode")&&!captureFilter.Contains("setpts")&&!captureFilter.Split("[p]scale")[0].Contains("fps="),"录像分支不强制帧率/补帧/改写PTS；5fps只用于预览");
   var id=r.Current!.Id;await r.StartAsync("TEST-DELAYED","isolated");Check(r.Current.Id==id,"重复当前物流不切段");
   var next=r.StartAsync("TEST-NEXT","isolated");await Task.WhenAll(next,Pump(r,4));Check(r.Ready&&r.Current.Id!=id,"换单启动期间并发健康检查不误停");
   await r.StopAsync();Check(!r.Active&&r.Current!.State=="saved","正常结束保留已保存状态");
  }
 }
 if(!baseline){
  await using(var r=New("no-first-frame")){var start=r.StartAsync("TEST-NO-FRAME","isolated");var health=Pump(r,17);bool failed=false;try{await start;}catch(TimeoutException){failed=true;}await health;Check(failed&&!r.Active&&r.Current!.State=="interrupted","15秒首帧超时保护保留且与健康检查不死锁");}
  await using(var r=New("manual-stop-during-start")){var start=r.StartAsync("TEST-EARLY-STOP","isolated");await Task.Delay(100);var stop=r.StopAsync("用户主动取消测试");await Task.WhenAll(start,stop,Pump(r,4));Check(!r.Active&&r.Current!.State=="saved"&&r.Current.Reason=="用户主动取消测试","启动期间主动停止仍执行且不与健康检查死锁");}
  await using(var r=New("worker-exit")){await r.StartAsync("TEST-EXIT","isolated");await Pump(r,4);Check(!r.Active&&r.Current!.State=="interrupted"&&r.Current.ExitCode==9&&r.Diagnostics.Contains("FAKE: camera read I/O error"),"真正进程异常不伪装成功，保留退出码和诊断",new{r.Status,r.Current?.Reason});}
  await using(var r=New("stalled")){await r.StartAsync("TEST-STALL","isolated");await Pump(r,10);Check(!r.Active&&r.Current!.State=="interrupted"&&r.Status.Contains("8秒"),"超过8秒无帧仍触发保护且标记中断",r.Status);}
  await using(var r=New("disk-stalled")){await r.StartAsync("TEST-DISK","isolated");await Pump(r,17);Check(!r.Active&&r.Current!.State=="interrupted"&&r.Status.Contains("15秒"),"超过15秒文件不增长仍触发保护且标记中断",r.Status);}
  await using(var r=New("startup-error")){bool failed=false;try{await r.StartAsync("TEST-BUSY","isolated");}catch{failed=true;}Check(failed&&!r.Active,"启动失败可见且释放进程");}
 }
 await File.WriteAllTextAsync(Path.Combine(dir,"result.json"),JsonSerializer.Serialize(new{ok=true,baseline,checks},Recorder.JsonOptions));Console.WriteLine(JsonSerializer.Serialize(new{ok=true,baseline,checks},Recorder.JsonOptions));return 0;
}catch(Exception ex){await File.WriteAllTextAsync(Path.Combine(dir,"result.json"),JsonSerializer.Serialize(new{ok=false,baseline,checks,error=ex.ToString()},Recorder.JsonOptions));Console.Error.WriteLine(ex);return 1;}
