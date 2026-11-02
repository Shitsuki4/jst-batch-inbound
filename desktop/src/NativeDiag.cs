using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;
namespace Receiving.Native;

// 桌面程序自己的诊断日志和带错误码的弹窗。
// 错误码登记在 backend/app/errors.py（errors_test.py 核对这里用到的每个码都已登记）。
// 日志写在数据目录 data\logs\native-diagnostic.jsonl，和本机服务的 diagnostic.jsonl 放在一起，
// 设置页「最近的报错」和诊断包都会带上。规则和 diaglog.py 一致：满 2 MB 换新文件，最多留 5 个旧文件；
// 密码、令牌、Cookie、签名不落盘，网址只留问号前的部分。
public sealed class CodedException:Exception{
 public string Code{get;}
 public CodedException(string code,string message,Exception? inner=null):base(NativeDiag.Format(code,message),inner){Code=code;}
}

public static class NativeDiag {
 public const long MaxBytes=2*1024*1024;
 public const int Backups=5;
 public const string FileName="native-diagnostic.jsonl";
 static readonly object Gate=new();
 static readonly Regex CodeRx=new(@"^\[(E-[A-Z]+-\d{4})\]\s*",RegexOptions.Compiled);
 static readonly (Regex rx,string rep)[] Secrets={
  (new Regex(@"(?i)\b(gh[pousr]_[A-Za-z0-9]{20,})",RegexOptions.Compiled),"***"),
  (new Regex(@"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}",RegexOptions.Compiled),"$1 ***"),
  (new Regex(@"(?i)((?:password|passwd|pwd|token|secret|cookie|session|sign|signature|u_lid|u_id|u_co_id|__viewstate|jt_?token|access_token|refresh_token|sms_code|code_key)\s*[=:]\s*)(""[^""]*""|'[^']*'|[^\s&;,""']+)",RegexOptions.Compiled),"$1***"),
  (new Regex(@"(https?://[^\s?#""']+)\?[^\s""']*",RegexOptions.Compiled),"$1?…"),
 };
 public static string? DirOverride;
 public static string LogDir=>DirOverride??Path.Combine(AppPaths.DataRoot,"data","logs");
 public static string LogPath=>Path.Combine(LogDir,FileName);

 public static string Redact(string? text){var s=text??"";foreach(var (rx,rep) in Secrets)s=rx.Replace(s,rep);return s;}
 // 报错文字前面带上错误码；已经带码的（例如本机服务返回的）不重复加
 public static string Format(string code,string? message){var m=(message??"").Trim();return CodeRx.IsMatch(m)?m:"["+code+"] "+m;}
 public static string CodeOf(Exception ex,string fallback){if(ex is CodedException c)return c.Code;var m=CodeRx.Match(ex.Message??"");return m.Success?m.Groups[1].Value:fallback;}
 public static string NewId()=>Guid.NewGuid().ToString("N")[..12];

 public static string Write(string level,string code,string op,string message,Exception? ex=null){
  var rid=NewId();
  try{
   var rec=new Dictionary<string,object?>{["ts"]=DateTimeOffset.Now.ToString("yyyy-MM-dd HH:mm:ss.fff zzz"),["level"]=level,["code"]=code,["op"]=op,["page"]="native",["rid"]=rid,["msg"]=Redact(message),["source"]="native",["version"]=typeof(NativeDiag).Assembly.GetName().Version?.ToString()};
   if(ex!=null){rec["exception"]=ex.GetType().FullName;rec["trace"]=Redact(ex.ToString());}
   var line=JsonSerializer.Serialize(rec)+"\n";
   lock(Gate){Directory.CreateDirectory(LogDir);Rotate();File.AppendAllText(LogPath,line,new UTF8Encoding(false));}
  }catch{}
  return rid;
 }
 static void Rotate(){
  var f=new FileInfo(LogPath);if(!f.Exists||f.Length<MaxBytes)return;
  var oldest=LogPath+"."+Backups;if(File.Exists(oldest))File.Delete(oldest);
  for(int i=Backups-1;i>=1;i--){var src=LogPath+"."+i;if(File.Exists(src))File.Move(src,LogPath+"."+(i+1));}
  File.Move(LogPath,LogPath+".1");
 }
 public static string Error(string code,string op,string message,Exception? ex=null)=>Write("ERROR",code,op,Format(code,message),ex);

 // 带错误码和日志编号的弹窗；先写日志再弹，弹窗失败也不丢记录
 public static MessageBoxResult Show(Window? owner,string code,string op,string message,string title="操作未完成",MessageBoxImage image=MessageBoxImage.Warning,Exception? ex=null){
  var text=Format(code,message);var rid=Error(code,op,text,ex);
  var body=text+"\n\n日志编号 "+rid+"（设置 → 诊断与日志 可导出诊断包）";
  if(UiTest.Active)return MessageBoxResult.OK;
  try{return owner!=null?MessageBox.Show(owner,body,title,MessageBoxButton.OK,image):MessageBox.Show(body,title,MessageBoxButton.OK,image);}catch{return MessageBoxResult.None;}
 }
}
