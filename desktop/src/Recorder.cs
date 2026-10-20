using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
namespace Receiving.Native;

public sealed class Recorder : IAsyncDisposable {
 readonly SemaphoreSlim gate=new(1); readonly WorkerJob job=new();
 Process? proc;Task? frames,errors;VideoSegment? segment;bool stopping;long lastFrame,lastBytes,lastGrowth,lastOverlay;
 string lastError="",watermarkPath="",overlayEndpoint="";VideoContext liveContext=new();UserSettings options=new();
 readonly object errorLock=new();readonly Queue<string> recentErrors=new();
 public event Action<byte[]>? Frame;public event Action? Changed;public event Action<string>? Cue;
 public string Engine{get;set;}="ffmpeg.exe";public string Probe{get;set;}="ffprobe.exe";public string Camera{get;set;}="HBS Camera";
 public string DirectoryPath{get;set;}="";public string Resolution{get;set;}="1280x720";public double FrameRate{get;set;}=30;public string CaptureFormat{get;set;}="auto";
 public bool RequireDiskConfiguration{get;set;}=true;
 public long MinFreeBytes{get;set;} public long WarningFreeBytes{get;set;}
 public Func<string,long> FreeSpace{get;set;}=p=>new DriveInfo(Path.GetPathRoot(Path.GetFullPath(p))!).AvailableFreeSpace;
 public string Warning{get;private set;}="";public string Status{get;private set;}="未录制";
 public VideoSegment? Current=>segment;public bool Active=>proc!=null;
 public bool Ready=>proc!=null&&!proc.HasExited&&!stopping&&segment?.State=="recording";
 public double FrameAge=>lastFrame==0?double.PositiveInfinity:Stopwatch.GetElapsedTime(lastFrame).TotalSeconds;
 public bool DiskConfigured=>!RequireDiskConfiguration||options.DiskConfigured;
 public UserSettings Options=>options.Copy();
 public static JsonSerializerOptions JsonOptions=ArchiveLayout.Json;
 public void Configure(UserSettings settings,bool requireDisk=true) {
  if(Active)throw new InvalidOperationException("录制中不能更改视频配置");
  options=settings.Copy();RequireDiskConfiguration=requireDisk;Camera=settings.Camera;Resolution=settings.Resolution;FrameRate=settings.FrameRate;CaptureFormat=settings.CaptureFormat;
  DirectoryPath=settings.Directory;Engine=settings.Engine;
  MinFreeBytes=settings.StopGb==null?0:(long)decimal.Ceiling(settings.StopGb.Value*1024*1024*1024);
  WarningFreeBytes=settings.WarningGb==null?0:(long)decimal.Ceiling(settings.WarningGb.Value*1024*1024*1024);Warning="";
 }
 public string Diagnostics {get {string tail;lock(errorLock)tail=string.Join(Environment.NewLine,recentErrors);return $"版本：3.0.3\n状态：{Status}\n提醒：{Warning}\n相机：{Camera}\n请求：{Resolution} / {FrameRate}fps / {CaptureFormat}\n实际：{segment?.EffectiveMode}\n磁盘阈值：{options.WarningGb?.ToString()??"未配置"} GB 预警 / {options.StopGb?.ToString()??"未配置"} GB 停止\n退出码：{segment?.ExitCode?.ToString()??"未退出/未知"}\n原因：{segment?.Reason}\n录像：{segment?.File}\n日志：{(segment==null?"尚未开始":segment.File+".log")}\n\n录像进程最近输出：\n{(string.IsNullOrWhiteSpace(tail)?"（无错误输出）":tail)}";}}
 string ErrorTail(){lock(errorLock)return string.Join(Environment.NewLine,recentErrors);}
 public static ProcessStartInfo StartInfo(string exe,IEnumerable<string> args,string? dir=null){var s=new ProcessStartInfo(exe){UseShellExecute=false,CreateNoWindow=true,RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardErrorEncoding=Encoding.UTF8};foreach(var a in args)s.ArgumentList.Add(a);if(dir!=null)s.WorkingDirectory=dir;return s;}
 public static async Task<(int Code,string Out,string Err)> Run(string exe,IEnumerable<string> args,int seconds=20){using var p=Process.Start(StartInfo(exe,args))??throw new Exception("进程启动失败");var o=p.StandardOutput.ReadToEndAsync();var e=p.StandardError.ReadToEndAsync();try{await p.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(seconds));}catch{if(!p.HasExited)p.Kill(true);throw;}return(p.ExitCode,await o,await e);}
 public async Task<List<string>> Cameras(){var r=await Run(Engine,["-hide_banner","-list_devices","true","-f","dshow","-i","dummy"]);return Regex.Matches(r.Err,"\"([^\"]+)\" \\(video\\)").Select(m=>m.Groups[1].Value).Distinct().ToList();}
 public async Task<List<CameraMode>> CameraModes(){var r=await Run(Engine,["-hide_banner","-list_options","true","-f","dshow","-i","video="+Camera]);return CameraMode.Parse(r.Err);}
 void Notify(string status){Status=status;Changed?.Invoke();}
 void Persist(){if(segment!=null)ArchiveLayout.Write(DirectoryPath,segment);}
 void Sound(string kind)=>Cue?.Invoke(kind);
 public Task StartAsync(string tracking,string key)=>StartAsync(tracking,key,null);
 public async Task StartAsync(string tracking,string key,VideoContext? context){await gate.WaitAsync();try{
  if(proc!=null&&segment?.BatchKey==key&&segment.Tracking==tracking&&!proc.HasExited){Sound("duplicate");return;}
  await StopCore("换物流单");options.ValidateTracking(tracking);
  if(!DiskConfigured)throw new InvalidOperationException("请先在设置 → 视频举证中填写磁盘预警和停止GB，满足预警 > 停止 > 0");
  Directory.CreateDirectory(ArchiveLayout.Checked(DirectoryPath,DirectoryPath,true));
  var available=FreeSpace(DirectoryPath);if(available<=MinFreeBytes)throw new IOException("磁盘剩余空间已达到停止阈值，未开始录像；请在设置中核对阈值或手动清理");
  liveContext=(context??VideoContext.For(tracking,key)).Copy();liveContext.Tracking=tracking;
  // Re-enumerate at every start, including explicitly selected formats.
  // A saved high-resolution request must never silently become a lower mode.
  var mode=CameraMode.Choose(await CameraModes(),Resolution,FrameRate,CaptureFormat);
  segment=ArchiveLayout.Reserve(DirectoryPath,liveContext,key,DateTimeOffset.Now);segment.EffectiveMode="等待核验输入流："+mode;Persist();
  var day=Path.GetDirectoryName(segment.File)!;var watermark="wm_"+segment.Id+".txt";watermarkPath=ArchiveLayout.Checked(DirectoryPath,Path.Combine(day,watermark));
  File.WriteAllText(watermarkPath,OverlayText(options,liveContext,DateTimeOffset.Now),new UTF8Encoding(false));lastOverlay=Stopwatch.GetTimestamp();
  overlayEndpoint=VideoOverlay.NewEndpoint();
  var transforms=new List<string>();
  if(options.Rotation==90)transforms.Add("transpose=1");else if(options.Rotation==270)transforms.Add("transpose=2");else if(options.Rotation==180){transforms.Add("hflip");transforms.Add("vflip");}
  if(options.Mirror)transforms.Add("hflip");
  var font=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Fonts),"msyh.ttc").Replace('\\','/').Replace(":","\\:");
  // The initial text file is immutable. Updates change the filter's in-memory
  // text with an acknowledged command, never a file being mmap/read by FFmpeg.
  transforms.Add($"drawtext=fontfile='{font}':textfile={watermark}:reload=0:expansion=none:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=6:x=12:y=12");
  transforms.Add(VideoOverlay.Filter(overlayEndpoint));
  var filter="[0:v]"+string.Join(',',transforms)+"[annotated];[annotated]split=2[v][p];[p]scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2,fps=5,format=bgr24[preview]";
  // Keep the device capture clock. Replacing it with packet-read wallclock time
  // turns buffered reads/scheduling jitter into duplicate PTS and dropped frames.
  // Do not mask genuine capture gaps with forced CFR, setpts, or frame synthesis.
  var args=new List<string>{"-hide_banner","-loglevel","info","-n","-f","dshow","-rtbufsize","256M","-thread_queue_size","512","-framerate",mode.Fps.ToString(System.Globalization.CultureInfo.InvariantCulture),"-video_size",$"{mode.Width}x{mode.Height}","-use_video_device_timestamps","1","-probesize","32","-analyzeduration","0","-fpsprobesize","0"};
  args.AddRange(mode.Format=="mjpeg"?new[]{"-vcodec","mjpeg"}:new[]{"-pixel_format",mode.Format});
  args.AddRange(new[]{"-i","video="+Camera,"-filter_complex_threads","2","-filter_complex",filter,"-map","[v]","-an","-c:v","libx264","-threads","2","-preset","veryfast","-crf",options.Quality.ToString(),"-g",((int)Math.Ceiling(Math.Max(15,mode.Fps))).ToString(System.Globalization.CultureInfo.InvariantCulture),"-pix_fmt","yuv420p","-flush_packets","1","-movflags","+frag_keyframe+empty_moov+default_base_moof","-f","mp4",Path.GetFileName(segment.File),"-map","[preview]","-an","-c:v","rawvideo","-threads","1","-f","rawvideo","pipe:1"});
  lastFrame=0;lastError="";lock(errorLock)recentErrors.Clear();stopping=false;
  var logFile=segment.File+".log";File.WriteAllText(logFile,$"开始：{DateTimeOffset.Now:O}\n相机：{Camera}\n请求：{Resolution}/{FrameRate}fps/{CaptureFormat}\n实际：{mode}\n",new UTF8Encoding(false));
  proc=Process.Start(StartInfo(Engine,args,day))??throw new Exception("录像进程未启动");var p=proc;job.Add(p);lastBytes=0;lastGrowth=Stopwatch.GetTimestamp();
  var ready=new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
  var inputMode=new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
  frames=Task.Run(async()=>{try{while(true){var data=new byte[480*270*3];await p.StandardOutput.BaseStream.ReadExactlyAsync(data);lastFrame=Stopwatch.GetTimestamp();ready.TrySetResult();Frame?.Invoke(data);}}catch(Exception ex)when(ex is IOException or EndOfStreamException or ObjectDisposedException){ready.TrySetException(new IOException("相机未输出画面："+lastError));}});
  errors=Task.Run(async()=>{try{while(await p.StandardError.ReadLineAsync() is string line){if(line.TrimStart().StartsWith("Stream #0:0",StringComparison.Ordinal)&&line.Contains("Video:"))inputMode.TrySetResult(line);lastError=line;lock(errorLock){recentErrors.Enqueue(line);while(recentErrors.Count>12)recentErrors.Dequeue();}try{await File.AppendAllTextAsync(logFile,line+Environment.NewLine);}catch{}}}catch(IOException){}});
  Notify("相机启动中…");await ready.Task.WaitAsync(TimeSpan.FromSeconds(15));if(p.HasExited)throw new IOException("录像进程提前退出");
  mode.VerifyInput(await inputMode.Task.WaitAsync(TimeSpan.FromSeconds(5)));segment.EffectiveMode=mode.ToString();
  segment.FirstFrameAt=DateTimeOffset.Now;segment.State="recording";Persist();Notify("正在录制 · "+mode);Sound("start");UpdateDiskWarning(available);
 }catch(Exception ex){
  if(proc!=null)await StopCore("启动失败："+ex.Message,true);
  else if(segment?.State=="starting"){segment.State="interrupted";segment.Reason="启动失败："+ex.Message;segment.EndAt=DateTimeOffset.Now;Persist();}
  Notify("未就绪："+ex.Message);Sound("error");CleanupOverlay();throw;
 }finally{gate.Release();}}
 public async Task UpdateContextAsync(VideoContext context){await gate.WaitAsync();try{if(segment!=null&&segment.BatchKey==context.Key&&File.Exists(ArchiveLayout.Checked(DirectoryPath,segment.File))){liveContext=context.Copy();segment.Context=liveContext.Copy();if(Active){try{await UpdateOverlayAsync();}catch(Exception ex){await StopCore("业务叠字更新失败："+ex.Message,true);Notify("录像已停：叠字更新失败，请复制录像诊断");throw;}}Persist();}}finally{gate.Release();}}
 public async Task RefreshMetadataAsync(string path){await gate.WaitAsync();try{
  if(Active)throw new InvalidOperationException("请先结束录像再刷新文件元数据");
  if(!File.Exists(ArchiveLayout.Checked(DirectoryPath,path))){if(segment?.File==path){segment=null;liveContext=new();Notify("录像文件已移除；未录制");}return;}
  var fresh=ArchiveLayout.Read(DirectoryPath,path+".json");if(segment!=null&&fresh.Id!=segment.Id)throw new IOException("录像标识不匹配，拒绝替换当前片段");
  segment=fresh;liveContext=fresh.Context.Copy();Changed?.Invoke();
 }finally{gate.Release();}}
 public async Task SetEvidenceAsync(string note,bool protect){await gate.WaitAsync();try{
  if(segment==null)throw new InvalidOperationException("尚无录像");
  if(!File.Exists(ArchiveLayout.Checked(DirectoryPath,segment.File)))throw new IOException("录像文件已移动或删除，请刷新索引");
  var fresh=ArchiveLayout.Read(DirectoryPath,segment.File+".json");if(fresh.Id!=segment.Id)throw new IOException("录像索引已变化，请刷新");
  if(note!=fresh.Note&&!options.NotesEnabled)throw new InvalidOperationException("请先在设置中开启备注功能");
  if(protect!=fresh.Protected&&!options.ProtectionEnabled)throw new InvalidOperationException("请先在设置中开启售后保护；关闭功能不会解除已有保护");
  if(note.Length>300||note.Contains('\0'))throw new ArgumentException("备注最多300字");segment.Note=note;segment.Protected=protect;Persist();Changed?.Invoke();
 }finally{gate.Release();}}
 public async Task<string> SavePhotoAsync(byte[] jpeg){await gate.WaitAsync();try{if(!options.PhotoEnabled)throw new InvalidOperationException("请先在设置中开启拍照举证");if(!Ready||FrameAge>3||segment==null)throw new InvalidOperationException("没有实时画面，不能保存过期截图");var path=ArchiveLayout.Checked(DirectoryPath,segment.File+"_"+DateTime.Now.ToString("HHmmssfff")+"_"+Guid.NewGuid().ToString("N")[..6]+".jpg");using(var f=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.None))await f.WriteAsync(jpeg);segment.Photos.Add(Path.GetFileName(path));Persist();return path;}finally{gate.Release();}}
 public async Task StopAsync(string reason="手动结束"){await gate.WaitAsync();try{await StopCore(reason);}finally{gate.Release();}}
 async Task StopCore(string reason,bool abnormal=false){
  if(proc==null)return;stopping=true;var p=proc;Notify("正在保存录像…");Sound("stop");var interrupted=p.HasExited||abnormal;
  try{
   try{if(!p.HasExited){await p.StandardInput.WriteLineAsync("q");await p.StandardInput.FlushAsync();await p.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(15));}}
   catch{interrupted=true;try{if(!p.HasExited)p.Kill(true);await p.WaitForExitAsync();}catch{}}
   try{if(frames!=null)await frames.WaitAsync(TimeSpan.FromSeconds(3));if(errors!=null)await errors.WaitAsync(TimeSpan.FromSeconds(3));}catch{interrupted=true;}
   segment!.ExitCode=p.HasExited?p.ExitCode:null;segment.ErrorDetail=ErrorTail();segment.EndAt=DateTimeOffset.Now;segment.Reason=reason;segment.State="interrupted";
   try{var r=await Run(Probe,["-v","error","-show_entries","format=duration:stream=codec_type","-of","json",segment.File]);using var j=JsonDocument.Parse(r.Out);var dur=j.RootElement.GetProperty("format").GetProperty("duration").GetString();segment.Duration=double.Parse(dur!,System.Globalization.CultureInfo.InvariantCulture);var streams=j.RootElement.GetProperty("streams").EnumerateArray().Select(s=>s.GetProperty("codec_type").GetString()).ToArray();if(r.Code==0&&p.ExitCode==0&&!interrupted&&segment.Duration>0&&double.IsFinite(segment.Duration)&&File.Exists(segment.File)&&new FileInfo(segment.File).Length>0&&streams.Contains("video")&&!streams.Contains("audio"))segment.State="saved";}
   catch{segment.Reason+="；文件完整性未验证";}
   Persist();Notify(segment.State=="saved"?"已保存；未录制":"录像中断/异常，检查文件");Sound(segment.State=="saved"?"saved":"error");
  }finally{p.Dispose();proc=null;stopping=false;CleanupOverlay();Changed?.Invoke();}
 }
 public static string OverlayText(UserSettings settings,VideoContext context,DateTimeOffset time){
  var lines=new List<string>();
  string Clean(string v)=>Regex.Replace(v??"","[\\x00-\\x1f]"," ");
  void Add(string label,string value){var text=label+Clean(value);if(text.Length>150)text=text[..150]+"…（完整信息见索引）";for(var start=0;start<text.Length;start+=60)lines.Add(text.Substring(start,Math.Min(60,text.Length-start)));}
  if(settings.OverlayTime)Add("时间：",time.ToString("yyyy-MM-dd HH:mm:ss zzz"));
  if(settings.OverlayTracking)Add("物流：",context.Tracking);
  if(settings.OverlayPurchase)Add("采购：",context.Purchases.Count==0?"无采购单":string.Join(" / ",context.Purchases.Select(p=>p.Po)));
  if(settings.OverlayOuter)Add("外部：",context.Purchases.Count==0?"未关联":string.Join(" / ",context.Purchases.Select(p=>string.IsNullOrWhiteSpace(p.Outer)?"无外部单号":p.Outer)));
  if(settings.OverlaySku)Add("最近商品：",string.IsNullOrEmpty(context.LastSku)?"尚未确认":context.LastSku);
  if(settings.OverlayQuantity)Add("确认数量：",context.ConfirmedQuantity?.ToString("0.########",System.Globalization.CultureInfo.InvariantCulture)??"尚未确认");
  return lines.Count==0?" ":string.Join('\n',lines);
 }
 async Task UpdateOverlayAsync(){if(watermarkPath=="")return;await VideoOverlay.SendAsync(overlayEndpoint,OverlayText(options,liveContext,DateTimeOffset.Now));lastOverlay=Stopwatch.GetTimestamp();}
 void CleanupOverlay(){overlayEndpoint="";if(watermarkPath=="")return;try{File.Delete(ArchiveLayout.Checked(DirectoryPath,watermarkPath));}catch{}watermarkPath="";}
 void UpdateDiskWarning(long available){var next=WarningFreeBytes>0&&available<=WarningFreeBytes?$"磁盘剩余 {available/1073741824.0:0.00} GB，低于预警阈值，请手动整理（不会自动删除）":"";var newly=Warning==""&&next!="";Warning=next;if(newly)Sound("error");}
 // Keep the preview.4 race fix: health never queues a decision made before first frame.
 public async Task HealthAsync(){
  if(!await gate.WaitAsync(0))return;
  try{
   if(proc==null||stopping||segment?.State!="recording")return;
   if(proc.HasExited){var code=proc.ExitCode;await StopCore("录像进程意外退出，退出码 "+code,true);Notify("录像异常：录像进程已退出（代码 "+code+"），请复制录像诊断");return;}
   if(FrameAge>8){await StopCore("已开始录像后连续8秒无新画面",true);Notify("录像异常：连续8秒无新画面，请检查相机并复制录像诊断");return;}
   var size=File.Exists(segment.File)?new FileInfo(segment.File).Length:0;
   if(size>lastBytes){lastBytes=size;lastGrowth=Stopwatch.GetTimestamp();}
   else if(Stopwatch.GetElapsedTime(lastGrowth).TotalSeconds>15){await StopCore("15秒录像文件未增长，请检查磁盘",true);Notify("录像异常：15秒文件未增长，请检查磁盘");return;}
   long available;
   try{available=FreeSpace(DirectoryPath);}catch(Exception ex){await StopCore("无法检查磁盘剩余空间："+ex.Message,true);Notify("录像已停：磁盘状态不可用，请检查目录");return;}
   if(available<=MinFreeBytes){await StopCore("磁盘剩余达到自定义停止阈值");Notify("已达到磁盘停止阈值；"+(segment.State=="saved"?"本段已保存，停止新录像":"本段异常，请核对文件"));Sound("error");return;}
   UpdateDiskWarning(available);
   if(lastOverlay==0||Stopwatch.GetElapsedTime(lastOverlay).TotalSeconds>=1){try{await UpdateOverlayAsync();}catch(Exception ex){await StopCore("业务叠字更新失败："+ex.Message,true);Notify("录像已停：叠字更新失败，请复制录像诊断");}}
  }finally{gate.Release();}
 }
 public void SimulateWorkerFailureForTest(){proc?.Kill(true);}
 public async ValueTask DisposeAsync(){await StopAsync("退出程序");job.Dispose();}
}

