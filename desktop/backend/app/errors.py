# -*- coding: utf-8 -*-
"""统一错误码。

规则：用户看到的每一条报错都带一个错误码，格式 E-<类别>-<四位数字>，例如 E-JST-1401。
- 这里的 REGISTRY 是唯一的错误码表：中文含义 + 建议处理办法。
- 前端（web/errcode.js）、原生外壳（src/ErrorCodes.cs）、安装程序用到的错误码也必须登记在这里，
  errors_test.py 会扫描源码，出现未登记的错误码、或报错没有错误码都会失败。
- 文档 docs/错误码.md 由 `python app/errors.py --markdown` 生成，测试会核对是否与本表一致。

「当前账号无此操作权限」只用于聚水潭明确返回的权限拒绝（E-JST-1401）。
网络、超时、服务器出错、业务校验、找不到采购单、本机服务异常都有各自的错误码，绝不再归成权限问题。
"""
import re
import socket
import sqlite3
import sys

CODE_RE = re.compile(r"E-[A-Z]{2,5}-\d{4}")
PREFIX_RE = re.compile(r"^\s*\[(E-[A-Z]{2,5}-\d{4})\]\s*")

CATEGORIES = [
    ("NET", "网络与本机服务连接"),
    ("JST", "聚水潭接口"),
    ("LGN", "聚水潭登录"),
    ("PO", "有采购单收货"),
    ("SCAN", "扫码与收货明细"),
    ("INB", "生成入库单"),
    ("UNP", "无采购单收货"),
    ("JNT", "多采购单合并（联合收货）"),
    ("EXP", "快递单登记"),
    ("INV", "库存盘点"),
    ("WCH", "采购监控"),
    ("CAT", "商品资料"),
    ("REC", "录像"),
    ("DATA", "本机数据、备份与恢复"),
    ("REQ", "请求与参数"),
    ("UPD", "软件更新"),
    ("INS", "安装程序"),
    ("PDA", "PDA"),
    ("RMT", "远程管理页"),
    ("NAT", "桌面程序（原生外壳）"),
    ("UI", "页面输入"),
    ("APP", "程序内部"),
]

