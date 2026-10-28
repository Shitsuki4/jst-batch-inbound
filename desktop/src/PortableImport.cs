using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
namespace Receiving.Native;

// One-time copy of a portable folder's data into the installed-mode data root
// (%LOCALAPPDATA%\ReceivingAssistant\v3). Run by the slim upgrade installer
// (offline_setup.iss compiled with /DUpgradeOnly) on machines that only had the deps installer and
// ran the portable build, when the user picked their portable folder:
//   Receiving.Native.exe --import-portable <portable folder> --report <json file>
// Rules: copy only, never move; the portable folder is never modified. Refused (nothing written)
// when the installed data root already has data, when the portable copy is running, or when the
// folder does not look like a portable install with data. Everything is copied into a staging
// folder first and verified by SHA256 before it is moved into place.
// Exit codes: 0 imported, 2 refused (nothing changed), 3 failed (nothing changed).
public static class PortableImport {
 public sealed class Refused(string message):Exception(message);
 // restore.py DB_NAMES + operation_log.db: at least one must exist for the folder to count as data.
 static readonly string[] DbNames={"jst.db","catalog.db","unplanned.db","express.db","purchase_watch.db","joint_receipts.db","learned_codes.db","operation_log.db"};
 // Top-level entries of data\ never copied: the update download cache and the old process's runtime state.
 static readonly string[] SkipData={"updates","native-connection.json","native-connection.json.tmp","service-instance.json","restore-pending.tmp"};

 public static int Run(string[] args){
  string Arg(string name){var i=Array.IndexOf(args,name);return i>=0&&i+1<args.Length?args[i+1]:"";}
  var source=Arg("--import-portable");var report=Arg("--report");
  int code;object result;
  try{var r=Import(source,AppPaths.DataRoot,AppPaths.Installed);code=0;result=new{ok=true,message=r.Message,source=r.Source,files=r.Files,bytes=r.Bytes,recording_dir=r.RecordingDir};}
  catch(Refused e){code=2;result=new{ok=false,refused=true,message=e.Message};}
  catch(Exception e){code=3;result=new{ok=false,refused=false,message="复制便携版数据失败："+e.Message+"。便携版文件夹没有改动。"};}
  if(report!="")try{File.WriteAllText(report,JsonSerializer.Serialize(result),new UTF8Encoding(false));}catch{}
  return code;
 }

 public sealed record Result(string Source,int Files,long Bytes,string RecordingDir,string Message);

