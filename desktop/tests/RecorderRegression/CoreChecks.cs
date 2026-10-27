using System.Text.Json;
using Receiving.Native;
public static class CoreChecks {
 public static List<string> Run(string root) {
  Directory.CreateDirectory(root);var passed=new List<string>();
  void Check(bool ok,string name){if(!ok)throw new Exception(name);passed.Add(name);}
  void Reject(Action f,string name){var rejected=false;try{f();}catch(ArgumentException){rejected=true;}catch(IOException){rejected=true;}catch(InvalidOperationException){rejected=true;}Check(rejected,name);}
  var config=Path.Combine(root,"native-settings.json");
  var defaults=UserSettings.Read(config);
  Check(defaults.ShowPreviewOnStartup&&defaults.AutoRecordEnabled&&!File.Exists(config),"首次启动监控和扫码录制默认开启，读取不写盘");
  Check(!defaults.DiskConfigured,"默认开启不伪造磁盘阈值或录制就绪");
  File.WriteAllText(config,"{\"SchemaVersion\":2,\"WarningGb\":6,\"StopGb\":2}");
  var legacy=UserSettings.Read(config);
  Check(legacy.ShowPreviewOnStartup&&legacy.AutoRecordEnabled&&legacy.SchemaVersion==3&&legacy.WarningGb==6,"旧配置缺失新开关时开启，保留已有阈值并迁移版本");
  File.WriteAllText(config,"{\"Width\":800,\"Height\":700}");
  Check(UserSettings.Read(config).Width==420&&UserSettings.Read(config).Height==370,"无版本旧浮窗尺寸迁移保持兼容");
  legacy.AutoRecordEnabled=false;legacy.ShowPreviewOnStartup=false;legacy.Write(config);
  var disabled=UserSettings.Read(config);
  Check(!disabled.AutoRecordEnabled&&!disabled.ShowPreviewOnStartup,"明确关闭两个开关后重读仍关闭");
  disabled.SoundVolume=43;disabled.Write(config);
  Check(!UserSettings.Read(config).AutoRecordEnabled&&!UserSettings.Read(config).ShowPreviewOnStartup&&UserSettings.Read(config).SoundVolume==43,"其他设置保存不重置已关闭开关");
  disabled.AutoRecordEnabled=true;disabled.ShowPreviewOnStartup=true;disabled.Write(config);
  Check(UserSettings.Read(config).AutoRecordEnabled&&UserSettings.Read(config).ShowPreviewOnStartup,"重新开启并重启配置回读仍开启");
  var goodBytes=File.ReadAllBytes(config);disabled.WarningGb=1;
  Reject(()=>disabled.Write(config),"无效配置写入失败");
  Check(File.ReadAllBytes(config).SequenceEqual(goodBytes),"无效保存不覆盖原配置");disabled.WarningGb=6;
  Directory.CreateDirectory(config+".tmp");
  var failedWrite=false;try{disabled.Write(config);}catch(Exception e)when(e is IOException or UnauthorizedAccessException){failedWrite=true;}
  Check(failedWrite&&File.ReadAllBytes(config).SequenceEqual(goodBytes),"磁盘临时路径写入失败保留原配置");Directory.Delete(config+".tmp");
  foreach(var bad in new[]{"{broken","null","{\"AutoRecordEnabled\":null}","{\"Camera\":null}"}){
   File.WriteAllText(config,bad);var failedRead=false;try{UserSettings.Read(config);}catch(Exception e)when(e is JsonException or IOException or ArgumentException){failedRead=true;}
   Check(failedRead&&File.ReadAllText(config)==bad,"损坏配置拒绝读取且原字节保留："+bad);
  }
  var webSettings=new UserSettings{Engine=Environment.ProcessPath!,Directory=root,WarningGb=6,StopGb=2,AutoRecordEnabled=false};
  using(var draft=JsonDocument.Parse("{\"showPreviewOnStartup\":false,\"soundVolume\":41}")){
   var edited=UserSettings.FromWeb(webSettings,draft.RootElement);edited.Write(config);
   Check(!UserSettings.Read(config).ShowPreviewOnStartup&&!UserSettings.Read(config).AutoRecordEnabled,"网页修改启动监控并保存不绕过独立自动录像开关");
  }
  using(var bypass=JsonDocument.Parse("{\"autoRecordEnabled\":true}"))Reject(()=>UserSettings.FromWeb(webSettings,bypass.RootElement),"通用设置不能绕过明确确认启用自动录像");
  Reject(()=>new UserSettings{Camera=null!}.Validate(false,false),"损坏配置的null字符串被明确拒绝");
  Reject(()=>new UserSettings{Width=double.NaN}.Validate(false,false),"损坏浮窗尺寸被明确拒绝");
  Reject(()=>new UserSettings{WarningGb=decimal.MaxValue,StopGb=1}.Validate(false),"超大GB配置被拒绝，不进入字节换算溢出");
  var s=new UserSettings();Reject(()=>s.Validate(false),"未设置GB时拒绝就绪，不擅自补默认阈值");
  foreach(var (warn,stop) in new[]{(0m,0m),(2m,2m),(1m,2m),(2m,-1m)}){s.WarningGb=warn;s.StopGb=stop;Reject(()=>s.Validate(false),"拒绝无效阈值 "+warn+"/"+stop);}
  s.WarningGb=6.5m;s.StopGb=1.25m;s.Validate(false);Check(s.DiskConfigured,"软件支持自定义小数GB阈值");
  s.PhotoEnabled=false;s.NotesEnabled=true;s.ProtectionEnabled=true;s.ZoomEnabled=true;s.CleanupDays=180;s.OverlaySku=true;s.OverlayQuantity=true;
  var clone=s.Copy();Check(clone.WarningGb==6.5m&&clone.NotesEnabled&&clone.ProtectionEnabled&&clone.ZoomEnabled&&!clone.PhotoEnabled&&clone.CleanupDays==180&&clone.OverlaySku,"配置复制保留所有新开关与数值");
  Check(HotkeySpec.Parse("alt+CTRL+f8").Text=="Ctrl+Alt+F8","全局组合键规范化");
  Reject(()=>HotkeySpec.Parse("F8"),"拒绝无修饰键的全局键");Reject(()=>HotkeySpec.Parse("Shift+A"),"拒绝可抢占扫码字符的全局键");
  s.HotkeyPreview="Ctrl+Alt+F8";s.HotkeyStop="Alt+Ctrl+F8";Reject(()=>s.Validate(false),"不同动作不能保存重复快捷键");s.HotkeyStop="";s.HotkeysEnabled=true;s.Validate(false);
  s.TrackingRuleEnabled=false;s.ValidateTracking("123-1");Check(true,"默认物流格式不限");s.TrackingRuleEnabled=true;s.TrackingPrefixes="SF,YT";s.TrackingMinLength=6;s.ValidateTracking("SF1234");Reject(()=>s.ValidateTracking("AB1234"),"可选前缀长度规则只校验完整原号");
  var context=new VideoContext{Source="普通",SessionId=11,BatchId=12,Tracking="SF123456",Purchases=new(){new("PO1","EXT1")}};var at=new DateTimeOffset(2026,10,8,12,30,40,TimeSpan.FromHours(8));
  var folder=ArchiveLayout.Folder(root,context,at);Check(folder.EndsWith(Path.Combine("2026年10月8日","PO1-EXT1","SF123456")),"按中文日期/采购外部成对/物流归档");
  var file=ArchiveLayout.Reserve(root,context,context.Key,at);Check(Path.GetFileName(file.File)=="SF123456.mp4","首段名为物流号.mp4");
  var next=ArchiveLayout.Reserve(root,context,context.Key,at);Check(Path.GetFileName(next.File)=="SF123456_123040_1.mp4"&&next.File!=file.File,"续录时分秒加序号且不覆盖首段");
  var more=Enumerable.Range(0,8).AsParallel().Select(_=>ArchiveLayout.Reserve(root,context,context.Key,at).File).ToArray();Check(more.Distinct().Count()==8,"并发预留不覆盖已有片段或sidecar");
  var multi=context.Copy();multi.Source="联合";multi.Purchases=new(){new("POA","OUTA"),new("POB","")};multi.Tracking="M1";
  Check(ArchiveLayout.Folder(root,multi,at).Contains("POA-OUTA__POB-无外部单号"),"多采购成对拼接，缺外部号保留占位");
  multi.Purchases=Enumerable.Range(1,8).Select(i=>new PurchaseLink("PURCHASE"+i+new string('P',40),"EXT"+new string('E',40))).ToList();
  Check(ArchiveLayout.Folder(root,multi,at).Contains("联合批次11"),"多采购过长才降级为联合批次，完整关联仍在索引");
  Check(ArchiveLayout.Component("A/B")!=ArchiveLayout.Component("A?B"),"清理文件名后仍以哈希区分碰撞");Check(!ArchiveLayout.Component("CON").Equals("CON"),"Windows设备名安全处理");
  Reject(()=>ArchiveLayout.Checked(root,Path.Combine(root,"..","outside.mp4")),"拒绝越过录像根目录的路径");
  var modes=CameraMode.Parse("vcodec=mjpeg min s=1280x720 fps=15 max s=1280x720 fps=30\npixel_format=yuyv422 min s=640x480 fps=15 max s=640x480 fps=30");
  Check(CameraMode.Choose(modes,"1280x720",30).Format=="mjpeg","自动格式只在同分辨率同帧率中选择");Reject(()=>CameraMode.Choose(modes,"1920x1080",30),"高分辨率不静默降档");Reject(()=>CameraMode.Choose(Array.Empty<CameraMode>(),"1280x720",30),"没有兼容模式时明确失败");
  var high=CameraMode.Parse("vcodec=mjpeg min s=3840x2160 fps=7.5 max s=3840x2160 fps=7.5\npixel_format=nv12 min s=2560x1440 fps=29.97 max s=2560x1440 fps=29.97\nvcodec=mjpeg min s=7680x4320 fps=5 max s=7680x4320 fps=5");
  Check(CameraMode.Choose(high,"3840x2160",7.5).Fps==7.5&&CameraMode.Choose(high,"2560x1440",29.97,"nv12").Width==2560&&CameraMode.Choose(high,"7680x4320",5).Height==4320,"2K/4K/8K及低帧率、小数帧率按设备报告保留");
  Reject(()=>CameraMode.Choose(high,"3840x2160",30),"4K低帧率不能捏造30fps");Reject(()=>CameraMode.Choose(high,"3840x2160",7.5,"nv12"),"不能跨格式拼接档位");
  new UserSettings{Resolution="3840x2160",FrameRate=7.5}.Validate(false,false);Check(true,"高分辨率小数帧率配置兼容");
  var four=CameraMode.Choose(high,"3840x2160",7.5);four.VerifyInput("Stream #0:0: Video: mjpeg, yuvj422p, 3840x2160, 7.5 fps");Reject(()=>four.VerifyInput("Stream #0:0: Video: mjpeg, 1920x1080, 7.5 fps"),"实际降分辨率不能报就绪");Reject(()=>four.VerifyInput("Stream #0:0: Video: mjpeg, 3840x2160, 5 fps"),"实际降帧率不能报就绪");
  var overlay=new UserSettings{OverlayPurchase=true,OverlayOuter=true,OverlaySku=true,OverlayQuantity=true};context.LastSku="SKU-A";context.ConfirmedQuantity=6;
  var text=Recorder.OverlayText(overlay,context,at);Check(text.Contains("SKU-A")&&text.Contains("数量：6")&&text.Contains("EXT1")&&text.Contains("2026-10-08"),"叠字包含六类已确认业务字段");
  overlay.OverlaySku=false;overlay.OverlayQuantity=false;Check(!Recorder.OverlayText(overlay,context,at).Contains("SKU-A")&&!Recorder.OverlayText(overlay,context,at).Contains("数量"),"独立关闭商品/数量叠字");
  Check(text!=Recorder.OverlayText(new UserSettings{OverlayPurchase=true,OverlayOuter=true,OverlaySku=true,OverlayQuantity=true},context,at.AddSeconds(1)),"叠字时间可逐秒更新");
  var unplanned=new VideoContext{Source="无采购单",SessionId=77,BatchId=78,Tracking="NOPO1"};
  var expressCtx=new VideoContext{Source="快递单",SessionId=88,BatchId=89,Tracking="KD-EXPRESS-1"};
  Check(ArchiveLayout.Folder(root,expressCtx,at).Contains("快递单_登记88")&&!ArchiveLayout.Folder(root,expressCtx,at).Contains("无采购单"),"快递单登记录像按独立分组归档（快递单_登记+批次号）");
  Check(Recorder.OverlayText(new UserSettings{OverlayPurchase=true},expressCtx,at).Contains("快递单登记"),"快递单登记叠字不显示为无采购单");
  var old=ArchiveLayout.Reserve(root,unplanned,unplanned.Key,at.AddDays(-200));File.WriteAllBytes(old.File,new byte[]{1,2,3,4});old.State="saved";old.EndAt=at.AddDays(-199);ArchiveLayout.Write(root,old);
  var library=new VideoLibrary(root);Check(!library.List().Single(x=>x.Id==old.Id).PendingArchive,"无采购录像先在原目录保存");
  var linked=unplanned.Copy();linked.Purchases=new(){new("BACKPO","BACKEXT")};library.Associate(linked);
  var pending=library.List("BACKEXT").Single();Check(pending.PendingArchive&&File.Exists(old.File),"后补采购只更新关联索引，可按外部号搜到，尚不搬文件");
  var target=library.ArchiveTarget(pending);Reject(()=>library.ArchiveConfirmed(pending,target,null,false),"未确认不能归档旧录像");Reject(()=>library.ArchiveConfirmed(pending,target,old.Id,true),"正在录像的片段不能移动");
  var moved=library.ArchiveConfirmed(pending,target,null,true);Check(File.Exists(moved)&&!File.Exists(old.File)&&File.ReadAllBytes(moved).SequenceEqual(new byte[]{1,2,3,4}),"停录且确认后归档，媒体字节不变且只存一份");
  var archived=library.List("BACKPO").Single();Check(!archived.PendingArchive&&archived.Segment.Id==old.Id&&archived.Segment.PreviousFile==old.File,"归档保留稳定ID和旧路径追溯");
  var candidates=library.CleanupCandidates(90,at,null);Check(candidates.Count==1&&candidates[0].Id==old.Id,"普通清理仅含超龄已保存片段，排除starting");
  Check(library.CleanupCandidates(0,at,null).Count==0&&library.CleanupCandidates(90,at,old.Id).Count==0,"不按天数或当前在录不列候选");
  library.Edit(archived,"售后现场备注",true,null);Check(library.CleanupCandidates(90,at,null).Count==0,"售后保护始终排除普通清理，即使后来关闭功能开关");
  var protectedItem=library.List("售后").Single();library.Edit(protectedItem,protectedItem.Note,false,null);var selected=library.CleanupCandidates(90,at,null);
  Reject(()=>library.DeleteConfirmed(selected,90,at,null,false),"未明确确认不删除");
  var deleted=library.DeleteConfirmed(selected,90,at,null,true);Check(deleted.Count==1&&deleted.Errors.Count==0&&!File.Exists(moved),"只删除用户已勾选确认的隔离候选，不递归删除目录");
  // A malicious sidecar cannot cause reads/moves/deletes outside the selected root.
  var poison=file.File+".json";file.File=Path.GetFullPath(Path.Combine(root,"..","outside.mp4"));File.WriteAllText(poison,JsonSerializer.Serialize(file));
  Check(library.List().All(x=>x.Id!=file.Id)&&library.Warnings.Count>0,"篡改索引路径被拒绝且显示警告");
  var safeContext=new VideoContext{Source="无采购单",SessionId=910,BatchId=911,Tracking="SAFE"};
  var safe=ArchiveLayout.Reserve(root,safeContext,safeContext.Key,at.AddDays(-200));File.WriteAllBytes(safe.File,new byte[]{1,2,3});safe.State="saved";safe.EndAt=at.AddDays(-199);ArchiveLayout.Write(root,safe);
  var preview=library.CleanupCandidates(90,at,null).Single(i=>i.Id==safe.Id);File.WriteAllBytes(safe.File,new byte[]{1,2,3,4});
  var changed=library.DeleteConfirmed(new[]{preview},90,at,null,true);Check(changed.Count==0&&changed.Errors.Count==1&&File.Exists(safe.File),"清理确认时重新检查媒体大小；变化后不删除");
  var valid=library.List().Single(i=>i.Id==safe.Id);
  using(var recorder=new RecorderScope(root)){
   recorder.Value.RefreshMetadataAsync(safe.File).GetAwaiter().GetResult();
   Reject(()=>recorder.Value.SetEvidenceAsync("未开启备注",false).GetAwaiter().GetResult(),"备注未开启时Recorder拒绝写入");
   Reject(()=>recorder.Value.SetEvidenceAsync("",true).GetAwaiter().GetResult(),"保护未开启时Recorder拒绝修改");
   File.Delete(safe.File);
   Reject(()=>recorder.Value.SetEvidenceAsync("",false).GetAwaiter().GetResult(),"文件被删除后Recorder不重建旧sidecar");
   recorder.Value.RefreshMetadataAsync(safe.File).GetAwaiter().GetResult();Check(recorder.Value.Current==null,"已删除片段刷新后不再占用当前录像状态");
  }
  var index=Path.Combine(root,"video-associations.json");File.WriteAllText(index,"{broken");
  Reject(()=>library.Associate(safeContext),"损坏关联索引不能被新关联静默覆盖");Check(File.ReadAllText(index)=="{broken","关联写入失败后原坏索引原样保留");
  foreach(var invalid in new[]{
   "{\"无采购单:910\":null}",
   "{\"无采购单:910\":{\"Source\":\"无采购单\",\"SessionId\":910,\"Purchases\":null}}",
   "{\"无采购单:910\":{\"Source\":\"无采购单\",\"SessionId\":910,\"Purchases\":[null]}}",
   "{\"无采购单:910\":{\"Source\":\"无采购单\",\"SessionId\":910,\"Purchases\":[{\"Po\":\"PO\",\"Outer\":null}]}}",
   "{\"无采购单:910\":{\"Source\":\"普通\",\"SessionId\":910}}"
  }) {
   File.WriteAllText(index,invalid);
   Reject(()=>library.Associate(safeContext),"合法JSON中无效关联结构不能静默覆盖");
   Check(File.ReadAllText(index)==invalid,"拒绝无效嵌套关联后原索引原样保留");
   library.List();Check(library.Warnings.Any(w=>w.StartsWith("关联索引读取失败")),"无效关联可见告警，不让空引用中断录像列表");
  }
  // 单段录像上限：默认0不限制、旧配置兼容、非负校验、保存回读、判定边界。大小按 GB（可两位小数），旧 MB 配置读入后大小不变。
  var limitConfig=Path.Combine(root,"segment-limit-settings.json");
  File.WriteAllText(limitConfig,"{\"SchemaVersion\":3,\"WarningGb\":6,\"StopGb\":2}");
  var noLimit=UserSettings.Read(limitConfig);
  Check(noLimit.MaxSegmentMinutes==0&&noLimit.MaxSegmentGb==0&&new UserSettings().MaxSegmentMinutes==0&&new UserSettings().MaxSegmentGb==0,"旧配置缺失单段上限时默认0（不限制），行为不变");
  foreach(var (minutes,gb) in new[]{(-1,0m),(0,-1m),(1441,0m),(0,0.001m),(0,1024.01m)}){var bad=new UserSettings{MaxSegmentMinutes=minutes,MaxSegmentGb=gb};Reject(()=>bad.Validate(false,false),"拒绝无效单段上限 "+minutes+"分钟/"+gb+"GB");}
  foreach(var mb in new[]{-1,5,1048577}){var bad=new UserSettings{MaxSegmentMb=mb};Reject(()=>bad.Validate(false,false),"拒绝无效旧版MB单段上限 "+mb+"MB");}
  var limited=new UserSettings{WarningGb=6,StopGb=2,MaxSegmentMinutes=30,MaxSegmentGb=1.5m};limited.Validate(false);limited.Write(limitConfig);
  Check(!File.ReadAllText(limitConfig).Contains("MaxSegmentMb"),"新配置只写 GB，不再写旧 MB 字段");
  var limitedBack=UserSettings.Read(limitConfig);
  Check(limitedBack.MaxSegmentMinutes==30&&limitedBack.MaxSegmentGb==1.5m,"单段时长/大小(GB)上限保存后重读保留");
  using(var scope=new RecorderScope(Path.Combine(root,"limit-recorder"))){scope.Value.Configure(limitedBack,false);Check(scope.Value.MaxSegmentSeconds==1800&&scope.Value.MaxSegmentBytes==1536L*1024*1024&&scope.Value.Diagnostics.Contains("单段上限：30 分钟 / 1.5 GB"),"录像器按GB设置换算单段上限并写入诊断");}
  // 旧版（PR 测试版）按 MB 保存的配置：读入后换算成 GB，字节数完全一致。
  foreach(var (mb,gb) in new[]{(2048,2m),(512,0.5m),(10,10m/1024m),(1000,1000m/1024m)}){
   File.WriteAllText(limitConfig,"{\"SchemaVersion\":3,\"WarningGb\":6,\"StopGb\":2,\"MaxSegmentMinutes\":0,\"MaxSegmentMb\":"+mb+"}");
   var prior=UserSettings.Read(limitConfig);
   Check(prior.MaxSegmentGb==gb&&prior.MaxSegmentMb==0&&UserSettings.SegmentBytes(prior.MaxSegmentGb)==mb*1024L*1024L,"旧配置 "+mb+" MB 换算为 GB 后大小不变");
   using var priorScope=new RecorderScope(Path.Combine(root,"limit-legacy-"+mb));priorScope.Value.Configure(prior,false);
   Check(priorScope.Value.MaxSegmentBytes==mb*1024L*1024L,"旧配置 "+mb+" MB 录像器字节上限不变");
   prior.Write(limitConfig);var rewritten=UserSettings.Read(limitConfig);
   Check(rewritten.MaxSegmentGb==gb&&!File.ReadAllText(limitConfig).Contains("MaxSegmentMb"),"旧 MB 配置重新保存后改为 GB 字段且大小不变");
  }
  var legacyUnsaved=new UserSettings{WarningGb=6,StopGb=2,MaxSegmentMb=3072};
  using(var scope=new RecorderScope(Path.Combine(root,"limit-legacy-direct"))){scope.Value.Configure(legacyUnsaved,false);Check(scope.Value.MaxSegmentBytes==3072L*1024*1024,"未经读取的旧 MB 设置直接配置录像器时仍按原大小");}
  File.WriteAllText(limitConfig,"{\"SchemaVersion\":3,\"WarningGb\":6,\"StopGb\":2,\"MaxSegmentMb\":4096,\"MaxSegmentGb\":1.25}");
  Check(UserSettings.Read(limitConfig).MaxSegmentGb==1.25m,"同时存在新旧字段时以 GB 为准");
  using(var draft=JsonDocument.Parse("{\"maxSegmentMinutes\":15,\"maxSegmentGb\":0}"))Check(UserSettings.FromWeb(webSettings,draft.RootElement).MaxSegmentMinutes==15,"网页可修改单段时长上限");
  foreach(var (raw,gb) in new[]{("0.5",0.5m),("1.5",1.5m),("2",2m),("0.25",0.25m),("\"0.75\"",0.75m),("0",0m)}){using var draft=JsonDocument.Parse("{\"maxSegmentGb\":"+raw+"}");Check(UserSettings.FromWeb(webSettings,draft.RootElement).MaxSegmentGb==gb,"网页单段大小可填 "+raw+" GB");}
  foreach(var bad in new[]{"{\"maxSegmentMinutes\":1.5}","{\"maxSegmentGb\":-1}","{\"maxSegmentGb\":0.125}","{\"maxSegmentGb\":0.001}","{\"maxSegmentGb\":1025}","{\"maxSegmentGb\":\"abc\"}","{\"maxSegmentMinutes\":null}","{\"maxSegmentMb\":2048}"}){using var draft=JsonDocument.Parse(bad);Reject(()=>UserSettings.FromWeb(webSettings,draft.RootElement),"网页单段上限非法值被拒绝："+bad);}
  Check(Recorder.SegmentLimitReason(0,0,long.MaxValue,1e9)==""&&Recorder.LimitText(0,0)=="不限","上限填0不限制");
  Check(Recorder.SegmentLimitReason(0,1800,long.MaxValue,1799.9)==""&&Recorder.SegmentLimitReason(0,1800,1,1800).Contains("时长上限 30 分钟"),"达到时长上限才分段");
  Check(Recorder.SegmentLimitReason(2048L*1024*1024,0,2048L*1024*1024-1,1e9)==""&&Recorder.SegmentLimitReason(2048L*1024*1024,0,2048L*1024*1024,1).Contains("大小上限 2 GB"),"达到大小上限才分段（按GB显示）");
  Check(Recorder.LimitText(UserSettings.SegmentBytes(0.5m),0)=="0.5 GB（先到先分段）"&&Recorder.SizeText(10L*1024*1024)=="0.009766 GB","上限文字按GB显示");
  return passed;
 }
 sealed class RecorderScope : IDisposable {
  public Recorder Value {get;}
  public RecorderScope(string root){Value=new Recorder{DirectoryPath=root};}
  public void Dispose()=>Value.DisposeAsync().AsTask().GetAwaiter().GetResult();
 }
}
