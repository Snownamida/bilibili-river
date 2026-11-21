#!/usr/bin/env python3
"""常看候选：同步观看历史 → 只保留关注过的 UP → 用「看了他多少投稿」排序。

判断标准不是内容分类，而是行为：这个 UP 最近发的视频，你点开了多大比例。
"""
import collections
import json
import os
import threading
import time

import config
import fetch
import signals
from bili import Bili, BiliError

LOCK = threading.Lock()
STATUS = os.path.join(config.DATA, "candidates_status.json")
OUT = os.path.join(config.DATA, "candidates.json")


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def read_status():
    try:
        with open(STATUS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def status(**kw):
    cur = read_status()
    cur.update(kw)
    cur["updated"] = int(time.time())
    _write(STATUS, cur)


def sync_history(days=180, max_pages=200, progress=status):
    """把观看历史拉进 SQLite。返回新增条数。"""
    b = Bili(config.COOKIE)
    con = fetch.db()
    cur = con.cursor()
    cutoff = int(time.time()) - days * 86400
    maxv = view_at = 0
    added = pages = 0
    for page in range(max_pages):
        try:
            d = b.history(maxv, view_at)
        except BiliError as e:
            if added == 0:
                raise
            progress(phase="sync", error=f"翻页中断：{e}")
            break
        items = d.get("list") or []
        if not items:
            break
        for it in items:
            h = it.get("history") or {}
            if not it.get("author_mid"):
                continue
            cur.execute(
                "INSERT OR IGNORE INTO history"
                "(bvid, aid, mid, uname, face, title, view_at, progress, duration, business)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (h.get("bvid"), h.get("oid"), it.get("author_mid"), it.get("author_name"),
                 it.get("author_face"), it.get("title"), it.get("view_at"),
                 it.get("progress"), it.get("duration"), h.get("business")))
            added += cur.rowcount
        con.commit()
        pages += 1
        last = items[-1]
        view_at = last.get("view_at") or 0
        maxv = (last.get("history") or {}).get("oid") or 0
        progress(phase="sync", pages=pages, added=added)
        if view_at < cutoff:
            break
        time.sleep(1.0)

    _refresh_follows(b, con, progress)
    con.close()
    return added


def _refresh_follows(b, con, progress):
    """把历史里出现的作者分 50 个一批查关注关系。"""
    known = {r[0] for r in con.execute("SELECT mid FROM follow_state")}
    mids = [r[0] for r in con.execute("SELECT DISTINCT mid FROM history") if r[0] not in known]
    total = len(mids)
    for i in range(0, total, 50):
        batch = mids[i:i + 50]
        try:
            d = b.relations(batch)
        except BiliError:
            time.sleep(3)
            try:
                d = b.relations(batch)
            except BiliError:
                continue
        for mid in batch:
            attr = (d.get(str(mid)) or {}).get("attribute", 0)
            con.execute("INSERT OR REPLACE INTO follow_state(mid, attribute, checked_at) VALUES(?,?,?)",
                        (mid, attr, int(time.time())))
        con.commit()
        progress(phase="follows", done=min(i + 50, total), total=total)
        time.sleep(1.0)


