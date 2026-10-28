# -*- coding: utf-8 -*-
"""自动更新（updater.py）回归：全部使用假 fetch 与临时目录，不访问网络。"""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import updater  # noqa: E402
from updater import (UpdateHost, compare_versions, is_newer, channel_url, parse_sha256sums,  # noqa: E402
                     pick_release, normalize_release, release_download_url, asset_names,
                     inbound_busy_reason)

API = "https://api.github.com/repos/%s/releases" % updater.REPO
PAYLOAD = bytes((i * 31 + 7) % 256 for i in range(200 * 1024))
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


class Resp(io.BytesIO):
    pass


class FakeFetch:
    """routes: url -> callable(headers, follow_redirects) 或 (status, headers, body)。记录每次调用。"""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def __call__(self, url, timeout, headers=None, follow_redirects=True):
        self.calls.append((url, dict(headers or {}), follow_redirects))
        r = self.routes.get(url)
        if r is None:
            raise IOError("no route %s" % url)
        if callable(r):
            r = r(dict(headers or {}), follow_redirects)
        status, hdrs, body = r
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        return status, hdrs or {}, Resp(body or b"")


def rel(version, draft=False, prerelease=False, assets=()):
    return {"tag_name": "desktop-v" + version, "draft": draft, "prerelease": prerelease,
            "name": "v" + version, "body": "notes", "assets": list(assets)}


class PureTests(unittest.TestCase):
    def test_version_compare(self):
        self.assertEqual(compare_versions("3.0.10", "3.0.9"), 1)
        self.assertEqual(compare_versions("desktop-v3.0.3", "v3.0.3"), 0)
        self.assertEqual(compare_versions("2.9.9", "3.0.0"), -1)
        self.assertFalse(is_newer("3.1.0-beta", "3.0.0"))
        self.assertFalse(is_newer("garbage", "3.0.0"))

    def test_ignore_draft_prerelease(self):
        self.assertIsNone(normalize_release(rel("9.0.0", draft=True)))
        self.assertIsNone(normalize_release(rel("9.0.0", prerelease=True)))
        self.assertIsNone(normalize_release({"tag_name": "android-v9.0.0"}))
        best = pick_release([rel("9.0.0", draft=True), rel("8.0.0", prerelease=True),
                             rel("3.0.10"), rel("3.0.9"), {"tag_name": "other-v99.0.0"}])
        self.assertEqual(best["version"], "3.0.10")

    def test_proxy_url(self):
        u = release_download_url("desktop-v3.0.4", "SHA256SUMS.txt")
        self.assertEqual(channel_url("github", u), u)
        self.assertEqual(channel_url("proxy", u), "https://gh-proxy.com/" + u)
        for bad in ("http://github.com/x", "https://evil.com/x", "https://github.com/a b"):
            with self.assertRaises(ValueError):
                channel_url("proxy", bad)
        with self.assertRaises(ValueError):
            channel_url("mirror", u)
        with self.assertRaises(ValueError):
            release_download_url("desktop-v3.0.4", "../x")

    def test_detect_version_uses_app_version(self):
        import app_version
        old = os.environ.pop("JST_APP_VERSION", None)
        try:
            self.assertEqual(updater.detect_version(), app_version.VERSION)
            os.environ["JST_APP_VERSION"] = "9.8.7"
            self.assertEqual(updater.detect_version(), "9.8.7")
        finally:
            os.environ.pop("JST_APP_VERSION", None)
            if old is not None:
                os.environ["JST_APP_VERSION"] = old

    def test_sha256sums_parse(self):
        a, b, c = "A" * 64, "b" * 64, "c" * 64
        text = ("\ufeff%s  first.exe\r\n%s *second.zip\r\n\r\nnot a line\r\n%s  dir\\third.txt\r\n" % (a, b, c))
        out = parse_sha256sums(text.encode("utf-8"))
        self.assertEqual(out, {"first.exe": "a" * 64, "second.zip": b, "dir/third.txt": c})
        self.assertEqual(parse_sha256sums(text), out)


class HostBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="upd-test-")
        self.now = [1_000_000.0]
        self.sleeps = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def host(self, fetch, busy=None, mode="installed", current="3.0.3"):
        return UpdateHost(self.dir, current_version=current, install_mode=mode, fetch=fetch, busy=busy,
                          clock=lambda: self.now[0], sleep=self.sleeps.append)

    def latest_route(self, version, assets):
        return {API + "/latest": (200, {}, rel(version, assets=assets))}

    def wait_dl(self, h, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            st = h.status()["download"]["state"]
            if st not in ("downloading", "verifying"):
                return h.status()["download"]
            time.sleep(0.02)
        self.fail("download did not finish")


class CheckTests(HostBase):
    def test_check_and_24h_throttle(self):
        f = FakeFetch(self.latest_route("3.0.4", []))
        h = self.host(f)
        st = h.check_now(False)
        self.assertTrue(st["update_available"])
        self.assertEqual(st["latest"]["version"], "3.0.4")
        n = len(f.calls)
        self.now[0] += 23 * 3600
        self.assertFalse(h.due())
        h.check_now(False)
        self.assertEqual(len(f.calls), n, "24 小时内不应再次联网")
        h.check_now(True)                      # 手动检查不受节流
        self.assertEqual(len(f.calls), n + 1)
        self.now[0] += 24 * 3600 + 1
        self.assertTrue(h.due())
        h.update_settings({"auto_check": False})
        self.assertFalse(h.due())

    def test_failure_retry_after_hour_and_fallback(self):
        f = FakeFetch({})
        h = self.host(f)
        st = h.check_now(False)
        self.assertIn("检查更新失败", st["check_error"])
        self.now[0] += 1800
        self.assertFalse(h.due())
        self.now[0] += 1801
        self.assertTrue(h.due())
        # GitHub 失败 -> 加速站兜底
        f.routes["https://gh-proxy.com/" + API + "/latest"] = (200, {}, rel("3.0.5"))
        st = h.check_now(True)
        self.assertEqual((st["latest"]["version"], st["check_source"]), ("3.0.5", "proxy"))

    def test_redirect_fallback_uses_injected_fetch(self):
        page = "https://github.com/%s/releases/latest" % updater.REPO
        f = FakeFetch({page: lambda hd, fr: (302, {"Location": "https://github.com/%s/releases/tag/desktop-v3.1.0"
                                                    % updater.REPO}, b"") if not fr else (200, {}, b"<html>")})
        st = self.host(f).check_now(True)
        self.assertEqual((st["latest"]["version"], st["check_source"]), ("3.1.0", "github-page"))
        self.assertFalse([c for c in f.calls if c[0] == page][0][2], "跳转兜底必须 follow_redirects=False")


class DownloadTests(HostBase):
    def setup_release(self, sums_text, digest=None, payload_route=None):
        v = "3.0.4"
        name = asset_names(v)["installer"]
        asset = {"name": name, "size": len(PAYLOAD)}
        if digest:
            asset["digest"] = "sha256:" + digest
        routes = self.latest_route(v, [asset])
        routes[release_download_url("desktop-v" + v, "SHA256SUMS.txt")] = (200, {}, sums_text.encode("utf-8"))
        routes[release_download_url("desktop-v" + v, name)] = payload_route or (200, {}, PAYLOAD)
        return routes, name

    def test_download_ok(self):
        routes, name = self.setup_release("%s *%s\n" % (PAYLOAD_SHA.upper(), "x") + "%s  %s\n" % (PAYLOAD_SHA, "ReceivingAssistant-3.0.4-Offline-Setup.exe"))
        h = self.host(FakeFetch(routes))
        h.check_now(True)
        h.start_download()
        dl = self.wait_dl(h)
        self.assertEqual(dl["state"], "verified", dl)
        info = h.prepare_install()
        self.assertEqual(info["sha256"], PAYLOAD_SHA)
        # 安装前被替换 -> 拒绝
        with open(info["path"], "ab") as fh:
            fh.write(b"x")
        with self.assertRaises(ValueError):
            h.prepare_install()

    def test_digest_mismatch_rejects(self):
        routes, name = self.setup_release("%s  %s\n" % ("0" * 64, "ReceivingAssistant-3.0.4-Offline-Setup.exe"))
        h = self.host(FakeFetch(routes))
        h.check_now(True)
        h.start_download()
        dl = self.wait_dl(h)
        self.assertEqual(dl["state"], "failed")
        self.assertIn("SHA256", dl["error"])
        part = os.path.join(self.dir, "updates", "3.0.4", name + ".part")
        self.assertFalse(os.path.exists(part), "校验失败的文件必须删除")
        self.assertFalse(os.path.exists(part[:-5]))

    def test_sums_vs_api_digest_mismatch(self):
        routes, _ = self.setup_release("%s  %s\n" % (PAYLOAD_SHA, "ReceivingAssistant-3.0.4-Offline-Setup.exe"),
                                       digest="f" * 64)
        h = self.host(FakeFetch(routes))
        h.check_now(True)
        h.start_download()
        dl = self.wait_dl(h)
        self.assertEqual(dl["state"], "failed")
        self.assertIn("不一致", dl["error"])

    def test_resume_and_retry(self):
        state = {"n": 0}
        cut = 70 * 1024

        def payload(headers, fr):
            state["n"] += 1
            if state["n"] == 1:
                raise IOError("connection reset")          # 第一次：网络错误 -> 重试
            if state["n"] == 2:                             # 第二次：中途断开（声明全长只给一部分）
                return 200, {"Content-Length": str(len(PAYLOAD))}, PAYLOAD[:cut]
            rng = headers.get("Range", "")
            self.assertEqual(rng, "bytes=%d-" % cut, "第三次必须断点续传")
            return 206, {"Content-Length": str(len(PAYLOAD) - cut)}, PAYLOAD[cut:]

        routes, _ = self.setup_release("%s  %s\n" % (PAYLOAD_SHA, "ReceivingAssistant-3.0.4-Offline-Setup.exe"),
                                       payload_route=payload)
        h = self.host(FakeFetch(routes))
        h.check_now(True)
        h.start_download()
        dl = self.wait_dl(h)
        self.assertEqual(dl["state"], "verified", dl)
        self.assertEqual(state["n"], 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_busy_refusal(self):
        db = os.path.join(self.dir, "jst.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE inbound_job(id INTEGER PRIMARY KEY, status TEXT)")
        con.execute("INSERT INTO inbound_job(status) VALUES('creating')")
        con.commit()
        con.close()
        paths = [db, os.path.join(self.dir, "missing.db")]
        self.assertIn("入库任务", inbound_busy_reason(paths))
        h = self.host(FakeFetch({}), busy=lambda: inbound_busy_reason(paths))
        with self.assertRaises(ValueError) as cm:
            h.prepare_install()
        self.assertIn("入库任务", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            h.prepare_install(recording=True)
        self.assertIn("录像", str(cm.exception))
        con = sqlite3.connect(db)
        con.execute("UPDATE inbound_job SET status='done'")
        con.commit()
        con.close()
        self.assertEqual(inbound_busy_reason(paths), "")

    def test_dev_mode_no_download(self):
        routes, _ = self.setup_release("")
        h = self.host(FakeFetch(routes), mode="dev")
        h.check_now(True)
        with self.assertRaises(ValueError):
            h.start_download()


class UpgradeInstallerTests(HostBase):
    """安装版：本机运行组件与 Release 固定的完全一致时下载精简升级安装程序，否则下载离线完整安装程序。"""

    def make_runtime(self, ffmpeg=b"ffmpeg-bin", ffprobe=b"ffprobe-bin", webview=b"webview-bin"):
        root = os.path.join(self.dir, "runtime")
        os.makedirs(os.path.join(root, "webview2"), exist_ok=True)
        paths = {"ffmpeg.exe": os.path.join(root, "ffmpeg.exe"), "ffprobe.exe": os.path.join(root, "ffprobe.exe"),
                 "msedgewebview2.exe": os.path.join(root, "webview2", "msedgewebview2.exe")}
        for name, data in (("ffmpeg.exe", ffmpeg), ("ffprobe.exe", ffprobe), ("msedgewebview2.exe", webview)):
            with open(paths[name], "wb") as fh:
                fh.write(data)
        return paths

    @staticmethod
    def marker(ffmpeg=b"ffmpeg-bin", ffprobe=b"ffprobe-bin", webview=b"webview-bin"):
        sha = {"ffmpeg.exe": hashlib.sha256(ffmpeg).hexdigest(), "ffprobe.exe": hashlib.sha256(ffprobe).hexdigest(),
               "msedgewebview2.exe": hashlib.sha256(webview).hexdigest()}
        return "<!-- runtime-requirements: %s -->" % json.dumps({"webview2": "154.0.4258.62", "ffmpeg": "8.1.2", "sha256": sha})

    def release(self, body, with_upgrade=True, version="3.0.4"):
        names = asset_names(version)
        assets = [{"name": names["installer"], "size": 500 * 1048576}, {"name": names["portable"], "size": 86 * 1048576}]
        if with_upgrade:
            assets.append({"name": names["upgrade"], "size": 87 * 1048576})
        r = rel(version, assets=assets)
        r["body"] = "notes\n\n" + body
        return {API + "/latest": (200, {}, r)}

    def upgrade_host(self, routes, runtime, mode="installed"):
        return UpdateHost(self.dir, current_version="3.0.3", install_mode=mode, fetch=FakeFetch(routes),
                          clock=lambda: self.now[0], sleep=self.sleeps.append, runtime=runtime)

    def test_marker_parsed_and_hidden_from_notes(self):
        r = normalize_release(dict(rel("3.0.4"), body="hello\n" + self.marker()))
        self.assertEqual(r["notes"], "hello")
        self.assertEqual(r["runtime"]["webview2"], "154.0.4258.62")
        self.assertEqual(set(r["runtime"]["sha256"]), set(updater.RUNTIME_FILES))
        self.assertIsNone(normalize_release(dict(rel("3.0.4"), body="<!-- runtime-requirements: {bad json} -->"))["runtime"])
        broken = self.marker().replace(hashlib.sha256(b"ffmpeg-bin").hexdigest(), "xyz")
        self.assertIsNone(updater.parse_runtime_requirements(broken))
        self.assertIn("upgrade", asset_names("3.0.4"))
        self.assertEqual(asset_names("3.0.4")["upgrade"], "ReceivingAssistant-3.0.4-Upgrade-Setup.exe")

    def test_matching_runtime_picks_upgrade(self):
        h = self.upgrade_host(self.release(self.marker()), self.make_runtime())
        st = h.check_now(True)
        self.assertEqual(st["asset"]["kind"], "upgrade")
        self.assertEqual(st["asset"]["name"], "ReceivingAssistant-3.0.4-Upgrade-Setup.exe")
        self.assertEqual(st["asset"]["label"], "升级安装程序")
        self.assertEqual(st["asset"]["note"], "")
        self.assertTrue(st["asset"]["listed"])
        self.assertIn("upgrade", st["links"])

    def test_changed_runtime_falls_back_to_full_installer(self):
        h = self.upgrade_host(self.release(self.marker(ffmpeg=b"new-ffmpeg")), self.make_runtime())
        st = h.check_now(True)
        self.assertEqual(st["asset"]["kind"], "installer")
        self.assertIn("运行组件", st["asset"]["note"])
        self.assertIn("FFmpeg 8.1.2", st["asset"]["note"])

    def test_missing_runtime_or_marker_falls_back(self):
        paths = self.make_runtime()
        os.remove(paths["msedgewebview2.exe"])
        st = self.upgrade_host(self.release(self.marker()), paths).check_now(True)
        self.assertEqual(st["asset"]["kind"], "installer")
        self.assertIn("msedgewebview2.exe", st["asset"]["note"])
        shutil.rmtree(os.path.join(self.dir, "runtime"))
        os.remove(os.path.join(self.dir, "update-cache.json"))
        st = self.upgrade_host(self.release(""), self.make_runtime()).check_now(True)
        self.assertEqual(st["asset"]["kind"], "installer")
        self.assertIn("没有运行组件版本信息", st["asset"]["note"])

    def test_release_without_upgrade_asset_and_portable_mode(self):
        st = self.upgrade_host(self.release(self.marker(), with_upgrade=False), self.make_runtime()).check_now(True)
        self.assertEqual(st["asset"]["kind"], "installer")
        self.assertEqual(st["asset"]["note"], "")
        os.remove(os.path.join(self.dir, "update-cache.json"))
        st = self.upgrade_host(self.release(self.marker()), self.make_runtime(), mode="portable").check_now(True)
        self.assertEqual(st["asset"]["kind"], "portable")

    def test_hash_cache_reused_and_invalidated(self):
        paths = self.make_runtime()
        h = self.upgrade_host(self.release(self.marker()), paths)
        h.check_now(True)
        calls = []
        real = updater.sha256_file
        updater.sha256_file = lambda p, *a, **k: calls.append(p) or real(p, *a, **k)
        try:
            self.assertEqual(h.status()["asset"]["kind"], "upgrade")
            self.assertEqual(calls, [], "大文件哈希应走缓存")
            with open(paths["ffprobe.exe"], "ab") as fh:
                fh.write(b"tampered")
            os.utime(paths["ffprobe.exe"], ns=(1, 1))
            self.assertEqual(h.status()["asset"]["kind"], "installer")
            self.assertEqual(calls, [paths["ffprobe.exe"]])
        finally:
            updater.sha256_file = real

    def test_upgrade_download_verified_and_prepared(self):
        v = "3.0.4"
        routes = self.release(self.marker())
        name = asset_names(v)["upgrade"]
        routes[release_download_url("desktop-v" + v, "SHA256SUMS.txt")] = (200, {}, ("%s  %s\n" % (PAYLOAD_SHA, name)).encode())
        routes[release_download_url("desktop-v" + v, name)] = (200, {}, PAYLOAD)
        h = self.upgrade_host(routes, self.make_runtime())
        h.check_now(True)
        h.start_download()
        dl = self.wait_dl(h)
        self.assertEqual(dl["state"], "verified", dl)
        info = h.prepare_install()
        self.assertEqual(info["kind"], "upgrade")
        self.assertTrue(info["path"].endswith(name))

    def test_detect_runtime_reads_host_env(self):
        keys = ("JST_RUNTIME_WEBVIEW2", "JST_RUNTIME_FFMPEG", "JST_RUNTIME_FFPROBE")
        old = {k: os.environ.pop(k, None) for k in keys}
        try:
            self.assertEqual(updater.detect_runtime(), {"ffmpeg.exe": "", "ffprobe.exe": "", "msedgewebview2.exe": ""})
            os.environ.update({"JST_RUNTIME_WEBVIEW2": "C:\\rt\\webview2", "JST_RUNTIME_FFMPEG": "C:\\rt\\ffmpeg.exe",
                               "JST_RUNTIME_FFPROBE": "C:\\rt\\ffprobe.exe"})
            rt = updater.detect_runtime()
            self.assertEqual(rt["ffmpeg.exe"], "C:\\rt\\ffmpeg.exe")
            self.assertTrue(rt["msedgewebview2.exe"].endswith("msedgewebview2.exe"))
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


class UpgradeInstallerWiringTests(unittest.TestCase):
    """升级安装程序的打包约定：不删运行时、安装前核对运行组件、CI 上传 / 校验 / 中文名齐全。"""

    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

    def read(self, *parts):
        with open(os.path.join(self.ROOT, *parts), encoding="utf-8-sig") as fh:
            return fh.read()

    def test_iss_keeps_runtime_and_checks_it(self):
        iss = self.read("desktop", "installer", "offline_setup.iss")
        delete = iss.split("[InstallDelete]", 1)[1].split("\n[", 1)[0]
        self.assertIn("{app}\\backend\\_internal", delete)
        self.assertIn('#ifndef UpgradeOnly\nType: filesandordirs; Name: "{app}\\runtime\\webview2"\n#endif', delete,
                      "升级安装程序不能删除已安装的 WebView2 运行时")
        code = iss.split("[Code]", 1)[1]
        for needle in ("function InitializeSetup(", "RuntimeProblem(", "GetSHA256OfFile", "--import-portable",
                       "{param:PORTABLEDATA|}", "ReceivingAssistant\\runtime"):
            self.assertIn(needle, code)
        self.assertIn("{80E21946-081D-4C07-A40D-60C2244D7066}_is1", code, "必须按离线完整版的 AppId 找已安装目录")

    def test_workflow_ships_upgrade_installer(self):
        wf = self.read(".github", "workflows", "build-desktop-exe.yml")
        for needle in ("build-upgrade-windows.ps1", "verify-upgrade-installer.ps1",
                       "release-assets/upgrade-installer/ReceivingAssistant-${{ needs.build.outputs.version }}-Upgrade-Setup.exe",
                       "收货小助手-$APP_VERSION-升级安装程序.exe", "<!-- runtime-requirements: ${requirements} -->",
                       "tr -d '\\r' < release-assets/SHA256SUMS.txt | grep -qx",
                       "  ReceivingAssistant-$APP_VERSION-Upgrade-Setup.exe\""):
            self.assertIn(needle, wf)
        self.assertEqual(asset_names("9.9.9")["upgrade"], "ReceivingAssistant-9.9.9-Upgrade-Setup.exe")


if __name__ == "__main__":
    unittest.main(verbosity=2)
