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
- `desktop/dist/jst-batch-inbound-desktop-3.0.2-win-x64.zip`
- `desktop/dist/SHA256SUMS.txt`

便携 ZIP（约 86 MiB）不内置 FFmpeg/WebView2 payload，适合已联网或已装好依赖的机器。
首次启动若缺少依赖，现有检查窗口会调用 Windows `winget` 安装；断网电脑可先运行下面的
“运行依赖安装程序”。

便携版查找依赖的顺序：

- FFmpeg/ffprobe：程序旁 `runtime\ffmpeg` → 运行依赖安装程序装的
  `%LOCALAPPDATA%\ReceivingAssistant\runtime\ffmpeg` → `native-settings.json` 里保存的路径 →
  WinGet Links → PATH。
- WebView2：程序旁 `runtime\webview2` → `%LOCALAPPDATA%\ReceivingAssistant\runtime\webview2`
  （须含 `msedgewebview2.exe`）→ 系统 Evergreen 运行时。

离线完整安装版（存在 `installed-mode.txt`）只使用自带的 `runtime\`，不读取共享目录，行为不变。

## 运行依赖安装程序

与便携 ZIP 配套的独立安装程序：`便携 ZIP + 运行依赖安装程序` 是离线完整安装包之外的另一种离线方案。
它使用与离线完整安装包相同、经 SHA256 校验的 WebView2 固定运行时 CAB 和 FFmpeg full build，
按当前用户（无需管理员）安装到：

- `%LOCALAPPDATA%\ReceivingAssistant\runtime\ffmpeg`（ffmpeg.exe、ffprobe.exe、LICENSE、README.txt，及共享构建需要的 DLL）
- `%LOCALAPPDATA%\ReceivingAssistant\runtime\webview2`（并按微软要求为 AppContainer 授予读取/执行权限）

在“应用和功能”里单独显示为“收货小助手运行依赖”，可单独卸载；升级时先清空这两个目录。
安装、升级、卸载都不会触碰业务数据目录 `%LOCALAPPDATA%\ReceivingAssistant\v3`。
检测到小助手正在使用这些组件（如正在录像）时会拒绝安装 / 卸载，请先正常退出小助手。

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build-deps-windows.ps1 `
  -WebView2Cab   <Microsoft.WebView2.FixedVersionRuntime.*.x64.cab> `
  -WebView2CabSha256 <cab 的 SHA256> `
  -FfmpegArchive <ffmpeg-*-full_build.zip 或 .7z> `
  -FfmpegArchiveSha256 <压缩包 SHA256> `
  -InnoCompiler  <Inno Setup 6 的 ISCC.exe>
```

同样支持 `-FfmpegBuildDir`。产物为
`desktop/dist/deps-installer/收货小助手-<版本>-运行依赖安装程序.exe`（工作流传
`-InstallerName ReceivingAssistant-Deps-<版本>-Setup` 得到 ASCII 文件名），哈希追加到
`desktop/dist/SHA256SUMS.txt`。它不依赖便携包，也不会动 `desktop/dist/installer`，可以在离线完整安装包之后构建。

安装级验收（静默安装、与 payload 哈希比对、`ffmpeg -version` / `ffprobe -version`、WebView2 ACL、
便携版在只留 System32 的 PATH 下 `--dependency-check` 必须从共享目录解析到 FFmpeg 与 WebView2、
重装清理残留、静默卸载并确认移除、业务数据目录不变）：

```powershell
powershell -ExecutionPolicy Bypass -File desktop/verify-deps-installer.ps1 `
  -Installer desktop/dist/deps-installer/收货小助手-3.0.2-运行依赖安装程序.exe `
  -PayloadRoot desktop/dist/deps-payload `
  -PortableRoot desktop/dist/portable
```

报告写入 `desktop/build/verify-deps/deps-installer-report.json`。注意：验收会真实安装到当前用户的
`%LOCALAPPDATA%\ReceivingAssistant\runtime`，若本机已装运行依赖会拒绝执行。

## 完整离线安装程序

面向内网或无法联网的收货电脑，构建一个自带全部运行时的单文件安装程序（约 500 MiB）：

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build-windows.ps1 -SkipTests
powershell -ExecutionPolicy Bypass -File desktop/build-offline-windows.ps1 `
  -WebView2Cab   <Microsoft.WebView2.FixedVersionRuntime.*.x64.cab> `
  -WebView2CabSha256 <cab 的 SHA256> `
  -FfmpegArchive <ffmpeg-*-full_build.zip 或 .7z> `
  -FfmpegArchiveSha256 <压缩包 SHA256> `
  -InnoCompiler  <Inno Setup 6 的 ISCC.exe>
```

已在本机解压好 FFmpeg 时，可改用 `-FfmpegBuildDir <含 bin\ffmpeg.exe 的目录>`，跳过压缩包解压。

安装程序默认沿用中文文件名。GitHub Release 上传会丢弃文件名里的非 ASCII 字符，因此
工作流额外传 `-InstallerName ReceivingAssistant-3.0.2-Offline-Setup`，让发布产物使用 ASCII
文件名，再通过 Release 的 asset label 显示中文名。本机构建不传该参数即保持中文文件名。

离线安装程序会把 `.NET 自包含宿主 + Python 后端 + FFmpeg/ffprobe + WebView2 固定运行时`
一起打进安装包，安装与启动都无需联网下载这些依赖（ERP 业务仍需要网络）。安装完成后会为
`runtime\webview2` 授予 AppContainer 读取/执行权限（`S-1-15-2-1` / `S-1-15-2-2`），
否则 Windows 10 上 WebView2 渲染进程无法加载、业务界面可能闪退；覆盖升级重建该目录后也会重新授权。
它会写入 `installed-mode.txt`，程序据此把业务数据放到 `%LOCALAPPDATA%\ReceivingAssistant\v3`，
卸载默认保留，不覆盖旧数据。

