#!/usr/bin/env python3
"""每晚一条龙：补历史 → 补长期信号 → 重算常看候选 → 快照进私有数据仓库。

建议 crontab:
40 3 * * * cd /home/ubuntu/bilibili-river && /usr/bin/python3 nightly.py >> nightly.log 2>&1
"""
import json
import os
import time

import candidate
import config
import personal
import signals
import snapshot


LOCK = os.path.join(config.DATA, "nightly.lock")


def main():
    t0 = time.time()
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    print(time.strftime("%F %T"), "开始", flush=True)

    n = candidate.sync_history(180)
    print(f"  历史新增 {n} 条 ({time.time()-t0:.0f}s)", flush=True)

    sig = signals.sync_all()
    print(f"  信号 {sig} ({time.time()-t0:.0f}s)", flush=True)

    per = personal.sync()
    print(f"  个人资料 {per} ({time.time()-t0:.0f}s)", flush=True)

    items = candidate.build()
    tmp = os.path.join(config.DATA, "candidates.json")
    with open(tmp + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"ts": int(time.time()), "window_days": 90, "items": items}, f, ensure_ascii=False)
    os.replace(tmp + ".tmp", tmp)
    print(f"  候选 {len(items)} 个 ({time.time()-t0:.0f}s)", flush=True)

    counts, result = snapshot.run()
    print(f"  快照 {result} 行数 {counts} ({time.time()-t0:.0f}s)", flush=True)
    try:
        os.remove(LOCK)
    except OSError:
        pass


if __name__ == "__main__":
    main()
