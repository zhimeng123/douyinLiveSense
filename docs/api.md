# HTTP API

服务运行后可打开交互文档：<http://127.0.0.1:8787/docs>。所有接口除 `/login`、`POST /api/login` 外都需要登录 Cookie；未登录返回 401。状态变更接口会校验 `Origin`，跨站请求返回 403。

## 认证

| 接口 | 用途 |
| --- | --- |
| `GET /login` | 登录页，已登录时跳转 `/` |
| `POST /api/login` | 登录；`{"password":"..."}`，成功写入 `dls_session` Cookie |
| `GET /api/auth` | 当前会话，返回 `authenticated`、`username`、`expires_at` |
| `POST /api/logout` | 删除会话 Cookie |
| `POST /api/password` | 修改密码；`{"current":"...","new":"..."}`，当前密码错误返回 400 |

登录连续失败 5 次后，同一 IP 锁定 5 分钟，返回 429 与 `Retry-After`。Cookie 有效期由 `DLS_SESSION_DAYS` 控制。

## 守卫与采集

| 接口 | 用途 |
| --- | --- |
| `GET /api/monitor` | 守卫状态、主播、场次、采集时长、最近音频时刻、最近错误、数据目录 |
| `POST /api/monitor/start` | 开始守卫；`{"room":"123456789","realtime_analysis":false}` |
| `POST /api/monitor/stop` | 停止并保存尾段 |
| `WS /api/local/audio` | 本机录音；`?source=system\|microphone&realtime=true` |

`GET /api/monitor` 的关键字段：`state`（`stopped`、`checking`、`waiting`、`recording`、`reconnecting`、`needs_attention`）、`enabled`、`mode`（`douyin` / `local`）、`realtime_analysis`、`segment_count`、`captured_seconds`、`retry_in_seconds`、`last_error`。

`POST /api/monitor/start` 的 `room` 接受房间号、`live.douyin.com` 链接或 `live_web_rid` 链接。同一房间重复启动返回当前任务；启动另一房间返回 409；非法输入或需要处理返回 400。开启实时分析前必须先把识别方式设为“纯本地模型识别”，否则返回 409。

本机录音通过 WebSocket 传输：连接即开始，浏览器推送 16 kHz 小端 PCM 二进制帧，断开即结束。首条消息为 `{"session_id":"...","source":"microphone"}`，出错时发送 `{"error":"..."}` 后关闭。20 秒没有音频会自动结束。与直播间守卫互斥。

## 音频档案

| 接口 | 用途 |
| --- | --- |
| `GET /api/sessions` | 场次列表，含切片数与时长；`limit` 最大 100 |
| `GET /api/sessions/{id}` | 场次详情 |
| `GET /api/sessions/{id}/segments` | 已完成切片列表；`around=<sequence>` 定位到指定切片所在页 |
| `GET /api/sessions/{id}/events` | 采集事件与缺口 |
| `GET /api/segments/{id}/audio` | 回听 WAV，支持 HTTP Range；`download=true` 下载 |
| `GET /api/sessions/{id}/audio.zip` | 下载整场合集音频 ZIP，成员名为“主播名 · 开始时间” |
| `DELETE /api/sessions/{id}` | 硬删除合集（数据库、音频、分析记录） |

`DELETE` 在该合集正在采集或存在未完成分析时返回 409。`audio.zip` 在没有已完成切片时返回 404。ZIP 临时文件写在 `data/tmp/`，下载结束后自动删除。

## 分析

| 接口 | 用途 |
| --- | --- |
| `GET /api/analysis/status` | 模型就绪、工作进程状态、当前 run、排队数量 |
| `GET /api/analysis/queue` | 当前任务、排队概览；`limit`、`offset` |
| `PUT /api/analysis/realtime` | 守卫运行时切换实时分析；`{"enabled":true}` |
| `GET /api/analysis/requests` | 含完成记录的分析请求进度列表 |
| `GET /api/analysis/requests/{run_id}/segments` | 请求内的切片状态 |
| `POST /api/sessions/{id}/analysis` | 请求整场分析；`{"retry":true}` 重试未完成内容 |
| `GET /api/sessions/{id}/analysis` | 整场分析进度 |
| `POST /api/segments/{id}/analysis` | 单片复核；`{"reanalyse":true}` 新建版本，`{"retry":true}` 重试 |
| `GET /api/segments/{id}/analysis` | 单片复核状态与历史版本 |

识别方式不是“纯本地模型识别”时，所有分析请求返回 409。切片尚未保存完成时单片接口返回 409。重复请求会复用未完成的记录。

## 发言、文本与回听

| 接口 | 用途 |
| --- | --- |
| `GET /api/sessions/{id}/utterances` | 整场发言；`after` 游标、`limit` 最大 500 |
| `GET /api/segments/{id}/utterances` | 切片相关发言；`scope=session`（默认）或 `scope=segment` |
| `GET /api/utterances/{id}/audio` | 回听发言，可跨分段重建 WAV |
| `GET /api/sessions/{id}/transcript` | 下载当前整场分析的全部已生成文本 |
| `GET /api/segments/{id}/transcript` | 下载最新成功的单片复核文本 |

发言响应包含 `run_id`、`items` 与 `next_cursor`；每条发言带 `speaker_label`、`sources`（源切片与采样范围）和 `cross_segment`。文本下载支持 `format=txt`、`md`、`jsonl`。

## 偏好与模型

| 接口 | 用途 |
| --- | --- |
| `GET /api/preferences` | 识别方式、切片时长、范围、是否因任务锁定、平台是否支持 |
| `PUT /api/preferences` | 修改识别方式或切片时长；`{"mode":"record\|models","segment_seconds":30}` |
| `GET /api/models` | 平台支持、依赖、Token 状态（脱敏）、组件就绪、下载任务 |
| `PUT /api/models/token` | 保存并校验 Hugging Face Token；`{"token":"hf_..."}` |
| `DELETE /api/models/token` | 清除已保存的 Token |
| `POST /api/models/download` | 开始下载模型；`{"parts":[...]}`，返回 202 与 `job_id` |
| `GET /api/models/job` | 下载任务进度 |

`PUT /api/preferences`：任务进行中修改识别方式返回 409；选择 `models` 但模型未就绪返回 409 并附 `missing`；`feishu` 返回 409；切片时长必须在 1–14400 秒之间，否则 422。`GET /api/preferences` 的 `locked` 表示当前是否有任务在运行。

`PUT /api/models/token` 只接受有效的只读 Token，无法访问 Community-1 时返回 `{"gated":true,"warning":"..."}`。`POST /api/models/download` 在缺 Token 时返回 400 与 `needs_token`，依赖未安装时返回 409 与 `needs_dependencies`，已有任务时返回 409。

## 通用约定

- 列表分页用 `limit`、`offset`，`limit` 上限 100。
- 发言增量读取用 `after` 游标，`limit` 上限 500。
- 请求体非法返回 422；资源不存在返回 404；状态冲突返回 409；参数语义错误返回 400。
- 时间字段为 UTC ISO 8601，界面与文本显示北京时间（`Asia/Shanghai`）。