# code: (中文含义, 建议处理办法)
REGISTRY = {
    # ---- 本机服务连接（页面 / 桌面程序 → 本机收货服务）
    "E-NET-1001": ("没连上本机收货服务", "检查小助手是否还在运行；稍等几秒重试。会改数据的操作请刷新页面确认是否已经生效。"),
    "E-NET-1002": ("本机收货服务响应超时", "电脑可能很忙或卡住了，稍等后刷新页面确认操作是否已生效，再决定是否重试。"),
    "E-NET-1003": ("本机收货服务返回的数据无法识别", "刷新页面重试；仍出现请导出诊断包发给维护人员。"),
    "E-NET-1004": ("本机收货服务返回了 HTTP 错误", "刷新页面重试；仍出现请导出诊断包。"),
    # ---- 聚水潭网络
    "E-NET-2001": ("连不上聚水潭服务器", "检查电脑网络、代理或防火墙，网络恢复后重试。"),
    "E-NET-2002": ("聚水潭响应超时", "聚水潭或网络较慢，稍后重试；会改数据的操作先到聚水潭确认是否已生效。"),
    "E-NET-2003": ("连接聚水潭时证书或代理出错", "检查系统时间、代理和安全软件设置后重试。"),
    # ---- 聚水潭接口
    "E-JST-1001": ("聚水潭服务器出错", "聚水潭暂时异常（HTTP 5xx），稍后重试。"),
    "E-JST-1002": ("聚水潭拒绝了请求", "聚水潭返回了 HTTP 4xx（不是权限问题），刷新页面或重新登录后重试。"),
    "E-JST-1101": ("聚水潭登录已失效", "重新登录聚水潭后再操作。"),
    "E-JST-1102": ("还没有登录聚水潭", "先在小助手里登录聚水潭。"),
    "E-JST-1201": ("聚水潭返回的数据无法识别", "稍后重试；仍出现请导出诊断包。"),
    "E-JST-1301": ("聚水潭业务校验未通过", "按提示文字处理（例如单据状态、数量、仓库不符），处理后重试。"),
    "E-JST-1401": ("当前聚水潭账号无此操作权限", "聚水潭明确拒绝了这个账号的权限。请让管理员在聚水潭给该账号开通对应权限，或换有权限的账号。"),
    "E-JST-1999": ("聚水潭接口返回了其他错误", "按提示文字处理；仍出现请导出诊断包。"),
    # ---- 登录
    "E-LGN-1001": ("聚水潭登录失败", "核对账号、密码或验证码后重试。"),
    "E-LGN-1002": ("短信验证码登录失败", "重新获取验证码后再试。"),
    "E-LGN-1003": ("扫码登录未完成", "重新打开扫码登录，在手机上确认后再点完成。"),
    "E-LGN-1004": ("恢复保存的登录失败", "重新输入账号密码登录。"),
    # ---- 有采购单收货
    "E-PO-1000": ("有采购单收货操作未完成", "按提示文字处理后重试。"),
    "E-PO-1001": ("未找到该采购单", "核对采购单号或外部单号，确认单号类型；若是其他货主的单，在「设置」里补充所属货主ID。"),
    "E-PO-1002": ("采购单明细读取失败", "稍后重试；仍失败请到聚水潭确认采购单状态。"),
    "E-PO-1003": ("采购单的货主或仓库公司对不上", "重新查询采购单，确认选择的货主和分仓。"),
    "E-PO-1004": ("该采购单本轮已结单", "如需继续收货，请重新「创建收货批次」开始新一轮。"),
    "E-PO-2001": ("新旧采购单相同，不需要更换", "要更新明细请用「刷新采购单」。"),
    "E-PO-2002": ("已生成入库单，不能更换采购单", "本轮已有入库记录（含失败 / 待核实）。请在聚水潭核对原入库单；货确实收错单时，先在聚水潭作废原入库单，再为正确的采购单新建一轮收货重新扫码。"),
    "E-PO-2003": ("新采购单已有未结束的收货批次", "在「挂起批次」里打开那张单继续收货，或先结束那张单，再更换。"),
    "E-PO-2004": ("新采购单属于其他货主或仓库公司", "不能跨货主 / 仓库直接更换。请结束本轮后，为正确的采购单新建收货批次。"),
    "E-PO-2005": ("更换确认已失效", "预览之后收货明细或采购单有变化，请重新预览再确认。"),
    "E-PO-2006": ("新采购单在其他收货方式已有入库记录", "同一张采购单不能在两种收货方式里重复入库。请核对原入库记录，在原收货方式里继续。"),
    "E-PO-2007": ("本轮已结单，不能更换采购单", "已结单的记录只读。请为正确的采购单新建收货批次。"),
    "E-PO-2008": ("请先输入采购单号或外部单号", "输入或扫描正确的采购单号 / 外部单号后再查询。"),
    # ---- 扫码与明细
    "E-SCAN-1000": ("扫码或明细操作未完成", "按提示文字处理后重新扫码。"),
    "E-SCAN-1001": ("物流单号不能修改或删除", "该物流单号已关联入库单，请在聚水潭核对原单；补收请新建物流单号。"),
    # ---- 入库
    "E-INB-1000": ("生成入库单未完成", "按提示文字处理；创建结果不明时先到聚水潭确认，避免重复入库。"),
    "E-INB-1001": ("已生成或正在生成入库单", "同一批明细只能入库一次，请到聚水潭核对原入库单；补收请新建物流单号。"),
    # ---- 其他收货方式
    "E-UNP-1000": ("无采购单收货操作未完成", "按提示文字处理后重试。"),
    "E-JNT-1000": ("联合收货操作未完成", "按提示文字处理后重试。"),
    "E-EXP-1000": ("快递单登记操作未完成", "按提示文字处理后重试。"),
    "E-INV-1000": ("库存盘点操作未完成", "按提示文字处理后重试。"),
    "E-WCH-1000": ("采购监控操作未完成", "按提示文字处理后重试。"),
    "E-CAT-1000": ("商品资料操作未完成", "按提示文字处理后重试。"),
    # ---- 录像
    "E-REC-1000": ("录像操作未完成", "按提示文字处理；查看监控浮窗里的录像状态。"),
    "E-REC-1001": ("录像尚未就绪", "等监控浮窗显示「正在录制」后，点「重新确认当前物流单」再继续。"),
    "E-REC-1002": ("摄像头打开失败", "检查摄像头连接、是否被其他软件占用，在「设置 → 视频举证」里重新测试。"),
    "E-REC-1003": ("录像文件保存失败", "检查录像目录所在磁盘空间和写入权限。"),
    "E-REC-1004": ("录像引擎（FFmpeg）出错", "在「设置 → 视频举证」里测试录像；仍失败请导出诊断包。"),
    # ---- 数据
    "E-DATA-1001": ("本机数据库出错", "重试一次；仍出现请导出诊断包，不要删除 data 文件夹。"),
    "E-DATA-1002": ("本机文件读写失败", "检查磁盘空间、文件是否被占用，以及文件夹写入权限。"),
    "E-DATA-1003": ("备份失败", "检查磁盘空间后重试。"),
    "E-DATA-1004": ("恢复备份失败", "按提示文字处理；恢复前的数据不会被删除。"),
    "E-DATA-1005": ("正在等待恢复备份", "重启小助手完成恢复，或先取消恢复再修改数据。"),
    "E-DATA-1006": ("导出失败", "检查导出文件夹是否可写、文件是否被 Excel 占用。"),
    "E-DATA-1007": ("导出目录不是有效的文件夹路径", "在「设置 → 数据与备份 → 导出目录」里填完整路径（如 D:\\收货导出），或点「选择目录」。"),
    "E-DATA-1008": ("导出目录不存在，也创建不了", "检查盘符、U 盘或网络盘是否还在；或在「设置 → 数据与备份」里换一个导出目录、恢复默认。"),
    "E-DATA-1009": ("导出目录没有写入权限", "换一个有写入权限的文件夹（不要选 C:\\Program Files 这类系统目录），或恢复默认导出目录。"),
    "E-DATA-1010": ("导出目录设置保存失败", "检查数据目录是否可写后重试。"),
    # ---- 请求
    "E-REQ-1001": ("请求参数不合法", "刷新页面后重试。"),
    "E-REQ-1401": ("本机接口没有桌面程序授权", "请从收货小助手桌面程序里操作；本机其他程序不能直接调用收货服务。如果是在桌面程序里看到，请重启小助手。"),
    "E-REQ-1403": ("请求来源不允许", "只能在本机小助手窗口里操作；手机请用远程管理页或 PDA。"),
    "E-REQ-1404": ("接口不存在", "页面版本和程序版本可能不一致，请刷新页面或重启小助手。"),
    "E-REQ-1409": ("数据已变化，操作冲突", "刷新页面查看最新数据后再操作。"),
    # ---- 更新
    "E-UPD-1000": ("软件更新未完成", "按提示文字处理；也可以到发布页手动下载安装包。"),
    "E-UPD-1001": ("检查更新失败", "检查网络或更换更新渠道后重试。"),
    "E-UPD-1002": ("下载更新失败", "检查网络或更换更新渠道后重试。"),
    "E-UPD-1003": ("更新包校验失败", "文件可能下载不完整或被篡改，已停止安装，请重新下载。"),
    "E-UPD-1004": ("启动安装程序失败", "到下载文件夹手动运行安装包。"),
    # ---- 安装程序
    "E-INS-1001": ("缺少运行组件，升级安装程序不能安装", "改用离线完整安装程序。"),
    "E-INS-1002": ("本机运行组件与本版本不一致", "改用离线完整安装程序。"),
    "E-INS-1003": ("便携版数据导入失败", "安装后在「设置 → 数据与备份 → 从文件恢复」里导入。"),
    "E-INS-1004": ("小助手还在运行，无法安装或卸载", "先退出小助手（含托盘图标）再安装。"),
    "E-INS-1005": ("卸载时未能删除防火墙放行规则", "以管理员身份打开 Windows 防火墙，手动删除名称以「收货小助手远程管理页」开头的入站规则。"),
    "E-INS-1006": ("安装文件写入失败", "检查磁盘空间和安全软件拦截后重试。"),
    # ---- PDA
    "E-PDA-1000": ("PDA 操作未完成", "按提示文字处理后重试。"),
    "E-PDA-1001": ("PDA 未配对或配对已失效", "在电脑「设置 → PDA 与设备」里重新配对。"),
    "E-PDA-1002": ("PDA 连不上电脑", "确认 PDA 和电脑在同一网络，电脑上的小助手正在运行。"),
    # ---- 远程管理页
    "E-RMT-1000": ("远程管理页操作未完成", "按提示文字处理后重试。"),
    "E-RMT-1001": ("远程管理页登录已失效", "重新输入访问口令。"),
    # ---- 原生外壳
    "E-NAT-1001": ("本机收货服务启动失败", "重启小助手；仍失败请导出诊断包或查看 data\\logs。"),
    "E-NAT-1002": ("页面组件（WebView2）初始化失败", "重新安装或修复 WebView2 运行组件，或改用离线完整安装程序。"),
    "E-NAT-1003": ("桌面操作失败", "按提示文字处理后重试。"),
    "E-NAT-1004": ("打开文件或文件夹失败", "确认文件仍存在、没有被其他程序占用。"),
    "E-NAT-1005": ("小助手已经在运行", "不要重复启动；在任务栏或系统托盘里找到已打开的窗口。"),
    "E-NAT-1006": ("退出未完成", "等待保存完成后再试；仍退不出请导出诊断包。"),
    "E-NAT-1007": ("备份恢复后没能自动重新打开", "手动重新打开小助手完成恢复；不要重复提交入库单。"),
    "E-NAT-9001": ("桌面程序遇到未处理的异常", "重启小助手；请导出诊断包发给维护人员。"),
    # ---- 页面输入
    "E-UI-1001": ("页面输入没有通过检查", "按提示文字修改输入后重试。"),
    # ---- 程序内部
    "E-APP-9001": ("程序内部错误", "重试一次；仍出现请导出诊断包发给维护人员，并附上日志编号。"),
}

