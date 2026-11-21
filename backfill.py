#!/usr/bin/env python3
"""补档案：把每个 UP 最近 30 条投稿重新拉一遍，老的一律记成已读。

只在第一次建库、或者误删过数据之后手动跑。
"""
import time

import config
import fetch
from bili import Bili, BiliError


def main():
    con = fetch.db()
    cur = con.cursor()
    b = Bili(config.COOKIE)
    mids = [r[0] for r in cur.execute("SELECT DISTINCT mid FROM members")]
    now = int(time.time())
    added = total = 0
    for i, mid in enumerate(mids, 1):
        row = cur.execute("SELECT COUNT(*), MAX(pubdate) FROM videos WHERE mid=?", (mid,)).fetchone()
        n, max_pub = row[0], row[1]
        try:
            vids = b.user_videos(mid, ps=config.BACKFILL_PS)
        except BiliError as e:
            print(i, mid, "失败", e, flush=True)
            time.sleep(3)
            continue
        for v in vids:
            if not v["bvid"]:
                continue
            if max_pub:
                seen = 0 if v["pubdate"] > max_pub else 1
            else:
                seen = 0 if v["pubdate"] >= now - config.FIRST_UNREAD_DAYS * 86400 else 1
            cur.execute(
                "INSERT OR IGNORE INTO videos"
                "(bvid, aid, mid, title, pubdate, cover, duration, tname, first_seen, seen)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (v["bvid"], v["aid"], mid, v["title"], v["pubdate"], v["cover"],
                 v["duration"], v["tname"], now, seen))
            added += cur.rowcount
            total += 1
        con.commit()
        if i % 10 == 0:
            print(f"{i}/{len(mids)} 新增 {added}", flush=True)
        time.sleep(2)

    cur.execute("UPDATE videos SET seen=1 WHERE bvid IN (SELECT bvid FROM history WHERE bvid IS NOT NULL)")
    con.commit()
    con.close()
    print(f"完成：处理 {total} 条，新增 {added}", flush=True)


if __name__ == "__main__":
    main()
