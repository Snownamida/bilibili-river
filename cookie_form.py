#!/usr/bin/env python3
"""一次性网页：把 B 站 Cookie 粘进来，写成 0600 文件。

不经过 shell，所以不会进任何命令历史。只在 tailnet 上开几分钟。
"""
import html
import os
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

HOST = "100.87.197.52"
PORT = 8123
def _token():
    path = os.path.expanduser("~/.config/bili-river/form_token")
    try:
        t = open(path, encoding="utf-8").read().strip()
    except Exception:
        t = ""
    if not t:
        t = secrets.token_urlsafe(8)
        os.makedirs(os.path.dirname(path), exist_ok=True, mode=0o700)
        with open(path, "w", encoding="utf-8") as f:
            f.write(t)
        os.chmod(path, 0o600)
    return t


TOKEN = _token()
COOKIE = os.path.expanduser("~/.config/bili-river/cookie.header")
TIMEOUT = 900

PAGE = """<!doctype html><html lang="zh"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>写入 B 站 Cookie</title>
<style>
body{{font:15px/1.5 -apple-system,"PingFang SC",sans-serif;margin:0;padding:18px;background:#f6f7f9;color:#16181c}}
h1{{font-size:18px;margin:0 0 8px}} p{{color:#6b7280;font-size:13px}}
textarea{{width:100%;box-sizing:border-box;height:160px;padding:10px;border:1px solid #d1d5db;
  border-radius:10px;font:13px/1.4 ui-monospace,monospace}}
button{{margin-top:12px;width:100%;padding:13px;border:0;border-radius:10px;background:#fb7299;
  color:#fff;font-size:16px}}
.ok{{background:#e8f7ee;color:#136c33;padding:12px;border-radius:10px;font-size:13px;word-break:break-all}}
.err{{background:#fdecec;color:#a11;padding:12px;border-radius:10px;font-size:13px}}
</style>
<body>
<h1>写入 B 站 Cookie</h1>
<p>粘贴 DevTools 里 <code>Cookie:</code> 后面整串（一行或带换行都行）。不会经过 shell，也不会进命令历史。</p>
{body}
</body></html>"""


KNOWN = {
    "SESSDATA", "bili_jct", "buvid3", "buvid4", "buvid_fp", "buvid_fp_plain",
    "b_nut", "b_lsid", "_uuid", "DedeUserID", "DedeUserID__ckMd5", "sid",
    "bili_ticket", "bili_ticket_expires", "CURRENT_FNVAL", "CURRENT_QUALITY",
    "header_theme_version", "home_feed_column", "PVID", "bsource",
    "bmg_src_def_domain", "bmg_af_switch", "fingerprint", "fingerprint3",
    "rpdid", "LIVE_BUVID", "theme_style", "enable_web_push", "share_source",
}


def parse_cookie(raw):
    """从任意形态里捞 cookie：DevTools 的 Cookie 头、document.cookie、
    或者整段 Copy as cURL，都能认。只保留认识的字段名。"""
    raw = raw.replace("\r", "\n")
    out = {}
    for k, v in re.findall(r"([A-Za-z0-9_\-\.]+)=([^;\s\'\"\\,]+)", raw):
        if k in KNOWN and v:
            out[k] = v
    if not out:
        # 退一步：按分号/换行切，形状对就收
        for part in re.split(r"[;\n]+", raw):
            part = part.strip().strip('"').strip("'")
            if "=" in part:
                k, v = part.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if re.fullmatch(r"[A-Za-z0-9_\-\.]{2,40}", k) and v:
                    out[k] = v
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self.path.startswith("/" + TOKEN):
            return self._send(404, "not found")
        self._send(200, PAGE.format(body='<form method="post" action="/%s">'
                                    '<textarea name="c" placeholder="SESSDATA=...; bili_jct=..."></textarea>'
                                    '<button>保存</button></form>' % TOKEN))

    def do_POST(self):
        if not self.path.startswith("/" + TOKEN):
            return self._send(404, "not found")
        n = int(self.headers.get("Content-Length") or 0)
        raw = parse_qs(self.rfile.read(n).decode("utf-8", "replace")).get("c", [""])[0]
        ck = parse_cookie(raw)
        miss = [k for k in ("SESSDATA", "bili_jct") if k not in ck]
        print(f"POST {len(raw)} 字符 -> 字段 {list(ck)} 缺 {miss}", flush=True)
        if miss:
            return self._send(200, PAGE.format(body=(
                '<div class="err">缺少字段：%s。请粘贴整行 Cookie。</div>' % "、".join(miss))
                + '<p><a href="/%s">返回</a></p>' % TOKEN))
        os.makedirs(os.path.dirname(COOKIE), exist_ok=True, mode=0o700)
        line = "; ".join(f"{k}={v}" for k, v in ck.items())
        tmp = COOKIE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(line + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, COOKIE)
        names = "<br>".join(f"{html.escape(k)}（{len(v)} 字符）" for k, v in ck.items())
        self._send(200, PAGE.format(body=f'<div class="ok">已写入 {len(ck)} 个字段：<br>{names}<br><br>'
                                        '可以关掉这个页面了。</div>'))
        threading.Timer(2, self.server.shutdown).start()

    def do_HEAD(self):
        self._send(200, "")


def main():
    srv = ThreadingHTTPServer((HOST, PORT), H)
    threading.Timer(TIMEOUT, srv.shutdown).start()
    print(f"http://{HOST}:{PORT}/{TOKEN}/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