# 按接口路径给出默认错误码：业务校验类报错（ValueError 等）用所在功能的通用码，
# 具体原因由提示文字说明。更具体的错误由异常类型或显式错误码决定。
ROUTE_DEFAULTS = [
    ("/api/po/rematch", "E-PO-1000"),
    ("/api/po/", "E-PO-1000"),
    ("/api/session/", "E-PO-1000"),
    ("/api/state", "E-PO-1000"),
    ("/api/purchase", "E-PO-1000"),
    ("/api/receipt", "E-SCAN-1000"),
    ("/api/tracking/", "E-SCAN-1000"),
    ("/api/item/", "E-SCAN-1000"),
    ("/api/line/", "E-SCAN-1000"),
    ("/api/batch/", "E-SCAN-1000"),
    ("/api/inbound/", "E-INB-1000"),
    ("/api/unplanned", "E-UNP-1000"),
    ("/api/joint/", "E-JNT-1000"),
    ("/api/express", "E-EXP-1000"),
    ("/api/inventory/", "E-INV-1000"),
    ("/api/purchase-watch/", "E-WCH-1000"),
    ("/api/catalog", "E-CAT-1000"),
    ("/api/learned", "E-CAT-1000"),
    ("/api/sku", "E-CAT-1000"),
    ("/api/native/", "E-REC-1000"),
    ("/api/video", "E-REC-1000"),
    ("/api/recording", "E-REC-1000"),
    ("/api/restore/", "E-DATA-1004"),
    ("/api/backup/", "E-DATA-1003"),
    ("/api/export", "E-DATA-1006"),
    ("/api/update/", "E-UPD-1000"),
    ("/api/pda", "E-PDA-1000"),
    ("/pda/", "E-PDA-1000"),
    ("/api/remote", "E-RMT-1000"),
    ("/api/login", "E-LGN-1001"),
    ("/api/sms/", "E-LGN-1002"),
    ("/api/qr/", "E-LGN-1003"),
    ("/api/diag", "E-DATA-1002"),
]
GENERIC_DEFAULT = "E-REQ-1001"
PERMISSION = "E-JST-1401"


