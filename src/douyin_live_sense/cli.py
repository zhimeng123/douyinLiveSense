import argparse
import logging
import os

import uvicorn

from .analysis_types import ModelsNotReady
from .app import create_app
from .config import Settings
from .models import prepare_models
from .security import RedactingFormatter


def main() -> None:
    parser = argparse.ArgumentParser(description="本机抖音直播守卫与普通话分析")
    commands = parser.add_subparsers(dest="command")
    prepare = commands.add_parser("prepare-models", help="下载并固定本地分析模型")
    prepare.add_argument("--asr-only", action="store_true", help="仅准备普通话与标点模型")
    args = parser.parse_args()
    settings = Settings.from_env()
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(settings.cookie))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.command == "prepare-models":
        try:
            prepare_models(settings, args.asr_only)
        except (ModelsNotReady, ImportError) as error:
            parser.exit(1, f"{error}\n")
        except Exception as error:
            # Preparation is a command boundary: fail visibly without logging a token.
            from .security import redact

            parser.exit(
                1,
                redact(
                    f"模型准备失败：{type(error).__name__}: {error}\n", os.getenv("HF_TOKEN", "")
                ),
            )
        return
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=8787,
        workers=1,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
