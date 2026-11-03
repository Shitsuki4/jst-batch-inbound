# -*- coding: utf-8 -*-
"""导出文件：统一的导出目录（可在设置里自定义）、目录结构、CSV 单元格安全写法。

目录结构和录像目录一致（VideoArchive.cs：根目录\\2026年11月3日\\分组\\...）：
    导出根目录\\2026年11月3日\\收货明细\\收货明细_采购单123_20261103-171500.xlsx
    导出根目录\\2026年11月3日\\诊断包\\诊断包_20261103-171500.zip
自定义路径存在数据目录的 export-settings.json（非 ASCII 字符转义成 \\uXXXX，
卸载程序和 native-settings.json 里的录像目录一样能读出来）。改路径不移动旧文件。
"""
import json
import os
import re
import threading
import time
from datetime import datetime

SETTINGS_FILE = "export-settings.json"
KEY = "ExportDirectory"

# 浏览器下载的导出文件：按文件名归到对应类别的子目录。
CATEGORIES = (
    ("收货明细", ("收货明细",)),
    ("联合收货", ("joint-receipt", "联合收货")),
    ("无采购单收货", ("unplanned-receipt", "无采购单")),
    ("快递单登记", ("express", "快递单")),
    ("操作记录", ("operation-log", "operations", "操作记录")),
    ("学习条码", ("learned-codes", "学习条码")),
    ("精细导出", ("收货精细导出", "精细导出")),
    ("诊断包", ("diagnostic", "诊断包")),
)
OTHER = "其他导出"


class ExportDirError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.error_code = code


def day_folder(at=None):
    at = at or datetime.now()
    return "%d年%d月%d日" % (at.year, at.month, at.day)


def category_of(name):
    low = (name or "").lower()
    for cat, keys in CATEGORIES:
        if any(k.lower() in low for k in keys):
            return cat
    return OTHER


_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_component(name, fallback="导出"):
    s = _BAD.sub("_", str(name or "")).strip().strip(".")
    return s[:150] or fallback


def _is_absolute(path):
    if not path or not os.path.isabs(path):
        return False
    if os.name == "nt":
        drive, _ = os.path.splitdrive(path)
        return bool(drive)  # "\\foo" 没有盘符，会跟着当前盘变，不算绝对路径
    return True


class ExportDir:
    def __init__(self, data_dir, default_root):
        self.data_dir = data_dir
        self.default_root = os.path.abspath(default_root)
        self.lock = threading.RLock()

    @property
    def settings_path(self):
        return os.path.join(self.data_dir, SETTINGS_FILE)

    def custom(self):
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                v = json.load(f).get(KEY) or ""
            return v if isinstance(v, str) and _is_absolute(v) else ""
        except (OSError, ValueError, AttributeError):
            return ""

    def root(self):
        return os.path.abspath(self.custom() or self.default_root)

    @staticmethod
    def check(path):
        """目录要是绝对路径、能创建、能写。不行就抛带错误码的 ExportDirError。"""
        if not _is_absolute(path):
            raise ExportDirError("E-DATA-1007", "导出目录要写完整路径，例如 D:\\收货导出：%s" % (path or "（空）"))
        if os.path.exists(path) and not os.path.isdir(path):
            raise ExportDirError("E-DATA-1007", "这个路径是一个文件，不是文件夹：%s" % path)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            raise ExportDirError("E-DATA-1008", "导出目录不存在，也创建不了：%s（%s）" % (path, e.strerror or e))
        probe = os.path.join(path, ".write-test-%d-%d.tmp" % (os.getpid(), int(time.time() * 1000)))
        try:
            with open(probe, "wb") as f:
                f.write(b"ok")
        except OSError as e:
            raise ExportDirError("E-DATA-1009", "导出目录没有写入权限：%s（%s）" % (path, e.strerror or e))
        finally:
            try:
                os.remove(probe)
            except OSError:
                pass
        return os.path.abspath(path)

    def set_root(self, path):
        """path 为空：恢复默认目录。返回 {old, new, default}。旧目录里的文件保持不动。"""
        path = (path or "").strip().strip('"')
        with self.lock:
            old = self.root()
            new = self.check(path) if path else self.check(self.default_root)
            value = "" if os.path.normcase(new) == os.path.normcase(self.default_root) else new
            os.makedirs(self.data_dir, exist_ok=True)
            tmp = self.settings_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({KEY: value}, f, ensure_ascii=True)  # 卸载程序按 ANSI 读，中文必须转义
            os.replace(tmp, self.settings_path)
            return {"old": old, "new": new, "default": self.default_root, "custom": bool(value)}

    def status(self):
        root = self.root()
        info = {"directory": root, "default": self.default_root, "custom": bool(self.custom()),
                "layout": os.path.join(root, day_folder(), "类别", "文件"),
                "writable": True, "error": "", "error_code": ""}
        try:
            self.check(root)
        except ExportDirError as e:
            info.update(writable=False, error=str(e), error_code=e.error_code)
        return info

    def target(self, name, category=None, at=None):
        """导出文件的完整路径：根目录\\日期\\类别\\文件名。重名时加 (2)、(3)……不覆盖旧文件。"""
        name = safe_component(name)
        category = safe_component(category or category_of(name), OTHER)
        folder = os.path.join(self.root(), day_folder(at), category)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            raise ExportDirError("E-DATA-1008", "导出目录创建不了：%s（%s）。请在「设置 → 数据与备份」里换一个导出目录" % (folder, e.strerror or e))
        stem, ext = os.path.splitext(name)
        path, n = os.path.join(folder, name), 2
        while os.path.exists(path):
            path, n = os.path.join(folder, "%s (%d)%s" % (stem, n, ext)), n + 1
        return path

    def relative_url(self, path):
        rel = os.path.relpath(path, self.root()).replace(os.sep, "/")
        from urllib.parse import quote
        return "/files/" + "/".join(quote(p) for p in rel.split("/"))

    def resolve(self, rel):
        """/files/ 后面的相对路径 → 导出目录里的文件；越出导出目录的一律不给。"""
        root = os.path.realpath(self.root())
        parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts or any(p == ".." for p in parts):
            return None
        p = os.path.realpath(os.path.join(root, *parts))
        if os.path.commonpath([root, p]) != root or not os.path.isfile(p):
            return None
        return p


# ---------- CSV 单元格 ----------
_FORMULA = ("=", "+", "-", "@", "\t", "\r", "\n", "\uff1d", "\uff0b", "\uff0d", "\uff20")


def csv_cell(v):
    """CSV 单元格的安全写法。

    - 公式注入：以 = + - @ 制表符 回车（含全角 ＝＋－＠）开头的文字前面加单引号，
      Excel / WPS 当文字显示，不会当公式执行。
    - 长数字：11 位及以上的纯数字（物流单号、条码）或以 0 开头的纯数字，写成 ="..."，
      Excel 打开不会变成科学计数、不会丢末尾位和开头的 0。只对文字生效，数量等数值不动。
    """
    if v is None:
        return ""
    if not isinstance(v, str):
        return v
    if v.isdigit() and v.isascii() and (len(v) >= 11 or (len(v) > 1 and v.startswith("0"))):
        return '="%s"' % v
    if v.startswith(_FORMULA) or v.lstrip(" \u3000").startswith(_FORMULA):
        return "'" + v
    return v


def csv_row(values):
    return [csv_cell(v) for v in values]
