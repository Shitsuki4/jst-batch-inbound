using System.Text.Json;
using Receiving.Native;
// Data-layer checks for the rebuilt 录像查询 delete path (VideoLibrary.DeleteSelected / AssociateMany).
public static class LibraryDeleteChecks {
 public static List<string> Run(string root) {
  Directory.CreateDirectory(root);var passed=new List<string>();
  void Check(bool ok,string name){if(!ok)throw new Exception(name);passed.Add(name);}
  var at=DateTimeOffset.Now;
  VideoSegment Seed(string tracking,long session,bool protect=false,string state="saved",bool media=true){
   var c=new VideoContext{Source="普通",SessionId=session,BatchId=1,Tracking=tracking,Purchases=new(){new("PO"+session,"EXT"+session)}};
   var s=ArchiveLayout.Reserve(root,c,c.Key,at);if(media)File.WriteAllBytes(s.File,new byte[]{1,2,3,4});
   File.WriteAllText(s.File+".log","log");var photo=Path.GetFileName(s.File)+"_p.jpg";File.WriteAllBytes(Path.Combine(Path.GetDirectoryName(s.File)!,photo),new byte[]{9});s.Photos.Add(photo);
   s.State=state;s.Protected=protect;s.FirstFrameAt=at;s.EndAt=state=="recording"?null:at.AddSeconds(1);ArchiveLayout.Write(root,s);return s;
  }
  bool Gone(VideoSegment s)=>!File.Exists(s.File)&&!File.Exists(s.File+".json")&&!File.Exists(s.File+".log")&&s.Photos.All(n=>!File.Exists(Path.Combine(Path.GetDirectoryName(s.File)!,n)));
  var library=new VideoLibrary(root);
  var a=Seed("DEL-A",1);var b=Seed("DEL-B",2);var shared=Seed("DEL-SHARED",1);var prot=Seed("DEL-PROT",3,true);var live=Seed("DEL-LIVE",4,false,"recording");var missing=Seed("DEL-MISSING",5,false,"saved",false);
  var contexts=new[]{a,b,prot,missing}.Select(s=>s.Context).ToList();
  Check(library.AssociateMany(contexts)==4&&library.AssociateMany(contexts)==0,"批量关联只在内容变化时写入");
  var items=library.List();LibraryItem I(VideoSegment s)=>items.Single(i=>i.Id==s.Id);
  try{library.DeleteSelected(items,null,false,false);Check(false,"未确认不能删除");}catch(InvalidOperationException){Check(true,"未确认不能删除");}
  var r=library.DeleteSelected(new[]{I(a),I(b),I(prot),I(live),I(missing)},live.Id,false,true);
  Check(r.Deleted==3&&r.MissingMedia==1&&r.Errors.Count==0&&r.Skipped.Count==2,"删除所选：在录与售后保护跳过，缺失视频只清索引");
  Check(Gone(a)&&Gone(b)&&!File.Exists(missing.File+".json")&&File.Exists(prot.File)&&File.Exists(live.File)&&File.Exists(shared.File),"只删除所选片段及其照片 / 日志 / 索引");
  var index=JsonSerializer.Deserialize<Dictionary<string,JsonElement>>(File.ReadAllText(Path.Combine(root,"video-associations.json")))!;
  Check(index.ContainsKey("普通:1")&&!index.ContainsKey("普通:2")&&!index.ContainsKey("普通:5")&&index.ContainsKey("普通:3")&&r.AssociationsPruned==2,"只清理不再被任何录像引用的采购关联");
  var again=library.DeleteSelected(new[]{I(a)},null,false,true);Check(again.Deleted==0&&again.Skipped.Count==1&&again.Errors.Count==0,"重复删除已不存在的录像只提示跳过");
  items=library.List();var p=library.DeleteSelected(new[]{I(prot)},null,true,true);Check(p.Deleted==1&&Gone(prot),"明确包含售后保护时才删除保护片段");
  items=library.List();var grown=I(shared);File.WriteAllBytes(shared.File,new byte[]{1,2,3,4,5});
  var changed=library.DeleteSelected(new[]{grown},null,false,true);Check(changed.Deleted==0&&changed.Errors.Count==1&&File.Exists(shared.File),"确认后文件大小变化则不删除");
  items=library.List();File.WriteAllText(Path.Combine(root,"video-associations.json"),"{broken");
  var corrupt=library.DeleteSelected(new[]{I(shared)},null,false,true);Check(corrupt.Deleted==1&&File.ReadAllText(Path.Combine(root,"video-associations.json"))=="{broken","关联索引损坏时仍可删除录像且不覆盖坏索引");
  var stale=library.List().Single(i=>i.Id==live.Id);
  Check(library.DeleteSelected(new[]{stale},null,false,true).Skipped.Count==1&&File.Exists(live.File),"刚在写入的 recording 片段即使不在当前进程也跳过");
  return passed;
 }
}
