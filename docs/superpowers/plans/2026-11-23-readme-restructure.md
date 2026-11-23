# README 重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 393 行 README 改成 120–150 行入口页，参考内容拆进 `docs/` 六个文件，并补上修改密码、ZIP 下载、删除合集等新功能。

**Architecture:** README 只保留定位、功能、快速开始、日常使用、数据、常用配置、部署摘要、常见问题、文档导航、边界与许可；其余内容整段搬到 `docs/` 并以相对链接互连。

**Tech Stack:** Markdown、现有 shell 脚本、无头 Chrome（截图）。

**Spec:** `docs/superpowers/specs/2026-11-23-readme-restructure-design.md`

## Global Constraints

- 全部中文；术语固定为：场次、切片、整场分析、单片复核、识别方式、守卫。
- 事实以代码为准：`src/douyin_live_sense/app.py`（路由与响应）、`static/`（界面文案）、`config.py` / `preferences.py`（默认值）、`cli.py` / `scripts/`（命令）、`pyproject.toml`（依赖与平台）。
- 不写入真实主播名、房间号、Token、Cookie、真实音频内容。
- README 正文不超过 150 行；`docs/` 文件各自不超过 200 行。
- 所有跨文件链接使用相对路径，必须指向真实存在的文件或现有章节。
- 不修改任何 `.py`、`.js`、`.html`、`.css` 或脚本。
- 截图保存到 `docs/images/`，宽度 1440，不含真实数据。

## Review Focus

- README 提到的按钮/菜单与 `static/index.html`、`app.js` 的实际文案不一致（如“补齐未分析切片”“删除合集”“下载整场音频”）。
- 部署文档漏掉 `DLS_ALLOWED_HOSTS` 或 `DLS_TRUST_PROXY`，读者按文档配反代会得到 403 或登录循环。
- API 文档的路径、参数、状态码与实际路由不一致。
- 截图误带真实主播名或历史录音。
- 拆分后原信息丢失：磁盘阈值、时间基准、平台限制、许可与模型条款。

---

### Task 1: 截图并准备 `docs/images/`

**Files:**
- Create: `docs/images/home.png`
- Temp: `/private/var/folders/fz/_twxyr9x54bdv4y0_c71f7qc0000gn/T/opencode/readme-shot/`

**Interfaces:**
- Produces: `docs/images/home.png`，README 第 2 节引用。

- [ ] **Step 1: 用独立数据目录初始化临时实例**

```sh
SHOT=/private/var/folders/fz/_twxyr9x54bdv4y0_c71f7qc0000gn/T/opencode/readme-shot
mkdir -p "$SHOT/data"
DLS_DATA_DIR="$SHOT/data" uv run --no-sync --env-file .env \
  douyin-live-sense init-auth --password shot-only-password
```

预期：打印“初始登录密码：shot-only-password”，生成 `$SHOT/data/auth.json`。

- [ ] **Step 2: 后台启动服务（端口 8799，避免影响日常实例）**

```sh
DLS_DATA_DIR="$SHOT/data" DLS_PORT=8799 ./scripts/run.sh > "$SHOT/server.log" 2>&1 &
```

预期：`curl -s http://127.0.0.1:8799/api/monitor` 返回 JSON；日志出现 `Uvicorn running on http://127.0.0.1:8799`。

- [ ] **Step 3: 登录拿会话 Cookie**

```sh
curl -s -D - -o /dev/null -X POST http://127.0.0.1:8799/api/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"shot-only-password"}' | grep -i '^set-cookie'
```

预期：`set-cookie: dls_session=...`。记下 token 值。

- [ ] **Step 4: 无头 Chrome + CDP 截图**

原因：chrome-devtools MCP 读取 `DevToolsActivePort` 报 `EPERM`。用 Chrome 的无头模式与临时 profile，不影响用户浏览器。

```sh
"/Applications/Google ChromeTemp.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --no-first-run --hide-scrollbars \
  --remote-debugging-port=9333 \
  --user-data-dir="$SHOT/chrome" about:blank > "$SHOT/chrome.log" 2>&1 &
```

用 Python（`.venv` 已装 `websockets`）连 `http://127.0.0.1:9333/json/version` 的 `webSocketDebuggerUrl`，依次发送：

