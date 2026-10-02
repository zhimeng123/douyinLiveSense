import hashlib
import importlib.util
import json
import os
from pathlib import Path

from .analysis_types import ModelsNotReady
from .config import Settings

MODEL_REPOS = {
    "diarization": "pyannote/speaker-diarization-community-1",
    "asr": "funasr/paraformer-zh",
    "punctuation": "funasr/ct-punc",
}
PIPELINE_VERSION = "mandarin-v1"


def read_manifest(data_dir: Path) -> dict:
    path = data_dir / "models" / "manifest.json"
    return json.loads(path.read_text()) if path.is_file() else {"models": {}}


def model_files_exist(data_dir: Path, entry: dict) -> bool:
    path = data_dir / entry["path"]
    return (
        (path / "config.yaml").is_file()
        and bool(entry.get("sha256"))
        and all((path / name).is_file() for name in entry["sha256"])
    )


def model_status(settings: Settings) -> dict:
    missing_packages = [
        name for name in ("torch", "funasr", "pyannote") if importlib.util.find_spec(name) is None
    ]
    try:
        manifest = read_manifest(settings.data_dir)
        missing_models = [
            name
            for name in MODEL_REPOS
            if name not in manifest["models"]
            or not model_files_exist(settings.data_dir, manifest["models"][name])
        ]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"ready": False, "reason": f"模型清单不可读：{error}", "models": MODEL_REPOS}
    reasons = []
    if missing_packages:
        reasons.append("请执行 uv sync --locked --extra analysis 安装分析依赖")
    if missing_models:
        reasons.append("未准备模型：" + "、".join(missing_models) + "；请执行 prepare-models")
    return {"ready": not reasons, "reason": "；".join(reasons), "models": MODEL_REPOS}


def pipeline_config(settings: Settings) -> dict:
    models = read_manifest(settings.data_dir).get("models", {})
    if not all(name in models for name in MODEL_REPOS):
        models = {}
    return {
        "version": PIPELINE_VERSION,
        "providers": {"diarization": "pyannote-local", "transcription": "funasr-local"},
        "models": {name: entry["revision"] for name, entry in models.items()},
        "speaker_similarity": settings.speaker_similarity,
        "speaker_margin": settings.speaker_margin,
        "speaker_min_seconds": 2.0,
        "pause_seconds": 0.35,
        "max_turn_seconds": 30,
    }


def config_key(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:24]


def prepare_models(settings: Settings, asr_only: bool = False) -> None:
    # This command is the only place that resolves mutable Hub aliases/downloads.
    from huggingface_hub import HfApi, snapshot_download

    root = settings.data_dir / "models"
    root.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest(settings.data_dir)
    token = os.environ.get("HF_TOKEN")
    names = ["asr", "punctuation"] if asr_only else ["asr", "punctuation", "diarization"]
    for name in names:
        existing = manifest["models"].get(name)
        if existing and model_files_exist(settings.data_dir, existing):
            target = settings.data_dir / existing["path"]
            if all(
                file_hash(target / file) == digest for file, digest in existing["sha256"].items()
            ):
                print(f"{name}：复用已固定版本 {existing['revision']}", flush=True)
                continue
        if name == "diarization" and not token:
            raise ModelsNotReady(
                "普通话模型已准备；Community-1 需要先接受 Hugging Face 条款并配置 HF_TOKEN。"
            )
        repo = MODEL_REPOS[name]
        revision = existing["revision"] if existing else HfApi(token=token).model_info(repo).sha
        target = root / name / revision
        print(f"准备 {repo}，版本 {revision}", flush=True)
        snapshot_download(
            repo,
            revision=revision,
            local_dir=target,
            token=token,
            force_download=bool(existing),
            ignore_patterns=["*.onnx", "*.zip", "*.tar*", "*.wav", "*.mp3"],
        )
        hashes = {
            str(path.relative_to(target)): file_hash(path)
            for path in sorted(target.rglob("*"))
            if path.is_file() and ".cache" not in path.parts
        }
        manifest["models"][name] = {
            "repo": repo,
            "revision": revision,
            "path": str(target.relative_to(settings.data_dir)),
            "sha256": hashes,
        }
        temporary = root / "manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(temporary, root / "manifest.json")
    print("模型文件准备完成。分析进程将使用本地文件。", flush=True)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while data := file.read(1024 * 1024):
            digest.update(data)
    return digest.hexdigest()
