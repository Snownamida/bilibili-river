#!/usr/bin/env python3
"""B 站河道：页面 + API + 定时抓取。

只绑内网/tailnet（本机 IP 见 local_config.py）
启动后每 30 分钟自己抓一轮；也可以 POST /api/refresh 立刻抓。
"""
import glob
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from xml.sax.saxutils import escape

import config
import fetch

_fetch_lock = threading.Lock()


def db():
    con = sqlite3.connect(config.DB, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def read_run():
    try:
        with open(config.RUN, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def items(days=7, grp="all", unseen=False):
    since = int(time.time()) - int(days) * 86400
    sql = ["SELECT v.bvid, v.aid, v.title, v.pubdate, v.cover, v.duration, v.tname, v.seen,",
           "       v.mid, m.uname, GROUP_CONCAT(DISTINCT m.grp) AS grps",
           "FROM videos v JOIN members m ON m.mid = v.mid",
           "WHERE v.pubdate >= ?"]
    args = [since]
    if grp and grp != "all":
        sql.append("AND m.grp = ?")
        args.append(grp)
    sql.append("GROUP BY v.bvid")
    if unseen:
        sql.append("HAVING v.seen = 0")
    sql.append("ORDER BY v.pubdate DESC LIMIT 600")
    con = db()
    try:
        rows = [dict(r) for r in con.execute(" ".join(sql), args)]
    finally:
        con.close()
    for r in rows:
        r["grps"] = (r.get("grps") or "").split(",")
    return rows


def mark_seen(bvid=None, days=None):
    con = db()
    try:
        if bvid:
            con.execute("UPDATE videos SET seen=1 WHERE bvid=?", (bvid,))
        elif days:
            since = int(time.time()) - int(days) * 86400
            con.execute("UPDATE videos SET seen=1 WHERE seen=0 AND pubdate>=?", (since,))
        con.commit()
    finally:
        con.close()


def feed_xml(limit=60):
    rows = items(days=30)[:limit]
    run = read_run() or {}
    updated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((run.get("ts") or time.time())))
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<feed xmlns="http://www.w3.org/2005/Atom">',
           '<title>B站河道</title>',
           f'<id>http://{config.HOST}:{config.PORT}/</id>',
           '<link rel="self" href="/feed.xml"/>',
           f'<updated>{updated}</updated>']
    for r in rows:
        t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["pubdate"]))
        out.append("<entry>")
        out.append(f'<title>{escape(r["title"] or "")}</title>')
        out.append(f'<id>tag:bilibili,{r["bvid"]}</id>')
        out.append(f'<link href="https://www.bilibili.com/video/{r["bvid"]}"/>')
        out.append(f'<updated>{t}</updated>')
        out.append(f'<summary>{escape((r["uname"] or "") + " · " + "/".join(r["grps"]))}</summary>')
        out.append("</entry>")
    out.append("</feed>")
    return "\n".join(out)


EXPORT_FILES = {"manifest.json", "data/river.db", "data/selection.json",
                "data/groups.json", "cookie.header"}
MAX_IMPORT = 100 * 1024 * 1024


def make_export(include_cookie=False):
    """打包一份可迁移的备份：数据库 + 勾选的分组 + 分组缓存（可选 cookie）。"""
    buf = io.BytesIO()
    man = {
        "app": "bili-river",
        "version": 1,
        "exported_at": int(time.time()),
        "groups": config.selected_groups(),
        "includes_cookie": bool(include_cookie),
    }
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add_bytes(name, data, mode=0o644):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = mode
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(data))

        add_bytes("manifest.json", json.dumps(man, ensure_ascii=False, indent=2).encode("utf-8"))

        if os.path.exists(config.DB):
            fd, tmp = tempfile.mkstemp(suffix=".db", dir=config.DATA)
            os.close(fd)
            src = sqlite3.connect(config.DB)
            dst = sqlite3.connect(tmp)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
            tar.add(tmp, arcname="data/river.db")
            os.unlink(tmp)

        for name, path in (("data/selection.json", config.SELECTION),
                           ("data/groups.json", config.GROUPS_FILE)):
            if os.path.exists(path):
                with open(path, "rb") as f:
                    add_bytes(name, f.read())

        if include_cookie and os.path.exists(config.COOKIE):
            with open(config.COOKIE, "rb") as f:
                add_bytes("cookie.header", f.read(), mode=0o600)

    return buf.getvalue()


