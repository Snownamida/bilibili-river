#!/usr/bin/env python3
"""抓一轮：分组 → 成员 → 每个 UP 的最新投稿 → 入库。

B 站对机房 IP 有频率风控（HTTP 412），所以这里刻意很慢：
每个 UP 之间歇 REQUEST_GAP 秒，412 了先等 2 分钟再试一次，
连续两个 UP 都失败就整轮退避半小时。

用法: python3 fetch.py
结果写进 data/run.json，页面顶部显示。
"""
import json
import os
import sqlite3
import time

import config
from bili import Bili, BiliError

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos(
    bvid       TEXT PRIMARY KEY,
    aid        INTEGER,
    mid        INTEGER NOT NULL,
    title      TEXT,
    pubdate    INTEGER,
    cover      TEXT,
    duration   INTEGER,
    tname      TEXT,
    first_seen INTEGER,
    seen       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_videos_pub ON videos(pubdate DESC);
CREATE TABLE IF NOT EXISTS members(
    mid    INTEGER NOT NULL,
    grp    TEXT NOT NULL,
    uname  TEXT,
    PRIMARY KEY(mid, grp)
);
CREATE TABLE IF NOT EXISTS signals(
    kind  TEXT NOT NULL,
    oid   TEXT NOT NULL,
    bvid  TEXT,
    mid   INTEGER,
    uname TEXT,
    title TEXT,
    ts    INTEGER,
    extra TEXT,
    PRIMARY KEY(kind, oid)
);
CREATE INDEX IF NOT EXISTS idx_signals_mid ON signals(mid);
CREATE TABLE IF NOT EXISTS history(
    bvid     TEXT,
    aid      INTEGER,
    mid      INTEGER,
    uname    TEXT,
    face     TEXT,
    title    TEXT,
    view_at  INTEGER,
    progress INTEGER,
    duration INTEGER,
    business TEXT,
    PRIMARY KEY(bvid, view_at)
);
CREATE INDEX IF NOT EXISTS idx_history_mid ON history(mid, view_at);
CREATE TABLE IF NOT EXISTS follow_state(
    mid        INTEGER PRIMARY KEY,
    attribute  INTEGER,
    checked_at INTEGER
);
CREATE TABLE IF NOT EXISTS fetch_state(
    mid          INTEGER PRIMARY KEY,
    last_fetched INTEGER DEFAULT 0,
    last_error   TEXT
);
"""


def touch_state(cur, mid, err=None):
    cur.execute(
        "INSERT INTO fetch_state(mid, last_fetched, last_error) VALUES(?,?,?) "
        "ON CONFLICT(mid) DO UPDATE SET last_fetched=excluded.last_fetched,"
        " last_error=excluded.last_error",
        (mid, int(time.time()), err))


def db():
    os.makedirs(config.DATA, exist_ok=True)
    con = sqlite3.connect(config.DB, timeout=15)
    con.executescript(SCHEMA)
    return con


def _write_json(path, obj):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def cooling_down():
    try:
        with open(config.RUN, encoding="utf-8") as f:
            return int(json.load(f).get("cooldown_until") or 0) > time.time()
    except Exception:
        return False


def fetch_one(b, mid, ps):
    """最多试两次；412/-352 等 2 分钟再试。"""
    last = None
    for attempt in range(2):
        try:
            return b.user_videos(mid, ps=ps), None
        except BiliError as e:
            last = e
            if e.code not in (412, -352):
                return None, e
            if attempt == 0:
                print(f"  mid={mid} {e}，等 120 秒重试", flush=True)
                time.sleep(120)
    return None, last


def run(force=False):
    t0 = time.time()
    lock = os.path.join(config.DATA, "nightly.lock")
    if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 1200:
        print("nightly 正在跑，跳过这一轮", flush=True)
        return None
    if not force and cooling_down():
        print("还在退避期，跳过这一轮", flush=True)
        return None

    con = db()
    cur = con.cursor()
    errs = []
    added = 0
    mids = []
    uname = ""

    b = Bili(config.COOKIE)
    try:
        me = b.me()
        uname = me.get("uname") or ""
        raw_groups = b.groups()
        all_groups = {g["name"]: g for g in raw_groups}
        _write_json(config.GROUPS_FILE, {
            "ts": int(time.time()),
            "groups": [{"tagid": g["tagid"], "name": g["name"], "count": g["count"]}
                       for g in raw_groups],
        })
    except BiliError as e:
        return _finish(con, t0, 0, 0, [{"where": "登录", "msg": str(e)}], b, uname, cooldown=86400)

    targets = []
    for name in config.selected_groups():
        g = all_groups.get(name)
        if not g:
            errs.append({"where": name, "msg": "找不到这个关注分组"})
            continue
        targets.append(g)

    if not targets:
        return _finish(con, t0, 0, 0, errs, b, uname)

    # 成员表按组更新：只有这组拉成功了才替换，失败就保留旧的
    seen_mid = set()
    for g in targets:
        try:
            rows = b.group_members(g["tagid"])
        except BiliError as e:
            errs.append({"where": g["name"], "msg": str(e)})
            continue
        cur.execute("DELETE FROM members WHERE grp=?", (g["name"],))
        for u in rows:
            cur.execute("INSERT OR REPLACE INTO members(mid, grp, uname) VALUES(?,?,?)",
                        (u["mid"], g["name"], u["uname"]))
            if u["mid"] not in seen_mid:
                seen_mid.add(u["mid"])
                mids.append(u["mid"])
        con.commit()
        time.sleep(2)

    # 按「最久没查过」轮转，每次只查一小批：B 站大概十分钟能容忍十来次请求
    names = [g["name"] for g in targets]
    total_mids = con.execute(
        f"SELECT COUNT(DISTINCT mid) FROM members WHERE grp IN ({','.join('?' * len(names))})",
        names).fetchone()[0]
    q = ("SELECT m.mid FROM (SELECT DISTINCT mid FROM members WHERE grp IN "
         f"({','.join('?' * len(names))})) m "
         "LEFT JOIN fetch_state s ON s.mid = m.mid "
         "ORDER BY COALESCE(s.last_fetched, 0) ASC, m.mid ASC")
    args = list(names)
    if config.BATCH:
        q += " LIMIT ?"
        args.append(config.BATCH)
    mids = [r[0] for r in con.execute(q, args)]

    now = int(time.time())
    fail_streak = 0
    for i, mid in enumerate(mids, 1):
        n = cur.execute("SELECT COUNT(*) FROM videos WHERE mid=?", (mid,)).fetchone()[0]
        ps = config.PS if n else config.BACKFILL_PS
        vids, err = fetch_one(b, mid, ps)
        if vids is None:
            errs.append({"where": f"mid={mid}", "msg": str(err)})
            touch_state(cur, mid, str(err))
            con.commit()
            fail_streak += 1
            print(f"[{i}/{len(mids)}] mid={mid} 失败：{err}", flush=True)
            if err.code == -101:
                errs.append({"where": "abort", "msg": "cookie 失效"})
                return _finish(con, t0, i, added, errs, b, uname, cooldown=86400,
                               total_mids=total_mids)
            if fail_streak >= 2:
                errs.append({"where": "abort", "msg": "连续失败，退避 30 分钟"})
                return _finish(con, t0, i, added, errs, b, uname,
                               cooldown=config.COOLDOWN, total_mids=total_mids)
            time.sleep(config.REQUEST_GAP)
            continue

        fail_streak = 0
        touch_state(cur, mid)
        for v in vids:
            if not v["bvid"]:
                continue
            # 首次入库：48 小时以外的直接算已读，不然第一次打开满屏红点
            seen = 0 if now - v["pubdate"] <= config.UNREAD_GRACE else 1
            cur.execute(
                "INSERT OR IGNORE INTO videos"
                "(bvid, aid, mid, title, pubdate, cover, duration, tname, first_seen, seen)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (v["bvid"], v["aid"], mid, v["title"], v["pubdate"], v["cover"],
                 v["duration"], v["tname"], now, seen))
            if cur.rowcount:
                added += 1
            else:
                cur.execute("UPDATE videos SET title=?, cover=?, duration=?, pubdate=? WHERE bvid=?",
                            (v["title"], v["cover"], v["duration"], v["pubdate"], v["bvid"]))
        con.commit()
        print(f"[{i}/{len(mids)}] mid={mid} 拿到 {len(vids)} 条，累计新增 {added}", flush=True)
        time.sleep(config.REQUEST_GAP)
        if config.PAUSE_EVERY and i % config.PAUSE_EVERY == 0 and i < len(mids):
            time.sleep(config.PAUSE_SECONDS)

    return _finish(con, t0, len(mids), added, errs, b, uname, total_mids=total_mids)


def _finish(con, t0, n_mid, added, errs, b, uname, cooldown=0, total_mids=0):
    total = con.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    con.close()
    run = {
        "ts": int(time.time()),
        "uname": uname,
        "mids": n_mid,
        "total_mids": total_mids,
        "added": added,
        "total": total,
        "calls": getattr(b, "calls", 0),
        "seconds": round(time.time() - t0, 1),
        "cooldown_until": int(time.time()) + cooldown if cooldown else 0,
        "errors": errs,
    }
    os.makedirs(config.DATA, exist_ok=True)
    tmp = config.RUN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False)
    os.replace(tmp, config.RUN)
    print(json.dumps(run, ensure_ascii=False), flush=True)
    return run


if __name__ == "__main__":
    run()
