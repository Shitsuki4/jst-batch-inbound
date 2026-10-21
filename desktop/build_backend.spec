from pathlib import Path
ROOT=Path(SPECPATH).resolve();APP=ROOT/'backend'/'app'
hidden=[
 'backup','catalog','cdp','codes','download','edgecookie','inbound','inventory',
 'joint_receipt','joint_replace','jst','login_memory','native_bridge','operations',
 'oplog','oplog_hooks',
 'pda','purchase_snapshot','purchase_watch','qrview','receipt_copy','receipt_lifecycle',
 'remote_access','report_clipboard','service_runtime','store','unplanned','unplanned_link','xlsx',
 'cryptography','qrcode','PIL'
]
a=Analysis([str(APP/'native_entry.py')],pathex=[str(APP)],binaries=[],datas=[(str(APP/'web'),'web'),(str(APP/'remote_web'),'remote_web'),(str(APP/'installer'),'installer')],hiddenimports=hidden,hookspath=[],hooksconfig={},runtime_hooks=[],excludes=['tkinter','unittest','pydoc','doctest','test','distutils'],noarchive=False,optimize=1)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='Receiving.Service',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=True,icon=str(ROOT/'src'/'app.ico'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='backend')
