import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from douyin_live_sense.analysis_types import ModelsNotReady
from douyin_live_sense.models import MODEL_REPOS, file_hash, model_status, prepare_models
from douyin_live_sense.providers import diarization_device


def prepared(settings):
    entries = {}
    for name, repo in MODEL_REPOS.items():
        path = settings.data_dir / "models" / name / "fixed-revision"
        path.mkdir(parents=True)
        (path / "config.yaml").write_text("configuration")
        (path / "model.bin").write_bytes(b"weights")
        entries[name] = {
            "repo": repo,
            "revision": "fixed-revision",
            "path": str(path.relative_to(settings.data_dir)),
            "sha256": {file.name: file_hash(file) for file in path.iterdir()},
        }
    (settings.data_dir / "models" / "manifest.json").write_text(json.dumps({"models": entries}))
    return entries


def test_missing_weight_is_not_ready(settings, monkeypatch):
    entries = prepared(settings)
    monkeypatch.setattr("importlib.util.find_spec", lambda _: object())
    assert model_status(settings)["ready"]
    (settings.data_dir / entries["asr"]["path"] / "model.bin").unlink()
    status = model_status(settings)
    assert not status["ready"] and "asr" in status["reason"]


def test_prepared_models_reuse_without_token_or_network(settings, monkeypatch):
    prepared(settings)
    monkeypatch.delenv("HF_TOKEN", raising=False)

    def unexpected(*args, **kwargs):
        pytest.fail("已准备模型不应访问网络")

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(HfApi=unexpected, snapshot_download=unexpected),
    )
    prepare_models(settings)


def test_corrupt_weight_repairs_same_revision(settings, monkeypatch):
    entries = prepared(settings)
    target = settings.data_dir / entries["asr"]["path"]
    (target / "model.bin").write_bytes(b"corrupt")
    calls = []

    def download(repo, **kwargs):
        calls.append((repo, kwargs))
        (kwargs["local_dir"] / "model.bin").write_bytes(b"weights")

    def unexpected(*args, **kwargs):
        pytest.fail("修复模型不应解析最新版本")

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(HfApi=unexpected, snapshot_download=download),
    )
    prepare_models(settings, asr_only=True)
    assert len(calls) == 1
    assert calls[0][1]["revision"] == "fixed-revision"
    assert calls[0][1]["force_download"]
    assert (target / "model.bin").read_bytes() == b"weights"


def test_missing_diarization_requires_token(settings, monkeypatch):
    entries = prepared(settings)
    (settings.data_dir / entries["diarization"]["path"] / "model.bin").unlink()
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setitem(
        sys.modules, "huggingface_hub", SimpleNamespace(HfApi=None, snapshot_download=None)
    )
    with pytest.raises(ModelsNotReady, match="HF_TOKEN"):
        prepare_models(settings)


@pytest.mark.parametrize(
    ("cuda", "mps", "expected"), [(True, True, "cuda"), (False, True, "mps"), (False, False, "cpu")]
)
def test_auto_device_uses_available_accelerator(settings, monkeypatch, cuda, mps, expected):
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
        device=lambda name: name,
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    assert diarization_device(settings) == expected
    assert diarization_device(replace(settings, diarization_device="cpu")) == "cpu"
    if not mps:
        with pytest.raises(ModelsNotReady, match="不支持"):
            diarization_device(replace(settings, diarization_device="mps"))
