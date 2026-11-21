#!/usr/bin/env python3
"""B 站河道：页面 + API + 定时抓取。

只绑 tailnet：http://100.87.197.52:8100
启动后每 30 分钟自己抓一轮；也可以 POST /api/refresh 立刻抓。
"""
import json
import os
import sqlite3
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
    sql = ["SELECT v.bvid, v.title, v.pubdate, v.cover, v.duration, v.tname, v.seen,",
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
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
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