构建脚本会校验两个依赖的 SHA256、组装 `desktop/dist/offline-payload`、用 Inno Setup 编译出
`desktop/dist/installer/收货小助手-<版本>-离线完整安装程序.exe`，并把它的哈希追加到
`desktop/dist/SHA256SUMS.txt`。

### 卸载

开始菜单有“卸载收货小助手”（指向 `{app}\unins000.exe`）。交互卸载先显示数据选项窗口：

| 选项 | 行为 |
| --- | --- |
| 保留全部数据（默认） | 只删程序，与以前一致 |
| 自定义 | 勾选要保留的类别，未勾选的删除；只有本机存在的类别可勾选，并显示占用空间 |
| 完全卸载 | 二次确认（列出将删除的路径与大小、建议先备份录像）后删除整个 v3 目录和安装目录内的导出 |

数据类别与路径（`v3` = `%LOCALAPPDATA%\ReceivingAssistant\v3`，`{app}` = 安装目录）：

| 类别 | 路径 |
| --- | --- |
| 收货数据库与自动备份 | `v3\data\*.db*`（jst / catalog / unplanned / purchase_watch / joint_receipts，含 -wal/-shm）、`v3\data\backups`、`v3\service-exit-warning.txt` |
| 录像文件 | `v3\录像`（含 `video-associations.json`）；设置里改到 v3 内其他目录时一并处理 |
| 导出文件 | `{app}\backend\导出`（打包后端把导出写在 exe 旁） |
| 本机设置与登录记忆 | `v3\native-settings.json`、`v3\data\login-session.dpapi`（DPAPI 保持登录）、`v3\data\edge-login`、`v3\data\pda-private`（PDA 配对证书） |
| 缓存与日志 | `v3\webview-profile`、`v3\video-cues`、`v3\native-errors.log`、`v3\data\logs`、`v3\data\report-clipboard`、连接/实例文件、`{app}\backend\data`（diag.log） |

删除只在 `v3` 与 `{app}` 内进行；`%LOCALAPPDATA%\ReceivingAssistant\runtime`（运行依赖安装程序）
永远不动，录像目录改到 v3 之外时也不会删除。

小助手仍在运行时（WMI 查到可执行文件位于 `{app}` 内的进程，或 `Local\ReceivingAssistantInstalled` /
`Local\ReceivingAssistantService` 互斥体被占用），卸载程序列出进程并提供“强制结束并继续卸载 / 取消”，
提示可能中断 ERP 写入、未保存录像会丢失。确认后在标准“确定要卸载”之后用 `taskkill /F /PID …`
逐个结束 `{app}` 内的进程（不带 `/T`，不波及用户另外打开的程序），确认进程退出、互斥体释放后再继续；
结束失败则中止卸载，程序和数据都不改动。之后 `AppMutex` 仍作为最后一道检查（例如便携版后端仍占用服务互斥体）。
安装 / 覆盖升级在运行中仍然直接拒绝，不提供强制结束。

静默卸载：

```powershell
unins000.exe /VERYSILENT /SUPPRESSMSGBOXES             # 保留全部数据；小助手在运行则拒绝卸载
unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /FORCECLOSE # 运行中也强制结束后继续
unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /PURGEDATA  # 删除全部数据（不可恢复）
```

### 安装级验收

安装级验收（静默安装、只留 System32 的 PATH 下做依赖检查、与 payload 逐文件哈希比对、
WebView2 AppContainer ACL、重装保留用户数据并重新授权、开始菜单快捷方式含卸载入口、
小助手运行中静默卸载被拒绝且 `/FORCECLOSE` 能结束进程并卸载、默认静默卸载保留用户数据、卸载后重装、
`/PURGEDATA` 删除 v3 数据但不碰 `runtime` 目录）：

```powershell
powershell -ExecutionPolicy Bypass -File desktop/verify-offline-installer.ps1 `
  -Installer desktop/dist/installer/收货小助手-3.0.2-离线完整安装程序.exe `
  -PayloadRoot desktop/dist/offline-payload
```

报告中 `ok` 为 `true` 表示全部检查通过。`/PURGEDATA` 检查会删除本机的
`%LOCALAPPDATA%\ReceivingAssistant\v3`，所以只在 GitHub Actions 上自动执行；本机需要时加 `-AllowDataPurge`（请先备份）。

## GitHub Actions

`.github/workflows/build-desktop-exe.yml` 支持：

- `workflow_dispatch` 手动构建。
- 修改 `desktop/**` 或该工作流时运行 PR 构建。
- 推送 `desktop-v*` 标签时构建并创建 GitHub Release。

工作流会下载固定版本的 WebView2 固定运行时、FFmpeg full build 和 Inno Setup 并校验
SHA256，然后依次构建便携 ZIP、编译完整离线安装程序并跑安装级验收、编译运行依赖安装程序并跑验收，
最后把便携 ZIP、离线安装程序与运行依赖安装程序一起作为产物上传；标签构建会把它们发布到 Release。

要把产物补传到已存在的 Release（例如 `desktop-v3.0.2`），在手动触发时填写
`publish_release_tag` 输入框即可。

发布示例：

```powershell
git tag desktop-v3.0.2
git push origin desktop-v3.0.2
```
