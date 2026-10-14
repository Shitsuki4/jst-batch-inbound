# 扫码入库助手（聚水潭）

仓库包含两套可独立构建的 Windows 客户端：

- [`desktop`](desktop)：3.0 桌面版。WPF 宿主负责监控浮窗、扫码自动录像和录像索引，Python
  本地服务负责聚水潭业务、SQLite、PDA 和导出。
- [`扫码入库助手`](扫码入库助手)：旧版 Python + 原生 HTML/JS 单文件 EXE。

## 桌面版 3.0

首次启动默认打开监控浮窗并启用“扫描物流单号自动录像”。用户在设置中明确关闭后，状态会
持久化，关闭软件再打开仍保持关闭。磁盘预警和停止阈值未配置时不会开始录像，30 秒测试录像
必须由用户明确点击。

构建便携包：

```powershell
powershell -ExecutionPolicy Bypass -File desktop/build-windows.ps1
```

产物为 `desktop/dist/portable` 和
`desktop/dist/jst-batch-inbound-desktop-3.0.0-win-x64.zip`。完整构建与测试说明见
[`desktop/README.md`](desktop/README.md)。

桌面工作流 [`.github/workflows/build-desktop-exe.yml`](.github/workflows/build-desktop-exe.yml)
支持手动触发、`desktop/**` PR 和 `desktop-v*` 标签。标签构建会创建 GitHub Release。

```powershell
git tag desktop-v3.0.0
git push origin desktop-v3.0.0
```

## 旧版单文件客户端

```powershell
cd 扫码入库助手
python -m pip install -r requirements.txt
python app/server.py
```

默认访问 `http://127.0.0.1:8765`，端口被占用时会自动顺延。

旧版打包 EXE：

```powershell
cd 扫码入库助手
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller build_exe.spec --noconfirm
```

产物为 `扫码入库助手/dist/扫码入库助手.exe`。首次运行时会在 EXE 同目录生成 `data/` 和
`导出/`，升级 EXE 不会覆盖这两个目录。

旧版工作流 [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml) 仅支持
手动触发和旧路径 PR 构建，不再随 `main` 推送或 `v*` 标签自动运行。

## GitHub Actions 构建结果

两个工作流都会上传 EXE/ZIP、后端可执行文件和 SHA256 校验文件。桌面工作流还运行 Python
回归、录像器核心回归和 FFmpeg 合成源 30 秒录像验证。
