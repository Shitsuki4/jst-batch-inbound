"""应用版本号：Python 后端唯一来源。

必须与 desktop/src/Receiving.Native.csproj 的 <Version> 一致（review_fixes_test 会校验）。
备份清单、原生健康检查等都从这里取，升级时只改这一处（及 csproj / 安装脚本）。
backup.before_upgrade 以此判断「本版本是否已做过升级前备份」，版本不变就不会触发新的升级前备份。
"""
VERSION = "4.1.3"
