#!/usr/bin/env python3
"""先把分区明确的自动分掉；剩下知识类留给我逐个看。"""
import json
import sqlite3
from collections import Counter

GAME = {'单机游戏','网络游戏','手机游戏','游戏攻略','游戏解说','电子竞技','桌游棋牌','音游','沙盒游戏','GMV','游戏赛事'}
DIGI = {'数码','手机平板','电脑装机','硬件'}
MUSIC = {'音乐','音乐综合','音乐现场','VOCALOID·UTAU','MMD·3D','演奏','翻唱','舞蹈','音乐教学','原创音乐'}
ANIME = {'动画','番剧','完结动画','连载动画','MAD·AMV','动漫杂谈','同人·手书','官方延伸'}
LIFE = {'日常','生活','vlog','出行','美食','手工','动物','三农','运动','时尚','家居','旅游','美食侦探','美食测评','三农'}
FUN = {'搞笑','鬼畜调教'}
FILM = {'影视杂谈','影视剪辑','电影解说','纪录片','电视剧','影视整活'}
TOOL = {'计算机技术','软件应用'}
MIL = {'人文历史'}
KNOW = {'科学科普','科学·探索·自然','社科·法律·心理','财经商业','校园学习','职业职场','科工机械'}


def classify(top, sign):
    tags = [x.get('tag') for x in top if x.get('tag')]
    if not tags:
        return None, 0.0
    c = Counter(tags)
    top_tag, n = c.most_common(1)[0]
    share = n / len(tags)
    text = ' '.join((x.get('t') or '') for x in top) + ' ' + (sign or '')
    if top_tag in GAME: return '游戏', share
    if top_tag in MUSIC: return '音乐插图MMD舞蹈', share
    if top_tag in ANIME: return '动漫番剧', share
    if top_tag in DIGI: return '数码', share
    if top_tag in TOOL: return '工具', share
    if top_tag in FUN: return '搞笑', share
    if top_tag in FILM: return '解读', share
    if top_tag in LIFE: return 'vlog', share
    if top_tag in MIL and any(k in text for k in ('军事','战争','战役','二战','冷战','武器','军')):
        return '军事历史', share
    return None, share   # 知识类，留人工


if __name__ == "__main__":
    c = sqlite3.connect('data/river.db')
    rows = c.execute("select mid, uname, sign, top_json from profiles").fetchall()
    out, rest = [], []
    for mid, name, sign, top in rows:
        items = json.loads(top or '[]')
        grp, share = classify(items, sign or '')
        (out if grp else rest).append((mid, name, grp, round(share, 2)))
    print('自动分掉', len(out), Counter(x[2] for x in out).most_common())
    print('待人工', len(rest))
    json.dump([{'mid': m, 'uname': n, 'group': g} for m, n, g, s in out],
              open('data/auto_groups.json', 'w'), ensure_ascii=False)
    json.dump([{'mid': m, 'uname': n} for m, n, g, s in rest],
              open('data/manual_todo.json', 'w'), ensure_ascii=False)
