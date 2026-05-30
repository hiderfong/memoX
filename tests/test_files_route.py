from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.config import Config
from src.web.api import app


def test_configure_uploads_dir_uses_configured_upload_directory(tmp_path):
    from src.web import api as api_mod

    original_uploads_dir = api_mod.UPLOADS_DIR
    configured_uploads = tmp_path / "configured-uploads"
    cfg = Config._from_dict(
        {
            "app": {},
            "server": {},
            "coordinator": {},
            "providers": {},
            "worker_templates": {},
            "knowledge_base": {"upload_directory": str(configured_uploads)},
            "auth": {"enabled": False},
        }
    )

    try:
        api_mod._configure_uploads_dir(cfg)
        assert api_mod.UPLOADS_DIR == configured_uploads
        assert configured_uploads.is_dir()
    finally:
        api_mod.UPLOADS_DIR = original_uploads_dir


def _set_file_access_config(monkeypatch, *, signing_secret: str = "test-file-secret", ttl: int = 300) -> None:
    from src.web import api as api_mod

    cfg = Config._from_dict(
        {
            "app": {},
            "server": {},
            "coordinator": {},
            "providers": {},
            "worker_templates": {},
            "knowledge_base": {},
            "auth": {"enabled": True, "users": []},
            "file_access": {
                "signing_secret": signing_secret,
                "signed_url_ttl_seconds": ttl,
            },
        }
    )
    monkeypatch.setattr(api_mod, "_config", cfg)


def _set_auth(monkeypatch, *, valid: bool) -> dict[str, str]:
    """Patch auth middleware to either allow or reject Bearer tokens."""
    mgr = MagicMock()
    mgr.validate_token.return_value = (
        {"username": "t", "role": "admin", "display_name": "Test"} if valid else None
    )
    monkeypatch.setattr(app.state, "_auth_manager", mgr, raising=False)
    return {"Authorization": "Bearer valid-token"}


def test_files_route_serves_existing_file_with_bearer(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    f = uploads / "abc_test.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", uploads)
    _set_file_access_config(monkeypatch)
    headers = _set_auth(monkeypatch, valid=True)

    client = TestClient(app)
    r = client.get("/api/files/abc_test.png", headers=headers)
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")
    assert r.headers["content-type"].startswith("image/")


def test_files_route_rejects_unsigned_request_even_if_public_prefix_configured(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "abc_test.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(api_mod, "_PUBLIC_PATHS", set(api_mod._PUBLIC_PATHS) | {"/api/files/"})
    _set_file_access_config(monkeypatch)
    _set_auth(monkeypatch, valid=False)

    client = TestClient(app)
    r = client.get("/api/files/abc_test.png")
    assert r.status_code == 401


def test_files_route_serves_existing_file_with_signed_url(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "abc_test.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", uploads)
    _set_file_access_config(monkeypatch)
    _set_auth(monkeypatch, valid=False)

    expires = int(api_mod.time.time()) + 300
    signature = api_mod._file_signature("abc_test.png", expires)

    client = TestClient(app)
    r = client.get(f"/api/files/abc_test.png?expires={expires}&signature={signature}")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")


def test_files_route_rejects_expired_signed_url(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "abc_test.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(api_mod, "_PUBLIC_PATHS", set(api_mod._PUBLIC_PATHS) | {"/api/files/"})
    _set_file_access_config(monkeypatch)
    _set_auth(monkeypatch, valid=False)

    expires = int(api_mod.time.time()) - 1
    signature = api_mod._file_signature("abc_test.png", expires)

    client = TestClient(app)
    r = client.get(f"/api/files/abc_test.png?expires={expires}&signature={signature}")
    assert r.status_code == 401


def test_files_route_rejects_bad_signed_url(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "abc_test.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(api_mod, "_PUBLIC_PATHS", set(api_mod._PUBLIC_PATHS) | {"/api/files/"})
    _set_file_access_config(monkeypatch)
    _set_auth(monkeypatch, valid=False)

    expires = int(api_mod.time.time()) + 300

    client = TestClient(app)
    r = client.get(f"/api/files/abc_test.png?expires={expires}&signature=bad")
    assert r.status_code == 401


def test_sign_file_route_returns_signed_url(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "abc test.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", uploads)
    _set_file_access_config(monkeypatch)
    headers = _set_auth(monkeypatch, valid=True)

    client = TestClient(app)
    r = client.post(
        "/api/files/sign",
        headers=headers,
        json={"name": "abc test.png", "ttl_seconds": 60},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["url"].startswith("http://testserver/api/files/abc%20test.png?")
    assert body["expires"] > int(api_mod.time.time())

    _set_auth(monkeypatch, valid=False)
    signed_path = body["url"].removeprefix("http://testserver")
    fetched = client.get(signed_path)
    assert fetched.status_code == 200


def test_sign_file_route_requires_signing_secret(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "abc_test.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", uploads)
    _set_file_access_config(monkeypatch, signing_secret="")
    headers = _set_auth(monkeypatch, valid=True)

    client = TestClient(app)
    r = client.post("/api/files/sign", headers=headers, json={"name": "abc_test.png"})
    assert r.status_code == 503


def test_sign_file_route_requires_bearer_even_if_public_prefix_configured(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "abc_test.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", uploads)
    monkeypatch.setattr(api_mod, "_PUBLIC_PATHS", set(api_mod._PUBLIC_PATHS) | {"/api/files/"})
    _set_file_access_config(monkeypatch)
    _set_auth(monkeypatch, valid=False)

    client = TestClient(app)
    r = client.post("/api/files/sign", json={"name": "abc_test.png"})
    assert r.status_code == 401


def test_files_route_rejects_traversal(tmp_path, monkeypatch):
    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", tmp_path)
    _set_file_access_config(monkeypatch)
    headers = _set_auth(monkeypatch, valid=True)
    client = TestClient(app)
    r = client.get("/api/files/..%2Fetc%2Fpasswd", headers=headers)
    assert r.status_code in (400, 404)


def test_files_route_404_on_missing(tmp_path, monkeypatch):
    from src.web import api as api_mod
    monkeypatch.setattr(api_mod, "UPLOADS_DIR", tmp_path)
    _set_file_access_config(monkeypatch)
    headers = _set_auth(monkeypatch, valid=True)
    client = TestClient(app)
    r = client.get("/api/files/nope.png", headers=headers)
    assert r.status_code == 404
