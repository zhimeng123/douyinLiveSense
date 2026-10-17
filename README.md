# ValuSense · 抖音直播监听与普通话记录

在本机监听一个抖音直播间，完整保存分段音频，并生成带**音频接收时间、人物音色编号和讲话内容**的中文普通话记录。支持手动分析历史录音，也支持监听时持续分析；中途开启实时分析，会先补齐本场已保存但尚未分析的音频。

网页、数据库、录音和分析模型均在本机运行。项目的 Python 包名和启动命令为 `douyin-live-sense`。

## 功能概览

| 功能 | 当前行为 |
| --- | --- |
| 直播守卫 | 未开播时等待，下播后等待下一场；断线自动重新获取播放地址并重连 |
| 本机录音 | 采集本机系统声音或麦克风，复用同一分析管道，实时展示并导出文档 |
| 音频档案 | 按场次保存 WAV，支持场次与任意切片独立展开、回听和下载 |
| 整场分析 | 手动请求本场全部已保存切片，或开启实时分析持续追加 |
| 单片复核 | 单独分析或重新分析一个切片，保存独立版本 |
| 分析队列 | 每个整场分析或单片复核请求有一条进度记录，可展开查看切片状态 |
| 人物编号 | 场次内按音色聚类，跨切片、断线重连与补分析保持一致；不同场次各自独立 |
| 文本记录 | 按时间排序，支持逐句回听及 TXT、Markdown、JSONL 下载 |
| 重启恢复 | 服务重启后恢复守卫、实时开关与未完成任务，并修复未完成音频 |
| 一键准备与启动 | `scripts/setup.sh` 下载模型做准备工作，`scripts/run.sh` 启动服务 |

## 快速开始

