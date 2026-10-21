using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
namespace Receiving.Native;
// Applies an update package the backend already downloaded and verified (POST /api/update/prepare).
// No WPF here: the window decides when it is safe; this class only re-verifies, unpacks and hands off.
public sealed record UpdatePlan(string Kind,string Version,string Package,string Target);
public static class AppUpdate {
 public static string AppVersion{get{var v=Assembly.GetEntryAssembly()?.GetName().Version;return v==null?"":v.ToString(3);}}
 public static string InstallMode=>AppPaths.Installed?"installed":"portable";
 public static string CacheRoot=>Path.GetFullPath(Path.Combine(AppPaths.DataRoot,"data","updates"));
 const string ReleasePage="https://github.com/Shitsuki4/jst-batch-inbound/releases";
 const string ProxyPrefix="https://gh-proxy.com/";
 // Only our own release page (optionally through the gh-proxy.com mirror) may be opened from the web UI.
 public static bool AllowedUrl(string value){
  if(string.IsNullOrWhiteSpace(value)||value.Length>2000||value.Any(c=>char.IsWhiteSpace(c)||char.IsControl(c)||c=='"'))return false;
  var rest=value.StartsWith(ProxyPrefix,StringComparison.Ordinal)?value[ProxyPrefix.Length..]:value;
  if(!rest.StartsWith(ReleasePage,StringComparison.Ordinal))return false;
  var tail=rest[ReleasePage.Length..];if(tail!=""&&tail[0] is not('/' or '?' or '#'))return false;
  if(tail.Contains("..",StringComparison.Ordinal)||tail.Contains('\\'))return false;
  return Uri.TryCreate(value,UriKind.Absolute,out var uri)&&uri.Scheme==Uri.UriSchemeHttps&&uri.Host is "github.com" or "gh-proxy.com";
 }
 public static void OpenUrl(string value){if(!AllowedUrl(value))throw new Exception("只允许打开本项目的发布页");Process.Start(new ProcessStartInfo(value){UseShellExecute=true});}
 // Re-verifies the package inside data/updates and, for the portable build, unpacks it next to the current folder.
 public static UpdatePlan Prepare(string kind,string version,string package,string sha256){
  if(kind!=(AppPaths.Installed?"installer":"portable"))throw new Exception("更新包类型与当前版本（"+(AppPaths.Installed?"安装版":"便携版")+"）不符");
  if(string.IsNullOrWhiteSpace(version)||version.Any(c=>!(char.IsAsciiLetterOrDigit(c)||c is '.' or '-' or '_')))throw new Exception("更新版本号无效");
  var expected=(sha256??"").Trim().ToLowerInvariant();if(expected.StartsWith("sha256:",StringComparison.Ordinal))expected=expected[7..];
  if(expected.Length!=64||expected.Any(c=>!Uri.IsHexDigit(c)))throw new Exception("缺少有效的SHA256校验值");
  var root=CacheRoot.TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar;var full=Path.GetFullPath(package);
  if(!full.StartsWith(root,StringComparison.OrdinalIgnoreCase))throw new Exception("更新包不在更新缓存目录内，已拒绝");
  if(!File.Exists(full))throw new Exception("更新包不存在，请重新下载");
  var ext=Path.GetExtension(full).ToLowerInvariant();if(kind=="installer"?ext!=".exe":ext!=".zip")throw new Exception("更新包文件类型不符");
  string actual;using(var stream=new FileStream(full,FileMode.Open,FileAccess.Read,FileShare.Read)){actual=Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();}
  if(actual!=expected)throw new Exception("更新包SHA256校验不一致，已拒绝安装，请重新下载");
  if(kind=="installer")return new UpdatePlan(kind,version,full,full);
  return new UpdatePlan(kind,version,full,ExtractPortable(full,version));
 }
 static string ExtractPortable(string zip,string version){
  var current=Path.GetFullPath(AppContext.BaseDirectory).TrimEnd(Path.DirectorySeparatorChar);
  var parent=Path.GetDirectoryName(current)??throw new Exception("无法确定便携版所在目录");
  var name="jst-batch-inbound-desktop-"+version;var target=Path.Combine(parent,name);
  for(int i=2;Directory.Exists(target)||File.Exists(target);i++){if(i>50)throw new Exception("新版本目录已存在过多副本，请手动整理："+parent);target=Path.Combine(parent,name+"-"+i);}
  var staging=Path.Combine(parent,"."+name+".partial-"+Guid.NewGuid().ToString("N")[..8]);
  var stagingRoot=Path.GetFullPath(staging).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar;
  try{
   Directory.CreateDirectory(staging);
   using(var archive=ZipFile.OpenRead(zip)){
    foreach(var entry in archive.Entries){
     var dest=Path.GetFullPath(Path.Combine(staging,entry.FullName));
     if(!dest.StartsWith(stagingRoot,StringComparison.OrdinalIgnoreCase))throw new Exception("更新包含非法路径，已拒绝："+entry.FullName);
     if(entry.FullName.EndsWith("/",StringComparison.Ordinal)||entry.FullName.EndsWith("\\",StringComparison.Ordinal)){Directory.CreateDirectory(dest);continue;}
     Directory.CreateDirectory(Path.GetDirectoryName(dest)!);entry.ExtractToFile(dest,false);
    }
   }
   Directory.Move(staging,target);
  }catch{try{if(Directory.Exists(staging))Directory.Delete(staging,true);}catch{}throw;}
  // Zips usually wrap everything in one top-level folder; open the folder that actually holds the program.
  var dirs=Directory.GetDirectories(target);
  if(!File.Exists(Path.Combine(target,"Receiving.Native.exe"))&&Directory.GetFiles(target).Length==0&&dirs.Length==1&&File.Exists(Path.Combine(dirs[0],"Receiving.Native.exe")))return dirs[0];
  return target;
 }
 static string Quote(string s)=>"'"+s.Replace("'","''")+"'";
 // Hidden PowerShell waits for this process to exit, then starts the installer (normal UI) or opens the new portable folder.
 public static void Launch(UpdatePlan plan,int pid){
  var start=plan.Kind=="installer"
   ?"Start-Process -FilePath "+Quote(plan.Target)+" -WorkingDirectory "+Quote(Path.GetDirectoryName(plan.Target)!)
   :"Start-Process -FilePath 'explorer.exe' -ArgumentList "+Quote("\""+plan.Target+"\"");
  var script="$ErrorActionPreference='SilentlyContinue';$p=Get-Process -Id "+pid+";if($p){$null=$p.WaitForExit(900000)};Start-Sleep -Milliseconds 800;$ErrorActionPreference='Stop';"+start;
  var encoded=Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
  var exe=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"WindowsPowerShell","v1.0","powershell.exe");if(!File.Exists(exe))exe="powershell.exe";
  var p=new ProcessStartInfo(exe){UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden,WorkingDirectory=Path.GetTempPath()};
  foreach(var a in new[]{"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-EncodedCommand",encoded})p.ArgumentList.Add(a);
  using var launched=Process.Start(p)??throw new Exception("无法启动更新程序");
 }
}
