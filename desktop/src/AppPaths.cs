namespace Receiving.Native;
public static class AppPaths {
 public static bool Installed=>File.Exists(Path.Combine(AppContext.BaseDirectory,"installed-mode.txt"));
 public static string DataRoot=>Installed?Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"ReceivingAssistant","v3"):AppContext.BaseDirectory;
 // Shared runtime written by the separate "运行依赖安装程序" (desktop/installer/deps_setup.iss).
 // Only portable mode falls back to it; the offline full install always uses its own runtime\.
 public static string SharedRuntime=>Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"ReceivingAssistant","runtime");
 public static string LocalBrowserFolder=>Path.Combine(AppContext.BaseDirectory,"runtime","webview2");
 public static string SharedBrowserFolder=>Path.Combine(SharedRuntime,"webview2");
 public static string SharedFfmpegFolder=>Path.Combine(SharedRuntime,"ffmpeg");
 public static string? BrowserFolder{get{
  if(Directory.Exists(LocalBrowserFolder))return LocalBrowserFolder;
  if(!Installed&&File.Exists(Path.Combine(SharedBrowserFolder,"msedgewebview2.exe")))return SharedBrowserFolder;
  return null;
 }}
}
