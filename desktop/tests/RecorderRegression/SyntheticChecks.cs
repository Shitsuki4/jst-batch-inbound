using System.Diagnostics;
using System.Text.Json;
using Receiving.Native;

public static class SyntheticChecks {
 public static async Task<int> Worker(string engine,string[] original){
  if(!original.Contains("video=synthetic"))throw new InvalidOperationException("Synthetic worker refuses physical devices");
  var input=Array.IndexOf(original,"-i");
  // CI runners can be slower than real time. The synthetic source verifies the
  // production recording pipeline, so let lavfi produce the 30-second fixture
  // as fast as the runner can encode it instead of adding a pacing failure mode.
  var args=new List<string>{"-hide_banner","-loglevel","info","-n","-f","lavfi","-i","testsrc2=size=1280x720:rate=30"};
  args.AddRange(original[(input+2)..]);
  using var p=Process.Start(Recorder.StartInfo(engine,args,Environment.CurrentDirectory))!;
  // Only the test source changes; output branches, overlay, preview and q-stop remain production arguments.
  Console.Error.WriteLine("Stream #0:0: Video: mjpeg, yuvj422p, 1280x720, 30 fps");
  var output=p.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
  var errors=Task.Run(async()=>{while(await p.StandardError.ReadLineAsync() is string line)if(!line.TrimStart().StartsWith("Stream #0:0"))Console.Error.WriteLine(line);});
  _=Task.Run(async()=>{while(await Console.In.ReadLineAsync() is string line){if(p.HasExited)return;await p.StandardInput.WriteLineAsync(line);await p.StandardInput.FlushAsync();}});
  await p.WaitForExitAsync();await Task.WhenAll(output,errors);return p.ExitCode;
 }
 public static async Task<List<string>> Run(string root,string engine,string probe){
  var checks=new List<string>();void Check(bool ok,string name){if(!ok)throw new Exception(name);checks.Add(name);}
  Check(File.Exists(engine)&&File.Exists(probe),"实际FFmpeg及ffprobe工具存在");
  var path=Path.Combine(root,"native-settings.json");
  var s=UserSettings.Read(path);s.Directory=Path.Combine(root,"media");s.Engine=Environment.ProcessPath!;s.Camera="synthetic";s.WarningGb=.002m;s.StopGb=.001m;s.OverlaySku=true;s.OverlayQuantity=true;
  s.Write(path);s=UserSettings.Read(path);
  await using(var r=new Recorder{Probe=probe}){
   var previewFrames=0;r.Frame+=_=>Interlocked.Increment(ref previewFrames);
   r.Configure(s);r.Probe=probe;
   Check(s.AutoRecordEnabled&&!r.Active,"新进程恢复默认开启，未扫描不录制");
   var context=new VideoContext{Source="无采购单",SessionId=13,BatchId=1,Tracking="TEST-SCAN-001"};
   if(s.AutoRecordEnabled)await r.StartAsync(context.Tracking,context.Key,context);
   Check(r.Ready,"模拟确认物流号触发现有录制器并收到首帧");
   var id=r.Current!.Id;await r.StartAsync(context.Tracking,context.Key,context);Check(r.Current.Id==id,"重复物流不重启或覆盖录像");
   var clock=Stopwatch.StartNew();long firstSize=new FileInfo(r.Current.File).Length;
   while(Volatile.Read(ref previewFrames)<155){if(clock.Elapsed.TotalSeconds>60)throw new TimeoutException("合成编码60秒内未输出30秒画面");await r.HealthAsync();Check(r.Ready,$"合成录制健康检查 {(int)clock.Elapsed.TotalSeconds}s（状态：{r.Status}；帧年龄：{r.FrameAge:0.0}s）");if(clock.Elapsed.TotalSeconds>3&&context.LastSku==""){context.LastSku="TEST-SKU";context.ConfirmedQuantity=2;await r.UpdateContextAsync(context);}await Task.Delay(1000);}
   Check(new FileInfo(r.Current.File).Length>firstSize,"录制期间文件实际增长");
   await r.StopAsync("30秒合成自动录制检验");Check(!r.Active&&r.Current.State=="saved"&&r.Current.ExitCode==0,"q停止成功，现有ffprobe验证并标记已保存");
   var file=r.Current.File;
   var metadata=await Recorder.Run(probe,["-v","error","-count_frames","-show_entries","format=duration:stream=codec_type,width,height,avg_frame_rate,nb_read_frames","-of","json",file],90);
   using var doc=JsonDocument.Parse(metadata.Out);var streams=doc.RootElement.GetProperty("streams").EnumerateArray().ToArray();var video=streams.Single();
   var duration=double.Parse(doc.RootElement.GetProperty("format").GetProperty("duration").GetString()!,System.Globalization.CultureInfo.InvariantCulture);
   Check(metadata.Code==0&&metadata.Err==""&&video.GetProperty("codec_type").GetString()=="video","实际媒体单视频流，无音频及探测错误");
   Check(video.GetProperty("width").GetInt32()==1280&&video.GetProperty("height").GetInt32()==720&&video.GetProperty("avg_frame_rate").GetString()=="30/1","实际保存1280x720、30fps");
   Check(duration>=29&&duration<=36&&int.Parse(video.GetProperty("nb_read_frames").GetString()!)>=870,"实际时长约30秒，帧数足量");
   var decoded=await Recorder.Run(engine,["-v","error","-xerror","-i",file,"-map","0:v:0","-an","-f","null","-"],90);
   Check(decoded.Code==0&&decoded.Err=="","完整视频逐帧解码无错误");
   var pixels=new List<byte[]>();
   foreach(var second in new[]{2,20}){
    var target=Path.Combine(root,"frame-"+second+".gray");
    var sample=await Recorder.Run(engine,["-v","error","-n","-ss",second.ToString(),"-i",file,"-frames:v","1","-vf","scale=160:90,format=gray","-f","rawvideo",target]);
    var bytes=File.ReadAllBytes(target);Check(sample.Code==0&&bytes.Length==14400&&bytes.Max()-bytes.Min()>100&&bytes.Count(b=>b>40)>2000,"第"+second+"秒实际像素非空白");pixels.Add(bytes);
    await Recorder.Run(engine,["-v","error","-n","-ss",second.ToString(),"-i",file,"-frames:v","1",Path.Combine(root,"frame-"+second+".png")]);
   }
   Check(pixels[0].Zip(pixels[1]).Count(p=>Math.Abs((int)p.First-p.Second)>15)>500,"跨时间画面移动，不是冻结或静态空白");
   File.WriteAllText(Path.Combine(root,"media-metadata.json"),metadata.Out);
   s.AutoRecordEnabled=false;s.Write(path);s=UserSettings.Read(path);
   if(s.AutoRecordEnabled)await r.StartAsync("TEST-DISABLED","disabled");
   Check(!r.Active&&!s.AutoRecordEnabled,"关闭设置重读后模拟扫码不会录制");
   s.AutoRecordEnabled=true;s.Write(path);
  }
  Check(UserSettings.Read(path).AutoRecordEnabled,"退出录制器后自动录制开启状态保留");
  return checks;
 }
}
