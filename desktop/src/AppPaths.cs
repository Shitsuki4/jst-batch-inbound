namespace Receiving.Native;
public static class AppPaths {
 public static bool Installed=>File.Exists(Path.Combine(AppContext.BaseDirectory,"installed-mode.txt"));
 public static string DataRoot=>Installed?Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"ReceivingAssistant","v3"):AppContext.BaseDirectory;
 public static string? BrowserFolder=>Directory.Exists(Path.Combine(AppContext.BaseDirectory,"runtime","webview2"))?Path.Combine(AppContext.BaseDirectory,"runtime","webview2"):null;
}
