# 收货小助手（聚水潭扫码入库）

面向仓库收货台的 Windows 桌面工具：扫物流单号、扫商品条码，自动对照聚水潭采购单统计实收，
一键生成入库单；同时按物流单号自动录制开箱视频，方便售后举证。

当前版本：**3.0.2**　·　[下载最新版](https://github.com/Shitsuki4/jst-batch-inbound/releases)

## 功能

- **扫码收货**：扫物流单号开批次，扫商品条码累加实收（同一商品扫几次记几件），数量只允许正整数。
- **采购单对单**：自动关联聚水潭采购单，实时显示应收、实收、差异；同一 SKU 在采购单多行出现时按 SKU 合并计算。
- **入库**：按实收生成聚水潭入库单，提交前留快照、按指纹防重复，网络超时不会自动重建，最后回读核对数量。
- **无采购单收货 / 多采购单联合收货**：登记无单到货、多张采购单合并收货与汇总对单。
- **PDA 联动**：安卓 PDA 通过局域网配对后扫码，配对码有按 IP 限流和输错作废保护。
- **自动录像**：确认物流单号即开始录制，切换单号自动分段；监控浮窗可移动、缩放、置顶。
- **导出**：差异报告、扫码流水 CSV，支持按时间、采购单、物流单号、商品编码筛选。

## 安装

在 [Releases](https://github.com/Shitsuki4/jst-batch-inbound/releases) 下载：

| 文件 | 适用 |
| --- | --- |
| `ReceivingAssistant-<版本>-Offline-Setup.exe`（约 500 MiB） | 推荐。自带 .NET、Python 后端、FFmpeg、WebView2，内网或断网电脑也能装 |
| `jst-batch-inbound-desktop-<版本>-win-x64.zip`（约 86 MiB） | 便携版，缺依赖时首次启动会通过 `winget` 安装 |
| `ReceivingAssistant-Deps-<版本>-Setup.exe`（运行依赖安装程序） | 与便携版配套：先运行它安装 FFmpeg/ffprobe 和 WebView2 固定运行时，便携版即可在断网电脑使用，无需 `winget`。便携 ZIP + 依赖 exe = 离线完整安装包之外的另一种离线方案 |

- 按当前用户安装，不需要管理员权限。
- 运行依赖安装程序把组件装到 `%LOCALAPPDATA%\ReceivingAssistant\runtime`，在“应用和功能”里显示为“收货小助手运行依赖”，可单独卸载，不碰业务数据。
- 业务数据、设置、登录资料在 `%LOCALAPPDATA%\ReceivingAssistant\v3`，升级和卸载默认都保留。
- **升级前请先正常退出小助手**；业务服务还在运行时安装程序会拒绝继续，以免装出新旧混合的文件。
- 安装包暂未做代码签名，Windows 可能提示“未知发布者”。

首次使用：进入录像设置选择摄像头和录像目录，并**显式保存磁盘预警 / 停止阈值**，未配置阈值时不会开始录像。

## 架构

```
desktop/
├── src/                 WPF 宿主 Receiving.Native.exe（.NET 10）
│                        监控浮窗、FFmpeg 录像、录像索引、WebView2 工作区
├── backend/app/         Python 本地服务 Receiving.Service.exe
│                        聚水潭接口、SQLite、PDA、导出、网页界面（web/）
├── installer/           Inno Setup 离线安装包脚本与说明文档
├── tests/               录像器回归测试
└── build-*.ps1          构建脚本
```

宿主启动时为后端分配端口和令牌；后端只监听 `127.0.0.1`，并校验 Host / Origin。
后端端口被占用时以退出码 75 退出，宿主换端口重启（最多 5 次）。

## 开发

需要 Windows、.NET 10 SDK、Python 3.11、WebView2、FFmpeg/ffprobe。

```powershell
# 运行宿主
dotnet run --project desktop/src/Receiving.Native.csproj

# 构建便携包（含 Python 回归与录像回归）
powershell -ExecutionPolicy Bypass -File desktop/build-windows.ps1

# 只构建不跑测试
powershell -ExecutionPolicy Bypass -File desktop/build-windows.ps1 -SkipTests
```

后端测试（Linux / macOS 也能跑大部分）：

```bash
cd desktop/backend/app
pip install cryptography qrcode Pillow
python regression_runner.py        # 回归套件
python concurrency_fixes_test.py   # 单个测试文件直接运行
```

离线安装包构建、安装级验收等完整说明见 [`desktop/README.md`](desktop/README.md)。

## 发布

1. 修改 `desktop/src/Receiving.Native.csproj` 里的 `<Version>`（标签版本必须与之一致，否则 CI 直接失败）。
2. 合并到 `main` 后打标签：

```powershell
git tag desktop-vX.Y.Z
git push origin desktop-vX.Y.Z
```

CI（[`build-desktop-exe.yml`](.github/workflows/build-desktop-exe.yml)）会构建便携 ZIP、离线安装包和运行依赖安装程序，
跑完回归与安装 / 覆盖升级 / 卸载验收后创建 GitHub Release。PR 修改 `desktop/**` 时也会自动构建。

## 版本记录

### 3.0.2

- 新增运行依赖安装程序（`ReceivingAssistant-Deps-<版本>-Setup.exe`，本地构建名“收货小助手-<版本>-运行依赖安装程序”）：与便携 ZIP 配套，离线安装 FFmpeg/ffprobe 和 WebView2 固定运行时到 `%LOCALAPPDATA%\ReceivingAssistant\runtime`，断网电脑不再需要 `winget`。
- 便携版查找依赖时，在程序旁 `runtime\` 之后、系统安装之前，优先使用运行依赖安装程序装好的共享组件；离线完整安装版行为不变。
- 环境检查窗口提示：断网电脑可先运行「运行依赖安装程序」。
- CI 构建并验收运行依赖安装程序（静默安装、`ffmpeg -version` / `ffprobe -version`、便携版依赖检查、静默卸载），随 Release 一起发布。

### 3.0.1

- 修复快速连扫时重复计数、后一件覆盖前一件导致丢件的问题；扫码改为排队处理，重试按请求编号去重。
- 所有收货数量（含无采购单、联合收货）只允许正整数。
- 修复同一 SKU 在采购单多行出现时差异和进度算错；商品库按原始 SKU 判重（区分大小写）。
- 修复数据库并发读写可能提前提交半截入库数据、出错不回滚的问题。
- 修复局域网设备可通过慢速请求卡住桌面端；PDA 配对限流改为按 IP，输错 5 次作废配对码。
- 安装包升级时检测后台服务、清理旧版运行时文件；后端端口冲突自动换端口。
- CI 发布加固：版本号取自 csproj、手动发布参数校验、最小权限、Action 固定 SHA。
- 移除旧版单文件客户端（2.0.21），源码保留在标签 [`legacy-final-2.0.21`](https://github.com/Shitsuki4/jst-batch-inbound/tree/legacy-final-2.0.21)。

### 3.0.0

- 桌面版首发：WPF 宿主 + Python 服务，监控浮窗、扫码自动录像、离线完整安装包。
