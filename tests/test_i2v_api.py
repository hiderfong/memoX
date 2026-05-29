from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.web.api import app


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
