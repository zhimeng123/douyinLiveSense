# 部署与配置

应用只提供 HTTP；TLS、域名和公网暴露交给外部代理或隧道。**不要直接把 8787 暴露到公网。**

## 平台与依赖

- Python 3.11（由 `.python-version` 指定），依赖由 `uv.lock` 固定。
- 需要 [uv](https://docs.astral.sh/uv/) 与 FFmpeg（持续解码直播音频）。
- 完整识别（转写 + 人物判断）支持 macOS ARM64 与 Linux x86_64；Linux ARM64 缺 torchcodec，只能录音。
- 准备脚本：

```sh
./scripts/setup.sh              # 安装核心依赖；平台支持时同时安装分析扩展
./scripts/setup.sh --record-only  # 只装录音依赖
./scripts/setup.sh --check      # 只显示平台与安装模式
./scripts/setup.sh --password '你的密码'  # 同时设置初始管理员密码
```

安装脚本会：检查 uv 与 FFmpeg → 同步依赖 → 缺少 `.env` 时从 `.env.example` 生成 → 初始化管理员密码。首次密码只在终端打印一次，磁盘只保存 scrypt 哈希。

## 完整环境变量

配置文件位于项目根目录的 `.env`，模板为 [.env.example](../.env.example)。程序通过环境变量读取配置，`./scripts/run.sh` 使用 `--env-file .env` 加载它。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DOUYIN_COOKIE` | 空 | 抖音提示登录或风控时提供自己的登录 Cookie |
| `DLS_DATA_DIR` | 启动目录下的 `data/` | 数据、录音、模型及导出目录；建议使用绝对路径 |
| `DLS_ANALYSIS_THREADS` | `4` | 分析进程 CPU 线程数 |
| `DLS_DIARIZATION_DEVICE` | `auto` | 人物判断设备：`auto`、`cpu`、`mps`、`cuda` |
| `DLS_HOST` | `127.0.0.1` | HTTP 监听地址 |
| `DLS_PORT` | `8787` | HTTP 监听端口 |
| `DLS_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Host 允许列表，反代域名必须加入 |
| `DLS_TRUST_PROXY` | `false` | 信任 `X-Forwarded-Proto` / `X-Forwarded-For` |
| `DLS_SECURE_COOKIES` | `auto` | Cookie 安全策略：`auto`、`always`、`never` |
| `DLS_SESSION_DAYS` | `7` | 登录 Cookie 有效天数，范围 1–90 |
| `DLS_ADMIN_PASSWORD` | 空 | 自动化部署时给 `init-auth` / `reset-password` 使用的初始密码 |
| `HF_TOKEN` | 空 | 高级回退，仅用于兼容已有环境；网页保存的 Token 优先存到 `data/models/hf_token` |

修改 `DOUYIN_COOKIE`、监听地址等运行配置后需要重启服务。Cookie 不写入数据库或 API，日志会隐藏 Cookie 与带签名的播放地址。

## 本机（默认）

```sh
./scripts/setup.sh
./scripts/run.sh
```

打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)。保持 `DLS_HOST=127.0.0.1`，这是最简单的部署方式，同一数据目录只允许一个服务实例运行。

## 可信局域网

显式监听局域网地址，并填写允许的 Host：

```dotenv
DLS_HOST=0.0.0.0
DLS_ALLOWED_HOSTS=192.168.1.20,localhost,127.0.0.1
```

仅在可信网络或防火墙后使用；局域网部署仍然是 HTTP，不能替代 HTTPS。

## 域名 + Caddy

保持 `DLS_HOST=127.0.0.1`，把域名加入 `DLS_ALLOWED_HOSTS`，并启用代理协议识别：

```dotenv
DLS_ALLOWED_HOSTS=live.example.com,127.0.0.1,localhost
DLS_TRUST_PROXY=true
DLS_SECURE_COOKIES=auto
```

复制并修改 [deploy/Caddyfile.example](../deploy/Caddyfile.example)，由 Caddy 负责证书与反代。示例会自动把 WebSocket 升级转发到应用。

## 无域名隧道

Cloudflare Tunnel 或 Tailscale Serve/Funnel 可以把本机 HTTP 服务接到 HTTPS 入口。应用侧仍使用 `DLS_HOST=127.0.0.1`、`DLS_TRUST_PROXY=true`，并把隧道提供的主机名加入 `DLS_ALLOWED_HOSTS`。例如 Tailscale：

```sh
./scripts/run.sh
sudo tailscale serve --https=443 http://127.0.0.1:8787
```

公网 Funnel 还需要额外的访问控制；不要把管理员登录页当作唯一的网络边界。

## systemd

复制 [deploy/douyin-live-sense.service](../deploy/douyin-live-sense.service)，修改 `User`、`WorkingDirectory` 和 `EnvironmentFile` 后安装：

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now douyin-live-sense
sudo journalctl -u douyin-live-sense -f
```

Linux 安装 FFmpeg：Debian/Ubuntu 用 `sudo apt update && sudo apt install -y ffmpeg`，Fedora 用 `sudo dnf install -y ffmpeg`，Arch 用 `sudo pacman -S ffmpeg`。分析依赖与模型约需 15 GB，录音空间另计。

## 网络与安全

- 反代必须转发 WebSocket `/api/local/audio`；Nginx 需要显式设置 `Upgrade` 和 `Connection` 头，Caddy 自动处理。
- HTTPS 反代必须让 `X-Forwarded-Proto: https` 到达应用，并设置 `DLS_TRUST_PROXY=true`，否则登录后会循环回登录页。
- 应用有同源校验：反代里的 `Host` 头必须保留端口（Nginx 用 `$http_host`，不要用 `$host`），否则浏览器操作返回 403。
- 会话 Cookie 为 HttpOnly + SameSite=Lax；`DLS_SECURE_COOKIES=always` 可强制 Secure。

## 认证

单管理员账号，没有注册入口。

- 首次初始化：`./scripts/setup.sh`，或 `uv run --env-file .env douyin-live-sense init-auth`（已有密码时不会覆盖）。
- 忘记密码：`uv run --env-file .env douyin-live-sense reset-password`，会重新生成会话密钥，已登录的浏览器全部失效。
- 日常修改：登录后打开菜单“修改登录密码”，调用 `POST /api/password`，需要提供当前密码和新密码。
- 连续 5 次密码错误后，同一 IP 锁定 5 分钟（内存计数，重启服务即清除）。

## 备份与迁移

- 停服务后复制整个 `data/` 目录即可迁移；换数据目录会使用另一套数据库、模型和登录密码。
- `data/backups/` 保存数据库升级前的一致性备份。
- SQLite 使用 WAL，运行中直接复制文件可能不一致；请先停止服务。