1. `Network.enable`，`Network.setCookie` `{name:"dls_session", value:<token>, domain:"127.0.0.1", path:"/"}`。
2. `Emulation.setDeviceMetricsOverride` `{width:1440, height:1000, deviceScaleFactor:2, mobile:false}`。
3. `Page.navigate` 到 `http://127.0.0.1:8799/#/home`，等待 `Page.loadEventFired` 后再等 1 秒。
4. `Page.captureScreenshot` `{format:"png", clip:{x:0,y:0,width:1440,height:1000,scale:1}}`，base64 解码写入 `docs/images/home.png`。

预期：文件存在且 `sips -g pixelWidth` 显示 2880（2 倍图）；打开图片能看到顶栏、标语、采集卡片，且音频档案为空。

- [ ] **Step 5: 停服务并清理临时目录**

```sh
kill %1 2>/dev/null; pkill -f "remote-debugging-port=9333"
rm -rf "$SHOT"
```

预期：`curl --max-time 2 http://127.0.0.1:8799/` 失败；临时目录不存在。

- [ ] **Step 6: 提交**

```sh
git add docs/images/home.png docs/superpowers/specs/2026-11-23-readme-restructure-design.md
git commit -m "docs: 新增 README 界面截图与重构设计"
```

---

### Task 2: `docs/deploy.md`

**Files:**
- Create: `docs/deploy.md`

**Interfaces:**
- Consumes: `README.md` 现有“配置、认证与模型”“部署方式”两节。
- Produces: 章节 `# 部署与配置`，README 第 9 节链接到 `docs/deploy.md`。

- [ ] **Step 1: 写入六个小节**

1. `## 平台与依赖`：Python 3.11、uv、FFmpeg；macOS ARM64 / Linux x86_64 支持完整识别，Linux ARM64 仅录音（缺 torchcodec）。
2. `## 完整环境变量`：表格含 `DOUYIN_COOKIE`、`DLS_DATA_DIR`、`DLS_ANALYSIS_THREADS`(4)、`DLS_DIARIZATION_DEVICE`(auto/cpu/mps/cuda)、`DLS_HOST`(127.0.0.1)、`DLS_PORT`(8787)、`DLS_ALLOWED_HOSTS`、`DLS_TRUST_PROXY`(false)、`DLS_SECURE_COOKIES`(auto/always/never)、`DLS_SESSION_DAYS`(1–90，默认 7)、`DLS_ADMIN_PASSWORD`、`HF_TOKEN`（高级回退，网页 Token 存在 `data/models/hf_token`）。
3. `## 本机`、`## 可信局域网`、`## 域名 + Caddy`、`## 无域名隧道`、`## systemd`：参数与示例照现有 README 保留，补 `--record-only` 与 `./scripts/setup.sh --check`。
4. `## 网络与安全`：只提供 HTTP，不要把 8787 直接暴露公网；反代必须转发 WebSocket `/api/local/audio`；`DLS_TRUST_PROXY=true` 配合 `X-Forwarded-Proto`。
5. `## 认证`：单管理员；`init-auth` / `reset-password` 命令；网页菜单“修改登录密码”调用 `POST /api/password`；密码只存 scrypt 哈希；连续失败锁定 5 次 / 5 分钟（来源 `auth.py` 的 `LoginLimiter` 默认值）。
6. `## 备份与迁移`：停服务后复制 `data/`；`data/backups/` 是升级前的一致性备份。

- [ ] **Step 2: 校验命令与默认值**

Run: `grep -n "DLS_" docs/deploy.md | wc -l` 与 `grep -c "DLS_" .env.example`

预期：文档包含 `.env.example` 中全部 `DLS_` 变量，且默认值与 `config.py` 一致。

- [ ] **Step 3: 提交**

```sh
git add docs/deploy.md && git commit -m "docs: 拆分部署与配置文档"
```

---

### Task 3: `docs/analysis.md`

**Files:**
- Create: `docs/analysis.md`
- Modify: `README.md`（写 README 时链接）

**Interfaces:**
- Consumes: 现有 README 的“配置识别方式”“分析使用方式”“人物编号的范围”“综合文本与下载范围”“分析队列与进度”五节。
- Produces: 章节 `# 识别与分析`。

