using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text.Json;
namespace Receiving.Native;

// Query the selected DirectShow device, never advertise controls absent from its driver.
public static class CameraControls {
 public sealed record Control(string Key,string Label,bool Supported,int Min=0,int Max=0,int Step=0,int Default=0,int Value=0,bool AutoSupported=false,bool ManualSupported=false,bool Automatic=false,string Message="");
 static readonly (string Key,string Label,int Property)[] Properties=[("exposure","曝光",4),("focus","对焦",6),("zoom","硬件变焦",3)];
 static readonly Guid Category=new("860BB310-5D01-11D0-BD3B-00A0C911CE86"),FilterId=new("56A86895-0AD4-11CE-B03A-0020AF0BA770"),BagId=new("55272A00-42CB-11CE-8135-00AA004BB851");
 [ComImport,Guid("29840822-5B84-11D0-BD3B-00A0C911CE86"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 interface ICreateDevEnum {[PreserveSig]int CreateClassEnumerator(ref Guid category,out IEnumMoniker? result,int flags);}
 [ComImport,Guid("55272A00-42CB-11CE-8135-00AA004BB851"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 interface IPropertyBag {[PreserveSig]int Read([MarshalAs(UnmanagedType.LPWStr)]string name,[MarshalAs(UnmanagedType.Struct)]out object value,IntPtr errorLog);[PreserveSig]int Write([MarshalAs(UnmanagedType.LPWStr)]string name,[MarshalAs(UnmanagedType.Struct)]ref object value);}
 [ComImport,Guid("C6E13370-30AC-11D0-A18C-00A0C9118956"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
 interface IAMCameraControl {
  [PreserveSig]int GetRange(int property,out int min,out int max,out int step,out int defaultValue,out int flags);
  [PreserveSig]int Set(int property,int value,int flags);
  [PreserveSig]int Get(int property,out int value,out int flags);
 }
 static void Release(object? value){if(value!=null&&Marshal.IsComObject(value))Marshal.ReleaseComObject(value);}
 static T WithCamera<T>(string camera,Func<IAMCameraControl?,T> action){
  if(string.IsNullOrWhiteSpace(camera)||camera.Length>300||camera.Any(char.IsControl))throw new ArgumentException("请先选择有效摄像头");
  object? deviceEnum=null,filter=null;IEnumMoniker? devices=null;IMoniker? selected=null;
  try{
   deviceEnum=Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("62BE5D10-60EB-11D0-BD3B-00A0C911CE86"),true)!);
   var category=Category;var hr=((ICreateDevEnum)deviceEnum!).CreateClassEnumerator(ref category,out devices,0);
   if(hr<0)Marshal.ThrowExceptionForHR(hr);if(devices==null)throw new IOException("没有检测到视频设备");
   var item=new IMoniker[1];
   while(devices.Next(1,item,IntPtr.Zero)==0){
    var moniker=item[0];object? bag=null;bool keep=false;
    try{
     var bagId=BagId;moniker.BindToStorage(null!,null!,ref bagId,out bag);var props=(IPropertyBag)bag;
     var name=props.Read("FriendlyName",out var friendly,IntPtr.Zero)==0?friendly as string:"";
     var path=props.Read("DevicePath",out var devicePath,IntPtr.Zero)==0?devicePath as string:"";
     if(!string.Equals(camera,name,StringComparison.Ordinal)&&!string.Equals(camera,path,StringComparison.OrdinalIgnoreCase))continue;
     if(selected!=null)throw new IOException("发现同名摄像头，无法安全选择硬件控制目标；请断开重复设备后再查询");
     selected=moniker;keep=true;
    }finally{Release(bag);if(!keep)Release(moniker);}
   }
   if(selected==null)throw new IOException("所选摄像头不存在，请重新枚举设备");
   var filterId=FilterId;selected.BindToObject(null!,null!,ref filterId,out filter);
   return action(filter as IAMCameraControl);
  }finally{Release(filter);Release(selected);Release(devices);Release(deviceEnum);}
 }
 static Control[] Snapshot(IAMCameraControl? control)=>Properties.Select(p=>{
  if(control==null)return new Control(p.Key,p.Label,false,Message:"驱动不支持此硬件控制");
  var hr=control.GetRange(p.Property,out var min,out var max,out var step,out var def,out var caps);
  if(hr<0||min>max||step<=0||(caps&3)==0)return new Control(p.Key,p.Label,false,Message:"驱动未报告可用范围（不支持）");
  hr=control.Get(p.Property,out var value,out var flags);
  if(hr<0)return new Control(p.Key,p.Label,false,Message:"无法读取当前参数，未开放修改");
  return new Control(p.Key,p.Label,true,min,max,step,def,value,(caps&1)!=0,(caps&2)!=0,(flags&1)!=0);
 }).ToArray();
 public static Control[] Read(string camera)=>WithCamera(camera,Snapshot);
 public static Control[] Set(string camera,JsonElement args)=>WithCamera(camera,control=>{
  if(!args.TryGetProperty("control",out var key)||key.ValueKind!=JsonValueKind.String)throw new ArgumentException("缺少硬件控制项");
  var item=Snapshot(control).SingleOrDefault(p=>p.Key==key.GetString())??throw new ArgumentException("未知硬件控制项");
  if(!item.Supported)throw new IOException(item.Message);
  if(!args.TryGetProperty("automatic",out var auto)||auto.ValueKind is not(JsonValueKind.True or JsonValueKind.False))throw new ArgumentException("请选择自动或手动模式");
  var automatic=auto.GetBoolean();if(automatic&&!item.AutoSupported||!automatic&&!item.ManualSupported)throw new ArgumentException("驱动不支持所选模式");
  int value=item.Value;
  if(!automatic&&(!args.TryGetProperty("value",out var input)||!input.TryGetInt32(out value)||value<item.Min||value>item.Max||((long)value-item.Min)%item.Step!=0))throw new ArgumentException("数值超出设备范围或不符合步长");
  var property=Properties.Single(p=>p.Key==item.Key).Property;var hr=control!.Set(property,value,automatic?1:2);
  if(hr<0)throw new IOException($"驱动拒绝设置（0x{hr:X8}），请重新查询设备参数");
  var result=Snapshot(control);var actual=result.Single(p=>p.Key==item.Key);
  if(!actual.Supported||actual.Automatic!=automatic||!automatic&&actual.Value!=value)throw new IOException("设备回读与请求不符，未确认设置成功；请重新查询");
  return result;
 });
}