当前开发与真实模型验证环境为 macOS。需要 **Python 3.11、[uv](https://docs.astral.sh/uv/) 和 FFmpeg**。项目用 `.python-version` 选择 Python 3.11，用 `uv.lock` 固定依赖。

### 1. 一键准备

克隆项目后，在项目根目录执行：

```sh
./scripts/setup.sh
```

脚本按顺序完成准备工作：检查 `uv` 与 FFmpeg → `uv sync --locked --extra analysis` 安装依赖 → 缺少 `.env` 时从 [.env.example](.env.example) 生成 → 运行 `prepare-models` 下载并校验本地模型。首次需要联网，并为依赖与模型预留数 GB 空间。

模型分两类：

- **普通话转写与标点**（`funasr/paraformer-zh`、`ct-punc`）：无需授权，直接下载。
- **人物判断**（`pyannote/speaker-diarization-community-1`）：受限模型，需要先在 [Community-1 模型页面](https://huggingface.co/pyannote/speaker-diarization-community-1) 登录并接受使用条款，再创建 Token 填入 `.env`。选择 **Read / readonly** 权限即可；细粒度 Token 需允许读取该受限模型，下载不需要 Write 或 Inference 权限。检测不到 Token 时脚本会打印上述步骤并退出，不会静默跳过，以免误以为模型已全部就绪。

```sh
# 暂时只用普通话转写与标点模型
./scripts/setup.sh --asr-only
```

模型准备固定完整 revision 并保存文件 SHA256；再次执行会验证并复用已有文件，修复缺失或损坏的文件。全部模型准备好后，运行分析使用本地文件，不再需要 Token。`--asr-only` 跳过人物判断模型，完整人物标记仍需全部模型就绪。

### 2. 启动服务

```sh
./scripts/run.sh
```

打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)。服务仅监听本机地址，同一数据目录只允许一个服务实例运行。

输入房间 ID 或链接，点击“开启守卫”。以下输入会识别为同一房间：

```text
123456789
https://live.douyin.com/123456789
https://live.douyin.com/?live_web_rid=123456789
```

关闭网页后后台仍会采集，但终端中的服务需要保持运行。点击网页“停止”会保存尾段并关闭守卫；终端按 `Ctrl+C` 会保存尾段并退出服务，同时保留守卫设置，下次启动自动恢复。

### 手动步骤（等价于脚本）

不使用脚本时，上面的准备与启动对应以下命令：

```sh
# macOS：如果尚未安装 FFmpeg
brew install ffmpeg

uv sync --locked --extra analysis
test -f .env || cp .env.example .env
uv run --locked --extra analysis --env-file .env douyin-live-sense prepare-models
uv run --locked --extra analysis --env-file .env douyin-live-sense
```

配置文件位于**项目根目录的 `.env`**，模板为 [.env.example](.env.example)。程序通过环境变量读取配置；启动命令使用 `--env-file .env` 加载它。真实 Token 和 Cookie 应只保存在本地配置中。

### 仅采集音频

采集不依赖分析模型，可跳过模型准备，用下面的命令安装和运行：

```sh
uv sync --locked
test -f .env || cp .env.example .env
uv run --locked --env-file .env douyin-live-sense
```

之后使用分析功能时，重新执行 `uv sync --locked --extra analysis`，并在运行命令中保留 `--extra analysis`，避免 uv 同步时卸载可选分析依赖。

### 本机录音

不监听抖音，直接采集本机音频时，打开网页在“本机录音”卡片选择来源：

- **系统声音**：浏览器弹窗中选择标签页或屏幕，并勾选“分享音频”。macOS 上通常只能分享浏览器提供的标签页/应用音频；采集整机系统声音可能需要在系统中安装回环设备。
- **麦克风**：选择麦克风输入。

点击“开启录音”开始，页面实时显示发言并生成 TXT、Markdown、JSONL 文档；再次点击“停止录音”结束本次采集。本机录音与抖音守卫互斥，一次只能运行一个；录音期间需保持本标签页打开（后台标签页仍会采集音频，关闭标签页即停止）。本机录音不随服务重启自动恢复。

## 配置与模型

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DOUYIN_COOKIE` | 空 | 抖音提示登录或风控时提供自己的登录 Cookie |
| `HF_TOKEN` | 空 | 首次下载受限的人物判断模型 |
| `DLS_DATA_DIR` | 启动目录下的 `data/` | 数据、录音、模型及导出目录；建议使用绝对路径 |
| `DLS_ANALYSIS_THREADS` | `4` | 分析进程 CPU 线程数 |
| `DLS_DIARIZATION_DEVICE` | `auto` | 人物判断设备，可选 `auto`、`cpu`、`mps`、`cuda` |

默认尝试匿名获取直播。能否匿名访问取决于抖音的当前响应；明确的登录或风控错误会显示“需要处理”，普通网络故障继续重试。修改 Cookie 等运行配置后需要重启服务；若守卫因错误停止，再从网页开启。Cookie 不写入数据库或 API，应用日志隐藏 Cookie 和带签名的播放地址。

| 分析用途 | 模型 | SDK |
| --- | --- | --- |
| 讲话区间、窗口内人物判断、声纹向量 | `pyannote/speaker-diarization-community-1` | pyannote.audio 4.0.7 |
| 中文普通话转写 | `funasr/paraformer-zh` | FunASR 1.4.16 |
| 中文标点 | `funasr/ct-punc` | FunASR 1.4.16 |

实际模型 revision 和文件校验信息保存在 `data/models/manifest.json`。分析记录保存所用模型版本及判断参数。

人物判断自动选择可用的 CUDA、Apple MPS 或 CPU；指定不可用设备会显示错误。普通话和标点模型使用 CPU。模型在独立子进程中加载并复用，分析任务串行执行；首次加载和补分析积压会增加等待时间。

## 分析使用方式

### 手动分析整场

默认只采集音频。展开“音频档案”中的场次，点击“分析本场全部切片”。守卫区域的“分析当前场次全部切片”对应正在监听的场次。

- 已结束场次：请求全部已保存切片，包括不足 30 秒的尾段。
- 正在采集的场次：请求点击时已保存完成的 WAV；正在写入的切片等后续请求。
- 再次点击：同一分析配置下复用已有请求，补分析新增切片，已成功内容不重复生成。

### 实时分析及补分析

采集中开启“实时生成普通话记录”，会先处理本场此前未分析的已保存音频，再持续处理新切片。手动整场分析与实时分析共用本场的分析记录和人物编号。

关闭实时开关后，已经请求的内容继续处理，后续切片等待手动请求。实时开启时点击“停止”，会将最终尾段纳入分析；守卫继续等待下一场时，新场次也会自动分析。服务重启恢复守卫、实时开关与未完成任务；下一次手动开启守卫默认关闭实时分析。

这是按音频切片和相邻上下文处理的实时记录，通常在切片保存后才生成文字，不保证逐字即时字幕。模型尚未准备时保留分析请求并提示原因，录音采集继续。

### 单片分析与重新分析

点击“第 N 段”可独立展开，查看涉及该切片的发言。切片内的“分析”创建单片复核，“重新分析”创建新的复核版本；可以切换查看“整场记录”和“单片复核”。

单片复核使用同一连接的相邻音频作为上下文，只保存中心切片的结果。它独立于整场分析，不覆盖整场文字和人物编号。连续点击未完成的请求会复用该请求；新版本等待或失败时，仍可查看上一次成功结果。分析失败保留错误，支持重试未完成内容。

## 人物编号的范围

| 场景 | 是否复用人物记忆 |
| --- | --- |
| 同一整场分析中的不同切片 | 是，持续匹配已保存的声纹向量 |
| 同一分析中断线重连、手动补分析或服务重启 | 是，人物档案保存在 SQLite 中 |
| 不同直播场次 | 否，各场次独立 |
| 单片复核及其不同版本 | 否，每次复核版本独立 |
| 更换模型配置创建的新分析版本 | 否，使用对应版本的人物档案 |

当前没有跨场次的全局人物声纹库。人物编号代表音色聚类，不等于姓名或抖音账号；不同场次中的“人物 1”不能直接认作同一个人。样本过短或匹配不明确时显示“人物待确认”。

同时讲话会标记“重叠讲话”，文字来自混合音频。当前没有逐人声源分离，也没有通过抖音连麦事件确认身份。可以点击“回听发言”复核原音，包括跨 WAV 的同一次发言。

## 综合文本与下载范围

“整场综合文本记录”汇总本场当前整场分析的发言，按音频接收时间排列；跨切片的同一次发言只记一次。网页分批读取记录，初次最多显示 100 条，后续可“加载更多发言”。场次、综合记录和任意切片均可单独展开或折叠，轮询保留展开状态；刷新网页后档案默认折叠。

**整场下载包含该整场分析已生成的全部发言，范围不受网页已加载条数限制，也不限于第一条切片。** TXT、Markdown、JSONL 均从数据库完整读取并按时间排序。

仍未分析、失败或尚待收尾的内容不会出现在下载中。要获得完整场次记录，应停止采集、请求全部已保存切片并等待分析完成。单片复核结果单独下载，不自动并入整场综合记录。

SQLite 是记录的来源；文档随分析更新，每次从数据库原子重建。异常退出后可恢复待更新文档，导出失败不丢失数据库中的分析结果。TXT 与 Markdown 包含采集缺口；JSONL 每行保存一条发言、源切片采样范围和模型配置，缺口由场次事件接口读取。

## 分析队列与进度

左上角菜单图标展开悬浮菜单，包含 **ValuSense** 主页和**分析队列**。再次点击图标、点击遮罩或按 `Esc` 可收起。页面使用 `#/home`、`#/queue`，支持浏览器前进后退。

队列按分析请求展示记录：一个整场请求对应一条记录，单片复核的各版本各有记录。每条记录显示进度条、已完成/总切片数、音频时长和正在处理的切片；完成记录保留。展开后可查看切片状态、编号和 ID，点击切片跳回对应档案。请求及切片列表每页 50 条。

进度按**已成功提交结果的切片数**计算，正在处理的切片不会提前计入完成。末尾发言尚待收尾时最高显示 99%；完成或实时分析已追上时显示 100%。实时采集产生新切片后，总数会增加，进度可能再次下降。进度条不估算单个模型调用的内部进度。

模型缺失、等待上下文和失败会显示具体原因。菜单等待数量按尚未处理的已请求切片统计。调度在当前窗口完成后优先处理手动单片复核，同类型按请求先后执行，整场内部按切片顺序处理。

## 音频、时间与恢复

- 音频为 **16 kHz、单声道、16 位 PCM WAV**，约 115 MB/小时，历史音频完整保留，不自动删除。
- 切段按收到的 **480,000 个采样，即 30 秒音频**计算，不对齐时钟的整分钟。停止、断线或退出时保存不足 30 秒的尾段。
- 时间以每次连接首次收到 PCM 的本机时间为基准，段内按采样数计算；重连建立新基准，保留真实缺口。数据库存 UTC，界面和文本显示北京时间。这是音频接收时间，包含直播传输与缓冲延迟。
- 未开播时每 30 秒检查；网络错误按 2、5、10、30 秒退避重试；20 秒没有收到音频会重连。只有明确下播或源场次 ID 改变才划分新场次，普通网络异常保留当前场次。
- 每次开启分段前检查磁盘。剩余不足 1 GB 时保存尾段并停止采集，释放空间后手动重新开启。
- 分段关闭完成才登记为可用；服务启动时检查未完成文件，按实际落盘采样修复可读 WAV，无法恢复的文件保留并记录错误。

默认存储结构：

```text
data/
  sense.sqlite3                     # 守卫、场次、分段、分析请求、人物和发言
  audio/<room>/<session>/
    000001_<segment-id>.wav          # 已保存完成的音频
    000002_<segment-id>.wav.part     # 正在写入的音频
  models/
    manifest.json                   # 模型 revision 与 SHA256
    <name>/<revision>/               # 模型文件
  analysis/<session>/<run>/
    transcript.txt
    transcript.md
    transcript.jsonl
  backups/                          # 数据库升级前的一致性备份
```

`data/`、`.env`、数据库和临时音频已加入 Git 忽略规则。修改数据目录后使用的是另一套数据库与模型；迁移数据时应先停止服务。

## 架构与模型替换

后端使用 Python 3.11、FastAPI 和 SQLite；网页使用原生 HTML、CSS、JavaScript。直播解析依赖固定的 **StreamGet 4.0.10**，通过适配层调用公开接口，优先 FLV、无 FLV 时使用 HLS。FFmpeg 负责持续解码和进程回收。

| 模块 | 职责 |
| --- | --- |
| `source.py`、`monitor.py` | 直播状态与地址解析、守卫、重连和场次管理；`monitor.py` 同时提供本机录音模式 |
| `local_capture`（浏览器端） | 浏览器 WebAudio 采集 → WebSocket PCM → 复用切段与分析管道 |
| `audio.py`、`storage.py` | 按采样切段、原子落盘、采集索引与恢复 |
| `analysis.py`、`analysis_worker.py` | 分析调度、可复用模型子进程 |
| `providers.py`、`models.py` | 人物判断/转写适配器、模型准备与版本配置 |
| `analysis_storage.py`、`analysis_audio.py` | 请求、人物和发言持久化，窗口上下文与音频来源 |
| `transcripts.py` | 按时间排序并原子导出文档 |
| `app.py`、`static/` | 本机 HTTP API、网页及轮询 |
| `scripts/` | 一键准备模型（`setup.sh`）与启动服务（`run.sh`） |

人物判断与转写通过 `DiarizationProvider`、`TranscriptionProvider` 分别接入，使用采样位置、窗口内标签和纯数据结果。更换模型主要调整适配器与模型配置，采集、队列和文档可以复用；仍需验证时间戳、标签语义和重叠行为。更换声纹模型需要新的向量命名空间和分析版本。

当前实现的是本地模型。接入云服务还需要客户端、凭据与超时处理；服务如果只返回单次请求的说话人标签，仍需处理跨切片人物对齐。

## HTTP API

完整参数和响应结构见运行后的 [交互文档](http://127.0.0.1:8787/docs)。

| 接口 | 用途 |
| --- | --- |
| `GET /api/monitor` | 守卫状态、主播、采集时长及最近错误 |
| `POST /api/monitor/start` | 开始；`{"room":"123456789","realtime_analysis":false}` |
| `POST /api/monitor/stop` | 停止并保存尾段 |
| `WS /api/local/audio` | 本机录音；`?source=system\|microphone&realtime=true`，浏览器推送 16 kHz PCM，连接即开始、断开即结束 |
| `GET /api/sessions`、`GET /api/sessions/{id}` | 场次列表与详情 |
| `GET /api/sessions/{id}/segments`、`GET /api/sessions/{id}/events` | 已完成分段与采集事件 |
| `GET /api/segments/{id}/audio` | 回听 WAV，支持 HTTP Range；`download=true` 下载 |
| `GET /api/analysis/status`、`GET /api/analysis/queue` | 模型/工作进程状态、当前任务和等待数量 |
| `PUT /api/analysis/realtime` | 守卫运行时切换实时；`{"enabled":true}` |
| `GET /api/analysis/requests` | 含完成记录的分析请求进度列表 |
| `GET /api/analysis/requests/{run_id}/segments` | 请求内的切片状态 |
| `POST /api/sessions/{id}/analysis`、`GET /api/sessions/{id}/analysis` | 请求整场分析、查询进度；POST 可用 `{"retry":true}` 重试 |
| `POST /api/segments/{id}/analysis`、`GET /api/segments/{id}/analysis` | 单片复核；POST 支持 `reanalyse` 或 `retry` |
| `GET /api/sessions/{id}/utterances` | 整场发言，支持写入游标增量读取 |
| `GET /api/segments/{id}/utterances` | 切片相关发言；`scope=session` 为整场，`scope=segment` 为成功复核 |
| `GET /api/utterances/{id}/audio` | 回听发言，可跨分段重建 WAV |
| `GET /api/sessions/{id}/transcript` | 下载当前整场分析的全部已生成文本 |
| `GET /api/segments/{id}/transcript` | 下载最新成功单片复核 |

列表通过 `limit`、`offset` 分页，最大 `limit=100`；发言接口通过 `after` 游标读取，最大 `limit=500`。文本下载支持 `format=txt`、`md`、`jsonl`。

守卫状态为 `stopped`、`checking`、`waiting`、`recording`、`reconnecting`、`needs_attention`。重复启动同一房间返回当前任务，启动另一房间返回 HTTP 409；非法房间输入返回 HTTP 400。

## 开发与验证

```sh
uv run --locked --extra analysis pytest -q
uv run --locked --extra analysis ruff check src tests tools
uv run --locked --extra analysis ruff format --check src tests tools
uv run --locked --extra analysis python -m compileall -q src tests tools
node --check src/douyin_live_sense/static/app.js
```

自动化测试使用模拟直播源、可控分析适配器和临时目录，覆盖守卫、切段、重启恢复、手动/实时切换、跨片发言、独立复核、队列进度、模型准备及异常提交。模拟结果不证明线上抖音可用或真实模型准确率。

真实验收工具需联网或已准备模型，分别执行：

```sh
# 真实 StreamGet + FFmpeg：至少 30 分钟采集，专用转发器模拟断线
# 运行前停止使用默认数据目录的其他服务
uv run --locked --extra analysis --env-file .env python tools/live_acceptance.py --room 123456789 --minutes 30

# 独立数据目录：先手动采集，再开启实时补分析，断线恢复后补齐尾段
uv run --locked --extra analysis --env-file .env python tools/analysis_acceptance.py --minutes 30

# 独立数据目录：复用已有场次，验证整场和单片复核版本
# 将 SESSION_ID 替换为本机实际场次 ID
uv run --locked --extra analysis --env-file .env python tools/archive_acceptance.py --session-id SESSION_ID
```

验收工具的转发器只中断专用连接，不修改系统网络。房间未开播或触发风控时，真实验收可能等待或失败。报告与音频保存在忽略的 `data/` 中。

已执行的模拟测试、真实直播、真实模型及网页验证分别记录在 [VERIFICATION.md](VERIFICATION.md)。开发方案见 [分析与档案计划](docs/analysis-and-archive-plan.md)。

## 当前边界

- 一次监听一个房间，只支持中文普通话分析。
- 人物判断和文字可能出错，短发言、音乐、专业词汇及多人同时讲话会影响结果；尚无人工标注的 CER、DER 准确率评估。
- 分析速度与硬件和内容有关，可能积压；进度和待处理状态如实显示。
- 电脑休眠或服务未运行时无法采集，缺口会保留。首版通过启动命令运行，不安装开机服务。
- 不自动清理历史音频；请为长期采集预留磁盘空间。抖音接口变化可能需要更新直播解析依赖或适配层。
