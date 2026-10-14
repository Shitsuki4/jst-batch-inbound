using System.Net;
using System.Net.Sockets;
using NetMQ;
using NetMQ.Sockets;
namespace Receiving.Native;

// Private loopback-only control channel to our FFmpeg child; never carries
// camera frames. One request/ack is serialized by Recorder.gate. Socket lifetime
// stays on one worker thread, including timeout/disposal (NetMQ thread affinity).
internal static class VideoOverlay {
 public static string NewEndpoint(){using var listener=new TcpListener(IPAddress.Loopback,0);listener.Start();return "tcp://127.0.0.1:"+((IPEndPoint)listener.LocalEndpoint).Port;}
 static void Validate(string endpoint){if(!Uri.TryCreate(endpoint,UriKind.Absolute,out var uri)||uri.Scheme!="tcp"||uri.Host!="127.0.0.1"||uri.Port<=0||uri.AbsolutePath!="/"||uri.Query!=""||uri.Fragment!=""||uri.UserInfo!="")throw new ArgumentException("叠字控制仅允许本机临时端口");}
 public static string Filter(string endpoint){Validate(endpoint);return "zmq=b='"+endpoint.Replace(":","\\:")+"'";}
 public static string Command(string text){if(text.Contains('\0'))throw new ArgumentException("叠字不能包含空字符");return "drawtext text '"+text.Replace("'","'\\''")+"'";}
 public static Task SendAsync(string endpoint,string text){Validate(endpoint);var command=Command(text);return Task.Run(()=>{
  using var socket=new RequestSocket();socket.Options.Linger=TimeSpan.Zero;socket.Connect(endpoint);
  if(!socket.TrySendFrame(TimeSpan.FromSeconds(1),command)||!socket.TryReceiveFrameString(TimeSpan.FromSeconds(2),out var reply))throw new IOException("叠字控制超时，未收到录像进程确认");
  if(!reply.StartsWith("0 ",StringComparison.Ordinal))throw new IOException("录像进程拒绝叠字更新："+reply);
 });}
}