def build(window_days=90, max_ups=60, progress=status):
    """按「最近 window_days 天，这个 UP 发的东西你看了多大比例」排序。"""
    con = fetch.db()
    since = int(time.time()) - window_days * 86400
    rows = con.execute(
        "SELECT h.mid, MAX(h.uname), MAX(h.face), COUNT(DISTINCT h.bvid) n,"
        "       AVG(CASE WHEN h.duration > 0 THEN MIN(1.0, 1.0*h.progress/h.duration) ELSE 0 END) comp,"
        "       MAX(h.view_at) last, COUNT(DISTINCT date(h.view_at, 'unixepoch')) n_days"
        " FROM history h JOIN follow_state f ON f.mid = h.mid"
        " WHERE h.view_at >= ? AND f.attribute IN (2, 6)"
        " GROUP BY h.mid ORDER BY n DESC LIMIT ?", (since, max_ups)).fetchall()

    b = Bili(config.COOKIE)
    out = []
    total = len(rows)
    for i, (mid, uname, face, n, comp, last, n_days) in enumerate(rows, 1):
        watched = {r[0] for r in con.execute(
            "SELECT bvid FROM history WHERE mid=? AND view_at>=?", (mid, since))}
        groups = [r[0] for r in con.execute(
            "SELECT grp FROM members WHERE mid=?", (mid,))]
        uploads = []
        try:
            uploads = [v for v in b.user_videos(mid, ps=30) if v["pubdate"] >= since]
        except BiliError:
            pass
        seen = [v for v in uploads if v["bvid"] in watched]
        rate = (len(seen) / len(uploads)) if uploads else None
        out.append({
            "mid": mid, "uname": uname, "face": face,
            "watched": n, "seen": len(seen), "uploads": len(uploads), "rate": rate,
            "completion": round(comp or 0, 3), "days": n_days, "last": last,
            "groups": groups,
        })
        progress(phase="analyze", done=i, total=total)
        time.sleep(2.0)

    # 近期行为分：观看率为主，次数和完播做修正；样本小的降权
    def recent(x):
        r = x["rate"] if x["rate"] is not None else 0
        s = r * (0.5 + 0.5 * x["completion"]) * (1 + min(x["watched"], 10) / 20)
        if x["watched"] < 3 or x["uploads"] < 5:
            s *= 0.5
        return s

    # 长期信号：点赞/投币/收藏/稍后再看，归一化后和近期行为加权
    sig = signals.counts()
    for x in out:
        s = sig.get(x["mid"], {})
        x["signals"] = {k: s.get(k, 0) for k in ("like", "coin", "fav", "toview")}
        x["recent"] = round(recent(x), 3)

    def raw_signal(x):
        s = x["signals"]
        return s["like"] + s["coin"] * 1.5 + s["fav"] * 1.2 + s["toview"] * 0.8

    # 长期信号普遍稀疏（中位数 1 左右），用饱和函数，别让某一个 outlier 通吃
    def score(x):
        longterm = min(1.0, raw_signal(x) / 8.0)
        return 0.8 * x["recent"] + 0.2 * longterm

    for x in out:
        x["score"] = round(score(x), 3)
    out.sort(key=lambda x: x["score"], reverse=True)
    con.close()
    return out


def apply_group(mids, name="常看", batch=20):
    """把 mids 加进名为 name 的分组（没有就建）。只加不删，原有分组不动。"""
    b = Bili(config.COOKIE)
    if not b.csrf:
        raise BiliError("cookie 里没有 bili_jct，先补上才能写回")
    g = next((x for x in b.groups() if x["name"] == name), None)
    created = False
    if not g:
        d = b.create_tag(name)
        g = {"tagid": d.get("tagid"), "name": name}
        created = True
    mids = [int(m) for m in mids]
    # addUsers 是"把分组设成这一组"，不是叠加；所以先查现有分组，再补上目标组
    rel = {}
    for i in range(0, len(mids), 50):
        try:
            rel.update(b.relations(mids[i:i + 50]))
        except BiliError:
            pass
        time.sleep(1.0)

    buckets = collections.defaultdict(list)
    skipped = 0
    for mid in mids:
        tags = (rel.get(str(mid)) or {}).get("tag") or []
        if g["tagid"] in tags:
            skipped += 1
            continue
        want = tuple(sorted({int(t) for t in tags} | {g["tagid"]}))
        buckets[want].append(mid)

    moved = 0
    for want, group in buckets.items():
        for i in range(0, len(group), batch):
            chunk = group[i:i + batch]
            b.add_users(chunk, list(want))
            moved += len(chunk)
            time.sleep(1.5)
    return {"tagid": g["tagid"], "created": created, "moved": moved, "skipped": skipped}


def run(days=180, window_days=90, max_ups=60):
    if not LOCK.acquire(blocking=False):
        return False

    def job():
        try:
            status(running=True, phase="sync", done=0, total=0, error=None,
                   added=0, pages=0)
            added = sync_history(days)
            status(phase="analyze", added=added)
            items = build(window_days, max_ups)
            _write(OUT, {"ts": int(time.time()), "days": days,
                         "window_days": window_days, "items": items})
            status(running=False, phase="done", count=len(items))
        except Exception as e:
            status(running=False, phase="error", error=repr(e)[:200])
        finally:
            LOCK.release()

    threading.Thread(target=job, daemon=True).start()
    return True


def load():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ts": 0, "items": []}


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    print(sync_history(days))
    print(json.dumps(build(), ensure_ascii=False)[:500])
