#!/usr/bin/env python3
"""从私有数据仓库的 JSONL 快照恢复本地数据库。

用法: python3 restore.py [--force]
默认只补空表；--force 会清空同名表再导入。
"""
import json
import os
import shutil
import sys

import config
import fetch

SRC = os.environ.get("BILI_DATA_DIR", os.path.expanduser("~/bilibili-data"))


def restore(force=False):
    con = fetch.db()
    cur = con.cursor()
    counts = {}
    for name in os.listdir(SRC):
        if not name.endswith(".jsonl"):
            continue
        table = name[:-6]
        path = os.path.join(SRC, name)
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
        if not cols:
            continue
        if force:
            cur.execute(f"DELETE FROM {table}")
        n = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                keys = [k for k in row if k in cols]
                if not keys:
                    continue
                sql = (f"INSERT OR REPLACE INTO {table}({','.join(keys)}) "
                       f"VALUES({','.join('?' * len(keys))})")
                cur.execute(sql, [row[k] for k in keys])
                n += 1
        con.commit()
        counts[table] = n
    con.close()

    for name, dest in (("selection.json", config.SELECTION),
                       ("groups.json", config.GROUPS_FILE),
                       ("candidates.json", os.path.join(config.DATA, "candidates.json")),
                       ("tidy_proposals.json", os.path.join(config.DATA, "tidy_proposals.json"))):
        src = os.path.join(SRC, name)
        if os.path.exists(src):
            shutil.copy2(src, dest)
    return counts


if __name__ == "__main__":
    force = "--force" in sys.argv
    print(restore(force))
