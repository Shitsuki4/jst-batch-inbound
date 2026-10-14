# 扫码入库助手（聚水潭）

聚水潭查询、扫码收货、按实收生成待入库单的 Windows 客户端。应用源码位于
[`扫码入库助手`](扫码入库助手)，使用 Python 3.11、原生 HTML/JS 和 PyInstaller 构建单文件 EXE。

## 本地运行

```powershell
cd 扫码入库助手
python -m pip install -r requirements.txt
python app/server.py
```

默认访问 `http://127.0.0.1:8765`，端口被占用时会自动顺延。

## 打包 EXE

```powershell
cd 扫码入库助手
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller build_exe.spec --noconfirm
```

产物为 `扫码入库助手/dist/扫码入库助手.exe`。首次运行时会在 EXE 同目录生成 `data/` 和
`导出/`，升级 EXE 不会覆盖这两个目录。

## GitHub Actions

工作流 [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml) 会在以下情况构建 EXE：

- 手动触发 `workflow_dispatch`
- 推送到 `main`
- 推送 `v*` 标签

每次构建会上传 `jst-batch-inbound-windows-x64` 制品，包含 EXE、ZIP 和 SHA256 校验文件；
标签构建还会自动创建 GitHub Release。

发布新版本示例：

```powershell
git tag v2.0.21
git push origin v2.0.21
```
