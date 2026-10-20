using System.Text.Json;
namespace Receiving.Native;

public sealed record LibraryItem(VideoSegment Segment, VideoContext Linked, bool PendingArchive, long Bytes, long Stamp) {
 public string Id => Segment.Id;
 public string Tracking => Segment.Tracking;
 public string Purchase => string.Join(" / ", Linked.Purchases.Select(p => p.Po));
 public string Outer => string.Join(" / ", Linked.Purchases.Select(p => string.IsNullOrWhiteSpace(p.Outer)?"无外部单号":p.Outer));
 public string State => Segment.State;
 public string Note => Segment.Note;
 public bool Protected => Segment.Protected;
 public DateTimeOffset At => Segment.RequestedAt;
 public string File => Segment.File;
 public bool Archived => Segment.ArchivedAt != null;
 public string ArchiveStatus => PendingArchive ? "后补采购待确认归档" : Archived ? "已归档（后补采购）" : "原位保留";
 // Recorder stores the ffprobe duration on stop; fall back to the first-frame/end wall clock.
 public double DurationSeconds => Segment.Duration > 0 && double.IsFinite(Segment.Duration) ? Segment.Duration : Segment.EndAt != null && Segment.FirstFrameAt != null ? Math.Max(0,(Segment.EndAt.Value-Segment.FirstFrameAt.Value).TotalSeconds) : 0;
 public bool Matches(string value) => string.IsNullOrWhiteSpace(value) || new[] { Tracking,Purchase,Outer,Note,Segment.Context.LastSku,Segment.BatchKey }.Any(v=>v.Contains(value,StringComparison.OrdinalIgnoreCase));
}
public sealed record LibraryDeleteResult(int Deleted, int MissingMedia, List<string> Skipped, List<string> Errors, int AssociationsPruned);
public sealed class VideoLibrary {
 readonly string root; readonly object sync = new();
 public string Root => root;
 string AssociationFile => Path.Combine(root,"video-associations.json");
 public List<string> Warnings { get; } = new();
 public VideoLibrary(string directory) { root = ArchiveLayout.Root(directory); }
 Dictionary<string,VideoContext> Associations(bool strict=false) {
  if (!File.Exists(AssociationFile)) return new();
  try {
   var index=JsonSerializer.Deserialize<Dictionary<string,VideoContext>>(File.ReadAllText(ArchiveLayout.Checked(root,AssociationFile)),ArchiveLayout.Json) ?? throw new IOException("关联索引为空，已保留原文件");
   foreach(var entry in index) ValidateAssociation(entry.Key,entry.Value);
   return index;
  }
  catch(Exception ex) { Warnings.Add("关联索引读取失败："+ex.Message); if(strict)throw new IOException("关联索引损坏，未覆盖原文件；请先修复索引",ex); return new(); }
 }
 static void ValidateAssociation(string key,VideoContext? context) {
  if(context==null || string.IsNullOrWhiteSpace(context.Source) || context.Source=="测试" || context.SessionId<=0 || context.BatchId<0
   || key!=context.AssociationKey || context.Tracking==null || context.LastSku==null || context.Purchases==null
   || context.Purchases.Any(p=>p==null || string.IsNullOrWhiteSpace(p.Po) || p.Outer==null))
   throw new IOException("关联索引内容无效（批次标识或采购明细），已保留原文件");
 }
 public void Associate(VideoContext context) {
  if (context.SessionId <= 0 || context.Source == "测试") return;
  ValidateAssociation(context.AssociationKey,context);
  lock(sync) {
   Directory.CreateDirectory(root); ArchiveLayout.Checked(root,root,true);
   var index = Associations(true); index[context.AssociationKey] = context.Copy();
   ArchiveLayout.AtomicJson(root,AssociationFile,index);
  }
 }
 // Batch form used by the library view's background refresh: one validated write, only when something changed.
 public int AssociateMany(IEnumerable<VideoContext> contexts) {
  var list=contexts.Where(c=>c!=null&&c.SessionId>0&&c.Source!="测试").ToList(); if(list.Count==0) return 0;
  foreach(var c in list) ValidateAssociation(c.AssociationKey,c);
  lock(sync) {
   Directory.CreateDirectory(root); ArchiveLayout.Checked(root,root,true);
   var index=Associations(true); var changed=0;
   foreach(var c in list) {
    if(index.TryGetValue(c.AssociationKey,out var old) && JsonSerializer.Serialize(old)==JsonSerializer.Serialize(c)) continue;
    index[c.AssociationKey]=c.Copy(); changed++;
   }
   if(changed>0) ArchiveLayout.AtomicJson(root,AssociationFile,index);
   return changed;
  }
 }
 public List<LibraryItem> List(string query="") {
  lock(sync) {
   Warnings.Clear(); var index=Associations(); var result=new List<LibraryItem>();
   foreach (var file in ArchiveLayout.Sidecars(root)) {
    try {
     var s=ArchiveLayout.Read(root,file); var linked=s.Context.Copy();
     if(index.TryGetValue(s.Context.AssociationKey,out var current)) linked.Purchases=current.Purchases.ToList();
     var pending = linked.Source=="无采购单" && linked.Purchases.Count>0 && !Path.GetDirectoryName(s.File)!.Equals(ArchiveLayout.Folder(root,linked,s.RequestedAt),StringComparison.OrdinalIgnoreCase);
     var item=new LibraryItem(s,linked,pending,File.Exists(s.File)?new FileInfo(s.File).Length:0,new FileInfo(file).LastWriteTimeUtc.Ticks);
     if(item.Matches(query))result.Add(item);
    }catch(Exception ex){Warnings.Add(Path.GetFileName(file)+"："+ex.Message);}
   }
   return result.OrderByDescending(x=>x.At).ToList();
  }
 }
 LibraryItem Fresh(LibraryItem shown) {
  var s=ArchiveLayout.Read(root,shown.File+".json");
  if(s.Id!=shown.Id || new FileInfo(shown.File+".json").LastWriteTimeUtc.Ticks!=shown.Stamp) throw new IOException("所选录像状态已变化，请刷新后重新预览确认");
  var file=new FileInfo(ArchiveLayout.Checked(root,s.File));
  if(!file.Exists||file.Length<=0||file.Length!=shown.Bytes)throw new IOException("录像文件缺失或大小已变化，请刷新后重新确认");
  return shown with { Segment=s, Bytes=file.Length };
 }
 public void Edit(LibraryItem shown,string note,bool protect,string? activeId) {
  lock(sync) {
   if(shown.Id==activeId)throw new IOException("当前正在录像，请停录后在索引中编辑；浮窗可编辑当前录像备注");
   var item=Fresh(shown);if(note.Length>300 || note.Contains('\0'))throw new ArgumentException("备注最多300字，不能包含空字符");
   item.Segment.Note=note;item.Segment.Protected=protect;ArchiveLayout.Write(root,item.Segment);
  }
 }
 public static bool CleanupEligible(LibraryItem item,int days,DateTimeOffset now,string? activeId) => days is 90 or 180 && item.State=="saved" && item.Segment.EndAt!=null && item.Segment.EndAt<=now.AddDays(-days) && !item.Protected && item.Id!=activeId && item.Bytes>0;
 public List<LibraryItem> CleanupCandidates(int days,DateTimeOffset now,string? activeId) => List().Where(i=>CleanupEligible(i,days,now,activeId)).ToList();
 IEnumerable<string> OwnedFiles(VideoSegment s) {
  yield return ArchiveLayout.Checked(root,s.File);
  foreach(var suffix in new[]{".log",".watermark.txt"})if(File.Exists(s.File+suffix))yield return ArchiveLayout.Checked(root,s.File+suffix);
  foreach(var name in s.Photos) {
   if(name!=Path.GetFileName(name) || !name.StartsWith(Path.GetFileName(s.File)+"_",StringComparison.OrdinalIgnoreCase) || !(name.EndsWith(".jpg",StringComparison.OrdinalIgnoreCase)||name.EndsWith(".png",StringComparison.OrdinalIgnoreCase)))throw new IOException("照片索引不属于当前录像，已拒绝文件操作");
   var full=ArchiveLayout.Checked(root,Path.Combine(Path.GetDirectoryName(s.File)!,name));if(File.Exists(full))yield return full;
  }
 }
 // Called only after the UI has previewed, selected and explicitly confirmed the exact candidates.
 public (int Count,List<string> Errors) DeleteConfirmed(IReadOnlyList<LibraryItem> selected,int days,DateTimeOffset now,string? activeId,bool confirmed) {
  if(!confirmed)throw new InvalidOperationException("必须预览勾选并确认删除");
  lock(sync) {
   var count=0;var errors=new List<string>();
   foreach(var shown in selected.DistinctBy(s=>s.Id)) {
    try {
     var item=Fresh(shown);if(!CleanupEligible(item,days,now,activeId))throw new IOException("录像不再符合候选条件（在录/保护/异常/时间）");
     var paths=OwnedFiles(item.Segment).ToArray(); // Validate the entire set before deleting anything.
     foreach(var p in paths)File.Delete(ArchiveLayout.Checked(root,p));
     File.Delete(ArchiveLayout.Checked(root,item.File+".json"));count++;
    }catch(Exception ex){errors.Add(shown.Tracking+"："+ex.Message);}
   }
   return(count,errors);
  }
 }
 // The one delete path used by the library UI ("删除所选", including old-recording cleanup).
 // Re-reads every sidecar under the lock: the active recording is always skipped, protected
 // segments are skipped unless explicitly included, and a missing media file only cleans the index.
 public LibraryDeleteResult DeleteSelected(IReadOnlyList<LibraryItem> selected,string? activeId,bool includeProtected,bool confirmed) {
  if(!confirmed)throw new InvalidOperationException("必须在确认框中明确确认删除");
  lock(sync) {
   int deleted=0,missing=0;var skipped=new List<string>();var errors=new List<string>();var touched=new HashSet<string>();
   foreach(var shown in selected.DistinctBy(s=>s.Id)) {
    try {
     var sidecar=ArchiveLayout.Checked(root,shown.File+".json");
     if(!File.Exists(sidecar)){skipped.Add(shown.Tracking+"：索引已不存在（可能已被删除）");continue;}
     var s=ArchiveLayout.Read(root,sidecar);
     if(s.Id!=shown.Id){errors.Add(shown.Tracking+"：索引已被其他录像替换，请刷新后重试");continue;}
     if(s.Id==activeId){skipped.Add(shown.Tracking+"：正在录像");continue;}
     var media=new FileInfo(ArchiveLayout.Checked(root,s.File));
     if(s.State is "recording" or "starting" && media.Exists && DateTime.UtcNow-media.LastWriteTimeUtc<TimeSpan.FromMinutes(2)){skipped.Add(shown.Tracking+"：文件可能仍在写入");continue;}
     if(s.Protected&&!includeProtected){skipped.Add(shown.Tracking+"：售后保护");continue;}
     if(media.Exists&&shown.Bytes>0&&media.Length!=shown.Bytes){errors.Add(shown.Tracking+"：文件大小已变化，请刷新后重新确认");continue;}
     if(!media.Exists)missing++;
     var paths=OwnedFiles(s).ToArray(); // Validate the entire set before deleting anything.
     foreach(var p in paths)File.Delete(ArchiveLayout.Checked(root,p));
     File.Delete(sidecar);deleted++;touched.Add(s.Context.AssociationKey);
    }catch(Exception ex){errors.Add(shown.Tracking+"："+ex.Message);}
   }
   var pruned=0;
   try{pruned=PruneAssociations(touched);}catch(Exception ex){Warnings.Add("关联索引整理失败（录像已删除）："+ex.Message);}
   return new(deleted,missing,skipped,errors,pruned);
  }
 }
 // Drops association entries that only deleted segments referenced. A damaged index is left untouched.
 int PruneAssociations(HashSet<string> candidates) {
  if(candidates.Count==0||!File.Exists(AssociationFile))return 0;
  Dictionary<string,VideoContext> index;try{index=Associations(true);}catch{return 0;}
  var still=new HashSet<string>();
  foreach(var f in ArchiveLayout.Sidecars(root)){try{still.Add(ArchiveLayout.Read(root,f).Context.AssociationKey);}catch{}}
  var removed=candidates.Count(k=>!still.Contains(k)&&index.Remove(k));
  if(removed>0)ArchiveLayout.AtomicJson(root,AssociationFile,index);
  return removed;
 }
 public string ArchiveTarget(LibraryItem item) => ArchiveLayout.Candidate(root,item.Linked,item.Segment.RequestedAt);
 // Same-volume moves, explicit consent, no wildcard/recursive move, and best-effort rollback.
 public string ArchiveConfirmed(LibraryItem shown,string expectedTarget,string? activeId,bool confirmed) {
  if(!confirmed)throw new InvalidOperationException("必须确认归档原路径和新路径");
  lock(sync) {
   var item=Fresh(shown);
   if(item.Id==activeId || item.State!="saved" || !item.PendingArchive)throw new IOException("仅已停录、已验证的后补采购录像可以确认归档");
   var index=Associations(true);if(!index.TryGetValue(item.Linked.AssociationKey,out var current)||!current.Purchases.SequenceEqual(item.Linked.Purchases))throw new IOException("采购关联已变化，请刷新后重新确认");
   var target=ArchiveTarget(item);if(!target.Equals(expectedTarget,StringComparison.OrdinalIgnoreCase))throw new IOException("归档目标已被占用，请刷新后重新确认；没有覆盖旧文件");
   var source=item.File;var files=OwnedFiles(item.Segment).ToArray();
   if(!File.Exists(source))throw new FileNotFoundException("原录像文件不存在");
   Directory.CreateDirectory(Path.GetDirectoryName(target)!);ArchiveLayout.Checked(root,target);
   using var reservation=new FileStream(target+".reserve",FileMode.CreateNew,FileAccess.Write,FileShare.None);
   var moved=new List<(string From,string To)>();var wrote=false;
   try {
    if(File.Exists(target)||File.Exists(target+".json"))throw new IOException("目标已存在，已取消归档");
    foreach(var p in files) {
     var dest=ArchiveLayout.Checked(root,target+p[source.Length..]);
     File.Move(ArchiveLayout.Checked(root,p),dest,false);moved.Add((p,dest));
    }
    var clone=JsonSerializer.Deserialize<VideoSegment>(JsonSerializer.Serialize(item.Segment),ArchiveLayout.Json)!;
    clone.File=target;clone.Context=item.Linked.Copy();clone.PreviousFile=source;clone.ArchivedAt=DateTimeOffset.Now;
    clone.Photos=item.Segment.Photos.Select(n=>Path.GetFileName(target)+n[Path.GetFileName(source).Length..]).ToList();
    ArchiveLayout.Write(root,clone);wrote=true;
    File.Delete(ArchiveLayout.Checked(root,source+".json"));return target;
   }catch(Exception ex) {
    var failures=new List<string>();if(wrote)try{File.Delete(ArchiveLayout.Checked(root,target+".json"));}catch(Exception e){failures.Add(e.Message);}
    foreach(var pair in moved.AsEnumerable().Reverse())try{File.Move(ArchiveLayout.Checked(root,pair.To),ArchiveLayout.Checked(root,pair.From),false);}catch(Exception e){failures.Add(e.Message);}
    if(failures.Count>0)throw new IOException("归档未完成，部分文件回滚失败，请保留两个目录并检查："+string.Join("；",failures),ex);
    throw;
   }finally{reservation.Dispose();File.Delete(ArchiveLayout.Checked(root,target+".reserve"));}
  }
 }
}
