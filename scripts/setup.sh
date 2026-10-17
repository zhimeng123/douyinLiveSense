#!/usr/bin/env bash
# 一键准备：同步依赖、生成配置、下载并校验本地分析模型。
#
#   ./scripts/setup.sh              完整准备（含人物判断模型，需要 HF_TOKEN）
#   ./scripts/setup.sh --asr-only   只准备普通话转写与标点模型
#   ./scripts/setup.sh --help       查看说明
set -euo pipefail

cd "$(dirname "$0")/.."

asr_only=0
for arg in "$@"; do
  case "$arg" in
    --asr-only)
      asr_only=1
      ;;
    -h | --help)
      sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "未知参数：$arg" >&2
      echo "用法：./scripts/setup.sh [--asr-only]" >&2
      exit 2
      ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  cat >&2 <<'EOF'
未找到 uv。请先安装：https://docs.astral.sh/uv/
  macOS：brew install uv
  其他：curl -LsSf https://astral.sh/uv/install.sh | sh
EOF
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  cat >&2 <<'EOF'
未找到 FFmpeg。直播采集依赖它持续解码音频。
  macOS：brew install ffmpeg
EOF
  exit 1
fi

echo "==> 同步依赖（含分析扩展）"
uv sync --locked --extra analysis

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> 缺少配置，已从 .env.example 生成 .env"
fi

if [ "$asr_only" -eq 0 ]; then
  token="${HF_TOKEN:-}"
  if [ -z "$token" ]; then
    token="$(sed -n 's/^HF_TOKEN[[:space:]]*=[[:space:]]*//p' .env | tail -1 | tr -d "\"'" | tr -d '[:space:]')"
  fi
  if [ -z "$token" ]; then
    cat >&2 <<'EOF'

人物判断模型 pyannote/speaker-diarization-community-1 是受限模型，需要 Hugging Face 授权：

  1. 登录 https://huggingface.co/pyannote/speaker-diarization-community-1 并接受使用条款
  2. 在 https://huggingface.co/settings/tokens 创建只读（Read）Token
  3. 把 Token 填入本目录 .env 的 HF_TOKEN="..."
  4. 重新执行 ./scripts/setup.sh

如果暂时只要普通话转写与标点模型，可执行：./scripts/setup.sh --asr-only
EOF
    exit 1
  fi
fi

echo "==> 下载并校验本地模型（首次需要联网与数 GB 空间）"
model_args=()
if [ "$asr_only" -eq 1 ]; then
  model_args+=(--asr-only)
fi
uv run --locked --extra analysis --env-file .env \
  douyin-live-sense prepare-models ${model_args[@]+"${model_args[@]}"}

if [ "$asr_only" -eq 1 ]; then
  cat >&2 <<'EOF'

注意：本次只准备了普通话转写与标点模型，人物判断仍不可用。
补全人物判断需要配置 HF_TOKEN 后重新执行 ./scripts/setup.sh。
EOF
fi

cat <<'EOF'

准备完成。启动服务：

  ./scripts/run.sh

然后打开 http://127.0.0.1:8787
EOF
