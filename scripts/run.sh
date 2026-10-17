#!/usr/bin/env bash
# 启动本机服务（网页 + 采集 + 分析），默认 http://127.0.0.1:8787。
#
#   ./scripts/run.sh                    启动服务
#   ./scripts/run.sh prepare-models     透传给 douyin-live-sense 子命令
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv。请先安装：https://docs.astral.sh/uv/ ，或运行 ./scripts/setup.sh" >&2
  exit 1
fi

if [ ! -f .env ]; then
  echo "缺少 .env。请先运行 ./scripts/setup.sh 完成准备。" >&2
  exit 1
fi

exec uv run --locked --extra analysis --env-file .env douyin-live-sense "$@"
