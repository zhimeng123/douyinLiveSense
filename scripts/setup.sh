#!/usr/bin/env bash
# Prepare dependencies and initialize the single administrator password.
set -euo pipefail

cd "$(dirname "$0")/.."

record_only=0
check_only=0
password_arg=()

usage() {
  cat <<'EOF'
用法：./scripts/setup.sh [--record-only] [--check] [--password TEXT]

默认安装核心依赖和当前平台支持的分析依赖，但不下载模型。
--record-only 只安装核心录音依赖。
--check        只显示平台和安装模式，不修改环境。
--password     设置初始管理员密码。
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --record-only)
      record_only=1
      shift
      ;;
    --check)
      check_only=1
      shift
      ;;
    --password)
      if [ "$#" -lt 2 ]; then
        echo "--password 需要一个值。" >&2
        exit 2
      fi
      password_arg=(--password "$2")
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

system="$(uname -s)"
arch="$(uname -m)"
analysis=1
analysis_reason=""
if [ "$system" = "Linux" ] && { [ "$arch" = "aarch64" ] || [ "$arch" = "arm64" ]; }; then
  analysis=0
  analysis_reason="Linux ARM64 缺少 torchcodec"
fi
if [ "$record_only" -eq 1 ]; then
  analysis=0
  analysis_reason="用户选择仅录音"
fi

if [ "$check_only" -eq 1 ]; then
  if [ "$analysis" -eq 1 ]; then
    echo "安装模式：完整识别"
  else
    echo "安装模式：仅录音（${analysis_reason:-平台不支持完整识别}）"
  fi
  exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  cat >&2 <<'EOF'
未找到 uv。请先安装：https://docs.astral.sh/uv/
  macOS：brew install uv
  其他：curl -LsSf https://astral.sh/uv/install.sh | sh
EOF
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "未找到 FFmpeg。直播采集依赖它持续解码音频。" >&2
  case "$system" in
    Darwin) echo "  macOS：brew install ffmpeg" >&2 ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        echo "  Debian/Ubuntu：sudo apt update && sudo apt install -y ffmpeg" >&2
      elif command -v dnf >/dev/null 2>&1; then
        echo "  Fedora：sudo dnf install -y ffmpeg" >&2
      elif command -v pacman >/dev/null 2>&1; then
        echo "  Arch：sudo pacman -S ffmpeg" >&2
      fi
      ;;
  esac
  exit 1
fi

echo "==> 同步核心依赖"
uv sync --locked
if [ "$analysis" -eq 1 ]; then
  echo "==> 同步分析扩展"
  uv sync --locked --extra analysis
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> 缺少配置，已从 .env.example 生成 .env"
fi

echo "==> 初始化管理员登录密码"
uv run --no-sync --env-file .env douyin-live-sense init-auth "${password_arg[@]}"

cat <<'EOF'

准备完成。启动服务：

  ./scripts/run.sh

然后打开 http://127.0.0.1:8787
初始密码已在上面打印。
EOF
