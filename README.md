# LiveSense · 抖音直播监听守卫

输入抖音直播间 ID 或链接，在本机网页开启守卫。未开播时持续等待，断线自动重连，下播后等待下一场。音频完整保存在本机，可按场次回听、下载。

目前实现第一阶段：监听和音频保存。发言人编号、普通话转写和动态追加文档尚未实现。

## 启动

需要 macOS、Python 3.11、[uv](https://docs.astral.sh/uv/) 和 FFmpeg。`uv` 会根据 `.python-version` 选择 Python 3.11；当前开发环境已经安装 FFmpeg。

在项目目录执行：

```sh
uv sync --locked
uv run --locked douyin-live-sense
```

打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)。服务仅监听本机地址，使用单进程；同一数据目录只允许一个服务实例运行。

支持以下三种输入：

```text
123456789
https://live.douyin.com/123456789
https://live.douyin.com/?live_web_rid=123456789
```

点击“开启守卫”后，即使关闭网页也会继续采集。点击“停止”结束守卫并保存不足 30 秒的尾段。终端按 `Ctrl+C` 关闭服务也会保存尾段，但保留守卫启用状态，下一次启动服务时自动恢复。电脑休眠或服务未运行期间无法采集，恢复后会记录缺口。

如果未安装 FFmpeg，可通过 Homebrew 安装：

```sh
brew install ffmpeg
```

## 可选 Cookie 与存储目录

默认不要求账号登录。房间是否允许匿名获取由抖音决定；只有明确的登录或风控错误才进入“需要处理”，普通网络故障自动重试。

需要 Cookie 时，将 `.env.example` 复制为 `.env`，填写自己登录抖音后的 Cookie，再使用以下命令启动：

```sh
uv run --locked --env-file .env douyin-live-sense
```

`.env` 不会由程序自动读取；也可以直接通过环境变量提供 `DOUYIN_COOKIE`。更新 Cookie 后需要重启服务，随后在网页重新开启守卫。应用不会把 Cookie 写入数据库，也不会在 API 中返回它；应用日志会隐藏 Cookie 与带签名的 URL。

默认目录为启动位置下的 `data/`，可以通过 `DLS_DATA_DIR` 设置绝对路径。所有音频保留，不自动清理；剩余空间不足 1 GB 时，保存当前尾段并停止采集，需要释放空间后手动重新开启。

```text
data/
  sense.sqlite3                # 守卫设置、场次、分段、断线事件
  audio/<room>/<session>/
    000001_<segment-id>.wav    # 已保存完成的音频
    000002_<segment-id>.wav.part # 正在写入的音频
```

每段最长 30 秒，16 kHz、单声道、16 位 PCM WAV，约 115 MB/小时。服务启动时检查尚未登记完成的分段，用实际落盘采样修复 WAV 文件头；无法恢复的文件保留，并记录失败事件。

时间基于首次收到解码音频的本机时间，段内按采样数计算；重连建立新的时间基准，不拼平缺口。数据库保存 UTC 时间，界面显示北京时间。它是音频接收时间，存在直播传输与缓冲延迟，不能当作主播端精确发言时刻。

## 行为与架构

- `DouyinSource` 封装固定版本 `streamget==4.0.10` 的公开接口，复用其抖音状态、签名和播放地址解析。优先 FLV，未提供时使用 HLS，按标清获取以减少带宽。
- `Monitor` 管理单房间状态、30 秒开播检查、2/5/10/30 秒重试和跨场次守卫。只有明确下播或源场次 ID 改变才划分新场次。
- `FFmpegCapture` 持续解码，20 秒无音频时重连，并在取消或退出时回收进程。CDN 返回 403 时先重新获取地址，避免将地址过期误判为必须登录。
- `SegmentWriter` 按采样切段，用临时文件、原子重命名和分段索引保证已完成音频可读取。
- FastAPI 提供本机 API 与静态网页。界面每 2 秒更新，场次列表每 10 秒更新；音频回听使用已完成的本地文件。

后续分析只需要消费已完成分段，通过场次 ID、分段 ID、采样数和时间信息对齐，不需要改变抖音获取层。

## HTTP API

交互文档：[http://127.0.0.1:8787/docs](http://127.0.0.1:8787/docs)。

| 接口 | 用途 |
| --- | --- |
| `GET /api/monitor` | 状态、当前房间、时长、最近错误、保存目录 |
| `POST /api/monitor/start` | 开始守卫，JSON 为 `{"room":"123456789"}` |
| `POST /api/monitor/stop` | 手动停止并保存尾段 |
| `GET /api/sessions?limit=50&offset=0` | 场次列表，按开始时间倒序 |
| `GET /api/sessions/{id}/segments?limit=100&offset=0` | 已完成分段，按顺序编号排序 |
| `GET /api/sessions/{id}/events` | 断线缺口、恢复及错误事件 |
| `GET /api/segments/{id}/audio` | 回听，支持 HTTP Range |
| `GET /api/segments/{id}/audio?download=true` | 下载 WAV |

状态值：`stopped`、`checking`、`waiting`、`recording`、`reconnecting`、`needs_attention`。重复启动同一房间返回当前任务，切换到其他房间返回 HTTP 409；非法房间返回 HTTP 400。分页 `limit` 最大 100。

分段字段包括 `id`、`session_id`、`sequence`、`epoch_id`、`start_at`、`end_at`、`sample_rate`、`sample_count`、`relative_path`、`state`。`epoch_id` 区分连续采集连接；只有 `ready` 分段可通过音频 API 读取。

## 验证

```sh
uv run --locked pytest -q
uv run --locked ruff check src tests tools
uv run --locked ruff format --check src tests tools
uv run --locked python -m compileall -q src tests tools
node --check src/douyin_live_sense/static/app.js
```

自动化测试使用模拟直播源及临时目录，覆盖守卫状态、重试、样本切段、临时文件恢复、API 与 FFmpeg 进程清理。它们不证明抖音当前线上接口可用。

真实直播验收单独运行，需要联网，且应先关闭其他使用此目录的服务：

```sh
uv run --locked python tools/live_acceptance.py --room 123456789 --minutes 30
```

验收调用真实 StreamGet SDK 和 FFmpeg，采集至少 30 分钟音频，在第 3 分钟通过本机专用转发连接制造 12 秒中断并检查重连，不修改系统网络。音频保存到 `data/`，结果保存到 `data/live-validation.json`。验收期间可以通过网页观察状态；结束后关闭测试服务。直播房间若下播或风控，验收会等待或失败，结果如实记录。

测试用转发器不会用于正常运行。音频与验证结果均加入忽略规则。
