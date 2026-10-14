# -*- coding: utf-8 -*-
"""
最小化 CDP（Chrome DevTools Protocol）客户端 —— 只为一件事：
从**运行中**的 Edge 里把全部 Cookie 直接拿回来。

为什么不用 SQLite 读 Cookies 库：
  1. Chromium 的 cookie 落盘是异步批量提交（约 30 秒一个周期），扫码完立刻关窗，
     大部分 cookie 还在内存里，强杀进程就全丢了；
  2. 会话级 cookie（无过期时间的，聚水潭的 u_id / SessionId 正是这类）默认**只存内存**，
     不写 SQLite —— 浏览器一关就没了，读库永远读不到。
  所以必须趁浏览器活着，通过 --remote-debugging-port 用 CDP 的 Storage.getCookies 拿。

只用标准库：urllib 发 HTTP 拿调试端点，socket 手写 WebSocket 握手与帧
（CDP 执行命令必须走 WebSocket，标准库没有现成客户端，自己写最省）。
"""
import base64
import json
import os
import socket
import struct
import urllib.request


class CdpError(Exception):
    pass


def _http_json(port, path, timeout=5):
    url = "http://127.0.0.1:%d%s" % (port, path)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        raise CdpError("CDP 端点不可达（%s）" % e)


def alive(port, timeout=3):
    """调试端口上有没有活着的浏览器"""
    if not port:
        return False
    try:
        _http_json(port, "/json/version", timeout)
        return True
    except CdpError:
        return False


# ---------------------------------------------------------------- WebSocket

def _ws_handshake(sock, host, port, path):
    key = base64.b64encode(os.urandom(16)).decode()
    req = ("GET %s HTTP/1.1\r\n"
           "Host: %s:%d\r\n"
           "Upgrade: websocket\r\n"
           "Connection: Upgrade\r\n"
           "Sec-WebSocket-Key: %s\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key))
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise CdpError("WebSocket 握手中断")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    status = head.split(b"\r\n")[0].decode("latin1", "replace")
    if " 101 " not in status:
        raise CdpError("WebSocket 握手被拒绝（%s）" % status.strip())
    return rest                      # 握手响应里可能已带了首帧数据


def _read_exact(sock, n, buf):
    """从 buf 起步凑够 n 字节；返回 (数据, 剩余 buf)"""
    data = b""
    while len(data) < n:
        if buf:
            data += buf
            buf = b""
            continue
        chunk = sock.recv(65536)
        if not chunk:
            raise CdpError("WebSocket 连接中断")
        data += chunk
    if len(data) > n:
        return data[:n], data[n:]
    return data, b""


def _recv_frame(sock, leftover):
    """读一帧。返回 (opcode, payload, leftover)。支持扩展长度与分片 ping。"""
    left = leftover
    for _ in range(64):              # ping/pong 偶发连发，别死循环
        head, left = _read_exact(sock, 2, left)
        opcode = head[0] & 0x0F
        masked = head[1] & 0x80
        ln = head[1] & 0x7F
        if ln == 126:
            b, left = _read_exact(sock, 2, left)
            ln = struct.unpack(">H", b)[0]
        elif ln == 127:
            b, left = _read_exact(sock, 8, left)
            ln = struct.unpack(">Q", b)[0]
        if masked:
            mk, left = _read_exact(sock, 4, left)
        else:
            mk = b""
        payload, left = _read_exact(sock, ln, left) if ln else (b"", left)
        if masked and payload:
            payload = bytes(c ^ mk[i % 4] for i, c in enumerate(payload))
        if opcode == 0x9:            # ping → pong
            _send_frame(sock, 0xA, payload)
            continue
        if opcode == 0x8:            # close
            raise CdpError("浏览器主动断开了调试连接")
        return opcode, payload, left
    raise CdpError("调试通道异常（ping 风暴）")


def _send_frame(sock, opcode, payload):
    head = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head.append(0x80 | n)
    elif n < 65536:
        head.append(0x80 | 126)
        head += struct.pack(">H", n)
    else:
        head.append(0x80 | 127)
        head += struct.pack(">Q", n)
    mask = os.urandom(4)
    head += mask
    sock.sendall(bytes(head) + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))


# ---------------------------------------------------------------- 对外入口

def command(port, method, params=None, timeout=6, target_id=None):
    """
    连上 CDP 调 Storage.getCookies，返回全部 cookie 的 list[dict]。
    只在浏览器活着时有意义；连不上抛 CdpError。
    """
    if target_id is None:
        ver = _http_json(port, "/json/version", timeout)
    else:
        targets = _http_json(port, "/json/list", timeout)
        matches = [t for t in targets if t.get("id") == target_id and t.get("type") == "page"]
        if len(matches) != 1:
            raise CdpError("未找到唯一的登录页面调试目标")
        ver = matches[0]
    ws_url = ver.get("webSocketDebuggerUrl") or ""
    if not ws_url.startswith("ws://"):
        raise CdpError("未取到调试地址")

    rest = ws_url[len("ws://"):]
    hostport, _, path = rest.partition("/")
    host, _, p = hostport.partition(":")
    port_w = int(p or port)
    if host not in ("127.0.0.1", "localhost") or port_w != int(port):
        raise CdpError("拒绝非本机调试地址")
    path = "/" + path

    sock = socket.create_connection((host, port_w), timeout=timeout)
    sock.settimeout(timeout)
    try:
        leftover = _ws_handshake(sock, host, port_w, path)
        _send_frame(sock, 0x1, json.dumps({"id": 1, "method": method, "params": params or {}}).encode())
        left = leftover
        for _ in range(20):          # 可能有事件帧混进来，循环取到自己的响应为止
            op, payload, left = _recv_frame(sock, left)
            if op != 0x1:
                continue
            msg = json.loads(payload.decode("utf-8", "replace"))
            if msg.get("id") == 1:
                if "error" in msg:
                    raise CdpError("CDP 调用失败：%s" % msg["error"].get("message"))
                return msg.get("result") or {}
        raise CdpError("未收到 CDP 响应")
    finally:
        try:
            sock.close()
        except Exception:
            pass


def get_all_cookies(port, timeout=6):
    return command(port, "Storage.getCookies", timeout=timeout).get("cookies") or []


def cookies_for_host(port, host_suffixes, timeout=6):
    """取指定域名后缀（不区分大小写）的 cookie，转成 {name: value}。
    父域（如 .erp321.com）排前面，同名时以覆盖面更广的为准。"""
    suffixes = tuple(s.lower() for s in host_suffixes)
    rows = get_all_cookies(port, timeout)
    rows = [r for r in rows
            if isinstance(r, dict) and any(r.get("domain", "").lower().lstrip(".") == s or
                r.get("domain", "").lower().endswith("." + s) for s in suffixes)]
    # 父域优先，其次域名短的在前（.erp321.com 比 www.erp321.com 更广）
    rows.sort(key=lambda r: (not r.get("domain", "").startswith("."), len(r.get("domain", ""))))
    out = {}
    for r in rows:
        name = r.get("name") or ""
        val = r.get("value") or ""
        if name and val and name not in out:
            out[name] = val
    return out


def free_port():
    """挑一个空闲的本地端口给 --remote-debugging-port 用"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