- [ ] **Step 1: 写入六个小节**

1. `## 三种识别方式`：仅录音（默认）、纯本地模型识别、飞书秒记（未支持，接口返回 409）。
2. `## 模型与下载`：Community-1（需接受条款 + 只读 Token）、paraformer-zh、ct-punc；版本 pyannote.audio 4.0.7 / FunASR 1.4.16；`data/models/manifest.json` 存 revision 与 SHA256；约 15 GB；下载可断点续传；`prepare-models` 子命令与 `--asr-only`。
3. `## 整场、实时与单片复核`：按钮文案“补齐未分析切片”“分析本场全部切片”“分析”“重新分析”；重复请求复用；单片复核不并入整场记录。
4. `## 分析队列与进度`：`#/queue`；进度按成功提交切片数；等待尾段最高 99%；每页 50；单片优先调度。
5. `## 人物编号`：保留现有范围表；跨场次不复用；无全局声纹库；“人物待确认”“重叠讲话”。
6. `## 文本与下载范围`：TXT/Markdown/JSONL；整场下载不受页面已加载条数限制；SQLite 是来源；缺口说明。

- [ ] **Step 2: 校验界面文案**

Run: `for t in 补齐未分析切片 分析本场全部切片 重新分析 人物待确认 重叠讲话; do grep -q "$t" docs/analysis.md || echo "missing $t"; done`

预期：无输出。

- [ ] **Step 3: 提交**

```sh
git add docs/analysis.md && git commit -m "docs: 拆分识别与分析文档"
```

---

### Task 4: `docs/api.md`

**Files:**
- Create: `docs/api.md`

**Interfaces:**
- Consumes: 现有 README“HTTP API”一节的表格。
- Produces: 章节 `# HTTP API`。

- [ ] **Step 1: 写入接口表与小节**

1. `## 认证`：`POST /api/login`、`GET /api/auth`、`POST /api/logout`、`POST /api/password`；Cookie 名 `dls_session`。
2. `## 守卫与采集`：`GET /api/monitor`、`POST /api/monitor/start`、`POST /api/monitor/stop`、`WS /api/local/audio?source=system|microphone&realtime=true`。
3. `## 档案`：`GET /api/sessions`、`GET /api/sessions/{id}`、`GET /api/sessions/{id}/segments`（支持 `around`）、`GET /api/sessions/{id}/events`、`GET /api/segments/{id}/audio`（`download=true`）、`GET /api/sessions/{id}/audio.zip`、`DELETE /api/sessions/{id}`（采集中或分析中返回 409）。
4. `## 分析与文本`：`GET/POST/PUT /api/analysis/*`、`/api/sessions/{id}/analysis`、`/api/segments/{id}/analysis`、`/api/sessions/{id}/utterances`、`/api/segments/{id}/utterances`、`/api/utterances/{id}/audio`、`/api/sessions/{id}/transcript`、`/api/segments/{id}/transcript`。
5. `## 偏好与模型`：`GET/PUT /api/preferences`（`mode`、`segment_seconds` 1–14400、`locked`）、`GET /api/models`、`PUT/DELETE /api/models/token`、`POST /api/models/download`、`GET /api/models/job`。
6. `## 通用约定`：列表 `limit` 最大 100；发言 `after` 游标、`limit` 最大 500；下载 `format=txt|md|jsonl`；状态 400/404/409/422 的触发条件。

- [ ] **Step 2: 校验路由覆盖**

Run:

