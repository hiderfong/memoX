import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.web.api import app
from web.routers import imaging as imaging_router
from storage.persistence import PersistenceStore


@pytest.fixture
def anon_client(monkeypatch):
    """Skip auth by setting up fake state and dependency override."""
    from unittest.mock import MagicMock

    from src.web import api as api_mod

    monkeypatch.setattr(api_mod, "_config", None)
    fake_auth = MagicMock()
    fake_auth.validate_token = MagicMock(return_value={
        "username": "test", "role": "admin", "display_name": "Test",
    })
    # Middleware path: _get_auth_from_request reads app.state._auth_manager
    monkeypatch.setattr(app.state, "_auth_manager", fake_auth, raising=False)
    # Route path: dependency override for _get_auth_from_request
    from src.auth import _get_auth_from_request
    monkeypatch.setitem(app.dependency_overrides, _get_auth_from_request, lambda request: fake_auth)
    return TestClient(app)


@pytest.fixture
def media_store(monkeypatch, tmp_path):
    import storage.persistence as persistence_mod

    store = PersistenceStore(tmp_path / "media.db")
    monkeypatch.setattr(persistence_mod, "_store", store)
    yield store
    store.close()
    monkeypatch.setattr(persistence_mod, "_store", None)


def test_i2v_endpoint_503_when_not_initialized(anon_client):
    with patch("imaging.get_i2v_client", return_value=None):
        r = anon_client.post("/api/videos/i2v", json={
            "image_url": "https://x/a.png", "prompt": "p"
        })
    assert r.status_code == 503


def test_i2v_endpoint_success(anon_client):
    fake = AsyncMock()
    fake.generate = AsyncMock(return_value="https://cdn/vid.mp4")
    fake.generate_from_file = AsyncMock()
    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/i2v", json={
            "image_url": "https://x/a.png",
            "prompt": "slow zoom",
            "duration": 5,
            "resolution": "720P",
        })
    assert r.status_code == 200
    data = r.json()
    assert data["url"] == "https://cdn/vid.mp4"
    assert data["image_url"] == "https://x/a.png"
    fake.generate.assert_awaited_once_with(
        image_url="https://x/a.png",
        prompt="slow zoom",
        resolution="720P",
        duration=5,
        negative_prompt=None,
        last_frame_url=None,
        driving_audio_url=None,
        first_clip_url=None,
        prompt_extend=None,
        watermark=None,
        seed=None,
    )
    fake.generate_from_file.assert_not_awaited()


def test_i2v_endpoint_persists_media_asset(anon_client, media_store):
    fake = AsyncMock()
    fake.generate = AsyncMock(return_value="https://cdn/vid.mp4")
    fake.generate_from_file = AsyncMock()

    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/i2v", json={
            "image_url": "https://x/a.png",
            "prompt": "slow zoom",
            "duration": 5,
            "resolution": "720P",
        })

    assert r.status_code == 200
    data = r.json()
    assert data["asset_id"].startswith("media_")
    assets = media_store.list_media_assets(operation="i2v")
    assert len(assets) == 1
    assert assets[0]["url"] == "https://cdn/vid.mp4"
    assert assets[0]["source_url"] == "https://x/a.png"
    assert assets[0]["parameters"]["duration"] == 5


def test_enqueue_i2v_job_returns_queued_asset(anon_client, media_store, monkeypatch):
    fake = AsyncMock()

    def capture(coro):
        coro.close()

    monkeypatch.setattr(imaging_router, "_schedule_media_task", capture)
    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/i2v/jobs", json={
            "image_url": "https://x/a.png",
            "prompt": "slow zoom",
            "duration": 5,
        })

    assert r.status_code == 200
    asset = r.json()["asset"]
    assert asset["status"] == "queued"
    assert asset["operation"] == "i2v"
    assert asset["parameters"]["duration"] == 5
    persisted = anon_client.get(f"/api/videos/assets/{asset['id']}")
    assert persisted.status_code == 200
    assert persisted.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_i2v_asset_job_updates_asset_to_success(media_store):
    fake = AsyncMock()
    fake.generate = AsyncMock(return_value="https://cdn/job.mp4")
    fake.generate_from_file = AsyncMock()
    asset = media_store.save_media_asset({
        "id": "media_job_1",
        "kind": "video",
        "status": "queued",
        "operation": "i2v",
        "source_url": "https://x/a.png",
        "prompt": "slow zoom",
    })

    with patch("imaging.get_i2v_client", return_value=fake):
        await imaging_router._run_i2v_asset_job(asset["id"], imaging_router.I2VRequest(
            image_url="https://x/a.png",
            prompt="slow zoom",
            duration=5,
        ))

    updated = media_store.get_media_asset(asset["id"])
    assert updated["status"] == "success"
    assert updated["url"] == "https://cdn/job.mp4"
    assert updated["parameters"]["duration"] == 5


