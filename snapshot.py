#!/usr/bin/env python3
"""把本地 SQLite 里的个人数据导出成 JSONL，提交到私有数据仓库。

只导数据，不导 cookie。
"""
import json
import os
import shutil
import sqlite3
import subprocess
import time

import config

DEST = os.environ.get("BILI_DATA_DIR", os.path.expanduser("~/bilibili-data"))
TABLES = ("history", "signals", "videos", "members", "fetch_state", "follow_state",
          "all_members", "profiles", "account", "followings", "followers", "blacks")


def _dump(con, table, path):
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for row in con.execute(f"SELECT * FROM {table}"):
            f.write(json.dumps(dict(zip(cols, row)), ensure_ascii=False) + "\n")
            n += 1
    return n


def export():
    os.makedirs(DEST, exist_ok=True)
    con = sqlite3.connect(config.DB)
    counts = {}
    try:
        for t in TABLES:
            counts[t] = _dump(con, t, os.path.join(DEST, t + ".jsonl"))
    finally:
        con.close()

    for name, path in (("selection.json", config.SELECTION),
                       ("groups.json", config.GROUPS_FILE),
                       ("candidates.json", os.path.join(config.DATA, "candidates.json"))):
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(DEST, name))

    with open(os.path.join(DEST, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"ts": int(time.time()), "date": time.strftime("%Y-%m-%d %H:%M"),
                   "rows": counts}, f, ensure_ascii=False, indent=2)
    return counts


def _git(*args):
    return subprocess.run(["git", "-C", DEST] + list(args), capture_output=True, text=True)


def push():
    _git("add", "-A")
    st = _git("status", "--porcelain")
    if not st.stdout.strip():
        return "没有变化"
    msg = "snapshot " + time.strftime("%Y-%m-%d %H:%M")
    _git("commit", "-m", msg)
    r = _git("push", "origin", "main")
    if r.returncode != 0:
        return "push 失败：" + (r.stderr or r.stdout)[-200:]
    return "pushed"


def run(commit=True):
    counts = export()
    result = push() if commit else "已导出"
    print(json.dumps({"counts": counts, "git": result}, ensure_ascii=False))
    return counts, result


if __name__ == "__main__":
    run()
