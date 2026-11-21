#!/usr/bin/env python3
"""B 站只读客户端：关注分组、分组成员、某个 UP 的最新投稿。

只发 GET，不改任何东西。cookie 只从文件读，不落日志。
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 安卓客户端网关：比网页的 space 接口宽松得多，实测 2 秒一个也不吃 412
APP_KEY = "1d8b6e7d45233436"
APP_SEC = "560c52ccd288fed045859ed18bffd973"
APP_UA = "Mozilla/5.0 BiliDroid/7.5.0 (bbcallen@gmail.com) os/android"

# WBI 混淆表，官方前端里抄的
MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43,
         5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16,
         24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59,
         6, 63, 57, 62, 11, 36, 20, 34, 44, 52]

DM_IMG = {
    "dm_img_list": "[]",
    "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
    "dm_cover_img_str": "QU5HTEUgKEludGVsLCBJbnRlbChSKSBVSEQgR3JhcGhpY3MgNjMwICgweDAw"
                        "MDAzRTlCKSBEaXJlY3QzRDExIHZzXzVfMCBwc181XzApLCBvciBzaW1pbGFy",
    "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
}


class BiliError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def load_cookie(path):
    """支持三种粘贴方式：整串 name=value;...、带 Cookie: 前缀、或者只有 SESSDATA 值。"""
    if not os.path.exists(path):
        raise BiliError(f"cookie 文件不存在：{path}")
    raw = open(path, encoding="utf-8").read().strip()
    m = re.search(r"(?im)^\s*cookie:\s*(.+)$", raw)
    if m:
        raw = m.group(1).strip()
    cookies = {}
    for part in re.split(r";\s*", raw):
        if "=" in part:
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k:
                cookies[k] = v
    if not cookies and len(raw) > 40:
        cookies["SESSDATA"] = raw          # 裸值就当 SESSDATA
    if "SESSDATA" not in cookies:
        raise BiliError("cookie 里没有 SESSDATA")
    return cookies


class Bili:
    def __init__(self, cookie_path):
        self.cookie = load_cookie(cookie_path)
        self._key = None
        self._key_at = 0
        self.calls = 0
        self._fingerprint()

    def _fingerprint(self):
        """补一个匿名 buvid3/buvid4，少挨一点风控。失败就算了。"""
        if "buvid3" in self.cookie:
            return
        try:
            req = urllib.request.Request(
                "https://api.bilibili.com/x/frontend/finger/spi",
                headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8", "replace")).get("data") or {}
            if d.get("b_3"):
                self.cookie["buvid3"] = d["b_3"]
            if d.get("b_4"):
                self.cookie["buvid4"] = d["b_4"]
        except Exception:
            pass

    # ---- 底层 ----
    def _get(self, url, params=None, referer="https://space.bilibili.com/"):
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Referer": referer,
            "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookie.items()),
            "Origin": "https://space.bilibili.com",
            "Accept": "application/json, text/plain, */*",
        })
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise BiliError(f"HTTP {e.code} 被拦", code=e.code) from None
        except Exception as e:
            raise BiliError(f"网络错误：{e!r}") from None
        try:
            data = json.loads(body)
        except Exception:
            raise BiliError("返回的不是 JSON（可能被风控页拦了）") from None
        code = data.get("code")
        if code != 0:
            msg = data.get("message") or ""
            if code == -101:
                msg = "SESSDATA 失效，请重新写入 cookie"
            elif code == -352:
                msg = "风控校验失败（-352）"
            raise BiliError(f"{msg} (code={code})", code=code)
        return data.get("data")

    # ---- WBI 签名 ----
    def _wbi_key(self):
        if self._key and time.time() - self._key_at < 6 * 3600:
            return self._key
        d = self._get("https://api.bilibili.com/x/web-interface/nav",
                      referer="https://www.bilibili.com/")
        img = (d or {}).get("wbi_img") or {}
        orig = (img.get("img_url", "").rsplit("/", 1)[-1].split(".")[0]
                + img.get("sub_url", "").rsplit("/", 1)[-1].split(".")[0])
        if len(orig) < 64:
            raise BiliError("拿不到 WBI key")
        self._key = "".join(orig[i] for i in MIXIN)[:32]
        self._key_at = time.time()
        return self._key

    def _sign(self, params):
        p = dict(params)
        p["wts"] = int(time.time())
        q = urllib.parse.urlencode(sorted(p.items()))
        q = "".join(c for c in q if c not in "!'()*")
        p["w_rid"] = hashlib.md5((q + self._wbi_key()).encode()).hexdigest()
        return p

    # ---- 业务 ----
    def me(self):
        d = self._get("https://api.bilibili.com/x/web-interface/nav",
                      referer="https://www.bilibili.com/")
        if not (d or {}).get("isLogin"):
            raise BiliError("cookie 没登录（SESSDATA 可能过期）", code=-101)
        return d

    def groups(self):
        """关注分组：[{tagid, name, count}]"""
        d = self._get("https://api.bilibili.com/x/relation/tags")
        return [{"tagid": g.get("tagid"), "name": g.get("name"), "count": g.get("count")}
                for g in (d or [])]

    def group_members(self, tagid, ps=50, max_pages=8):
        """分组成员，翻页取；超大分组最多取 8 页（400 人），别把接口打爆。"""
        out, pn = [], 1
        while True:
            d = self._get("https://api.bilibili.com/x/relation/tag",
                          {"tagid": tagid, "pn": pn, "ps": ps})
            rows = d if isinstance(d, list) else (d or {}).get("list") or []
            if not rows:
                break
            out.extend({"mid": u.get("mid"), "uname": u.get("uname")} for u in rows)
            if len(rows) < ps or pn >= max_pages:
                break
            pn += 1
            time.sleep(0.6)
        return out

    def _app_get(self, path, params):
        p = dict(params)
        p.update({"appkey": APP_KEY, "ts": int(time.time()),
                  "mobi_app": "android", "platform": "android", "build": 7050000})
        q = urllib.parse.urlencode(sorted(p.items()))
        p["sign"] = hashlib.md5((q + APP_SEC).encode()).hexdigest()
        url = "https://app.bilibili.com" + path + "?" + urllib.parse.urlencode(p)
        req = urllib.request.Request(url, headers={"User-Agent": APP_UA})
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise BiliError(f"app HTTP {e.code}", code=e.code) from None
        except Exception as e:
            raise BiliError(f"app 网络错误：{e!r}") from None
        if data.get("code") != 0:
            raise BiliError(f"app {data.get('message')} (code={data.get('code')})",
                            code=data.get("code"))
        return data.get("data") or {}

    def user_videos(self, mid, ps=20):
        """某个 UP 的最新投稿，按发布时间倒序。先走 app 网关，再退回网页接口。"""
        try:
            d = self._app_get("/x/v2/space/archive/cursor",
                              {"vmid": mid, "ps": ps, "order": "pubdate"})
            items = d.get("item") or []
            out = []
            for v in items:
                if v.get("goto") not in ("av", "bvid", None):
                    continue
                out.append({
                    "bvid": v.get("bvid"),
                    "aid": v.get("param"),
                    "mid": mid,
                    "title": (v.get("title") or "").strip(),
                    "pubdate": int(v.get("ctime") or 0),
                    "cover": (v.get("cover") or "").replace("http://", "https://"),
                    "duration": int(v.get("duration") or 0),
                    "tname": v.get("tname") or "",
                })
            # app 说成功就信它；只有它明确报错、或者有稿但返回空，才退回网页接口
            if out or not d.get("count"):
                return out
        except BiliError:
            pass
        return self._web_videos(mid, ps)

    def _web_videos(self, mid, ps=20):
        p = self._sign({
            "mid": mid, "ps": ps, "pn": 1, "order": "pubdate",
            "platform": "web", "web_location": 1550101, **DM_IMG,
        })
        d = self._get("https://api.bilibili.com/x/space/wbi/arc/search", p,
                      referer=f"https://space.bilibili.com/{mid}/video")
        vlist = ((d or {}).get("list") or {}).get("vlist") or []
        out = []
        for v in vlist:
            out.append({
                "bvid": v.get("bvid"),
                "aid": v.get("aid"),
                "mid": mid,
                "title": (v.get("title") or "").strip(),
                "pubdate": int(v.get("created") or 0),
                "cover": (v.get("pic") or "").replace("http://", "https://"),
                "duration": parse_len(v.get("length")),
                "tname": v.get("tname") or "",
            })
        return out


def parse_len(s):
    """'12:34' / '1:02:03' → 秒"""
    if not s:
        return 0
    try:
        parts = [int(x) for x in str(s).split(":")]
    except ValueError:
        return 0
    sec = 0
    for x in parts:
        sec = sec * 60 + x
    return sec
