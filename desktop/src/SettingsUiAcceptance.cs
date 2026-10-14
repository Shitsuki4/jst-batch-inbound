using System.Text.Json;
namespace Receiving.Native;

// Real WPF/WebView2 bridge checks, isolated from cameras, sound playback and ERP writes.
public sealed partial class MainWindow {
 static void SettingsAssert(bool value,string message){if(!value)throw new Exception(message);}
 async Task SetVideoFields(object values){
  var result=await Js("(()=>{const values="+JsonSerializer.Serialize(values)+";for(const [key,value] of Object.entries(values)){const id='video-'+key.replace(/[A-Z]/g,c=>'-'+c.toLowerCase()),el=document.getElementById(id);if(!el)throw Error('Missing '+id);if(el.type==='checkbox')el.checked=value;else el.value=value??'';el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}return true;})()");
  SettingsAssert(result.ValueKind==JsonValueKind.True,result.ToString());
 }
 async Task SaveVideoUi(bool valid){
  await WaitJs("!document.getElementById('video-save').disabled");
  await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-message').textContent='';document.getElementById('video-save').click();");
  await WaitJs("!document.getElementById('video-save').disabled && document.getElementById('video-message').textContent.length>0");
  if(valid)await WaitJs("!SettingsWorkspace.hasUnsaved() && document.getElementById('video-message').textContent.includes('已保存')");
  else SettingsAssert(await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-message').classList.contains('error')")=="true","设置应显示保存失败原因");
 }
 async Task RejectBridge(string action,object args,string text){
  var r=await Js("NativeDesk.call("+JsonSerializer.Serialize(action)+","+JsonSerializer.Serialize(args)+")");
  SettingsAssert(r.ValueKind==JsonValueKind.Object&&r.TryGetProperty("error",out var err)&&err.GetString()!.Contains(text),"应拒绝 "+action+"："+r);
 }
 async Task AcceptanceSettingsUi(){
  var checks=new List<string>();Exception? error=null;
  var report=UiTest.SettingsReload?"settings-ui-reload":"settings-ui-first";
  var config=Path.Combine(root,"native-settings.json");
  try{
   await WaitJs("Boolean(window.SettingsWorkspace && window.NativeDesk?.available && window.receivingSettingsLoaded)",60);
   SettingsAssert(!rec.Active&&rec.Current==null&&!preview.IsVisible,"设置专项不得录制或自动显示预览");
   SettingsAssert(enabled.IsChecked==settings.AutoRecordEnabled,"启动自动录像须恢复已保存状态");
   checks.Add("真实WPF/WebView2启动恢复自动录像开关，设置专项未启动相机或浮窗");
   SettingsAssert(CommandSourceAllowed(backend.Url+"/index.html")&&!CommandSourceAllowed("https://example.com/")&&!CommandSourceAllowed(backend.Url+"/api/state"),"桌面桥来源白名单");
   checks.Add("真实宿主桌面桥来源白名单");
   await web.CoreWebView2.ExecuteScriptAsync("ReceivingApp.navigate('settings');SettingsWorkspace.show('video');");
   await WaitJs("!document.getElementById('video-save').disabled");
   if(UiTest.SettingsReload){
    SettingsAssert(settings.WarningGb==12.5m&&settings.StopGb==3.25m&&settings.CleanupDays==180,"隔离配置跨进程未恢复");
    SettingsAssert(settings.OverlaySku&&settings.OverlayPurchase&&!settings.PhotoEnabled&&settings.NotesEnabled&&settings.ProtectionEnabled&&settings.ZoomEnabled,"辅助与叠字设置未恢复");
    SettingsAssert(settings.HotkeyPreview=="Ctrl+Alt+F23"&&settings.HotkeyStop=="Ctrl+Alt+F24"&&!settings.HotkeysEnabled&&hotkeys?.Registered==false,"禁用的自定义组合键不应注册");
    await WaitJs("document.getElementById('video-warning-gb').value==='12.5' && document.getElementById('video-stop-gb').value==='3.25' && document.getElementById('video-cleanup-days').value==='180'");
    checks.Add("新进程恢复磁盘阈值、清理周期、叠字和可选工具，真实UI回读一致");
    checks.Add("自定义组合键跨进程保留，禁用时不注册系统快捷键");
    SettingsAssert(settings.AutoRecordEnabled&&enabled.IsChecked==true&&!settings.ShowPreviewOnStartup,"重启后应恢复已启用录制及已关闭启动浮窗");
    await WaitJs("document.getElementById('video-enabled').checked && !document.getElementById('video-show-preview-on-startup').checked");
    checks.Add("前次明确启用后重启仍开启；启动浮窗关闭设置也恢复");
   }else{
    SettingsAssert(settings.WarningGb==null&&settings.StopGb==null&&!File.Exists(config),"首次运行不得替用户预设GB");
    await WaitJs("document.getElementById('video-warning-gb').value==='' && document.getElementById('video-stop-gb').value===''");
    checks.Add("首次界面磁盘预警/停止GB为空，不替用户填写数值");
    SettingsAssert(enabled.IsChecked==true&&settings.ShowPreviewOnStartup,"首次启动两个开关默认开启");
    await WaitJs("document.getElementById('video-enabled').checked && document.getElementById('video-show-preview-on-startup').checked");
    await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-enabled').click()");
    await WaitJs("!document.getElementById('video-enabled').disabled && !document.getElementById('video-enabled').checked");
    SettingsAssert(!UserSettings.Read(config).AutoRecordEnabled,"明确关闭应立即落盘");
    checks.Add("两个开关首次默认开启；明确关闭自动录像立即保存");
    await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-enabled').click()");
    await WaitJs("!document.getElementById('video-enabled').disabled && document.getElementById('video-message').textContent.includes('GB')");
    SettingsAssert(enabled.IsChecked!=true&&!rec.Active,"未配阈值不允许启用自动录像");
    checks.Add("实际点击自动录像开关：未配GB时原生拒绝并提示");
    // These values exist ONLY under this acceptance output, never a user's config.
    await SetVideoFields(new{warningGb=1,stopGb=2});await SaveVideoUi(false);
    SettingsAssert(UserSettings.Read(config).WarningGb==null&&settings.WarningGb==null,"无效阈值不应写入配置");
    checks.Add("真实UI提交预警不大于停止值被拒绝，未落盘");
    await SetVideoFields(new{showPreviewOnStartup=false,warningGb=12.5,stopGb=3.25,cleanupDays=180,overlayTime=false,overlayTracking=true,overlayPurchase=true,overlayOuter=true,overlaySku=true,overlayQuantity=true,photoEnabled=false,notesEnabled=true,protectionEnabled=true,zoomEnabled=true,soundStart=false,soundStop=true,soundSaved=false,soundDuplicate=true,soundError=false,soundVolume=31,hotkeysEnabled=false,hotkeyStop="Ctrl+Alt+F24",hotkeyContinue="",trackingRuleEnabled=true,trackingPrefixes="UI-,PKG-",trackingMinLength=5,trackingMaxLength=30});
    // Exercise the UI's key-combination capture without sending global OS keystrokes.
    await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-hotkey-preview').dispatchEvent(new KeyboardEvent('keydown',{key:'F23',ctrlKey:true,altKey:true,bubbles:true,cancelable:true}));");
    await WaitJs("document.getElementById('video-hotkey-preview').value==='Ctrl+Alt+F23'");
    await SaveVideoUi(true);
    SettingsAssert(settings.WarningGb==12.5m&&settings.StopGb==3.25m&&rec.MinFreeBytes==(long)(3.25m*1073741824),"宿主录像器阈值未同步");
    checks.Add("软件内自定义小数GB，经真实桥保存并同步录像器");
    SettingsAssert(!settings.OverlayTime&&settings.OverlayTracking&&settings.OverlayPurchase&&settings.OverlayOuter&&settings.OverlaySku&&settings.OverlayQuantity,"叠字选项未保存");checks.Add("六项叠字显示开关独立保存");
    SettingsAssert(!settings.PhotoEnabled&&settings.NotesEnabled&&settings.ProtectionEnabled&&settings.ZoomEnabled,"浮窗辅助项未保存");checks.Add("拍照/备注/售后保护/局部放大设置保存");
    SettingsAssert(!settings.SoundStart&&settings.SoundStop&&!settings.SoundSaved&&settings.SoundDuplicate&&!settings.SoundError&&settings.SoundVolume==31,"提示音设置未保存");checks.Add("五项提示音开关及音量保存，此专项不播放声音");
    SettingsAssert(settings.CleanupDays==180,"清理周期未保存");checks.Add("软件内选择180天清理提醒，无自动删除");
    SettingsAssert(settings.HotkeyPreview=="Ctrl+Alt+F23"&&settings.HotkeyStop=="Ctrl+Alt+F24"&&!settings.HotkeysEnabled&&hotkeys?.Registered==false,"组合键保存/禁用未生效");checks.Add("界面捕获自定义组合键并保存，禁用不注册系统热键");
    var savedBytes=File.ReadAllBytes(config);
    var disk=JsonSerializer.Deserialize<UserSettings>(File.ReadAllText(config))!;
    SettingsAssert(disk.WarningGb==settings.WarningGb&&disk.HotkeyPreview==settings.HotkeyPreview&&disk.Directory.StartsWith(Path.GetFullPath(root)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase),"配置必须仅写隔离目录");checks.Add("磁盘配置回读一致且录像路径严格位于测试目录");
    await RejectBridge("saveSettings",new{hotkeyStop="Ctrl+Alt+F23"},"相同");
    SettingsAssert(File.ReadAllBytes(config).SequenceEqual(savedBytes),"快捷键冲突不应覆盖原配置");checks.Add("重复快捷键被原生拒绝，原配置字节不变");
    await RejectBridge("saveSettings",new{warningGb=1,stopGb=2},"阈值");
    await RejectBridge("saveSettings",new{enabled=true},"不可修改");
    SettingsAssert(File.ReadAllBytes(config).SequenceEqual(savedBytes),"无效参数不应写盘");checks.Add("绕过UI的无效GB及未知设置键也被原生拒绝，原配置保留");
    var valid=await Js("NativeDesk.call('validateTracking',{tracking:'PKG-123'})");SettingsAssert(valid.TryGetProperty("valid",out var v)&&v.GetBoolean(),valid.ToString());
    await RejectBridge("validateTracking",new{tracking="OTHER-123"},"不符合");checks.Add("软件内物流规则通过真实桥生效，不截断或改写单号");
    await RejectBridge("auto",new{enabled=true,confirmed=false},"明确确认");checks.Add("自动录像原生开关必须带明确确认");
    await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('video-enabled').click()");
    await WaitJs("!document.getElementById('video-enabled').disabled && document.getElementById('video-enabled').checked");
    SettingsAssert(enabled.IsChecked==true&&!rec.Active&&rec.Current==null,"启用但未扫描物流时不应录像");checks.Add("配置后实际点击启用成功，未确认物流不启动相机");
   }
   SettingsAssert(!rec.Active&&rec.Current==null,"无相机专项意外启动录像");
   SettingsAssert(!Directory.EnumerateFiles(root,"*.mp4",SearchOption.AllDirectories).Any(),"无相机专项不应生成录像");
   var errors=await Js("window.__uiErrors");SettingsAssert(errors.ValueKind==JsonValueKind.Array&&errors.GetArrayLength()==0,errors.ToString());
   checks.Add("无未捕获前端异常，未生成录像文件");
   await CaptureWeb(report+".png");
  }catch(Exception ex){error=ex;try{await CaptureWeb(report+"-failure.png");await File.WriteAllTextAsync(Path.Combine(root,report+"-failure.json"),await web.CoreWebView2.ExecuteScriptAsync("({href:location.href,errors:window.__uiErrors,fetches:window.__uiFetch,body:document.body.innerText})"));}catch{}}
  finally{
   timer.Stop();await rec.DisposeAsync();hotkeys?.Dispose();sounds?.Dispose();preview.ExitAllowed=true;preview.Close();web.Dispose();closing=true;
   await File.WriteAllTextAsync(Path.Combine(root,report+".json"),JsonSerializer.Serialize(new{ok=error==null,scope="Real WPF/WebView2 settings only; no camera, audio playback or ERP writes",checks,error=error?.ToString()},Recorder.JsonOptions));
   Environment.ExitCode=error==null?0:1;Close();
  }
 }
}
