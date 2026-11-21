import zipfile
from urllib.parse import unquote

from conftest import FakeCapture, FakeSource, login
from fastapi.testclient import TestClient

from douyin_live_sense.app import create_app
from douyin_live_sense.audio import SegmentWriter
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.storage import Storage, utc_now


def make_client(settings, auth):
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    return store, TestClient(create_app(settings, monitor, auth=auth), base_url="http://127.0.0.1")


def add_ready_segments(store, settings, session_id, count):
    segments = []
    for index in range(count):
        writer = SegmentWriter(store, settings, session_id)
        writer.feed(bytes([index + 1, 0]) * 8, utc_now())
        segment_id = writer.segment["id"]
        writer.close()
        segments.append(store.segment(segment_id))
    return segments


def test_session_zip_contains_all_ready_segments(settings, auth):
    store, client = make_client(settings, auth)
    session = store.create_session("123", "live", "主播/甲", "标题")
    segments = add_ready_segments(store, settings, session["id"], 3)
    source_data = {segment["id"]: store.segment_path(segment).read_bytes() for segment in segments}
    try:
        with client:
            login(client)
            response = client.get(f"/api/sessions/{session['id']}/audio.zip")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/zip"
            assert "主播甲-" in unquote(response.headers["content-disposition"])
            import io

            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert archive.namelist() == [
                    f"{segment['sequence']:06d}_{segment['id']}.wav" for segment in segments
                ]
                for segment in segments:
                    member = f"{segment['sequence']:06d}_{segment['id']}.wav"
                    assert archive.read(member) == source_data[segment["id"]]
    finally:
        client.close()
        store.close()


def test_session_zip_skips_pending_segments(settings, auth):
    store, client = make_client(settings, auth)
    session = store.create_session("123", "live", "主播", "标题")
    ready = add_ready_segments(store, settings, session["id"], 1)[0]
    store.reserve_segment(session["id"], "pending-epoch", utc_now(), settings.sample_rate)
    try:
        with client:
            login(client)
            response = client.get(f"/api/sessions/{session['id']}/audio.zip")
            assert response.status_code == 200
            import io

            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert archive.namelist() == [f"{ready['sequence']:06d}_{ready['id']}.wav"]
    finally:
        client.close()
        store.close()


def test_session_zip_404_when_empty(settings, auth):
    store, client = make_client(settings, auth)
    session = store.create_session("123", "live", "主播", "标题")
    try:
        with client:
            login(client)
            response = client.get(f"/api/sessions/{session['id']}/audio.zip")
            assert response.status_code == 404
            assert response.json()["detail"] == "此场次尚无已保存完成的音频切片。"
    finally:
        client.close()
        store.close()


def test_session_zip_404_unknown_session(settings, auth):
    store, client = make_client(settings, auth)
    try:
        with client:
            login(client)
            response = client.get("/api/sessions/missing/audio.zip")
            assert response.status_code == 404
            assert response.json()["detail"] == "场次不存在。"
    finally:
        client.close()
        store.close()


def test_zip_temp_file_is_cleaned_up(settings, auth):
    store, client = make_client(settings, auth)
    session = store.create_session("123", "live", "主播", "标题")
    add_ready_segments(store, settings, session["id"], 1)
    try:
        with client:
            login(client)
            response = client.get(f"/api/sessions/{session['id']}/audio.zip")
            assert response.status_code == 200
            assert not list((settings.data_dir / "tmp").glob("*.zip"))
    finally:
        client.close()
        store.close()


def test_zip_download_requires_login(settings, auth):
    store, client = make_client(settings, auth)
    session = store.create_session("123", "live", "主播", "标题")
    add_ready_segments(store, settings, session["id"], 1)
    try:
        with client:
            response = client.get(f"/api/sessions/{session['id']}/audio.zip")
            assert response.status_code == 401
    finally:
        client.close()
        store.close()