def do_import(raw):
    """从备份恢复。导入前把当前库另存一份，导入失败不至于把现场毁掉。"""
    if _fetch_lock.locked():
        raise ValueError("正在抓取，等这一轮结束再导入")
    if len(raw) > MAX_IMPORT:
        raise ValueError("备份文件太大")
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            members = tar.getmembers()
            for m in members:
                if not m.isfile() or m.name not in EXPORT_FILES:
                    raise ValueError(f"备份里有不认识的文件：{m.name}")
            files = {m.name: tar.extractfile(m).read() for m in members}
    except tarfile.TarError:
        raise ValueError("不是有效的 .tar.gz 备份") from None

    man = {}
    if "manifest.json" in files:
        try:
            man = json.loads(files["manifest.json"])
        except Exception:
            man = {}
        if man.get("app") not in (None, "bili-river"):
            raise ValueError("这不是本项目的备份")

    os.makedirs(config.DATA, exist_ok=True)
    if os.path.exists(config.DB):
        shutil.copy2(config.DB, f"{config.DB}.before-import-{int(time.time())}")

    def atomic(path, data, mode=0o644):
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)

    if "data/river.db" in files:
        atomic(config.DB, files["data/river.db"])
    for name, path in (("data/selection.json", config.SELECTION),
                       ("data/groups.json", config.GROUPS_FILE)):
        if name in files:
            atomic(path, files[name])
    for old in sorted(glob.glob(config.DB + ".before-import-*"))[:-3]:
        try:
            os.remove(old)
        except OSError:
            pass
    if "cookie.header" in files:
        os.makedirs(os.path.dirname(config.COOKIE), exist_ok=True, mode=0o700)
        atomic(config.COOKIE, files["cookie.header"], mode=0o600)

    return man


def groups_cache():
    try:
        with open(config.GROUPS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ts": 0, "groups": []}


class Handler(BaseHTTPRequestHandler):
    server_version = "bili-river/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except BrokenPipeError:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(config.ROOT, "index.html"), encoding="utf-8") as f:
                return self._send(200, f.read())
        if u.path == "/api/items":
            days = _int(q.get("days", ["7"])[0], 7, 1, 90)
            grp = q.get("grp", ["all"])[0]
            unseen = q.get("unseen", ["0"])[0] == "1"
            rows = items(days, grp, unseen)
            per = {}
            for r in rows:
                for g in r["grps"]:
                    if g:
                        per[g] = per.get(g, 0) + 1
            return self._json({
                "items": rows,
                "groups": config.selected_groups(),
                "counts": per,
                "unread": sum(1 for r in rows if not r["seen"]),
                "run": read_run(),
            })
        if u.path == "/api/status":
            return self._json(read_run() or {})
        if u.path == "/api/export":
            blob = make_export(q.get("cookie", ["0"])[0] == "1")
            name = "bili-river-" + time.strftime("%Y%m%d-%H%M") + ".tar.gz"
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            try:
                self.wfile.write(blob)
            except BrokenPipeError:
                pass
            return
        if u.path == "/api/groups":
            c = groups_cache()
            return self._json({"groups": c.get("groups", []),
                               "ts": c.get("ts", 0),
                               "selected": config.selected_groups()})
        if u.path == "/feed.xml":
            return self._send(200, feed_xml(), "application/atom+xml; charset=utf-8")
        return self._send(404, "not found")

    def do_POST(self):
        u = urlsplit(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if u.path == "/api/import":
            try:
                man = do_import(raw)
            except ValueError as e:
                return self._json({"ok": False, "msg": str(e)}, 400)
            return self._json({"ok": True, "manifest": man})
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if u.path == "/api/seen":
            days = body.get("days")
            mark_seen(bvid=body.get("bvid"), days=days)
            return self._json({"ok": True})
        if u.path == "/api/groups":
            names = body.get("groups")
            if not isinstance(names, list) or not names:
                return self._json({"ok": False, "msg": "至少选一个分组"}, 400)
            known = {g["name"] for g in groups_cache().get("groups", [])}
            if known and any(n not in known for n in names):
                return self._json({"ok": False, "msg": "有分组不在缓存里，刷新后再试"}, 400)
            tmp = config.SELECTION + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"groups": names, "ts": int(time.time())}, f, ensure_ascii=False)
            os.replace(tmp, config.SELECTION)
            return self._json({"ok": True, "groups": names})
        if u.path == "/api/refresh":
            started = _start_fetch(background=True, force=True)
            return self._json({"ok": True, "started": started})
        return self._send(404, "not found")


def _int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except Exception:
        return default


def _start_fetch(background=True, force=False):
    if _fetch_lock.locked():
        return False

    def job():
        with _fetch_lock:
            try:
                fetch.run(force=force)
            except Exception as e:
                print("fetch failed:", repr(e), flush=True)

    if background:
        threading.Thread(target=job, daemon=True).start()
    else:
        job()
    return True


def scheduler():
    """按上一轮的 ts 排下一轮，重启不会立刻又去撞 B 站。"""
    time.sleep(5)
    while True:
        last = (read_run() or {}).get("ts", 0)
        wait = last + config.FETCH_INTERVAL - time.time()
        if wait > 0:
            time.sleep(min(wait, 300))
            continue
        _start_fetch(background=False)
        time.sleep(60)


def main():
    os.makedirs(config.DATA, exist_ok=True)
    if os.environ.get("BILI_NO_FETCH") != "1":
        threading.Thread(target=scheduler, daemon=True).start()
    srv = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    print(f"bili-river on http://{config.HOST}:{config.PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
