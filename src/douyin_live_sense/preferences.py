import json
import os
from pathlib import Path


class Preferences:
    def __init__(self, path: Path):
        self.path = path

    def enabled(self) -> bool:
        try:
            value = json.loads(self.path.read_text()).get("recognition", False)
        except (OSError, ValueError, AttributeError):
            return False
        return value is True

    def set_enabled(self, value: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "recognition": bool(value)}, ensure_ascii=False)
        )
        os.replace(temporary, self.path)
