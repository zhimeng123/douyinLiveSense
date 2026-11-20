import argparse
import logging
import os

import uvicorn

from .analysis_types import ModelsNotReady
from .app import create_app
from .auth import AuthStore
from .config import Settings
from .models import prepare_models
from .security import RedactingFormatter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本机抖音直播守卫与普通话分析")
    commands = parser.add_subparsers(dest="command")
    init_auth = commands.add_parser("init-auth", help="创建管理员登录密码")
    init_auth.add_argument(
        "--password", help="管理员密码，未提供时使用 DLS_ADMIN_PASSWORD 或随机密码"
    )
    reset_password = commands.add_parser("reset-password", help="重置管理员登录密码")
    reset_password.add_argument(
        "--password", help="新管理员密码，未提供时使用 DLS_ADMIN_PASSWORD 或随机密码"
    )
    prepare = commands.add_parser("prepare-models", help="下载并固定本地分析模型")
    prepare.add_argument("--asr-only", action="store_true", help="仅准备普通话与标点模型")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(settings.cookie))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    store = AuthStore(settings.auth_path)
    if args.command == "init-auth":
        if store.exists():
            print("登录密码已存在，未覆盖。")
            return 0
        password = args.password or os.environ.get("DLS_ADMIN_PASSWORD")
        password = store.create(password)
        print(f"初始登录密码：{password}")
        return 0
    if args.command == "reset-password":
        password = args.password or os.environ.get("DLS_ADMIN_PASSWORD")
        if not store.exists():
            parser.exit(1, "尚未设置登录密码，请先运行 ./scripts/setup.sh 或 init-auth。\n")
        password = store.reset(password)
        print(f"新登录密码：{password}")
        return 0
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
    if not store.exists():
        parser.exit(1, "尚未设置登录密码，请先运行 ./scripts/setup.sh 或 init-auth。\n")
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=1,
        log_config=None,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    main()
