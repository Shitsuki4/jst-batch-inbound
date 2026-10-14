using System.Diagnostics;
using System.Runtime.InteropServices;
namespace Receiving.Native;
// Only video workers belong here. Never attach the ERP service to this job.
public sealed class WorkerJob:IDisposable {
 [StructLayout(LayoutKind.Sequential)]struct Basic {public long PerProcess,PerJob;public uint Flags;public UIntPtr Min,Max;public uint Processes;public UIntPtr Affinity;public uint Priority,Scheduling;}
 [StructLayout(LayoutKind.Sequential)]struct Io {public ulong A,B,C,D,E,F;}
 [StructLayout(LayoutKind.Sequential)]struct Extended {public Basic Basic;public Io Io;public UIntPtr ProcessMemory,JobMemory,PeakProcess,PeakJob;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern IntPtr CreateJobObject(IntPtr attrs,string? name);
 [DllImport("kernel32.dll")]static extern bool SetInformationJobObject(IntPtr job,int cls,ref Extended info,uint size);
 [DllImport("kernel32.dll")]static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
 [DllImport("kernel32.dll")]static extern bool CloseHandle(IntPtr handle);
 IntPtr handle;
 public WorkerJob(){handle=CreateJobObject(IntPtr.Zero,null);var x=new Extended{Basic=new Basic{Flags=0x2000}};if(handle==IntPtr.Zero||!SetInformationJobObject(handle,9,ref x,(uint)Marshal.SizeOf<Extended>()))throw new IOException("无法初始化录像进程保护");}
 public void Add(Process p){if(!AssignProcessToJobObject(handle,p.Handle)){try{p.Kill(true);}catch{}throw new IOException("无法隔离录像进程，已停止启动");}}
 public void Dispose(){if(handle!=IntPtr.Zero){CloseHandle(handle);handle=IntPtr.Zero;}}
}
