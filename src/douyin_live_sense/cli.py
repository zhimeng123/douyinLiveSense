import logging

import uvicorn

from .app import create_app
from .config import Settings
from .security import RedactingFormatter


def main() -> None:
    settings = Settings.from_env()
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(settings.cookie))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
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
