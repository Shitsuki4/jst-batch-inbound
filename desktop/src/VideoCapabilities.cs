using System.Text.RegularExpressions;
namespace Receiving.Native;

public sealed record VideoCapabilityState(bool Ready, string[] Problems) {
 public string Detail => string.Join("\n", Problems);
}

// Informational commands only: no input device, recording, network or installer.
// The caller owns process timeouts/cleanup. Injectable runner keeps rejection
// cases testable without replacing installed binaries or opening a camera.
public static class VideoCapabilities {
 public static async Task<VideoCapabilityState> Check(string engine, string probe,
  Func<string,string[],Task<(int Code,string Out,string Err)>> run) {
  var problems=new List<string>();
  async Task<string?> Query(string exe,string label,params string[] args) {
   try {
    var r=await run(exe,args);
    if(r.Code!=0){problems.Add($"{label}检查失败（退出码 {r.Code}），请修复录像组件。");return null;}
    return r.Out+"\n"+r.Err;
   } catch(Exception ex) {problems.Add($"{label}检查失败：{ex.Message}");return null;}
  }
  var version=await Query(engine,"FFmpeg版本","-hide_banner","-version");
  if(version!=null&&!Regex.IsMatch(version,@"(?m)^ffmpeg version\s+\S+"))problems.Add("所选程序未返回有效的 FFmpeg 版本信息。");
  version=await Query(probe,"ffprobe版本","-hide_banner","-version");
  if(version!=null&&!Regex.IsMatch(version,@"(?m)^ffprobe version\s+\S+"))problems.Add("配套程序未返回有效的 ffprobe 版本信息。");
  var encoders=await Query(engine,"视频编码器","-hide_banner","-encoders");
  if(encoders!=null&&!Regex.IsMatch(encoders,@"(?m)^\s*V[A-Z.]{5}\s+libx264(?:\s|$)"))problems.Add("FFmpeg 缺少 libx264 视频编码器。");
  var filters=await Query(engine,"视频滤镜","-hide_banner","-filters");
  if(filters!=null)foreach(var name in new[]{"drawtext","zmq"})
   if(!Regex.IsMatch(filters,@"(?m)^\s*[A-Z.]{2,3}\s+"+name+@"\s+V->V(?:\s|$)"))problems.Add($"FFmpeg 缺少 {name} 视频滤镜（六项动态叠字必需）。");
  var devices=await Query(engine,"视频输入组件","-hide_banner","-devices");
  if(devices!=null&&!Regex.IsMatch(devices,@"(?m)^\s*D[E.]?\s+dshow(?:\s|$)"))problems.Add("FFmpeg 缺少 dshow 视频输入能力。");
  var textHelp=await Query(engine,"动态叠字命令","-hide_banner","-h","filter=drawtext");
  if(textHelp!=null&&!Regex.IsMatch(textHelp,@"(?m)^\s*text\s+<string>\s+[A-Z.]*T[A-Z.]*(?:\s|$)"))
   problems.Add("FFmpeg 的 drawtext 不支持运行时 text 更新，请使用支持动态叠字的完整版本。");
  return new(problems.Count==0,problems.ToArray());
 }
}