 public static Result Import(string source,string target,bool installed){
  if(!installed)throw new Refused("只有安装版可以导入便携版数据。");
  source=(source??"").Trim().Trim('"');
  if(source==""||!Path.IsPathFullyQualified(source))throw new Refused("请选择便携版所在的完整文件夹路径。");
  source=Path.TrimEndingDirectorySeparator(Path.GetFullPath(source));target=Path.TrimEndingDirectorySeparator(Path.GetFullPath(target));
  if(!Directory.Exists(source))throw new Refused("文件夹不存在："+source);
  if(!File.Exists(Path.Combine(source,"Receiving.Native.exe")))throw new Refused("这个文件夹里没有 Receiving.Native.exe，不是便携版文件夹："+source);
  if(File.Exists(Path.Combine(source,"installed-mode.txt")))throw new Refused("这是安装版的程序文件夹，不是便携版："+source);
  var data=Path.Combine(source,"data");
  if(!Directory.Exists(data)||!DbNames.Any(n=>File.Exists(Path.Combine(data,n))))throw new Refused("这个便携版文件夹里没有业务数据（data\\jst.db 等）："+source);
  if(Same(source,target)||Under(target,source)||Under(source,target))throw new Refused("便携版文件夹不能和安装版数据目录重叠。");
  if(Directory.Exists(Path.Combine(data,"restore-pending")))throw new Refused("便携版还有一次没做完的备份恢复。请先打开便携版让它完成恢复、正常退出，再导入。");
  if(IsRunning(source))throw new Refused("便携版收货小助手还在运行。请先正常退出它，再导入。");
  // Never merge into or overwrite installed-mode data.
  var targetData=Path.Combine(target,"data");
  if(File.Exists(Path.Combine(target,"native-settings.json"))||(Directory.Exists(targetData)&&Directory.EnumerateFileSystemEntries(targetData).Any()))
   throw new Refused("安装版数据目录里已经有数据（"+target+"），为避免覆盖没有导入。需要时可以在安装版「设置 → 备份与恢复 → 从文件恢复」里选择便携版的 data\\backups 下的备份文件夹。");

  Directory.CreateDirectory(target);
  var staging=Path.Combine(target,".portable-import-"+Guid.NewGuid().ToString("N"));
  try{
   int files=0;long bytes=0;
   foreach(var entry in new DirectoryInfo(data).EnumerateFileSystemInfos()){
    if(SkipData.Contains(entry.Name,StringComparer.OrdinalIgnoreCase))continue;
    if(entry.Attributes.HasFlag(FileAttributes.ReparsePoint))continue;
    if(entry is DirectoryInfo dir)CopyTree(dir.FullName,Path.Combine(staging,"data",entry.Name),ref files,ref bytes);
    else CopyVerified(entry.FullName,Path.Combine(staging,"data",entry.Name),ref files,ref bytes);
   }
   var warning=Path.Combine(source,"service-exit-warning.txt");
   if(File.Exists(warning))CopyVerified(warning,Path.Combine(staging,"service-exit-warning.txt"),ref files,ref bytes);
   // Recordings stay where they are (they can be many GB). A portable copy with the default
   // recording folder (<portable>\录像) keeps using it: write that path into the copied settings.
   string recordingDir="";var defaultRecordings=Path.Combine(source,"录像");
   var settingsPath=Path.Combine(source,"native-settings.json");UserSettings? settings=null;
   if(File.Exists(settingsPath)){
    try{settings=UserSettings.Read(settingsPath);}catch{settings=null;}
    if(settings==null){CopyVerified(settingsPath,Path.Combine(staging,"native-settings.json"),ref files,ref bytes);recordingDir="";}
   }else if(Directory.Exists(defaultRecordings))settings=new UserSettings();
   if(settings!=null){
    if(settings.Directory==""&&Directory.Exists(defaultRecordings))settings.Directory=defaultRecordings;
    recordingDir=settings.Directory;
    Directory.CreateDirectory(staging);settings.Write(Path.Combine(staging,"native-settings.json"));files++;
   }
   // Move into place: target\data is absent or empty (checked above).
   if(Directory.Exists(targetData))Directory.Delete(targetData,false);
   if(Directory.Exists(Path.Combine(staging,"data")))Directory.Move(Path.Combine(staging,"data"),targetData);
   foreach(var name in new[]{"service-exit-warning.txt","native-settings.json"}){var p=Path.Combine(staging,name);if(File.Exists(p))File.Move(p,Path.Combine(target,name),false);}
   Directory.Delete(staging,true);
   File.WriteAllText(Path.Combine(target,"portable-import.json"),JsonSerializer.Serialize(new{source,imported_at=DateTimeOffset.Now.ToString("o"),files,bytes,recording_dir=recordingDir},new JsonSerializerOptions{WriteIndented=true}),new UTF8Encoding(false));
   var message=$"已把便携版数据复制到安装版（{files} 个文件）。便携版文件夹没有改动。"+(recordingDir!=""?"录像继续保存在："+recordingDir:"");
   return new(source,files,bytes,recordingDir,message);
  }catch{
   // Nothing reached target\data unless the final move succeeded; drop the staging copy only.
   try{if(Directory.Exists(staging))Directory.Delete(staging,true);}catch{}
   throw;
  }
 }

 static bool Same(string a,string b)=>string.Equals(a,b,StringComparison.OrdinalIgnoreCase);
 static bool Under(string path,string root)=>path.StartsWith(root+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase);

 static void CopyTree(string from,string to,ref int files,ref long bytes){
  Directory.CreateDirectory(to);
  foreach(var entry in new DirectoryInfo(from).EnumerateFileSystemInfos()){
   if(entry.Attributes.HasFlag(FileAttributes.ReparsePoint))continue;
   if(entry is DirectoryInfo dir)CopyTree(dir.FullName,Path.Combine(to,entry.Name),ref files,ref bytes);
   else CopyVerified(entry.FullName,Path.Combine(to,entry.Name),ref files,ref bytes);
  }
 }

 static void CopyVerified(string from,string to,ref int files,ref long bytes){
  Directory.CreateDirectory(Path.GetDirectoryName(to)!);
  File.Copy(from,to,false);
  if(!HashOf(from).SequenceEqual(HashOf(to)))throw new IOException("复制后校验不一致："+from);
  files++;bytes+=new FileInfo(to).Length;
 }

 static byte[] HashOf(string path){using var s=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read);return SHA256.HashData(s);}

 // The portable host holds Local\ReceivingNativeTrial_<hash of its DataRoot> (its BaseDirectory with
 // the trailing separator, see Program.Main); the case of a typed path may differ, so also look
 // for any process started from the folder.
 static bool IsRunning(string source){
  var root=source+Path.DirectorySeparatorChar;
  var name="Local\\ReceivingNativeTrial_"+Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(root)))[..16];
  try{if(Mutex.TryOpenExisting(name,out var m)){m.Dispose();return true;}}catch(UnauthorizedAccessException){return true;}
  foreach(var p in Process.GetProcesses()){
   try{var file=p.MainModule?.FileName;if(file!=null&&file.StartsWith(root,StringComparison.OrdinalIgnoreCase))return true;}catch{}
   finally{p.Dispose();}
  }
  return false;
 }
}
