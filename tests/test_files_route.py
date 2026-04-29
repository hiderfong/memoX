from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.web.api import app


def _bypass_auth(monkeypatch):
    """Patch auth middleware to always allow (mirror test_i2v_api.py)."""
    from src.web import api as api_mod
    mgr = MagicMock()
    mgr.validate_token.return_value = {"username": "t", "role": "admin"}
    monkeypatch.setattr(api_mod, "get_auth_manager", lambda: mgr)


def test_files_route_serves_existing_file(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    f = uploads / "abc_test.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", uploads)
    _bypass_auth(monkeypatch)

    client = TestClient(app)
    r = client.get("/api/files/abc_test.png")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")
    assert r.headers["content-type"].startswith("image/")


def test_files_route_rejects_traversal(tmp_path, monkeypatch):
    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", tmp_path)
    _bypass_auth(monkeypatch)
    client = TestClient(app)
    r = client.get("/api/files/..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)


def test_files_route_404_on_missing(tmp_path, monkeypatch):
    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", tmp_path)
    _bypass_auth(monkeypatch)
    client = TestClient(app)
    r = client.get("/api/files/nope.png")
    assert r.status_code == 404
