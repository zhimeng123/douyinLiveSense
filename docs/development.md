# 开发与验收

## 环境

```sh
git clone https://github.com/zhimeng123/douyinLiveSense.git
cd douyinLiveSense
uv sync --locked --group dev
uv sync --locked --extra analysis   # 需要跑分析相关测试时
```

Python 版本由 `.python-version` 固定为 3.11，依赖由 `uv.lock` 固定。网页没有构建步骤，直接改 `src/douyin_live_sense/static/` 下的文件。

## 检查命令

```sh
uv run --locked --extra analysis pytest -q
uv run --locked --extra analysis ruff check src tests tools
uv run --locked --extra analysis ruff format --check src tests tools
uv run --locked --extra analysis python -m compileall -q src tests tools
node --check src/douyin_live_sense/static/app.js
node --check src/douyin_live_sense/static/login.js
bash -n scripts/setup.sh
bash -n scripts/run.sh
./scripts/setup.sh --check
```

与 [.github/workflows/tests.yml](../.github/workflows/tests.yml) 一致，CI 在 `ubuntu-22.04` 上只装核心与 dev 依赖，因此测试不能依赖分析扩展。

自动化测试使用模拟直播源、可控分析适配器、真实 FFmpeg 和临时目录，覆盖守卫、切段、重启恢复、手动/实时切换、跨片发言、独立复核、队列进度、模型准备及异常提交。模拟结果不证明线上抖音可用或真实模型准确率。

## 真实验收

三种工具都需要联网或已准备模型，报告与音频保存在被忽略的 `data/` 中。运行前先停止使用同一数据目录的其他服务。

```sh
# 真实 StreamGet + FFmpeg：至少 30 分钟采集，用专用转发器模拟断线
uv run --locked --extra analysis --env-file .env \
  python tools/live_acceptance.py --room 123456789 --minutes 30

# 独立数据目录：先手动采集，再开启实时补分析，断线恢复后补齐尾段
uv run --locked --extra analysis --env-file .env \
  python tools/analysis_acceptance.py --minutes 30

# 复用已有场次，验证整场与单片复核版本
uv run --locked --extra analysis --env-file .env \
  python tools/archive_acceptance.py --session-id SESSION_ID
```

`live_acceptance.py` 支持 `--interrupt-after`（默认 180 秒）与 `--outage-seconds`（默认 12 秒）；`analysis_acceptance.py` 支持 `--manual-seconds`、`--disable-after`、`--interrupt-after`、`--outage-seconds` 和 `--port`（默认 8790）。转发器只中断专用连接，不修改系统网络。

房间未开播或触发风控时，真实验收可能等待或失败。

## 记录

- [VERIFICATION.md](../VERIFICATION.md)：已执行的模拟测试、真实直播、真实模型与网页验证记录。
- [分析与档案计划](analysis-and-archive-plan.md)：整场分析、单片复核和分析队列的设计方案。
- [README 重构设计](superpowers/specs/2026-11-23-readme-restructure-design.md)：本文档体系的入口页拆分方案。
