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

## 备份 / 迁移

页面最底下有「导出 / 导出（含 cookie）/ 导入」。导出的 `.tar.gz` 里是：

```
manifest.json          版本、导出时间、勾选的分组
data/river.db          全部投稿 + 已读状态 + 抓取进度
data/selection.json    页面上勾选的分组
data/groups.json       分组缓存（人数）
cookie.header          只有勾"含 cookie"才有，权限 0600
```

服务器炸了重建：

1. 老机器页面底部点「导出（含 cookie）」，把文件存到手机/网盘
2. 新机器 `git clone`，装个 python3，跑 `./run.sh`
3. 打开页面 → 底部「导入」→ 选备份，自动恢复数据库、勾选、cookie
4. 新机器的 IP 写进 `local_config.py`，重启

命令行等价：

```bash
curl -o backup.tar.gz 'http://<host>:8100/api/export?cookie=1'
curl -X POST --data-binary @backup.tar.gz 'http://<host>:8100/api/import'
```

导入前会把现有库另存为 `data/river.db.before-import-<时间戳>`（保留最近 3 份）。

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
| `GET /api/export[?cookie=1]` | 下载备份 |
| `POST /api/import` | 上传备份恢复 |
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
