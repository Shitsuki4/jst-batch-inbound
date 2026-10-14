using System.IO;
using System.Text;
using System.Windows.Media;
namespace Receiving.Native;
public sealed class VideoSounds : IDisposable {
 readonly string folder;readonly MediaPlayer player=new();DateTimeOffset last;string lastKind="";
 public string Error {get;private set;}="";
 public VideoSounds(string root){folder=Path.Combine(root,"video-cues");player.MediaFailed+=(_,e)=>Error="提示音播放失败："+e.ErrorException.Message;}
 public void Play(string kind,UserSettings s,bool force=false){
  var enabled=kind switch{"start"=>s.SoundStart,"stop"=>s.SoundStop,"saved"=>s.SoundSaved,"duplicate"=>s.SoundDuplicate,"error"=>s.SoundError,_=>false};
  if(!force&&!enabled||s.SoundVolume==0)return;
  if(kind==lastKind&&DateTimeOffset.Now-last<TimeSpan.FromMilliseconds(600))return;
  last=DateTimeOffset.Now;lastKind=kind;
  try{
   Directory.CreateDirectory(folder);var path=Path.Combine(folder,kind+".wav");
   if(!File.Exists(path))File.WriteAllBytes(path,Wave(kind));
   player.Stop();player.Open(new Uri(path));player.Volume=s.SoundVolume/100.0;player.Play();Error="";
  }catch(Exception ex){Error="提示音播放失败："+ex.Message;}
 }
 static byte[] Wave(string kind){
  var tones=kind switch{"start"=>new[]{660,880},"stop"=>new[]{660,440},"saved"=>new[]{880,1100},"duplicate"=>new[]{440,440},"error"=>new[]{220,330,220},_=>throw new ArgumentException("未知提示音")};
  const int rate=22050,block=3307;var samples=block*tones.Length;using var stream=new MemoryStream();using var w=new BinaryWriter(stream,Encoding.ASCII,true);
  w.Write(Encoding.ASCII.GetBytes("RIFF"));w.Write(36+samples*2);w.Write(Encoding.ASCII.GetBytes("WAVEfmt "));w.Write(16);w.Write((short)1);w.Write((short)1);w.Write(rate);w.Write(rate*2);w.Write((short)2);w.Write((short)16);w.Write(Encoding.ASCII.GetBytes("data"));w.Write(samples*2);
  foreach(var tone in tones)for(var n=0;n<block;n++){var envelope=Math.Min(1,n/160.0)*Math.Min(1,(block-300-n)/250.0);envelope=Math.Max(0,envelope);w.Write((short)(5000*envelope*Math.Sin(2*Math.PI*tone*n/rate)));}
  w.Flush();return stream.ToArray();
 }
 public void Dispose(){player.Close();}
}