```sh
grep -oE '@app\.(get|post|put|delete|websocket)\("[^"]+' src/douyin_live_sense/app.py |
  sed -E 's/.*\("//' | sort -u > /tmp/routes.txt
grep -oE '`(GET|POST|PUT|DELETE|WS) [^`]+`' docs/api.md | sed -E 's/`(GET|POST|PUT|DELETE|WS) //; s/`//' | sort -u > /tmp/doc-routes.txt
comm -23 /tmp/routes.txt /tmp/doc-routes.txt
```

预期：输出为空（除 `/login`、`/api/auth` 等已在文档出现的条目；若有遗漏，补进文档）。

- [ ] **Step 3: 提交**

```sh
git add docs/api.md && git commit -m "docs: 拆分 HTTP API 文档"
```

---

### Task 5: `docs/architecture.md`

**Files:**
- Create: `docs/architecture.md`

**Interfaces:**
- Consumes: 现有 README“音频、时间与恢复”“架构与模型替换”两节。
- Produces: 章节 `# 架构与实现`。

- [ ] **Step 1: 写入五个小节**

1. `## 进程与模块`：保留现有模块职责表（`source.py`/`monitor.py`、`audio.py`/`storage.py`、`analysis.py`/`analysis_worker.py`、`providers.py`/`models.py`、`analysis_storage.py`/`analysis_audio.py`、`transcripts.py`、`app.py`/`static/`）。
2. `## 音频与时间`：16 kHz 单声道 16 位 PCM、约 115 MB/小时、按采样切段、尾段、时间基准与缺口、UTC 存储/北京时间显示。
3. `## 采集循环`：30 秒轮询、2/5/10/30 秒退避、20 秒无音频重连、磁盘 <1 GB 停止、启动时修复未完成 WAV。
4. `## 数据目录`：保留现有目录树（`sense.sqlite3`、`audio/`、`models/`、`analysis/`、`backups/`）。
5. `## 模型替换`：`DiarizationProvider` / `TranscriptionProvider` 适配层、向量命名空间、云服务接入缺口。

- [ ] **Step 2: 校验目录树与代码一致**

Run: `grep -n "sense.sqlite3\|audio/<room>\|manifest.json\|backups/" docs/architecture.md`

预期：四处均出现。

- [ ] **Step 3: 提交**

```sh
git add docs/architecture.md && git commit -m "docs: 拆分架构与实现文档"
```

---

### Task 6: `docs/troubleshooting.md`

**Files:**
- Create: `docs/troubleshooting.md`

**Interfaces:**
- Consumes: 现有 README“故障排查”与“当前边界”。
- Produces: 章节 `# 常见问题`。

- [ ] **Step 1: 写入分组的问答**

1. `## 访问与登录`：403 Host、登录后循环、反向代理 HTTPS、Cookie 被拦截。
2. `## 采集`：未开播等待、风控或需要登录 Cookie、“需要处理”状态、磁盘不足、休眠缺口、重复启动返回 409。
3. `## 分析与模型`：模型 401/403、依赖未安装（409 `needs_dependencies`）、`needs_token`、Linux ARM64 不支持、分析积压、设备不可用。
4. `## 音频与档案`：ZIP 下载 404、删除合集 409、回听跨段发言、Range 下载。

- [ ] **Step 2: 校验链接与命令**

Run: `grep -n "docs/\|scripts/" docs/troubleshooting.md`

预期：每个路径真实存在（用 `test -e` 逐条确认）。

- [ ] **Step 3: 提交**

```sh
git add docs/troubleshooting.md && git commit -m "docs: 拆分故障排查文档"
```

---

### Task 7: `docs/development.md`

**Files:**
- Create: `docs/development.md`

**Interfaces:**
- Consumes: 现有 README“开发与验证”。
- Produces: 章节 `# 开发与验收`。

- [ ] **Step 1: 写入四个小节**

1. `## 环境`：`uv sync --locked --group dev`，Python 3.11。
2. `## 检查命令`：pytest、ruff check/format、compileall、`node --check` 两个 JS 文件、`bash -n` 两个脚本、`./scripts/setup.sh --check`；与 `.github/workflows/tests.yml` 一致。
3. `## 真实验收`：`tools/live_acceptance.py`、`tools/analysis_acceptance.py`、`tools/archive_acceptance.py` 的参数与前置条件。
4. `## 记录`：`VERIFICATION.md` 记录已执行的模拟/真实直播/真实模型验证；链接 `docs/analysis-and-archive-plan.md`。

- [ ] **Step 2: 校验与 CI 一致**

Run: `diff <(sed -n '/run:/p' .github/workflows/tests.yml) <(grep '^`' docs/development.md) || true`

预期：CI 的每条命令都能在文档中找到；缺失则补。

- [ ] **Step 3: 提交**

```sh
git add docs/development.md && git commit -m "docs: 拆分开发与验收文档"
```

---

### Task 8: 重写 `README.md`

**Files:**
- Modify: `README.md`（全文替换）

**Interfaces:**
- Consumes: `docs/images/home.png`、`docs/deploy.md`、`docs/analysis.md`、`docs/api.md`、`docs/architecture.md`、`docs/troubleshooting.md`、`docs/development.md`。

- [ ] **Step 1: 按 12 节写出新 README**

1. 标题 `# ValuSense · 抖音直播监听、录音与普通话记录` + 一段定位：本机运行、默认仅录音、界面叫 ValuSense、包名与命令 `douyin-live-sense`。
2. `![界面截图](docs/images/home.png)`。
3. `## 适合谁` / 一段边界：单个中文直播间、本机存档、不自动清理、1 小时约 115 MB。
4. `## 功能`：表格 10 行内（守卫、本机录音、音频档案含 ZIP 与删除、整场分析、实时分析、单片复核、队列、人物编号、文本导出、重启恢复）。
5. `## 快速开始`：三步命令 + 打开 8787 + 初始密码；两种安装（默认含分析、`--record-only`）。
6. `## 日常使用`：输入房间号/链接、`#/home` 与 `#/queue`、本机录音、网站配置（识别方式 + 切片时长 1–14400 秒）、开启分析、档案回听/ZIP/删除、修改密码（菜单“修改登录密码”）。
7. `## 数据与磁盘`：目录树简化版 + 不自动清理 + 15 GB 模型。
8. `## 常用配置`：只列 `DOUYIN_COOKIE`、`DLS_DATA_DIR`、`DLS_HOST`、`DLS_PORT`、`DLS_ALLOWED_HOSTS`，其余指向 `docs/deploy.md`。
9. `## 部署`：本机默认 + 一句反代/隧道/systemd + 链接。
10. `## 常见问题`：4 条（403 Host、登录循环、WebSocket、模型 401/403）+ 链接 `docs/troubleshooting.md`。
11. `## 文档`：表格列出 6 个 docs 文件及用途；`## 开发与验证` 保留 5 条命令 + 链接。
12. `## 边界`、`## 许可证`：保留原边界 5 条与许可说明。

- [ ] **Step 2: 校验行数与链接**

```sh
wc -l README.md
grep -oE '\]\(([^)]+)\)' README.md | sed -E 's/\]\(|\)//g' | grep -v '^http' |
  while read -r p; do [ -e "$p" ] || echo "broken: $p"; done
```

预期：行数 ≤150；无 `broken:` 输出。

- [ ] **Step 3: 与代码核对新功能**

Run: `for t in 删除合集 下载整场音频 修改登录密码 初始密码 重置; do grep -q "$t" README.md || echo "missing $t"; done`

预期：无输出（`重置` 出现在 CLI 说明或 docs 链接说明中）。

- [ ] **Step 4: 提交**

```sh
git add README.md && git commit -m "docs: 重写 README 为入口页"
```

---

### Task 9: 全仓文档校验与收尾

**Files:**
- Modify: `docs/` 内文件（仅修错别字或失效链接）

- [ ] **Step 1: 检查所有相对链接**

```sh
for f in README.md docs/*.md; do
  grep -oE '\]\(([^)#]+)(#[^)]*)?\)' "$f" | sed -E 's/\]\(|\)//g' | grep -v '^http' |
    while read -r p; do [ -e "$(dirname "$f")/$p" ] || echo "$f -> $p"; done
done
```

预期：无输出。

- [ ] **Step 2: 运行现有文档与代码检查**

```sh
uv run --no-sync ruff check src tests tools
node --check src/douyin_live_sense/static/app.js
```

预期：全部通过（未改代码，用于确认工作区未被误改）。

- [ ] **Step 3: 确认服务与临时资源已清理**

```sh
curl -s --max-time 2 http://127.0.0.1:8799/ >/dev/null && echo "still running" || echo "stopped"
pgrep -f "remote-debugging-port=9333" || echo "no headless chrome"
```

预期：`stopped` 与 `no headless chrome`。

- [ ] **Step 4: 提交收尾**

```sh
git add -A && git commit -m "docs: 校正文档链接与措辞"
```

如无改动则跳过。