def test_i2v_endpoint_500_on_error(anon_client):
    fake = AsyncMock()
    fake.generate = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/i2v", json={
            "image_url": "https://x/a.png", "prompt": "p"
        })
    assert r.status_code == 500
    assert "boom" in r.json()["detail"]


def test_i2v_endpoint_uploads_local_file_to_dashscope(anon_client, monkeypatch, tmp_path):
    from src.web import api as api_mod

    monkeypatch.setattr(api_mod, "UPLOADS_DIR", tmp_path)
    (tmp_path / "local.png").write_bytes(b"fake-image")
    fake = AsyncMock()
    fake.generate_from_file = AsyncMock(return_value="https://cdn/local.mp4")
    fake.generate = AsyncMock()

    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/i2v", json={
            "image_url": "/api/files/local.png?expires=1&signature=sig",
            "prompt": "slow zoom",
        })

    assert r.status_code == 200
    data = r.json()
    assert data["url"] == "https://cdn/local.mp4"
    assert data["input_mode"] == "dashscope_upload"
    args, kwargs = fake.generate_from_file.await_args
    assert args[0] == tmp_path / "local.png"
    assert kwargs["prompt"] == "slow zoom"
    fake.generate.assert_not_awaited()


def test_i2v_batch_returns_partial_failures(anon_client):
    fake = AsyncMock()
    fake.generate = AsyncMock(side_effect=["https://cdn/ok.mp4", RuntimeError("bad source")])
    fake.generate_from_file = AsyncMock()

    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/i2v/batch", json={
            "items": [
                {"image_url": "https://x/a.png", "prompt": "p1"},
                {"image_url": "https://x/b.png", "prompt": "p2"},
            ]
        })

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["succeeded"] == 1
    assert data["failed"] == 1
    assert data["results"][0]["ok"] is True
    assert data["results"][0]["url"] == "https://cdn/ok.mp4"
    assert data["results"][1]["ok"] is False
    assert "bad source" in data["results"][1]["error"]


def test_i2v_batch_persists_success_and_failure_assets(anon_client, media_store):
    fake = AsyncMock()
    fake.generate = AsyncMock(side_effect=["https://cdn/ok.mp4", RuntimeError("bad source")])
    fake.generate_from_file = AsyncMock()

    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/i2v/batch", json={
            "items": [
                {"image_url": "https://x/a.png", "prompt": "p1"},
                {"image_url": "https://x/b.png", "prompt": "p2"},
            ]
        })

    assert r.status_code == 200
    data = r.json()
    assert data["results"][0]["asset_id"].startswith("media_")
    assert data["results"][1]["asset_id"].startswith("media_")
    assets = media_store.list_media_assets(operation="i2v", limit=10)
    assert [asset["status"] for asset in assets] == ["failed", "success"]
    assert assets[0]["error"] == "bad source"
    assert assets[1]["url"] == "https://cdn/ok.mp4"


def test_enqueue_i2v_batch_jobs_returns_queued_assets(anon_client, media_store, monkeypatch):
    fake = AsyncMock()

    def capture(coro):
        coro.close()

    monkeypatch.setattr(imaging_router, "_schedule_media_task", capture)
    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/i2v/batch/jobs", json={
            "items": [
                {"image_url": "https://x/a.png", "prompt": "p1"},
                {"image_url": "https://x/b.png", "prompt": "p2"},
            ]
        })

    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 2
    assert [asset["status"] for asset in data["assets"]] == ["queued", "queued"]
    assert len(media_store.list_media_assets(operation="i2v", status="queued")) == 2


