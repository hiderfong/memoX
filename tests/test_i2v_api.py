import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from src.web.api import app


@pytest.fixture
def anon_client(monkeypatch):
    # 跳过认证：patch get_auth_manager 使其返回总是通过验证的 mock
    from src.web import api as api_mod
    from unittest.mock import MagicMock
    monkeypatch.setattr(api_mod, "_config", None)
    fake_auth = MagicMock()
    fake_auth.validate_token = MagicMock(return_value={"username": "test", "role": "admin"})
    monkeypatch.setattr(api_mod, "get_auth_manager", lambda: fake_auth)
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
    )


def test_i2v_endpoint_500_on_error(anon_client):
    fake = AsyncMock()
    fake.generate = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("imaging.get_i2v_client", return_value=fake):
        r = anon_client.post("/api/videos/i2v", json={
            "image_url": "https://x/a.png", "prompt": "p"
        })
    assert r.status_code == 500
    assert "boom" in r.json()["detail"]