public sealed record CameraMode(string Format,int Width,int Height,double Fps){
 public override string ToString()=>$"{Width}x{Height} / {Format.ToUpperInvariant()} / {Fps}fps";
 public void VerifyInput(string line){
  var m=Regex.Match(line,@"Video:.*?\b(\d{1,5})x(\d{1,5})\b.*?\b([\d.]+) fps\b");
  if(!m.Success||int.Parse(m.Groups[1].Value)!=Width||int.Parse(m.Groups[2].Value)!=Height||!double.TryParse(m.Groups[3].Value,System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out var rate)||Math.Abs(rate-Fps)>0.005)
   throw new IOException("实际输入流与所选录像档位不一致，已停止，未假报就绪："+line);
 }
 public static List<CameraMode> Parse(string output){
  var result=new List<CameraMode>();
  foreach(Match m in Regex.Matches(output,@"(?:vcodec|pixel_format)=(\w+)\s+min s=(\d+)x(\d+) fps=([\d.]+) max s=(\d+)x(\d+) fps=([\d.]+)")){
   var format=m.Groups[1].Value;if(!UserSettings.ValidCaptureFormat(format))continue;
   if(!double.TryParse(m.Groups[4].Value,System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out var min)||!double.TryParse(m.Groups[7].Value,System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out var max)||min<=0||max<min)continue;
   if(!int.TryParse(m.Groups[2].Value,out var w1)||!int.TryParse(m.Groups[3].Value,out var h1)||!int.TryParse(m.Groups[5].Value,out var w2)||!int.TryParse(m.Groups[6].Value,out var h2))continue;
   // Only reported endpoint sizes; no made-up intermediate resolutions.
   // Preserve fractional/low-fps modes (e.g. 4K 7.5fps or 29.97fps).
   foreach(var (w,h) in new[]{(w1,h1),(w2,h2)})
    foreach(var fps in new[]{min,max,5,10,15,20,23.976,24,25,29.97,30,50,59.94,60,90,120,144,240})
     if(UserSettings.ValidSize(w,h)&&double.IsFinite(fps)&&fps>=1&&fps<=240&&fps>=min&&fps<=max)result.Add(new(format,w,h,fps));
  }
  return result.Distinct().OrderBy(m=>m.Width*m.Height).ThenBy(m=>m.Fps).ThenBy(m=>m.Format).ToList();
 }
 public static CameraMode Choose(IEnumerable<CameraMode> modes,string resolution,double fps,string format="auto"){
  var size=resolution.Split('x');var width=int.Parse(size[0]);var height=int.Parse(size[1]);
  var candidates=modes.Where(m=>m.Width==width&&m.Height==height&&Math.Abs(m.Fps-fps)<0.00001&&(format=="auto"||m.Format==format)).ToList();
  if(candidates.Count==0)throw new IOException("设备未报告所选分辨率、帧率与格式的组合，请重新检测兼容模式并选择；不会自动降档或假报就绪");
  return candidates.OrderBy(m=>m.Format=="mjpeg"?0:1).First();
 }
}
