using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
namespace Receiving.Native;

// This file has no UI or device dependency; it is also compiled by isolated tests.
public sealed class UserSettings {
 public int SchemaVersion { get; set; } = 3;
 public bool ShowPreviewOnStartup { get; set; } = true;
 public bool AutoRecordEnabled { get; set; } = true;
 public string Camera { get; set; } = "HBS Camera";
 public string Resolution { get; set; } = "1280x720";
 public double FrameRate { get; set; } = 30;
 public string CaptureFormat { get; set; } = "auto";
 public int Rotation { get; set; }
 public bool Mirror { get; set; }
 public int Quality { get; set; } = 23;
 public string Directory { get; set; } = "";
 public string Engine { get; set; } = "";
 public bool OverlayTime { get; set; } = true;
 public bool OverlayTracking { get; set; } = true;
 public bool OverlayPurchase { get; set; }
 public bool OverlayOuter { get; set; }
 public bool OverlaySku { get; set; }
 public bool OverlayQuantity { get; set; }
 public bool PhotoEnabled { get; set; } = true;
 public bool NotesEnabled { get; set; }
 public bool ProtectionEnabled { get; set; }
 public bool ZoomEnabled { get; set; }
 public bool SoundStart { get; set; } = true;
 public bool SoundStop { get; set; } = true;
 public bool SoundSaved { get; set; } = true;
 public bool SoundDuplicate { get; set; } = true;
 public bool SoundError { get; set; } = true;
 public int SoundVolume { get; set; } = 70;
 public int CleanupDays { get; set; } // 0 = no age reminder; never automatic deletion.
 public decimal? WarningGb { get; set; }
 public decimal? StopGb { get; set; }
 public bool HotkeysEnabled { get; set; }
 public string HotkeyPreview { get; set; } = "";
 public string HotkeyStop { get; set; } = "";
 public string HotkeyContinue { get; set; } = "";
 public bool TrackingRuleEnabled { get; set; }
 public string TrackingPrefixes { get; set; } = "";
 public int? TrackingMinLength { get; set; }
 public int? TrackingMaxLength { get; set; }
 public double Left { get; set; } = 100;
 public double Top { get; set; } = 100;
 public double Width { get; set; } = 420;
 public double Height { get; set; } = 370;
 public static readonly JsonSerializerOptions WebJson = new() {
  PropertyNamingPolicy = JsonNamingPolicy.CamelCase, NumberHandling = JsonNumberHandling.AllowReadingFromString
 };
 public UserSettings Copy() => JsonSerializer.Deserialize<UserSettings>(JsonSerializer.Serialize(this))!;
 public static UserSettings Read(string path) {
  if(!File.Exists(path))return new();
  using var doc=JsonDocument.Parse(File.ReadAllText(path));
  var result=doc.RootElement.Deserialize<UserSettings>()??throw new IOException("配置内容为空");
  if(!doc.RootElement.TryGetProperty("SchemaVersion",out _)){result.Width=420;result.Height=370;}
  result.Validate(paths:false,requireDisk:false);
  if(result.Directory!=""&&!Path.IsPathFullyQualified(result.Directory))throw new IOException("录像目录不是绝对路径");
  result.SchemaVersion=3;
  return result;
 }
 public void Write(string path) {
  Validate(paths:false,requireDisk:false);
  var temporary=path+".tmp";
  File.WriteAllText(temporary,JsonSerializer.Serialize(this,new JsonSerializerOptions{WriteIndented=true}));
  File.Move(temporary,path,true);
 }
 public static bool ValidSize(int w,int h) => w>=2 && h>=2 && w<=16384 && h<=16384 && w%2==0 && h%2==0;
 public static bool ValidCaptureFormat(string f) => f is "mjpeg" or "yuyv422" or "nv12" or "yuv420p" or "rgb24" or "bgr24" or "uyvy422" or "gray";
 public bool DiskConfigured => WarningGb is > 0 && StopGb is > 0 && WarningGb > StopGb;
 public void Validate(bool paths = true, bool requireDisk = true) {
  foreach(var p in typeof(UserSettings).GetProperties().Where(p=>p.PropertyType==typeof(string)))if(p.GetValue(this)==null)throw new ArgumentException("配置项不能为空："+p.Name);
  if(!double.IsFinite(Left)||!double.IsFinite(Top)||!double.IsFinite(Width)||!double.IsFinite(Height)||Width<100||Height<100||Width>10000||Height>10000)throw new ArgumentException("浮窗位置或尺寸无效");
  if (string.IsNullOrWhiteSpace(Camera) || Camera.Length > 300 || Camera.Any(char.IsControl)) throw new ArgumentException("请枚举或填写有效摄像头名称");
  var size = Regex.Match(Resolution, @"^(\d{1,5})x(\d{1,5})$");
  if (!size.Success || !ValidSize(int.Parse(size.Groups[1].Value),int.Parse(size.Groups[2].Value))) throw new ArgumentException("分辨率须为设备报告的偶数宽高（2～16384），请检测兼容模式");
  if (!double.IsFinite(FrameRate) || FrameRate < 1 || FrameRate > 240) throw new ArgumentException("帧率须为设备报告的1～240fps，支持小数帧率");
  if (CaptureFormat != "auto" && !ValidCaptureFormat(CaptureFormat)) throw new ArgumentException("不支持的相机输入格式");
  if (Rotation is not (0 or 90 or 180 or 270) || Quality < 15 || Quality > 35) throw new ArgumentException("旋转或视频质量参数无效");
  if (CleanupDays is not (0 or 90 or 180)) throw new ArgumentException("清理提醒只能选择90天、180天或不按天数");
  if (requireDisk || WarningGb != null || StopGb != null) {
   if (!DiskConfigured || WarningGb > 1000000) throw new ArgumentException("请在软件中填写磁盘阈值：预警 GB > 停止 GB > 0（最多1000000 GB）");
  }
  if (SoundVolume < 0 || SoundVolume > 100) throw new ArgumentException("提示音音量须为0到100");
  foreach (var name in new[] { HotkeyPreview, HotkeyStop, HotkeyContinue }) if (!string.IsNullOrEmpty(name)) HotkeySpec.Parse(name);
  HotkeyPreview = HotkeySpec.Normalize(HotkeyPreview); HotkeyStop = HotkeySpec.Normalize(HotkeyStop); HotkeyContinue = HotkeySpec.Normalize(HotkeyContinue);
  var keys = new[] { HotkeyPreview, HotkeyStop, HotkeyContinue }.Where(x => x != "").ToArray();
  if (keys.Distinct(StringComparer.OrdinalIgnoreCase).Count() != keys.Length) throw new ArgumentException("三个录像操作不能使用相同的全局快捷键");
  if (HotkeysEnabled && keys.Length == 0) throw new ArgumentException("请配置至少一个组合键，或关闭全局快捷键");
  if (TrackingPrefixes.Length > 500 || TrackingPrefixes.Any(char.IsControl)) throw new ArgumentException("物流前缀无效，请用逗号分隔");
  if (TrackingMinLength is < 1 or > 200 || TrackingMaxLength is < 1 or > 200 || (TrackingMinLength != null && TrackingMaxLength != null && TrackingMinLength > TrackingMaxLength)) throw new ArgumentException("物流长度应为1到200，最小长度不能大于最大长度");
  if (paths) {
   if (!Path.IsPathFullyQualified(Directory) || Directory.IndexOf('\0') >= 0) throw new ArgumentException("请填写完整录像目录");
   Directory = Path.GetFullPath(Directory);
   if (!Path.IsPathFullyQualified(Engine) || !File.Exists(Engine) || !string.Equals(Path.GetExtension(Engine), ".exe", StringComparison.OrdinalIgnoreCase)) throw new ArgumentException("请使用安装版内置 FFmpeg.exe 的完整路径");
  }
 }
 public void ValidateTracking(string tracking) {
  if (string.IsNullOrWhiteSpace(tracking) || tracking.Length > 200 || tracking.Any(char.IsControl)) throw new ArgumentException("物流号为空、过长或含有控制字符，请核对原单号");
  if (!TrackingRuleEnabled) return;
  var prefixes = TrackingPrefixes.Split(new[] { ',', '，', ';', '；' }, StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
  if ((TrackingMinLength != null && tracking.Length < TrackingMinLength) || (TrackingMaxLength != null && tracking.Length > TrackingMaxLength) || (prefixes.Length > 0 && !prefixes.Any(x => tracking.StartsWith(x, StringComparison.OrdinalIgnoreCase)))) throw new ArgumentException("物流单号不符合设置中的前缀 / 长度规则；未截断或更改原单号，请核对");
 }
 public static UserSettings FromWeb(UserSettings current, JsonElement args) {
  if (args.ValueKind != JsonValueKind.Object) throw new ArgumentException("视频设置参数无效");
  var node = JsonSerializer.SerializeToNode(current, WebJson)!.AsObject();
  var editable = new HashSet<string>(new[] { "showPreviewOnStartup","camera","resolution","frameRate","captureFormat","rotation","mirror","quality","directory","engine","overlayTime","overlayTracking","overlayPurchase","overlayOuter","overlaySku","overlayQuantity","photoEnabled","notesEnabled","protectionEnabled","zoomEnabled","soundStart","soundStop","soundSaved","soundDuplicate","soundError","soundVolume","cleanupDays","warningGb","stopGb","hotkeysEnabled","hotkeyPreview","hotkeyStop","hotkeyContinue","trackingRuleEnabled","trackingPrefixes","trackingMinLength","trackingMaxLength" }, StringComparer.Ordinal);
  foreach (var p in args.EnumerateObject()) {
   if (!editable.Contains(p.Name)) throw new ArgumentException("不可修改的视频设置项：" + p.Name);
   if (p.Value.ValueKind == JsonValueKind.String && (p.Value.GetString()!.Length > 2000 || p.Value.GetString()!.Contains('\0'))) throw new ArgumentException("无效参数：" + p.Name);
   node[p.Name] = JsonNode.Parse(p.Value.GetRawText());
  }
  UserSettings result;
  try { result = node.Deserialize<UserSettings>(WebJson)!; } catch (JsonException) { throw new ArgumentException("视频设置格式无效；GB请填写数字，开关请选择勾选状态"); }
  // Explicit nulls must not bypass validation or erase a non-null field.
  foreach (var p in typeof(UserSettings).GetProperties().Where(p => p.PropertyType == typeof(string))) if (p.GetValue(result) == null) throw new ArgumentException("参数不能为空：" + p.Name);
  result.Validate(); return result;
 }
}

public sealed record HotkeySpec(uint Modifiers, uint Key, string Text) {
 public static string Normalize(string value) => string.IsNullOrWhiteSpace(value) ? "" : Parse(value).Text;
 public static HotkeySpec Parse(string value) {
  var parts = value.Split('+', StringSplitOptions.TrimEntries);
  if (parts.Length < 2 || parts.Any(string.IsNullOrEmpty)) throw new ArgumentException("全局快捷键须为组合键，例如 Ctrl+Alt+F8；留空停用该动作");
  uint mods = 0; var names = new Dictionary<string,uint>(StringComparer.OrdinalIgnoreCase) { ["Ctrl"]=2, ["Control"]=2, ["Alt"]=1, ["Shift"]=4, ["Win"]=8 };
  foreach (var part in parts[..^1]) { if (!names.TryGetValue(part,out var m) || (mods & m) != 0) throw new ArgumentException("快捷键修饰键无效或重复"); mods |= m; }
  if ((mods & 11) == 0) throw new ArgumentException("全局快捷键至少包含 Ctrl、Alt、Win 之一，不能抢占普通扫码字符");
  var key = parts[^1].ToUpperInvariant(); uint vk;
  if (key.Length == 1 && char.IsAsciiLetterOrDigit(key[0])) vk = key[0];
  else if (Regex.IsMatch(key, @"^F([1-9]|1[0-9]|2[0-4])$")) vk = (uint)(111 + int.Parse(key[1..], CultureInfo.InvariantCulture));
  else throw new ArgumentException("组合键末键请选择 A-Z、0-9 或 F1-F24");
  if ((mods == 8 && key == "L") || (mods == 1 && key == "F4")) throw new ArgumentException("该组合键由Windows保留，请换一个");
  var text = string.Join("+", new[] { (2u,"Ctrl"), (1u,"Alt"), (4u,"Shift"), (8u,"Win") }.Where(p => (mods & p.Item1) != 0).Select(p => p.Item2).Append(key));
  return new(mods, vk, text);
 }
}
