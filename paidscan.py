#!/usr/bin/env python3
"""标记充电专属/付费视频：is_upower_exclusive 或 rights.pay。"""
import time
import fetch
from bili import Bili, BiliError


def scan(days=30, limit=500, gap=1.5):
    con = fetch.db(); cur = con.cursor()
    since = int(time.time()) - days * 86400
    rows = [r[0] for r in cur.execute(
        "SELECT bvid FROM videos WHERE paid_at IS NULL AND pubdate >= ? LIMIT ?", (since, limit))]
    b = Bili('/home/ubuntu/.config/bili-river/cookie.header')
    marked = 0
    for i, bvid in enumerate(rows, 1):
        try:
            d = b._get("https://api.bilibili.com/x/web-interface/view", {"bvid": bvid},
                       referer="https://www.bilibili.com/")
        except BiliError as e:
            if e.code in (412, -352):
                time.sleep(20)
                continue
            cur.execute("UPDATE videos SET paid_at=? WHERE bvid=?", (int(time.time()), bvid))
            con.commit()
            continue
        d = d or {}
        rights = d.get("rights") or {}
        paid = 1 if (d.get("is_upower_exclusive") or rights.get("pay") or rights.get("arc_pay")) else 0
        cur.execute("UPDATE videos SET paid=?, paid_at=? WHERE bvid=?",
                    (paid, int(time.time()), bvid))
        con.commit()
        marked += paid
        if i % 50 == 0:
            print(f"{i}/{len(rows)} 已标记 {marked}", flush=True)
        time.sleep(gap)
    con.close()
    return {"checked": len(rows), "paid": marked}


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    print(scan(days, limit))
