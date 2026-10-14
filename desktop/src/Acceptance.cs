using System.Text.Json;
using System.Diagnostics;
using System.Windows.Media;
using System.Windows.Media.Imaging;
namespace Receiving.Native;
public static class Acceptance {
 public static async Task Run(string[] args){
  var index=Array.IndexOf(args,"--output");var output=index>=0?args[index+1]:Path.Combine(AppContext.BaseDirectory,"acceptance");System.IO.Directory.CreateDirectory(output);
  var r=new Recorder{DirectoryPath=Path.Combine(output,"video")};var reports=new List<object>();var sw=Stopwatch.StartNew();var soakIndex=Array.IndexOf(args,"--soak-seconds");var seconds=soakIndex>=0?int.Parse(args[soakIndex+1]):60;
  void Record(string name,object detail)=>reports.Add(new{name,detail,at=DateTimeOffset.Now});
  byte[]? frame=null;r.Frame+=d=>Interlocked.Exchange(ref frame,d);
  try{
   var cams=await r.Cameras();if(!cams.Contains("HBS Camera"))throw new Exception("HBS Camera不存在");Record("camera-enumerated",cams);
   await r.StartAsync("TEST-A","TEST:1");var first=r.Current!.Id;for(int elapsed=0;elapsed<seconds;elapsed++){await Task.Delay(1000);await r.HealthAsync();if(!r.Active)throw new Exception("持续录像意外停止");}if(!r.Active||r.FrameAge>3)throw new Exception("连续录像停止或画面未更新");
   Record("continuous-recording",new{seconds,r.FrameAge,WorkingSetMB=Process.GetCurrentProcess().WorkingSet64/1024/1024});
   var b=Interlocked.Exchange(ref frame,null);if(b!=null){var png=new PngBitmapEncoder();png.Frames.Add(BitmapFrame.Create(BitmapSource.Create(480,270,96,96,PixelFormats.Bgr24,null,b,480*3)));using var f=System.IO.File.Create(Path.Combine(output,"camera-preview.png"));png.Save(f);}
   await r.StartAsync("TEST-A","TEST:1");if(r.Current!.Id!=first)throw new Exception("重复当前号不应切段");Record("same-tracking-no-cut",first);
   var cut=Stopwatch.StartNew();await r.StartAsync("TEST-B","TEST:2");Record("switch-A-B",new{seconds=cut.Elapsed.TotalSeconds,r.Current!.FirstFrameAt});await Task.Delay(10000);
   await r.StartAsync("TEST-A","TEST:1");if(r.Current!.Id==first)throw new Exception("回扫覆盖旧段");Record("return-A-new-file",r.Current.File);await Task.Delay(8000);await r.StopAsync("测试手动结束");
   if(r.Active||r.Current.State!="saved")throw new Exception("手动结束文件未通过验证");Record("manual-stop-valid-file",r.Current.Duration);
   await r.StartAsync("TEST-A","TEST:1");await Task.Delay(7000);await r.StopAsync("测试继续后结束");Record("manual-resume-new-segment",r.Current!.Id);
   r.Camera="DOES-NOT-EXIST-TEST";try{await r.StartAsync("TEST-INVALID","TEST:bad");throw new Exception("缺失相机应失败");}catch(Exception ex)when(ex.Message!="缺失相机应失败"){Record("missing-device-visible-failure",ex.Message);}r.Camera="HBS Camera";
   r.MinFreeBytes=long.MaxValue;try{await r.StartAsync("TEST-DISK","TEST:disk");throw new Exception("空间阈值未生效");}catch(Exception ex)when(ex.Message!="空间阈值未生效"){Record("low-space-guard-simulated-threshold",ex.Message);}r.MinFreeBytes=2L*1024*1024*1024;
   await r.StartAsync("TEST-WORKER-FAILURE","TEST:worker");await Task.Delay(7000);r.SimulateWorkerFailureForTest();await Task.Delay(1000);await r.HealthAsync();if(r.Active||r.Current!.State!="interrupted")throw new Exception("进程中断未标记");Record("worker-kill-isolation",r.Status);
   await r.StartAsync("TEST-RECOVERY","TEST:recovery");await Task.Delay(10000);await r.StopAsync("测试恢复完成");Record("camera-reopened-after-worker-failure",r.Current!.State);
   foreach(var f in System.IO.Directory.EnumerateFiles(r.DirectoryPath,"*.mp4.json",SearchOption.AllDirectories)){
    var s=JsonSerializer.Deserialize<VideoSegment>(await System.IO.File.ReadAllTextAsync(f))!;
    if(s.State!="saved")continue;var decoded=await Recorder.Run(r.Engine,["-v","error","-i",s.File,"-f","null","-"],90);if(decoded.Code!=0||!string.IsNullOrWhiteSpace(decoded.Err))throw new Exception("解码校验失败："+s.File+decoded.Err);
    Record("full-video-decode",new{s.File,s.Duration});
   }
   await System.IO.File.WriteAllTextAsync(Path.Combine(output,"camera-acceptance.json"),JsonSerializer.Serialize(new{ok=true,elapsedSeconds=sw.Elapsed.TotalSeconds,tests=reports,notTested=new[]{"48-72小时长稳","真实USB拔插","物理断电","真实ERP写入","PDA录像联动","无缝切段"}},Recorder.JsonOptions));
  }catch(Exception ex){await System.IO.File.WriteAllTextAsync(Path.Combine(output,"camera-acceptance.json"),JsonSerializer.Serialize(new{ok=false,error=ex.ToString(),tests=reports},Recorder.JsonOptions));throw;}
  finally{await r.DisposeAsync();}
 }
}
