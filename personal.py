#!/usr/bin/env python3
"""个人资料：账号信息、关注列表（带关注时间/分组）、粉丝、黑名单。"""
import json
import time

import config
import fetch
from bili import Bili, BiliError


def _pages(b, url, params, key="list", cap=80):
    out = []
    for pn in range(1, cap + 1):
        p = dict(params); p["pn"] = pn
        for attempt in range(2):
            try:
                d = b._get(url, p, referer="https://space.bilibili.com/")
                break
            except BiliError as e:
                if attempt == 0 and e.code in (412, -352):
                    time.sleep(20); continue
                raise
        rows = (d or {}).get(key) or []
        out.extend(rows)
        if len(rows) < int(params.get("ps", 50)):
            break
        time.sleep(1.5)
    return out


def sync_msgfeed(b, con, cap=40):
    """回复我的 / @我的 / 收到的赞。游标翻页：id + <kind>_time。"""
    cur = con.cursor()
    total = 0
    for kind in ("reply", "at", "like"):
        params = {"ps": 20}
        for _ in range(cap):
            try:
                d = b._get(f"https://api.bilibili.com/x/msgfeed/{kind}", params,
                           referer="https://message.bilibili.com/")
            except BiliError:
                break
            items = (d or {}).get("items") or []
            if not items:
                break
            for it in items:
                user = it.get("user") or {}
                item = it.get("item") or {}
                cur.execute("INSERT OR REPLACE INTO msgfeed(kind,id,mid,uname,title,ts,raw)"
                            " VALUES(?,?,?,?,?,?,?)",
                            (kind, str(it.get("id")), user.get("mid"), user.get("nickname"),
                             item.get("title") or item.get("desc") or "",
                             it.get(f"{kind}_time") or it.get("time") or 0,
                             json.dumps(it, ensure_ascii=False)))
                total += 1
            con.commit()
            c = (d or {}).get("cursor") or {}
            if c.get("is_end") or not c.get("id"):
                break
            params = {"ps": 20, "id": c["id"], f"{kind}_time": c.get("time")}
            time.sleep(1.5)
    return total


def sync():
    b = Bili(config.COOKIE)
    me = b.me()
    uid = me.get("mid")
    con = fetch.db(); cur = con.cursor()
    now = int(time.time())

    cur.execute("INSERT OR REPLACE INTO account(k,v,fetched_at) VALUES('nav',?,?)",
                (json.dumps({k: me.get(k) for k in
                             ("mid", "uname", "face", "money", "vipStatus", "vipDueDate",
                              "level_info", "official", "moral", "scores")}, ensure_ascii=False), now))

    fol = _pages(b, "https://api.bilibili.com/x/relation/followings",
                 {"vmid": uid, "ps": 50, "order": "desc", "platform": "web"})
    for i, u in enumerate(fol, 1):
        cur.execute("INSERT OR REPLACE INTO followings(mid,uname,sign,face,mtime,attribute,special,tags)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (u.get("mid"), u.get("uname"), u.get("sign"), u.get("face"),
                     u.get("mtime"), u.get("attribute"), 1 if u.get("special") else 0,
                     json.dumps(u.get("tag") or [], ensure_ascii=False)))
        if i % 200 == 0:
            con.commit()
    con.commit()

    fans = _pages(b, "https://api.bilibili.com/x/relation/followers", {"vmid": uid, "ps": 50})
    for u in fans:
        cur.execute("INSERT OR REPLACE INTO followers(mid,uname,face,mtime) VALUES(?,?,?,?)",
                    (u.get("mid"), u.get("uname"), u.get("face"), u.get("mtime")))
    con.commit()

    msg = sync_msgfeed(b, con)

    blk = _pages(b, "https://api.bilibili.com/x/relation/blacks", {"ps": 50})
    for u in blk:
        cur.execute("INSERT OR REPLACE INTO blacks(mid,uname,mtime) VALUES(?,?,?)",
                    (u.get("mid"), u.get("uname"), u.get("mtime")))
    con.commit()
    con.close()
    return {"followings": len(fol), "followers": len(fans), "blacks": len(blk), "msgfeed": msg}


if __name__ == "__main__":
    print(sync())
