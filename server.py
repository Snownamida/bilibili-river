#!/usr/bin/env python3
"""B 站河道：页面 + API + 定时抓取。

只绑内网/tailnet（本机 IP 见 local_config.py）
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

import candidate
import config
from bili import Bili
from bili import BiliError
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
           "WHERE v.pubdate >= ? AND v.hidden = 0 AND v.paid = 0"]
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


def summary(days, unseen):
    """分组计数和总数要独立算，不能拿筛过的结果集去数（否则别的组永远是 0）。"""
    since = int(time.time()) - int(days) * 86400
    base = "v.pubdate >= ? AND v.hidden = 0 AND v.paid = 0"
    where = base + (" AND v.seen = 0" if unseen else "")
    con = db()
    try:
        total = con.execute(
            f"SELECT COUNT(DISTINCT v.bvid) FROM videos v JOIN members m ON m.mid = v.mid "
            f"WHERE {where}", (since,)).fetchone()[0]
        total_all = con.execute(
            f"SELECT COUNT(DISTINCT v.bvid) FROM videos v JOIN members m ON m.mid = v.mid "
            f"WHERE {base}", (since,)).fetchone()[0]
        per_all = dict(con.execute(
            f"SELECT m.grp, COUNT(DISTINCT v.bvid) FROM videos v JOIN members m ON m.mid = v.mid "
            f"WHERE {base} GROUP BY m.grp", (since,)).fetchall())
        per = dict(con.execute(
            f"SELECT m.grp, COUNT(DISTINCT v.bvid) FROM videos v JOIN members m ON m.mid = v.mid "
            f"WHERE {where} GROUP BY m.grp", (since,)).fetchall())
        unread = con.execute(
            "SELECT COUNT(DISTINCT v.bvid) FROM videos v JOIN members m ON m.mid = v.mid "
            "WHERE v.pubdate >= ? AND v.seen = 0 AND v.hidden = 0", (since,)).fetchone()[0]
    finally:
        con.close()
    return per, per_all, total, total_all, unread


def set_hidden(bvid, hidden=1):
    con = db()
    try:
        con.execute("UPDATE videos SET hidden=? WHERE bvid=?", (1 if hidden else 0, bvid))
        con.commit()
    finally:
        con.close()


def mark_seen(bvid=None, days=None, seen=1):
    con = db()
    try:
        if bvid:
            con.execute("UPDATE videos SET seen=? WHERE bvid=?", (1 if seen else 0, bvid))
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


def tidy_data():
    import json as _json
    try:
        props = _json.load(open(os.path.join(config.DATA, "tidy_proposals.json"), encoding="utf-8"))
    except Exception:
        props = []
    con = db()
    try:
        faces = {r[0]: r[1] for r in con.execute("SELECT mid, face FROM followings")}
    except Exception:
        faces = {}
    tops = {r[0]: r[1] for r in con.execute("SELECT mid, top_json FROM profiles")}
    con.close()
    for p in props:
        p["face"] = faces.get(p["mid"], "")
        p.setdefault("confidence", 0.9)
        if not isinstance(p.get("groups"), list) or not p["groups"]:
            p["groups"] = [p.get("group")] if p.get("group") else []
        try:
            vids = json.loads(tops.get(p["mid"]) or "[]")[:3]
        except Exception:
            vids = []
        p["videos"] = [{"t": v.get("t"), "p": v.get("p"), "c": v.get("c")} for v in vids]
    return props


def tidy_apply(items):
    """items: [{mid, group}]，按组批量加。"""
    b = Bili(config.COOKIE)
    if not b.csrf:
        raise BiliError("cookie 里没有 bili_jct")
    by = {}
    for it in items:
        by.setdefault(it["group"], []).append(it["mid"])
    created, moved = [], 0
    for grp, mids in by.items():
        g = next((x for x in b.groups() if x["name"] == grp), None)
        if not g:
            d = b.create_tag(grp)
            g = {"tagid": d.get("tagid"), "name": grp}
            created.append(grp)
        for i in range(0, len(mids), 20):
            chunk = [int(m) for m in mids[i:i + 20]]
            b.add_users(chunk, g["tagid"])
            moved += len(chunk)
            time.sleep(1.5)
    return {"moved": moved, "created": created}


def shown_groups():
    """页面上的分组顺序：默认分组排最前面。"""
    gs = config.selected_groups()
    d = config.DEFAULT_GRP
    if d in gs:
        return [d] + [g for g in gs if g != d]
    return gs


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
        if u.path in ("/tidy", "/tidy.html"):
            with open(os.path.join(config.ROOT, "tidy.html"), encoding="utf-8") as f:
                return self._send(200, f.read())
        if u.path in ("/candidates", "/candidates.html"):
            with open(os.path.join(config.ROOT, "candidates.html"), encoding="utf-8") as f:
                return self._send(200, f.read())
        if u.path == "/api/candidates":
            data = candidate.load()
            data["status"] = candidate.read_status()
            return self._json(data)
        if u.path == "/api/candidates/status":
            return self._json(candidate.read_status())
        if u.path in ("/", "/index.html"):
            with open(os.path.join(config.ROOT, "index.html"), encoding="utf-8") as f:
                return self._send(200, f.read())
        if u.path == "/api/items":
            days = _int(q.get("days", ["7"])[0], 7, 1, 36500)
            grp = q.get("grp", ["all"])[0]
            unseen = q.get("unseen", ["0"])[0] == "1"
            rows = items(days, grp, unseen)
            per, per_all, total, total_all, unread = summary(days, unseen)
            if grp != "all":
                total = per.get(grp, 0)
                total_all = per_all.get(grp, 0)
            return self._json({
                "items": rows,
                "groups": shown_groups(),
                "default_grp": (config.DEFAULT_GRP if config.DEFAULT_GRP in config.selected_groups() else "all"),
                "counts": per,
                "total": total,
                "total_all": total_all,
                "unread": unread,
                "run": read_run(),
            })
        if u.path == "/api/status":
            return self._json(read_run() or {})
        if u.path == "/api/tidy":
            props = tidy_data()
            grp = q.get("grp", ["all"])[0]
            tidy_items = [p for p in props if grp == "all" or p.get("group") == grp]
            from collections import Counter
            counts = Counter(p.get("group") for p in props)
            return self._json({"items": tidy_items, "counts": counts,
                               "total": len(props)})
        if u.path == "/api/groups":
            c = groups_cache()
            return self._json({"groups": c.get("groups", []),
                               "ts": c.get("ts", 0),
                               "selected": shown_groups(),
                               "default_grp": (config.DEFAULT_GRP
                                               if config.DEFAULT_GRP in config.selected_groups()
                                               else "all")})
        if u.path == "/feed.xml":
            return self._send(200, feed_xml(), "application/atom+xml; charset=utf-8")
        return self._send(404, "not found")

    def do_POST(self):
        u = urlsplit(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if u.path == "/api/hide":
            bvid = body.get("bvid")
            if not bvid:
                return self._json({"ok": False, "msg": "缺 bvid"}, 400)
            set_hidden(bvid, int(body.get("hidden", 1)))
            return self._json({"ok": True})
        if u.path == "/api/seen":
            days = body.get("days")
            seen = int(body.get("seen", 1))
            mark_seen(bvid=body.get("bvid"), days=days, seen=seen)
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
        if u.path == "/api/tidy/unfollow":
            mid = body.get("mid")
            if not mid:
                return self._json({"ok": False, "msg": "缺 mid"}, 400)
            try:
                Bili(config.COOKIE).unfollow(int(mid))
            except BiliError as e:
                return self._json({"ok": False, "msg": str(e)}, 400)
            con = db()
            con.execute("DELETE FROM all_members WHERE mid=?", (int(mid),))
            con.commit(); con.close()
            try:
                props = json.load(open(os.path.join(config.DATA, "tidy_proposals.json"), encoding="utf-8"))
                props = [p for p in props if p.get("mid") != int(mid)]
                tmp = os.path.join(config.DATA, "tidy_proposals.json.tmp")
                json.dump(props, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                os.replace(tmp, os.path.join(config.DATA, "tidy_proposals.json"))
            except Exception:
                pass
            return self._json({"ok": True})
        if u.path == "/api/tidy/apply":
            items = body.get("items") or []
            if not items:
                return self._json({"ok": False, "msg": "没有选中"}, 400)
            try:
                r = tidy_apply(items)
            except BiliError as e:
                return self._json({"ok": False, "msg": str(e)}, 400)
            # 应用到 B 站后，本地也从“待分类”里挪走，不用等下一次同步
            con = db()
            for it in items:
                mid = int(it["mid"])
                row = con.execute("SELECT uname FROM all_members WHERE mid=? LIMIT 1", (mid,)).fetchone()
                con.execute("DELETE FROM all_members WHERE mid=? AND grp='默认分组'", (mid,))
                con.execute("INSERT OR REPLACE INTO all_members(mid,grp,uname) VALUES(?,?,?)",
                            (mid, it["group"], row[0] if row else None))
            con.commit(); con.close()
            try:
                path = os.path.join(config.DATA, "tidy_proposals.json")
                props = json.load(open(path, encoding="utf-8"))
                done = {}
                for it in items:
                    done.setdefault(int(it["mid"]), set()).add(it["group"])
                kept = []
                for p in props:
                    gs = p.get("groups") or ([p["group"]] if p.get("group") else [])
                    gs = [g for g in gs if g not in done.get(p.get("mid"), set())]
                    if gs:
                        p["groups"] = gs
                        p["group"] = gs[0]
                        kept.append(p)
                tmp = path + ".tmp"
                json.dump(kept, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                os.replace(tmp, path)
            except Exception:
                pass
            return self._json({"ok": True, **r})
        if u.path == "/api/group/apply":
            mids = body.get("mids") or []
            name = (body.get("tag") or "常看").strip()[:16]
            if not mids:
                return self._json({"ok": False, "msg": "没有勾选"}, 400)
            try:
                r = candidate.apply_group(mids, name)
            except BiliError as e:
                return self._json({"ok": False, "msg": str(e)}, 400)
            return self._json({"ok": True, **r})
        if u.path == "/api/candidates/run":
            started = candidate.run()
            return self._json({"ok": True, "started": started})
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


NIGHTLY_STATE = os.path.join(config.DATA, "nightly_state.json")


def nightly_loop():
    """每天 03:20 之后跑一次 nightly.py；错过了（比如半夜重启）就当天补跑。"""
    import nightly
    last_fail = 0
    while True:
        now = time.localtime()
        due = now.tm_hour > 3 or (now.tm_hour == 3 and now.tm_min >= 20)
        if due:
            try:
                with open(NIGHTLY_STATE, encoding="utf-8") as f:
                    done = json.load(f).get("date")
            except Exception:
                done = None
            today = time.strftime("%F")
            if done != today and time.time() - last_fail > 3600:
                try:
                    nightly.main()
                    with open(NIGHTLY_STATE, "w", encoding="utf-8") as f:
                        json.dump({"date": today, "ts": int(time.time())}, f)
                except Exception as e:
                    last_fail = time.time()
                    print("nightly failed:", repr(e), flush=True)
        time.sleep(300)


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
        threading.Thread(target=nightly_loop, daemon=True).start()
    srv = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    print(f"bili-river on http://{config.HOST}:{config.PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