class AppError(Exception):
    """带错误码的业务错误。str(e) 是给用户看的中文原因（不含错误码前缀）。"""

    def __init__(self, code, msg="", hint=""):
        if code not in REGISTRY:
            raise KeyError("未登记的错误码: %s" % code)
        self.error_code = code
        self.hint = hint
        super().__init__(msg or REGISTRY[code][0])


def title(code):
    return REGISTRY.get(code, ("", ""))[0]


def suggestion(code):
    return REGISTRY.get(code, ("", ""))[1]


def route_default(path):
    path = str(path or "")
    for prefix, code in ROUTE_DEFAULTS:
        if path.startswith(prefix):
            return code
    return GENERIC_DEFAULT


def strip_code(msg):
    m = PREFIX_RE.match(str(msg or ""))
    return (m.group(1), str(msg)[m.end():]) if m else ("", str(msg or ""))


def format_msg(code, msg):
    """[E-XXX-NNNN] 原因。已带错误码的不重复加。"""
    msg = str(msg or "").strip() or title(code)
    if PREFIX_RE.match(msg):
        return msg
    return "[%s] %s" % (code, msg)


_NETWORK_TYPES = (ConnectionError, socket.gaierror, socket.herror)
_PROGRAMMING = (KeyError, TypeError, AttributeError, IndexError, NameError, AssertionError,
                ZeroDivisionError, RecursionError, NotImplementedError, UnboundLocalError)


