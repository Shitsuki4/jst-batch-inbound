using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
namespace Receiving.Native;

public sealed record PurchaseLink(string Po, string Outer);
public sealed class VideoContext {
 public string Source { get; set; } = "普通";
 public long SessionId { get; set; }
 public long BatchId { get; set; }
 public string Tracking { get; set; } = "";
 public List<PurchaseLink> Purchases { get; set; } = new();
 public string LastSku { get; set; } = "";
 public decimal? ConfirmedQuantity { get; set; }
 public string Key => Source + ":" + SessionId + ":" + BatchId;
 public string AssociationKey => Source + ":" + SessionId;
 public VideoContext Copy() => JsonSerializer.Deserialize<VideoContext>(JsonSerializer.Serialize(this))!;
 public static VideoContext For(string tracking, string key) {
  var parts = key.Split(':');
  return new() { Tracking = tracking, Source = key.StartsWith("TEST",StringComparison.Ordinal) ? "测试" : parts[0], SessionId = parts.Length > 1 && long.TryParse(parts[1], out var sid) ? sid : 0, BatchId = parts.Length > 2 && long.TryParse(parts[2], out var bid) ? bid : 0 };
 }
}
public sealed class VideoSegment {
 public int ArchiveVersion { get; set; } = 2;
 public string Id { get; set; } = Guid.NewGuid().ToString("N");
 public string Tracking { get; set; } = "";
 public string BatchKey { get; set; } = "";
 public VideoContext Context { get; set; } = new();
 public DateTimeOffset RequestedAt { get; set; } = DateTimeOffset.Now;
 public DateTimeOffset? FirstFrameAt { get; set; }
 public DateTimeOffset? EndAt { get; set; }
 public int? ExitCode { get; set; }
 public string ErrorDetail { get; set; } = "";
 public string File { get; set; } = "";
 public string State { get; set; } = "starting";
 public string Reason { get; set; } = "";
 public double Duration { get; set; }
 public string Note { get; set; } = "";
 public bool Protected { get; set; }
 public List<string> Photos { get; set; } = new(); // basenames only, never arbitrary paths
 public string EffectiveMode { get; set; } = "";
 public string PreviousFile { get; set; } = "";
 public DateTimeOffset? ArchivedAt { get; set; }
}

