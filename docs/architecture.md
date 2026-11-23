# 架构与实现

后端 Python 3.11 + FastAPI + SQLite，网页是原生 HTML、CSS、JavaScript，没有前端构建步骤。直播解析依赖固定的 **StreamGet 4.0.10**，通过适配层调用公开接口，优先 FLV、无 FLV 时使用 HLS；FFmpeg 负责持续解码与进程回收。

## 进程与模块

| 模块 | 职责 |
| --- | --- |
| `source.py`、`monitor.py` | 直播状态与地址解析、守卫、重连和场次管理；`monitor.py` 同时提供本机录音模式 |
| `local_capture`（浏览器端） | 浏览器 WebAudio 采集 → WebSocket PCM → 复用切段与分析管道 |
| `audio.py`、`storage.py` | 按采样切段、原子落盘、采集索引与恢复 |
| `analysis.py`、`analysis_worker.py` | 分析调度、可复用的模型子进程 |
| `providers.py`、`models.py` | 人物判断/转写适配器、模型准备与版本配置 |
| `analysis_storage.py`、`analysis_audio.py` | 请求、人物和发言持久化，窗口上下文与音频来源 |
| `transcripts.py` | 按时间排序并原子导出文档 |
| `app.py`、`static/` | 本机 HTTP API、网页及轮询 |
| `auth.py`、`security.py` | 单管理员密码、会话签名、日志脱敏 |
| `scripts/` | 一键准备（`setup.sh`）与启动服务（`run.sh`） |

## 音频与时间

- 音频为 **16 kHz、单声道、16 位 PCM WAV**，约 115 MB/小时，历史音频完整保留，不自动删除。
- 切段按收到的采样数计算，长度等于“网站配置 → 录音时间配置”的切片时长（默认 30 秒，即 480,000 个采样），不对齐时钟整分钟。
- 停止、断线或退出时保存不足一个切片时长的尾段。
- 时间以每次连接首次收到 PCM 的本机时间为基准，段内按采样数计算；重连建立新基准，保留真实缺口。
- 数据库存 UTC，界面与文本显示北京时间。这是**音频接收时间**，包含直播传输与缓冲延迟。
- 每次开启分段前检查磁盘：剩余不足 1 GB 时保存尾段并停止采集。
- 分段关闭完成才登记为可用；服务启动时检查未完成文件，按实际落盘采样修复可读 WAV，无法恢复的文件保留并记录错误。

## 采集循环

- 未开播时每 30 秒检查一次。
- 网络错误按 2、5、10、30 秒退避重试。
- 20 秒没有收到音频会重连。
- 只有明确下播或源场次 ID 改变才划分新场次；普通网络异常保留当前场次和缺口。
- 明确的登录或风控错误进入 `needs_attention` 并停止重试；连续解析失败或不可见直播间不会无限重连。
- 同一数据目录只允许一个服务实例运行；重复启动同一房间返回当前任务，启动另一房间返回 409。

## 数据目录

```text
data/
  sense.sqlite3                     # 守卫、场次、分段、分析请求、人物和发言
  auth.json                         # 管理员密码哈希与会话密钥
  preferences.json                  # 识别方式与切片时长
  audio/<room>/<session>/
    000001_<segment-id>.wav          # 已保存完成的音频
    000002_<segment-id>.wav.part     # 正在写入的音频
  models/
    manifest.json                   # 模型 revision 与 SHA256
    hf_token                        # 网页保存的 Hugging Face Token
    <name>/<revision>/               # 模型文件
  analysis/<session>/<run>/
    transcript.txt
    transcript.md
    transcript.jsonl
  backups/                          # 数据库升级前的一致性备份
  tmp/                              # 临时 ZIP 等中间文件
```

`data/`、`.env`、数据库和临时音频已加入 Git 忽略规则。修改 `DLS_DATA_DIR` 后使用的是另一套数据库、模型和登录密码。

## 模型替换

人物判断与转写通过 `DiarizationProvider`、`TranscriptionProvider` 分别接入，使用采样位置、窗口内标签和纯数据结果。

- 更换模型主要调整适配器与模型配置，采集、队列和文档可以复用。
- 仍需验证时间戳、标签语义和重叠行为。
- 更换声纹模型需要新的向量命名空间和分析版本，否则旧人物档案会被误匹配。
- 当前实现的是本地模型。接入云服务还需要客户端、凭据与超时处理；如果服务只返回单次请求的说话人标签，仍需处理跨切片人物对齐。

## 重启恢复

服务重启后会恢复守卫设置、实时开关和未完成的分析任务，并修复未完成音频。下一次手动开启守卫默认关闭实时分析。本机录音依赖浏览器标签页，关闭标签页即停止，不随服务重启恢复。
