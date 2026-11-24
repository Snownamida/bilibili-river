#!/usr/bin/env python3
"""河道配置：改这里就够了。

河道 = 只推你选的一组 UP 的投稿。源用「关注分组」的名字，
每次抓取时解析成 tagid，所以在 B 站改分组不用改代码。
"""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
DB = os.path.join(DATA, "river.db")
RUN = os.path.join(DATA, "run.json")
COOKIE = os.environ.get("BILI_COOKIE", os.path.expanduser("~/.config/bili-river/cookie.header"))

# 只绑内网/tailnet；本机 IP 不放代码里，见文件末尾的 local_config
HOST = os.environ.get("BILI_HOST", "127.0.0.1")
PORT = int(os.environ.get("BILI_PORT", "8100"))

# 河道接哪些关注分组（名字，随便加/删）
DEFAULT_GRP = "常看"   # 页面开屏默认显示的分组（不在勾选里就退回"全部"）
GROUPS = []  # 空 = 等你在页面上勾；页面勾选结果存在 data/selection.json

# 每个 UP 每轮拉多少条；首次见到的 UP 多回填一些
PS = 20
BACKFILL_PS = 30
BACKFILL_DAYS = 30

BATCH = 40              # 每轮最多查多少个 UP（按"最久没查"轮转；0 = 不限）

GROUPS_FILE = os.path.join(DATA, "groups.json")     # B 站分组缓存（抓取时刷新）
SELECTION = os.path.join(DATA, "selection.json")    # 页面上勾选的分组
FETCH_INTERVAL = 7200   # 2 小时一轮
REQUEST_GAP = 5         # app 网关宽松，5 秒足够稳
PAUSE_EVERY = 10        # 每查 10 个 UP 多歇一会儿
PAUSE_SECONDS = 25
COOLDOWN = 1800         # 连续失败后退避多久

FIRST_UNREAD_DAYS = 14  # 第一次抓某个 UP 时，这么久以内的投稿算未看；更早的只当档案


def selected_groups():
    """页面上勾过的分组优先；没勾过就用 GROUPS 默认值。"""
    try:
        with open(SELECTION, encoding="utf-8") as f:
            names = json.load(f).get("groups")
        if isinstance(names, list):
            return [n for n in names if isinstance(n, str)]
    except Exception:
        pass
    return list(GROUPS)


# 本机私有覆盖（HOST/PORT 等），文件不进仓库
try:
    from local_config import *  # noqa: F401,F403
except ImportError:
    pass
