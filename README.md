# 收货小助手 / 扫码入库助手（聚水潭）

仓库只保留 3.0 桌面版 [`desktop`](desktop)：WPF 宿主负责监控浮窗、扫码自动录像和录像索引，
Python 本地服务负责聚水潭业务、SQLite、PDA 和导出。

旧版 Python + 原生 HTML/JS 单文件 EXE（`扫码入库助手/` 目录，最后版本 2.0.21）已从 `main`
删除，完整源码保留在标签 [`legacy-final-2.0.21`](https://github.com/Shitsuki4/jst-batch-inbound/tree/legacy-final-2.0.21)。

## 桌面版 3.0

首次启动默认打开监控浮窗并启用“扫描物流单号自动录像”。用户在设置中明确关闭后，状态会
持久化，关闭软件再打开仍保持关闭。磁盘预警和停止阈值未配置时不会开始录像，30 秒测试录像
必须由用户明确点击。

构建便携包：

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build-windows.ps1 -Version 3.0.0
```

产物为 `desktop/dist/portable` 和
`desktop/dist/jst-batch-inbound-desktop-<版本>-win-x64.zip`。完整构建与测试说明见
[`desktop/README.md`](desktop/README.md)。

## 发布

桌面工作流 [`.github/workflows/build-desktop-exe.yml`](.github/workflows/build-desktop-exe.yml)
支持手动触发、`desktop/**` PR 和 `desktop-v*` 标签。

- 版本号以 `desktop/src/Receiving.Native.csproj` 的 `<Version>` 为准。打 `desktop-vX.Y.Z`
  标签前先改 csproj；标签版本与 csproj 不一致时构建直接失败。
- 标签构建会创建 GitHub Release，资产名使用标签里的版本号。
- 手动触发时可填 `publish_release_tag`（格式 `desktop-vX.Y.Z`，仅限 `main` 分支），把产物追加到
  已有 Release；同名资产已存在时不会覆盖，会直接失败。

```powershell
git tag desktop-v3.0.0
git push origin desktop-v3.0.0
```

## GitHub Actions 构建结果

工作流会上传便携 ZIP、离线完整安装程序和 SHA256 校验文件，并运行 Python 回归、录像器核心
回归、FFmpeg 合成源 30 秒录像验证，以及离线安装程序的安装 / 覆盖升级 / 卸载验收。
