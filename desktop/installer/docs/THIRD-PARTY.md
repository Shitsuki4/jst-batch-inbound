# 第三方运行组件

本安装包内嵌以下运行组件，安装和启动不需要联网下载。

## Microsoft WebView2 Fixed Version Runtime

- 版本：154.0.4258.62（x64 固定版本运行时）
- 位置：`runtime/webview2`
- 来源：Microsoft 官方固定版本运行时下载
- 说明：固定版本不随系统自动升级，安全更新需要随新版安装包发布。

## FFmpeg / ffprobe

- 版本：Gyan.dev `8.1.2-full_build`
- 位置：`runtime/ffmpeg`
- 许可：GPLv3，完整许可见同目录 `LICENSE` 与 `README.txt`
- 来源构建：https://github.com/GyanD/codexffmpeg/releases/tag/8.1.2
- 对应源码：https://github.com/FFmpeg/FFmpeg/commit/38b88335f9
- 调用方式：以独立进程调用，允许用户替换为兼容构建。
- 注意：对外再分发时需自行复核包含外部库在内的完整对应源码提供义务。

## .NET / WPF 与 CPython

- 宿主为 .NET 自包含发布，Python 后端由 PyInstaller 打包。
- 相关许可证随各自组件保留在安装目录内。

未对上述组件施加额外复制或修改限制。