def test_video_edit_endpoint_success(anon_client):
    fake = AsyncMock()
    fake.edit = AsyncMock(return_value="https://cdn/edited.mp4")
    fake.edit_from_file = AsyncMock()

    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/edit", json={
            "video_url": "https://x/in.mp4",
            "prompt": "make it cinematic",
            "reference_image_urls": ["https://x/ref.png"],
            "resolution": "720P",
            "ratio": "16:9",
            "duration": 5,
        })

    assert r.status_code == 200
    data = r.json()
    assert data["url"] == "https://cdn/edited.mp4"
    assert data["input_mode"] == "url"
    fake.edit.assert_awaited_once_with(
        video_url="https://x/in.mp4",
        prompt="make it cinematic",
        reference_image_urls=["https://x/ref.png"],
        resolution="720P",
        ratio="16:9",
        duration=5,
        negative_prompt=None,
        audio_setting=None,
        prompt_extend=None,
        watermark=None,
        seed=None,
    )
    fake.edit_from_file.assert_not_awaited()


def test_video_edit_persists_asset_and_list_delete_endpoints(anon_client, media_store):
    fake = AsyncMock()
    fake.edit = AsyncMock(return_value="https://cdn/edited.mp4")
    fake.edit_from_file = AsyncMock()

    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/edit", json={
            "video_url": "https://x/in.mp4",
            "prompt": "make it cinematic",
            "reference_image_urls": ["https://x/ref.png"],
            "resolution": "720P",
        })

    assert r.status_code == 200
    asset_id = r.json()["asset_id"]
    listed = anon_client.get("/api/videos/assets", params={"operation": "video_edit"})
    assert listed.status_code == 200
    assets = listed.json()["assets"]
    assert [asset["id"] for asset in assets] == [asset_id]
    assert assets[0]["url"] == "https://cdn/edited.mp4"
    assert assets[0]["parameters"]["reference_image_urls"] == ["https://x/ref.png"]

    deleted = anon_client.delete(f"/api/videos/assets/{asset_id}")
    assert deleted.status_code == 200
    assert anon_client.get("/api/videos/assets").json()["assets"] == []


def test_enqueue_video_edit_job_returns_queued_asset(anon_client, media_store, monkeypatch):
    fake = AsyncMock()

    def capture(coro):
        coro.close()

    monkeypatch.setattr(imaging_router, "_schedule_media_task", capture)
    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/edit/jobs", json={
            "video_url": "https://x/in.mp4",
            "prompt": "make it cinematic",
            "resolution": "720P",
        })

    assert r.status_code == 200
    asset = r.json()["asset"]
    assert asset["status"] == "queued"
    assert asset["operation"] == "video_edit"
    assert asset["source_url"] == "https://x/in.mp4"


def test_retry_failed_media_asset_requeues_existing_record(anon_client, media_store, monkeypatch):
    fake = AsyncMock()

    def capture(coro):
        coro.close()

    asset = media_store.save_media_asset({
        "id": "media_retry",
        "kind": "video",
        "status": "failed",
        "operation": "i2v",
        "source_url": "https://x/a.png",
        "prompt": "slow zoom",
        "error": "temporary error",
        "parameters": {"duration": 5, "resolution": "720P"},
    })

    monkeypatch.setattr(imaging_router, "_schedule_media_task", capture)
    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post(f"/api/videos/assets/{asset['id']}/retry")

    assert r.status_code == 200
    queued = r.json()["asset"]
    assert queued["id"] == asset["id"]
    assert queued["status"] == "queued"
    assert queued["error"] == ""
    assert queued["parameters"]["retry_count"] == 1
    assert media_store.get_media_asset(asset["id"])["status"] == "queued"


def test_retry_rejects_running_media_asset(anon_client, media_store):
    asset = media_store.save_media_asset({
        "id": "media_running",
        "kind": "video",
        "status": "running",
        "operation": "i2v",
        "source_url": "https://x/a.png",
        "prompt": "slow zoom",
    })

    with patch("imaging.get_i2v_client", return_value=AsyncMock()):
        r = anon_client.post(f"/api/videos/assets/{asset['id']}/retry")

    assert r.status_code == 409


