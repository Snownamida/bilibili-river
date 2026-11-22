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
    for u in fol:
        cur.execute("INSERT OR REPLACE INTO followings(mid,uname,sign,face,mtime,attribute,special,tags)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (u.get("mid"), u.get("uname"), u.get("sign"), u.get("face"),
                     u.get("mtime"), u.get("attribute"), 1 if u.get("special") else 0,
                     json.dumps(u.get("tag") or [], ensure_ascii=False)))
    con.commit()

    fans = _pages(b, "https://api.bilibili.com/x/relation/followers", {"vmid": uid, "ps": 50})
    for u in fans:
        cur.execute("INSERT OR REPLACE INTO followers(mid,uname,face,mtime) VALUES(?,?,?,?)",
                    (u.get("mid"), u.get("uname"), u.get("face"), u.get("mtime")))
    con.commit()

    blk = _pages(b, "https://api.bilibili.com/x/relation/blacks", {"ps": 50})
    for u in blk:
        cur.execute("INSERT OR REPLACE INTO blacks(mid,uname,mtime) VALUES(?,?,?)",
                    (u.get("mid"), u.get("uname"), u.get("mtime")))
    con.commit()
    con.close()
    return {"followings": len(fol), "followers": len(fans), "blacks": len(blk)}


if __name__ == "__main__":
    print(sync())
