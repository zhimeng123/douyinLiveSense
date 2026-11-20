import fnmatch
import threading
import uuid
from datetime import UTC, datetime

from .models import (
    IGNORE_PATTERNS,
    MODEL_REPOS,
    model_status,
    prepare_model,
    read_token,
    write_token,
)
from .platform_support import analysis_supported
from .security import redact


class TokenInvalid(Exception):
    pass


class TokenRequired(Exception):
    pass


class DependenciesMissing(Exception):
    pass


class DownloadConflict(Exception):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _matches_ignored(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in IGNORE_PATTERNS)


def expected_bytes(
    settings, names: list[str], token: str | None, revisions: dict[str, str] | None = None
) -> int:
    from huggingface_hub import HfApi

    total = 0
    api = HfApi(token=token)
    for name in names:
        info = api.model_info(MODEL_REPOS[name], files_metadata=True)
        revision = getattr(info, "sha", None)
        if revisions is not None and revision:
            revisions[name] = revision
        for sibling in getattr(info, "siblings", ()) or ():
            filename = getattr(sibling, "rfilename", "")
            size = getattr(sibling, "size", None)
            if filename and size and not _matches_ignored(filename):
                total += size
    return total


class ModelManager:
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.RLock()
        self._job = None
        self._thread = None

    def _token_details(self) -> dict:
        path = self.settings.data_dir / "models" / "hf_token"
        file_token = ""
        try:
            file_token = path.read_text().strip()
        except FileNotFoundError:
            pass
        token = file_token or read_token(self.settings)
        if not token:
            return {"configured": False, "masked": "", "source": "none"}
        masked = token[:4] + "..." + token[-4:] if len(token) > 8 else "***"
        return {
            "configured": True,
            "masked": masked,
            "source": "file" if file_token else "env",
        }

    def status(self) -> dict:
        supported, reason = analysis_supported()
        readiness = model_status(self.settings)
        return {
            "supported": supported,
            "reason": reason or readiness["reason"],
            "dependencies": readiness["components"]["dependencies"],
            "token": self._token_details(),
            "components": {
                "transcription": readiness["components"]["transcription"],
                "diarization": readiness["components"]["diarization"],
            },
            "job": self.job(),
        }

    def set_token(self, token: str) -> dict:
        token = token.strip()
        if not token:
            raise TokenInvalid("Token 不可为空。")
        try:
            from huggingface_hub import HfApi

            api = HfApi(token=token)
            api.whoami()
        except Exception as error:
            raise TokenInvalid("Token 无效。") from error
        gated = False
        warning = ""
        try:
            api.model_info(MODEL_REPOS["diarization"])
        except Exception as error:
            if "403" in str(error) or "gated" in str(error).lower():
                gated = True
                warning = "Token 有效，但尚未获得 Community-1 模型权限。"
            else:
                raise TokenInvalid("无法验证 Community-1 模型权限。") from error
        write_token(self.settings, token)
        return {"gated": gated, "warning": warning}

    def clear_token(self) -> None:
        path = self.settings.data_dir / "models" / "hf_token"
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def start_download(self, parts: list[str]) -> dict:
        valid = {"transcription", "diarization"}
        if not parts or any(part not in valid for part in parts):
            raise ValueError("下载部分只能是 transcription 或 diarization。")
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise DownloadConflict("已有模型下载任务正在运行。")
        supported, reason = analysis_supported()
        if not supported:
            raise DependenciesMissing(reason)
        readiness = model_status(self.settings)
        if not readiness["components"]["dependencies"]["ready"]:
            raise DependenciesMissing(readiness["components"]["dependencies"]["reason"])
        token = read_token(self.settings) or None
        if "diarization" in parts and not token:
            raise TokenRequired("准备人物模型需要 HF Token。")
        names = []
        for part in parts:
            names.extend(["asr", "punctuation"] if part == "transcription" else ["diarization"])
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise DownloadConflict("已有模型下载任务正在运行。")
            revisions = {}
            total_bytes = expected_bytes(self.settings, names, token, revisions)
            job = {
                "id": f"job-{uuid.uuid4().hex}",
                "state": "running",
                "parts": list(parts),
                "names": names,
                "total_bytes": total_bytes,
                "revisions": revisions,
                "started_at": _now(),
                "finished_at": None,
                "error": "",
                "current": names[0],
            }
            self._job = job
            self._thread = threading.Thread(target=self._run, args=(job, names, token), daemon=True)
            self._thread.start()
            return self.job()

    def _run(self, job: dict, names: list[str], token: str | None = None) -> None:
        try:
            for name in names:
                job["current"] = name
                prepare_model(self.settings, name, token)
        except Exception as error:
            job["state"] = "failed"
            job["error"] = redact(str(error), token or "")
        else:
            job["state"] = "completed"
            job["current"] = ""
        finally:
            job["finished_at"] = _now()

    def job(self) -> dict | None:
        with self._lock:
            if self._job is None:
                return None
            job = dict(self._job)
        downloaded = 0
        root = self.settings.data_dir / "models"
        for name in job["names"]:
            model_root = root / name
            if not model_root.is_dir():
                continue
            revision = job.get("revisions", {}).get(name)
            count_root = model_root / revision if revision else model_root
            if not count_root.is_dir():
                continue
            for path in count_root.rglob("*"):
                if (
                    path.is_file()
                    and ".cache" not in path.parts
                    and not _matches_ignored(path.name)
                ):
                    try:
                        downloaded += path.stat().st_size
                    except OSError:
                        pass
        total = job["total_bytes"]
        job["downloaded_bytes"] = downloaded
        job["percent"] = min(100, downloaded * 100 / total) if total else 0
        return job