public static class ArchiveLayout {
 public static readonly JsonSerializerOptions Json = new() { WriteIndented = true, PropertyNameCaseInsensitive = true };
 static readonly object allocationGate = new();
 public static string Hash(string value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value)))[..10].ToLowerInvariant();
 public static string Component(string value, int max = 80) {
  var input = string.IsNullOrWhiteSpace(value) ? "未填写" : value;
  var clean = Regex.Replace(input, "[<>:\"/\\\\|?*\\x00-\\x1f]", "_").TrimEnd(' ', '.');
  if (clean.Length == 0 || Regex.IsMatch(clean, @"^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|$)", RegexOptions.IgnoreCase)) clean = "_" + clean;
  var changed = clean != input || clean.Length > max;
  if (changed) {
   var take = Math.Min(clean.Length, Math.Max(1, max - 11));
   if (take > 0 && char.IsHighSurrogate(clean[take-1])) take--;
   clean = clean[..take] + "_" + Hash(input);
  }
  return clean;
 }
 public static string Root(string root) {
  if (!Path.IsPathFullyQualified(root)) throw new IOException("录像目录必须是完整绝对路径");
  return Path.TrimEndingDirectorySeparator(Path.GetFullPath(root));
 }
 public static string Checked(string root, string path, bool allowRoot = false) {
  root = Root(root); var full = Path.GetFullPath(path);
  if (!(allowRoot && full.Equals(root,StringComparison.OrdinalIgnoreCase)) && !full.StartsWith(root.EndsWith(Path.DirectorySeparatorChar) ? root : root + Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)) throw new IOException("操作路径超出当前录像根目录，已拒绝");
  // Never follow a junction/symlink into a different tree, including ancestors.
  var walk = full;
  while (!string.IsNullOrEmpty(walk)) {
   if ((System.IO.File.Exists(walk) || System.IO.Directory.Exists(walk)) && (System.IO.File.GetAttributes(walk) & FileAttributes.ReparsePoint) != 0) throw new IOException("录像路径含链接/联接点，已拒绝跨目录操作：" + walk);
   walk = Path.GetDirectoryName(walk);
  }
  return full;
 }
 public static string Folder(string root, VideoContext context, DateTimeOffset at) {
  root = Root(root);
  var day = $"{at.Year}年{at.Month}月{at.Day}日";
  var trackingMax = Math.Min(72, (245 - root.Length - day.Length - 48 - 22) / 2);
  if (trackingMax < 16) throw new IOException("录像根目录过长，请选择更短的目录");
  var tracking = Component(context.Tracking, trackingMax);
  var pairs = context.Purchases.Where(p => !string.IsNullOrWhiteSpace(p.Po)).OrderBy(p => p.Po,StringComparer.Ordinal).ThenBy(p => p.Outer,StringComparer.Ordinal).Distinct().ToArray();
  string group;
  if (context.Source == "测试") group = "测试录像";
  else if (context.Source == "快递单") group = "快递单_登记" + context.SessionId;
  else if (pairs.Length == 0) group = "无采购单_登记" + context.SessionId;
  else group = string.Join("__", pairs.Select(p => Component(p.Po + "-" + (string.IsNullOrWhiteSpace(p.Outer) ? "无外部单号" : p.Outer), 120)));
  // Reserve room for continuation suffix, sidecars and atomic-write suffix.
  var maxGroup = Math.Min(120, 245 - (root.Length + day.Length + tracking.Length * 2 + 48));
  if (maxGroup < 22) throw new IOException("录像根目录过长，请在设置中选择更短的目录；没有截断原始单号");
  if (group.Length > maxGroup) {
   var identity = string.Join("|", pairs.Select(p => p.Po + "\t" + p.Outer));
   group = Component((pairs.Length > 1 ? "联合批次" : "采购") + context.SessionId + "_" + Hash(identity), maxGroup);
  }
  return Checked(root, Path.Combine(root, day, group, tracking));
 }
 public static string Candidate(string root, VideoContext context, DateTimeOffset at, Func<string,bool>? occupied = null) {
  var folder = Folder(root, context, at); var stem = Path.GetFileName(folder);
  occupied ??= p => System.IO.File.Exists(p) || System.IO.File.Exists(p + ".json") || System.IO.File.Exists(p + ".reserve") || System.IO.File.Exists(p + ".log");
  var first = Checked(root, Path.Combine(folder,stem + ".mp4")); if (!occupied(first)) return first;
  for (var n=1; n<100000; n++) { var p = Checked(root,Path.Combine(folder,$"{stem}_{at:HHmmss}_{n}.mp4")); if (!occupied(p)) return p; }
  throw new IOException("同物流文件过多，未覆盖任何已有文件");
 }
 public static VideoSegment Reserve(string root, VideoContext context, string key, DateTimeOffset at) {
  lock (allocationGate) {
   for (var attempt=0; attempt<10; attempt++) {
    var path = Candidate(root,context,at); System.IO.Directory.CreateDirectory(Path.GetDirectoryName(path)!); Checked(root,path);
    try {
     using (new FileStream(path+".reserve",FileMode.CreateNew,FileAccess.Write,FileShare.None)) { }
    } catch (IOException) { continue; }
    try {
     // A different writer may have created the media after the check. Never overwrite it.
     if (System.IO.File.Exists(path) || System.IO.File.Exists(path+".json")) continue;
     var s = new VideoSegment { File=path,Tracking=context.Tracking,BatchKey=key,Context=context.Copy(),RequestedAt=at };
     Write(root,s); return s;
    } finally { System.IO.File.Delete(Checked(root,path+".reserve")); }
   }
   throw new IOException("文件名正在被占用，请稍后重试；未覆盖旧录像");
  }
 }
 public static void AtomicJson<T>(string root,string path,T value) {
  path = Checked(root,path); var temp = Checked(root,path+".tmp."+Guid.NewGuid().ToString("N")[..8]);
  try {
   using (var f = new FileStream(temp,FileMode.CreateNew,FileAccess.Write,FileShare.None)) { JsonSerializer.Serialize(f,value,Json); f.Flush(true); }
   System.IO.File.Move(temp,path,true);
  } catch { if (System.IO.File.Exists(temp)) System.IO.File.Delete(temp); throw; }
 }
 public static void Write(string root,VideoSegment segment) => AtomicJson(root,segment.File+".json",segment);
 public static VideoSegment Read(string root,string sidecar) {
  sidecar = Checked(root,sidecar);
  if (new FileInfo(sidecar).Length > 1024*1024) throw new IOException("录像索引过大，已跳过");
  var s = JsonSerializer.Deserialize<VideoSegment>(System.IO.File.ReadAllText(sidecar),Json) ?? throw new IOException("录像索引为空");
  if (string.IsNullOrWhiteSpace(s.Id) || !Checked(root,s.File).Equals(sidecar[..^5],StringComparison.OrdinalIgnoreCase) || !s.File.EndsWith(".mp4",StringComparison.OrdinalIgnoreCase)) throw new IOException("录像索引路径不匹配");
  if (s.Context == null || string.IsNullOrEmpty(s.Context.Tracking)) s.Context = VideoContext.For(s.Tracking,s.BatchKey);
  s.Photos ??= new(); return s;
 }
 public static IEnumerable<string> Sidecars(string root) {
  root = Root(root); if (!System.IO.Directory.Exists(root)) yield break;
  Checked(root,root,true);
  var options = new EnumerationOptions { RecurseSubdirectories=true, AttributesToSkip=FileAttributes.ReparsePoint, IgnoreInaccessible=true };
  foreach (var p in System.IO.Directory.EnumerateFiles(root,"*.mp4.json",options)) yield return Checked(root,p);
 }
}
