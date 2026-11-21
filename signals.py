#!/usr/bin/env python3
"""长期兴趣信号：点赞 / 投币 / 收藏 / 稍后再看 / 追番。

这些东西比观看历史耐久（历史只留最近约 1200 条），全部落 SQLite，
之后再快照进私有数据仓库，就不怕平台那边过期。
"""
import json
import os
import time

import config
import fetch
from bili import Bili, BiliError

STATUS = os.path.join(config.DATA, "signals_status.json")


def _status(**kw):
    try:
        cur = json.load(open(STATUS, encoding="utf-8"))
    except Exception:
        cur = {}
    cur.update(kw)
    cur["updated"] = int(time.time())
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False)
    os.replace(tmp, STATUS)


def _put(cur, kind, oid, bvid, mid, uname, title, ts, extra=None):
    if oid is None:
        return 0
    cur.execute(
        "INSERT OR IGNORE INTO signals(kind, oid, bvid, mid, uname, title, ts, extra)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (kind, str(oid), bvid, mid, uname, title, ts, json.dumps(extra, ensure_ascii=False) if extra else None))
    return cur.rowcount


def _call(fn, *a, **kw):
    """一次重试；412 等 20 秒。"""
    for attempt in range(2):
        try:
            return fn(*a, **kw)
        except BiliError as e:
            if attempt == 0 and e.code in (412, -352):
                time.sleep(20)
                continue
            raise
    raise BiliError("重试失败")


def sync_likes(b, con, uid, cap=30):
    cur = con.cursor()
    added = 0
    for pn in range(1, cap + 1):
        d = _call(b._get, "https://api.bilibili.com/x/space/like/video",
                  {"vmid": uid, "pn": pn, "ps": 20}, referer="https://space.bilibili.com/")
        rows = (d or {}).get("list") or []
        if not rows:
            break
        for it in rows:
            owner = it.get("owner") or {}
            added += _put(cur, "like", it.get("aid"), it.get("bvid"), owner.get("mid"),
                          owner.get("name"), it.get("title"), it.get("pubdate"))
        con.commit()
        _status(kind="like", page=pn, added=added)
        if len(rows) < 20:
            break
        time.sleep(1.5)
    return added


def sync_coins(b, con, uid, cap=30):
    cur = con.cursor()
    added = 0
    for pn in range(1, cap + 1):
        d = _call(b._get, "https://api.bilibili.com/x/space/coin/video",
                  {"vmid": uid, "pn": pn, "ps": 20}, referer="https://space.bilibili.com/")
        rows = d if isinstance(d, list) else (d or {}).get("list") or []
        if not rows:
            break
        for it in rows:
            owner = it.get("owner") or {}
            added += _put(cur, "coin", it.get("aid"), it.get("bvid"), owner.get("mid"),
                          owner.get("name"), it.get("title"), it.get("time") or it.get("pubdate"))
        con.commit()
        _status(kind="coin", page=pn, added=added)
        if len(rows) < 20:
            break
        time.sleep(1.5)
    return added


def sync_favs(b, con, uid, cap=60):
    cur = con.cursor()
    added = 0
    d = _call(b._get, "https://api.bilibili.com/x/v3/fav/folder/created/list-all",
              {"up_mid": uid}, referer="https://space.bilibili.com/")
    folders = (d or {}).get("list") or []
    for f in folders:
        mid = f.get("id")
        if not mid:
            continue
        for pn in range(1, cap + 1):
            try:
                r = _call(b._get, "https://api.bilibili.com/x/v3/fav/resource/list",
                          {"media_id": mid, "pn": pn, "ps": 20, "platform": "web"},
                          referer=f"https://space.bilibili.com/{uid}/favlist?fid={mid}")
            except BiliError:
                break
            medias = ((r or {}).get("medias") or [])
            if not medias:
                break
            for it in medias:
                up = it.get("upper") or {}
                added += _put(cur, "fav", it.get("id"), it.get("bvid"), up.get("mid"),
                              up.get("name"), it.get("title"), it.get("fav_time"))
            con.commit()
            _status(kind="fav", folder=f.get("title"), page=pn, added=added)
            if not (r or {}).get("has_more"):
                break
            time.sleep(1.5)
    return added


def sync_toview(b, con):
    cur = con.cursor()
    d = _call(b._get, "https://api.bilibili.com/x/v2/history/toview", None,
              referer="https://www.bilibili.com/")
    rows = (d or {}).get("list") or []
    added = 0
    for it in rows:
        owner = it.get("owner") or {}
        added += _put(cur, "toview", it.get("aid"), it.get("bvid"), owner.get("mid"),
                      owner.get("name"), it.get("title"), it.get("add_at"))
    con.commit()
    _status(kind="toview", added=added, total=len(rows))
    return added


def sync_bangumi(b, con, uid, cap=10):
    cur = con.cursor()
    added = 0
    for typ in (1, 2):          # 1=番剧 2=影视
        for pn in range(1, cap + 1):
            try:
                d = _call(b._get, "https://api.bilibili.com/x/space/bangumi/follow/list",
                          {"type": typ, "follow_status": 0, "pn": pn, "ps": 20, "vmid": uid},
                          referer=f"https://space.bilibili.com/{uid}/bangumi")
            except BiliError:
                break
            rows = (d or {}).get("list") or []
            if not rows:
                break
            for it in rows:
                added += _put(cur, "bangumi", it.get("season_id"), None, None,
                              None, it.get("title"), int(time.time()), {"media_id": it.get("media_id"), "type": typ})
            con.commit()
            if len(rows) < 20:
                break
            time.sleep(1.5)
    return added


def sync_all(cap_like=1, cap_coin=1, cap_fav=60):
    b = Bili(config.COOKIE)
    me = b.me()
    uid = me.get("mid")
    con = fetch.db()
    _status(running=True, uid=uid, error=None)
    out = {}
    try:
        out["like"] = sync_likes(b, con, uid, cap_like)
        out["coin"] = sync_coins(b, con, uid, cap_coin)
        out["fav"] = sync_favs(b, con, uid, cap_fav)
        out["toview"] = sync_toview(b, con)
        out["bangumi"] = sync_bangumi(b, con, uid)
        _status(running=False, done=True, added=out)
    except Exception as e:
        _status(running=False, error=repr(e)[:200])
        raise
    finally:
        con.close()
    return out


def counts():
    """每个 UP 的长期信号计数。"""
    con = fetch.db()
    try:
        rows = con.execute(
            "SELECT mid, kind, COUNT(*) FROM signals WHERE mid IS NOT NULL GROUP BY mid, kind").fetchall()
    finally:
        con.close()
    out = {}
    for mid, kind, n in rows:
        out.setdefault(mid, {})[kind] = n
    return out


if __name__ == "__main__":
    print(sync_all())
