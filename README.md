# bilibili-river（B 站河道）

只推你选的一组 UP 的投稿。不是"把关注流筛一遍"，而是**按 UP 逐个拉最新投稿**，
所以一个月才发一条的 UP 也不会被发得勤的挤掉。

页面只绑 tailnet：**http://100.87.197.52:8100**
代码：`git@github.com:Snownamida/bilibili-river.git`（私有）

## 为什么不是油猴脚本 / RSSHub

- 油猴脚本是"先取一页再减"：一页就那么多条，发得勤的 UP 占满页面，低频 UP 落在页缝里就永远看不到。
- RSSHub 公共实例被 Cloudflare 挡，自建也要喂 cookie、同样吃风控。
- 这里就是一个轮询器：分组 → 成员 → 每个 UP 的投稿，`bvid` 去重，SQLite 落库。

## 怎么绕过风控

B 站对机房 IP 很凶，网页版 `space/wbi/arc/search` 实测一半请求回 `HTTP 412`。
现在走**安卓客户端网关** `app.bilibili.com/x/v2/space/archive/cursor`（公开 appkey 签名），
同一批 UP 实测 2 秒一个、10/10 全过。网页接口只作为兜底。

## 配置

`config.py`：

```python
GROUPS = ["老东西", "我追"]   # 接哪些关注分组（按名字，B 站改名后自动跟上）
BATCH = 40                    # 每轮最多查多少 UP，按"最久没查"轮转（0 = 不限）
PS = 20                       # 每个 UP 每轮拉多少
BACKFILL_PS = 30              # 第一次见到的 UP 回填多少
REQUEST_GAP = 5               # 每个 UP 之间歇多少秒
PAUSE_EVERY = 10              # 每查 10 个多重歇一次
PAUSE_SECONDS = 25
```

改完重启服务即可。

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

systemd（需要 sudo）：

```bash
sudo cp bili-river.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bili-river
```

## 数据

`data/river.db`：

- `videos`：bvid 主键，标题/封面/时长/发布时间/已读
- `members`：mid → 分组，每轮按 B 站的分组重建
- `fetch_state`：每个 UP 上次抓取时间，用来轮转和排错

`data/run.json`：上一轮的结果（时间、新增、错误、退避到期）。

## 接口

| 路径 | 说明 |
|---|---|
| `GET /api/items?days=7&grp=all&unseen=0` | 河道条目 |
| `GET /api/status` | 上一轮抓取状态 |
| `POST /api/seen` | `{"bvid": "BV..."}` 或 `{"days": 7}` 全部已读 |
| `POST /api/refresh` | 立刻抓一轮 |
| `GET /feed.xml` | Atom，喂给 RSS 阅读器也行 |

## 已知的坑

- 网页接口（成员列表、WBI 签名、兜底投稿列表）仍会偶发 412，所以脚本对它们有 120 秒重试和 30 分钟退避。
- cookie 里的 SESSDATA 会过期，过期后页面会红字提示。
- B 站换接口/签名规则就得改 `bili.py`，都在一个文件里。
