# bilibili-river（B 站河道）

只推你选的一组 UP 的投稿。不是"把关注流筛一遍"，而是**按 UP 逐个拉最新投稿**，
所以一个月才发一条的 UP 也不会被发得勤的挤掉。

页面只绑内网/tailnet：`http://<tailnet-ip>:8100`。本机 IP 放在 `local_config.py`，不进仓库。
代码：[Snownamida/bilibili-river](https://github.com/Snownamida/bilibili-river)（MIT）。

## 原理

1. 读你的关注分组 → 把勾选的分组成员拉下来
2. 按"最久没查"的顺序，每个 UP 拉一次最新投稿
3. `bvid` 去重入 SQLite
4. 页面按时间倒序展示，可切分组 / 时间窗 / 未看

因为请求是"按 UP"发起的，低频 UP 的投稿不会因为别人发得多而落在页缝里。

## 为什么不是油猴脚本 / RSSHub

- 油猴脚本是"先取一页再减"：一页就那么多条，发得勤的 UP 占满页面，低频 UP 就永远看不到。
- RSSHub 公共实例被 Cloudflare 挡，自建也要喂 cookie、同样吃风控。
- 这里的接口是安卓客户端网关 `app.bilibili.com/x/v2/space/archive/cursor`，比网页的
  `space/wbi/arc/search` 宽松得多（实测 2 秒一个、10/10 通过）。网页接口只作为兜底。

## 配置

`config.py` 是通用默认值；`local_config.py` 放本机私有值（已在 `.gitignore`，不会提交）：

```python
# local_config.py
HOST = "100.x.x.x"   # 只绑 tailnet / 内网
PORT = 8100
```

常用参数在 `config.py`：

```python
GROUPS = []                   # 空 = 等页面上勾选；勾选结果存 data/selection.json
BATCH = 40                    # 每轮最多查多少 UP，按"最久没查"轮转（0 = 不限）
PS = 20                       # 每个 UP 每轮拉多少
BACKFILL_PS = 30              # 第一次见到的 UP 回填多少
REQUEST_GAP = 5               # 每个 UP 之间歇多少秒
PAUSE_EVERY = 10              # 每查 10 个多重歇一次
PAUSE_SECONDS = 25
```

改完重启服务。

## cookie

`~/.config/bili-river/cookie.header`，权限 0600。内容是 DevTools 里 `Cookie:` 后面那串，
或者只放 `SESSDATA` 的值也行（脚本认）。

更新方法：

```bash
install -d -m700 ~/.config/bili-river && printf 'Cookie> ' && read -rs C && \
  printf '%s\n' "${C#Cookie: }" > ~/.config/bili-river/cookie.header && \
  chmod 600 ~/.config/bili-river/cookie.header && unset C && echo saved
```

SESSDATA 过期（页面顶部报"cookie 失效"）就重新写一次。

## 跑

```bash
./run.sh                 # 自检/拉起
tail -f river.log        # 日志
```

systemd（把 `User` 和路径改成你的）：

```bash
sudo cp bili-river.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bili-river
```

## 常看候选

页面 `/candidates`（河道页底部也有入口）。它不看内容分类，只看行为：

- **近期 80%**：最近 90 天这个 UP 发的视频，你点开了多大比例（观看率），再拿完播和观看次数修正
- **长期 20%**：点赞 / 投币 / 收藏 / 稍后再看（饱和到 8 分封顶，避免单个 outlier 通吃）

只统计你关注的人。

流程：同步观看历史（游标翻页，只留关注过的人）→ 对候选 UP 逐个拉最近投稿 → 算观看率 → 排序。
一次大约 5–8 分钟，结果存 `data/candidates.json`，勾选后可复制名单或导出 CSV。

### 写回 B 站

勾选候选 → 「加入组」→ 输入组名（默认「常看」）。没有这个组会先建，然后分批移人。

用的是 `x/relation/tags/addUsers`，**只加不删**：一个人可以同时待在这几个组里，
原来的分组不会被拆掉。需要 `bili_jct`（cookie 里已有）。

### 长期信号

| 类型 | 能拿多少 | 说明 |
|---|---|---|
| 收藏 | 全量（当前 565 条） | 默认收藏夹 + 自建收藏夹 |
| 稍后再看 | 全量（82） | 一次请求 |
| 追番/追剧 | 全量（155） | 两种类型 |
| 点赞 | **只有最近 20 条** | 公开接口忽略翻页参数 |
| 投币 | **只有最近 20 条** | 同上 |

点赞/投币拿不全，是 B 站接口的限制，不是脚本的问题。

## 手机 / 电脑

- **手机**：单列卡片。点卡片直接唤起 B 站 App（`bilibili://video/<aid>`）；两秒内没被 App 接管，
  会弹一个「用网页看」的兜底链接，不会把人卡死。
- **电脑**：内容居中、限宽 880px，封面放大；点击在新标签页打开网页版。

## 划掉不想看的

卡片右侧「✕」= 隐藏，所有视图里都不再出现（数据库 `hidden=1`），点完 6 秒内可撤销。
「已看」只是标记，仍能在「全部」里看到；两者不一样。

## 保留多久

不删。投稿全部留档（回填每个 UP 最近 30 条），「未看」的判定是：

- 比这个 UP 库里最新一条还新 → 新发的，未看
- 第一次抓这个 UP 时，只看最近 14 天的算未看，更早的只当档案
- 之后用观看历史把看过的标掉

所以发得不勤、跨度很大的 UP 也不会吃亏：隔半年发一条，进来就是“最新一条”，照样进未看。

## 数据仓库（私有）

长期数据每天快照到 **`github.com/Snownamida/bilibili-data`**（私有）：

```
history.jsonl    观看历史（平台只留最近约 1200 条，这里是长期存档）
signals.jsonl    点赞/投币/收藏/稍后再看/追番
videos.jsonl     河道抓到的投稿
members.jsonl    关注的 UP 与分组
selection.json / groups.json / candidates.json
meta.json        快照时间与行数
```

**不含 cookie**。整机迁移要用的 cookie 在下面「备份 / 迁移」的导出包里。

服务进程每天 03:20 后跑一次 `nightly.py`：补历史 → 补信号 → 重算候选 → 快照推送。
跑的时候会写 `data/nightly.lock`，河道抓取那一轮自动跳过，避免两个任务同时打接口。

## 恢复 / 迁移

数据都在私有仓库 `bilibili-data`（每晚快照），**cookie 不进去**——需要时从浏览器重新拿一次。

新机器：

```bash
git clone git@github.com:Snownamida/bilibili-river.git
git clone git@github.com:Snownamida/bilibili-data.git ~/bilibili-data
cd bilibili-river
python3 restore.py --force     # 把 JSONL 灌回 SQLite
# 写 cookie（SESSDATA + bili_jct）
./run.sh
```

## 数据

`data/river.db`：

- `videos`：bvid 主键，标题/封面/时长/发布时间/已读
- `members`：mid → 分组
- `fetch_state`：每个 UP 上次抓取时间，用来轮转

`data/run.json`：上一轮的结果（时间、新增、错误、退避到期）。

## 接口

| 路径 | 说明 |
|---|---|
| `GET /api/items?days=7&grp=all&unseen=0` | 河道条目 |
| `GET /api/status` | 上一轮抓取状态 |
| `GET /api/groups` | 分组缓存 + 当前勾选 |
| `POST /api/groups` | `{"groups": ["我追"]}` 保存勾选 |
| `GET /api/candidates` | 常看候选 + 进度 |
| `POST /api/candidates/run` | 重新同步历史并分析 |
| `POST /api/seen` | `{"bvid": "BV..."}` 或 `{"days": 7}` 全部已读 |
| `POST /api/refresh` | 立刻抓一轮 |
| `GET /feed.xml` | Atom，喂给 RSS 阅读器也行 |

## 风控

- 网页接口（成员列表、WBI 签名、兜底投稿列表）偶发 `HTTP 412`，有 120 秒重试和 30 分钟退避。
- app 网关宽松，但也不是无限：每轮上限 `BATCH` 就是给它的刹车。
- 退避状态写在 `run.json` 的 `cooldown_until`，重启不会立刻又去撞。

## 已知的坑

- SESSDATA 会过期，过期后页面红字提示。
- B 站换接口/签名规则就得改 `bili.py`，都在一个文件里。
- 页面没有登录鉴权，**只能绑 tailnet / 内网**，别开到公网。
