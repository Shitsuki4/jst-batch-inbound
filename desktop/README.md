# 收货小助手 3.0 桌面版

桌面版由 WPF 宿主 `Receiving.Native.exe` 和本地 Python 业务服务
`backend/Receiving.Service.exe` 组成。宿主负责监控浮窗、扫码自动录像、录像索引和
WebView2 工作区；Python 服务负责聚水潭业务、SQLite 数据、PDA 和导出。

## 关键默认行为

- 首次启动默认打开监控浮窗。
- 首次启动默认启用“扫描物流单号自动录像”。
- 用户在设置中明确关闭后，开关会原子写入 `native-settings.json`，关闭软件再打开仍保持关闭。
- 旧配置缺少这两个开关时会按默认开启迁移；损坏配置会保留原文件并保护性关闭录像。
- 未配置磁盘预警 GB 和停止 GB 时不会开始录像，也不会自动填入阈值。
- 30 秒测试录像只能由用户明确点击，启动软件不会主动访问摄像头。

## 本地运行

需要 Windows、.NET 10 SDK、Python 3.11、WebView2 和 FFmpeg/ffprobe。

```powershell
dotnet run --project desktop/src/Receiving.Native.csproj
```

宿主会在同目录查找 `backend/Receiving.Service.exe`。源码运行时，Python 服务可由
`desktop/backend/app/native_entry.py` 启动，或先完成下面的便携包构建后直接运行包内程序。

## 本地构建便携包

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build-windows.ps1
```

脚本会依次执行：

1. Python `compileall`。
2. 12 项 Python 回归。
3. 构建并运行 `RecorderRegression` 核心录像回归。
4. `dotnet publish` 自包含 WPF 宿主。
5. PyInstaller 构建 `backend/Receiving.Service.exe`。
6. 组装 `desktop/dist/portable`、生成 ZIP 和 SHA256。
7. 启动包内后端，调用健康检查并安全关闭。

只构建、不跑测试：

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build-windows.ps1 -SkipTests
```

执行真实 FFmpeg 合成源录像测试，验证 30 秒录制、文件增长、帧率、分辨率、逐帧解码和
跨时间画面变化：

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build-windows.ps1 -SkipTests -RunSynthetic
```

`-RunSynthetic` 需要 `SYNTH_FFMPEG`、`SYNTH_FFPROBE`，或 PATH 中可用的
`ffmpeg.exe`、`ffprobe.exe`。该测试使用 lavfi 合成画面，不访问真实摄像头。

## 产物

- `desktop/dist/portable/Receiving.Native.exe`
- `desktop/dist/portable/backend/Receiving.Service.exe`
- `desktop/dist/jst-batch-inbound-desktop-3.0.0-win-x64.zip`
- `desktop/dist/SHA256SUMS.txt`

便携包不内置约 1.4 GB 的 FFmpeg/WebView2 payload。首次启动若缺少依赖，现有检查窗口会调用
Windows `winget` 安装。

## GitHub Actions

`.github/workflows/build-desktop-exe.yml` 支持：

- `workflow_dispatch` 手动构建。
- 修改 `desktop/**` 或该工作流时运行 PR 构建。
- 推送 `desktop-v*` 标签时构建并创建 GitHub Release。

发布示例：

```powershell
git tag desktop-v3.0.0
git push origin desktop-v3.0.0
```