def _is_timeout(exc):
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(exc).lower()


def classify(exc=None, msg="", path=""):
    """给一条报错找错误码：显式 → 异常类型 → 消息里已有的码 → 所在功能默认。"""
    code = getattr(exc, "error_code", None) if exc is not None else None
    if code in REGISTRY:
        return code
    found, _ = strip_code(msg)
    if found in REGISTRY:
        return found
    if exc is not None:
        try:
            import urllib.error
            url_error = urllib.error.URLError
        except Exception:  # pragma: no cover
            url_error = ()
        try:
            import ssl
            ssl_error = ssl.SSLError
        except Exception:  # pragma: no cover
            ssl_error = ()
        name = type(exc).__name__
        if name == "LoginError":
            return "E-LGN-1001"
        if isinstance(exc, sqlite3.Error):
            text = str(exc)
            if "入库" in text:
                return "E-INB-1001"
            return "E-DATA-1001"
        if ssl_error and isinstance(exc, ssl_error):
            return "E-NET-2003"
        if _is_timeout(exc) and isinstance(exc, (OSError, url_error) if url_error else OSError):
            return "E-NET-2002"
        if url_error and isinstance(exc, url_error):
            return "E-NET-2001"
        if isinstance(exc, _NETWORK_TYPES):
            return "E-NET-2001"
        if isinstance(exc, OSError):
            return "E-DATA-1002"
        if isinstance(exc, _PROGRAMMING):
            return "E-APP-9001"
    return route_default(path)


def current_exception():
    return sys.exc_info()[1]


def registry_rows():
    order = {c: i for i, (c, _) in enumerate(CATEGORIES)}
    def key(code):
        cat = code.split("-")[1]
        return (order.get(cat, 99), code)
    return [(code,) + REGISTRY[code] for code in sorted(REGISTRY, key=key)]


def registry_json():
    names = dict(CATEGORIES)
    return [{"code": c, "category": names.get(c.split("-")[1], ""), "title": t, "fix": f}
            for c, t, f in registry_rows()]


def markdown():
    names = dict(CATEGORIES)
    out = ["# 错误码表", "",
           "收货小助手的每一条报错都带错误码，格式是 `E-类别-四位数字`，例如 `[E-JST-1401] 当前聚水潭账号无此操作权限`。",
           "报错弹窗里还会显示「日志编号」：在「设置 → 诊断与日志」里导出诊断包，或在日志文件里搜索这个编号，就能找到这次报错的完整记录。",
           "",
           "",
           "## 日志在哪里", "",
           "- 安装版：`%LOCALAPPDATA%\\ReceivingAssistant\\v3\\data\\logs`；便携版：便携文件夹里的 `data\\logs`。",
           "- `diagnostic.jsonl` 是本机收货服务、PDA 和远程管理页的报错，`native-diagnostic.jsonl` 是桌面程序自己的报错。",
           "- 单个文件满 2 MB 换新文件，最多保留 5 个旧文件。密码、令牌、Cookie、签名不会写进日志，网址只保留问号前面的部分。",
           "- 「设置 → 诊断与日志」可以导出诊断包（只含日志、版本与环境信息和本表，不含数据库和密码）、打开日志文件夹、按错误码或日志编号查最近的报错。",
           "",
           "本文件由 `python desktop/backend/app/errors.py --markdown > docs/错误码.md` 生成，请勿手改。", ""]
    current = None
    for code, t, fix in registry_rows():
        cat = code.split("-")[1]
        if cat != current:
            current = cat
            out += ["", "## %s（%s）" % (names.get(cat, cat), cat), "", "| 错误码 | 含义 | 建议处理 |", "| --- | --- | --- |"]
        out.append("| %s | %s | %s |" % (code, t, fix.replace("|", "/")))
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    if "--markdown" in sys.argv:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        sys.stdout.write(markdown())
