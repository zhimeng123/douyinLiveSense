import pytest

from douyin_live_sense.model_manager import (
    DownloadConflict,
    ModelManager,
    TokenRequired,
)


def test_start_download_requires_token_for_diarization(settings, monkeypatch):
    monkeypatch.setattr("douyin_live_sense.model_manager.analysis_supported", lambda: (True, ""))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    manager = ModelManager(settings)
    with pytest.raises(TokenRequired):
        manager.start_download(["diarization"])


def test_progress_counts_existing_bytes(settings):
    manager = ModelManager(settings)
    manager._job = {
        "id": "job-1",
        "state": "running",
        "parts": ["transcription"],
        "names": ["asr"],
        "total_bytes": 100,
        "started_at": "t",
        "finished_at": None,
        "error": "",
        "current": "asr",
    }
    target = settings.data_dir / "models" / "asr" / "rev"
    target.mkdir(parents=True)
    (target / "half.bin").write_bytes(b"x" * 40)
    result = manager.job()
    assert result["downloaded_bytes"] == 40
    assert 39 <= result["percent"] <= 41


def test_second_download_conflicts(settings, monkeypatch):
    monkeypatch.setattr(
        "douyin_live_sense.model_manager.ModelManager._run",
        lambda self, job, names: None,
    )
    manager = ModelManager(settings)
    manager._job = {
        "id": "job-1",
        "state": "running",
        "parts": ["transcription"],
        "names": ["asr"],
        "total_bytes": 1,
        "started_at": "t",
        "finished_at": None,
        "error": "",
        "current": "asr",
    }
    with pytest.raises(DownloadConflict):
        manager.start_download(["transcription"])


def test_failed_download_is_visible(settings, monkeypatch):
    def fail(settings, name, token):
        raise RuntimeError("network down")

    monkeypatch.setattr("douyin_live_sense.model_manager.prepare_model", fail)
    monkeypatch.setattr("douyin_live_sense.model_manager.expected_bytes", lambda *a, **k: 10)
    manager = ModelManager(settings)
    manager.start_download(["transcription"])
    manager._thread.join(timeout=2)
    status = manager.job()
    assert status["state"] == "failed" and "network down" in status["error"]