@pytest.mark.asyncio
async def test_media_scheduler_limits_concurrent_jobs(monkeypatch):
    monkeypatch.setattr(imaging_router, "MAX_CONCURRENT_MEDIA_TASKS", 1)
    imaging_router._reset_media_task_scheduler_for_tests()
    gate = asyncio.Event()
    events: list[str] = []

    async def job(name: str) -> None:
        events.append(f"start:{name}")
        await gate.wait()
        events.append(f"done:{name}")

    imaging_router._schedule_media_task(job("a"))
    imaging_router._schedule_media_task(job("b"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    status = imaging_router.media_task_queue_status()
    assert events == ["start:a"]
    assert status["runtime_running"] == 1
    assert status["runtime_pending"] == 1

    gate.set()
    for _ in range(20):
        if imaging_router.media_task_queue_status()["runtime_tracked"] == 0:
            break
        await asyncio.sleep(0.01)

    assert events == ["start:a", "done:a", "start:b", "done:b"]
    assert imaging_router.media_task_queue_status()["runtime_running"] == 0
    assert imaging_router.media_task_queue_status()["runtime_pending"] == 0
    imaging_router._reset_media_task_scheduler_for_tests()


def test_video_jobs_status_reports_runtime_and_persisted_counts(anon_client, media_store):
    media_store.save_media_asset({
        "id": "media_queued",
        "kind": "video",
        "status": "queued",
        "operation": "i2v",
        "source_url": "https://x/a.png",
        "prompt": "slow zoom",
    })
    media_store.save_media_asset({
        "id": "media_running",
        "kind": "video",
        "status": "running",
        "operation": "video_edit",
        "source_url": "https://x/in.mp4",
        "prompt": "make cinematic",
    })

    r = anon_client.get("/api/videos/jobs/status")

    assert r.status_code == 200
    data = r.json()
    assert data["max_concurrent"] >= 1
    assert data["persisted_queued"] == 1
    assert data["persisted_running"] == 1
    assert "runtime_pending" in data
    assert "runtime_running" in data


def test_mark_interrupted_media_assets_marks_active_records_failed(media_store):
    media_store.save_media_asset({
        "id": "media_queued",
        "kind": "video",
        "status": "queued",
        "operation": "i2v",
        "source_url": "https://x/a.png",
        "prompt": "slow zoom",
    })
    media_store.save_media_asset({
        "id": "media_running",
        "kind": "video",
        "status": "running",
        "operation": "video_edit",
        "source_url": "https://x/in.mp4",
        "prompt": "make it cinematic",
    })
    media_store.save_media_asset({
        "id": "media_success",
        "kind": "video",
        "status": "success",
        "operation": "i2v",
        "url": "https://cdn/out.mp4",
        "source_url": "https://x/ok.png",
        "prompt": "ok",
    })

    count = imaging_router.mark_interrupted_media_assets()

    assert count == 2
    queued = media_store.get_media_asset("media_queued")
    running = media_store.get_media_asset("media_running")
    success = media_store.get_media_asset("media_success")
    assert queued["status"] == "failed"
    assert running["status"] == "failed"
    assert "可点击重试" in queued["error"]
    assert queued["parameters"]["interrupted_status"] == "queued"
    assert running["parameters"]["interrupted_status"] == "running"
    assert success["status"] == "success"


def test_document_media_assets_include_local_and_remote_images(anon_client, monkeypatch, tmp_path):
    from src.web import api as api_mod

    class FakeRag:
        def get_document_chunks(self, doc_id):
            return [
                {
                    "id": "c1",
                    "content": "remote ![shot](https://example.com/shot.png)",
                    "chunk_index": 0,
                    "metadata": {
                        "type": "image",
                        "path": str(tmp_path / "local.png"),
                        "filename": "local.png",
                    },
                }
            ]

    monkeypatch.setattr(api_mod, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "_rag_engine", FakeRag())
    (tmp_path / "local.png").write_bytes(b"fake-image")

    r = anon_client.get("/api/documents/doc1/media-assets")

    assert r.status_code == 200
    assets = r.json()["assets"]
    assert {asset["kind"] for asset in assets} == {"local_image", "remote_image"}
    local = next(asset for asset in assets if asset["kind"] == "local_image")
    assert local["url"].endswith("/api/files/local.png")
    assert local["access"] == "bearer"
