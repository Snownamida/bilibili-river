#!/usr/bin/env python3
"""分组整理：先把所有分组和未分组的人拉全，再逐个补资料（签名/粉丝/最火视频）。"""
import json
import time

import config
import fetch
from bili import Bili, BiliError

def sync_groups():
    b = Bili(config.COOKIE)
    con = fetch.db(); cur = con.cursor()
    total = 0
    for g in b.groups():
        if g["tagid"] == -10:      # 特别关注是系统组，跳过
            continue
        try:
            rows = b.group_members(g["tagid"], max_pages=60)
        except BiliError as e:
            print("跳过", g["name"], e, flush=True)
            continue
        cur.execute("DELETE FROM all_members WHERE grp=?", (g["name"],))
        for u in rows:
            cur.execute("INSERT OR REPLACE INTO all_members(mid,grp,uname) VALUES(?,?,?)",
                        (u["mid"], g["name"], u["uname"]))
        con.commit()
        total += len(rows)
        print(f'{g["name"]}: {len(rows)}', flush=True)
        time.sleep(1.5)
    con.close()
    return total

def fetch_profiles(limit=50):
    b = Bili(config.COOKIE)
    con = fetch.db(); cur = con.cursor()
    mids = [r[0] for r in cur.execute(
        "SELECT DISTINCT a.mid FROM all_members a LEFT JOIN profiles p ON p.mid=a.mid "
        "WHERE a.grp='默认分组' AND p.mid IS NULL ORDER BY a.mid LIMIT ?", (limit,))]
    print("待补", len(mids), flush=True)
    done = 0
    for mid in mids:
        uname = (cur.execute("SELECT uname FROM all_members WHERE mid=? LIMIT 1", (mid,)).fetchone() or [""])[0]
        c = {}
        try:
            d = b._get("https://api.bilibili.com/x/web-interface/card", {"mid": mid},
                       referer="https://space.bilibili.com/")
            c = (d or {}).get("card") or {}
            uname = c.get("name") or uname
        except Exception:
            pass
        def grab(order, ps):
            for attempt in range(2):
                try:
                    return b._app_get("/x/v2/space/archive/cursor", {"vmid": mid, "ps": ps, "order": order})
                except BiliError as e:
                    if attempt == 0 and e.code in (-352, 412):
                        time.sleep(20)
                        continue
                    raise
        try:
            top = grab("click", 8)
            rec = grab("pubdate", 5)
            def brief(d):
                return [{"t": v.get("title"), "p": v.get("play"), "d": v.get("ctime"), "tag": v.get("tname")}
                        for v in (d.get("item") or [])]
            cur.execute("INSERT OR REPLACE INTO profiles(mid,uname,sign,follower,official,top_json,recent_json,fetched_at)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (mid, uname, c.get("sign") or "",
                         (d or {}).get("follower") or 0,
                         ((c.get("Official") or {}).get("title") or ""),
                         json.dumps(brief(top), ensure_ascii=False),
                         json.dumps(brief(rec), ensure_ascii=False), int(time.time())))
            con.commit(); done += 1
            if done % 10 == 0:
                print("done", done, flush=True)
        except BiliError as e:
            print("fail", mid, e, flush=True)
            try: con.rollback()
            except Exception: pass
        except Exception as e:
            print("err", mid, repr(e)[:80], flush=True)
            try: con.rollback()
            except Exception: pass
        time.sleep(2)
    con.close()
    return done

def refresh_top(limit=0, gap=2.0):
    """重抓每个人的最火视频，补上封面（旧的 top_json 没存 cover）。"""
    b = Bili(config.COOKIE)
    con = fetch.db(); cur = con.cursor()
    q = "SELECT mid FROM profiles ORDER BY fetched_at"
    if limit:
        q += f" LIMIT {int(limit)}"
    mids = [r[0] for r in cur.execute(q)]
    done = 0
    for mid in mids:
        try:
            d = b._app_get("/x/v2/space/archive/cursor", {"vmid": mid, "ps": 8, "order": "click"})
        except BiliError as e:
            if e.code in (412, -352):
                time.sleep(20)
            continue
        items = d.get("item") or []
        top = [{"t": v.get("title"), "p": v.get("play"), "d": v.get("ctime"),
                "tag": v.get("tname"), "c": (v.get("cover") or "").replace("http://", "https://")}
               for v in items]
        cur.execute("UPDATE profiles SET top_json=?, fetched_at=? WHERE mid=?",
                    (json.dumps(top, ensure_ascii=False), int(time.time()), mid))
        con.commit()
        done += 1
        if done % 50 == 0:
            print("done", done, flush=True)
        time.sleep(gap)
    con.close()
    return done


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "profiles":
        print(fetch_profiles(int(sys.argv[2]) if len(sys.argv) > 2 else 50))
    else:
        print("groups", sync_groups())
