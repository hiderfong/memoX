"""Scheduled Tasks API 集成测试"""
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _bypass_auth(monkeypatch):
    """Override auth for tests via app.state + dependency_overrides.

    Both the middleware (_get_auth_from_request(request)) and FastAPI route
    dependencies (Depends(_get_auth_from_request)) will use the mock AuthManager.
    """
    from src.auth import AuthUser, _get_auth_from_request
    from src.web.api import app

    mock_mgr = MagicMock()
    mock_mgr.validate_token.return_value = {
        "username": "t", "role": "admin", "display_name": "Test",
    }

    # Middleware path: _get_auth_from_request(request) checks request.app.state._auth_manager
    app.state._auth_manager = mock_mgr

    # Route path: FastAPI dependency_overrides
    app.dependency_overrides[_get_auth_from_request] = lambda request: mock_mgr

    return app


@pytest.fixture
def mock_store(tmp_path, monkeypatch):
    """创建一个真实的 PersistenceStore 并注入到 api 模块"""
    from src.web import api as api_mod
    from storage.persistence import PersistenceStore

    store = PersistenceStore(tmp_path / "sched_test.db")
    monkeypatch.setattr(api_mod, "get_store", lambda: store)
    yield store
    store.close()


class TestListScheduledTasks:
    def test_list_empty(self, mock_store, monkeypatch):
        _bypass_auth(monkeypatch)
        from fastapi.testclient import TestClient

        from src.web.api import app

        client = TestClient(app)
        r = client.get("/api/scheduled-tasks")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_returns_tasks(self, mock_store, monkeypatch):
        _bypass_auth(monkeypatch)
        from fastapi.testclient import TestClient

        from src.web.api import app

        mock_store.create_scheduled_task(
            task_id="t1",
            description="Test task",
            cron="0 9 * * *",
            enabled=True,
        )

        client = TestClient(app)
        r = client.get("/api/scheduled-tasks")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["description"] == "Test task"


class TestCreateScheduledTask:
    def test_create_valid(self, mock_store, monkeypatch):
        _bypass_auth(monkeypatch)
        from fastapi.testclient import TestClient

        from src.web.api import app

        client = TestClient(app)
        r = client.post("/api/scheduled-tasks", json={
            "description": "Daily report",
            "cron": "0 9 * * *",
            "enabled": True,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["description"] == "Daily report"
        assert data["cron"] == "0 9 * * *"
        assert data["enabled"] is True
        assert "id" in data

    def test_create_invalid_cron(self, mock_store, monkeypatch):
        _bypass_auth(monkeypatch)
        from fastapi.testclient import TestClient

        from src.web.api import app

        client = TestClient(app)
        r = client.post("/api/scheduled-tasks", json={
            "description": "Bad cron",
            "cron": "not a valid cron expression",
        })
        assert r.status_code == 400
        assert "Cron" in r.json()["detail"]

    def test_create_empty_description(self, mock_store, monkeypatch):
        _bypass_auth(monkeypatch)
        from fastapi.testclient import TestClient

        from src.web.api import app

        client = TestClient(app)
        r = client.post("/api/scheduled-tasks", json={
            "description": "   ",
            "cron": "*/5 * * * *",
        })
        assert r.status_code == 400
        assert "不能为空" in r.json()["detail"]

    def test_create_with_active_group_ids(self, mock_store, monkeypatch):
        _bypass_auth(monkeypatch)
        from fastapi.testclient import TestClient

        from src.web.api import app

        client = TestClient(app)
        r = client.post("/api/scheduled-tasks", json={
            "description": "With groups",
            "cron": "*/10 * * * *",
            "active_group_ids": ["group_a", "group_b"],
        })
        assert r.status_code == 200
        data = r.json()
        assert "group_a" in data["active_group_ids"]


class TestUpdateScheduledTask:
    def test_update_description(self, mock_store, monkeypatch):
        _bypass_auth(monkeypatch)
        from fastapi.testclient import TestClient

        from src.web.api import app

        mock_store.create_scheduled_task("t_upd", "Original", "*/5 * * * *", enabled=True)

        client = TestClient(app)
        r = client.patch("/api/scheduled-tasks/t_upd", json={
            "description": "Updated description",
        })
        assert r.status_code == 200
        assert r.json()["description"] == "Updated description"

    def test_update_invalid_cron(self, mock_store, monkeypatch):
        _bypass_auth(monkeypatch)
        from fastapi.testclient import TestClient

        from src.web.api import app

        mock_store.create_scheduled_task("t_bad", "Cron test", "* * * * *", enabled=True)

        client = TestClient(app)
        r = client.patch("/api/scheduled-tasks/t_bad", json={
            "cron": "99 * * * *",
        })
        assert r.status_code == 400

    def test_update_nonexistent(self, mock_store, monkeypatch):
        _bypass_auth(monkeypatch)
        from fastapi.testclient import TestClient

        from src.web.api import app

        client = TestClient(app)
        r = client.patch("/api/scheduled-tasks/does_not_exist", json={
            "description": "New desc",
        })
        assert r.status_code == 404


class TestDeleteScheduledTask:
    def test_delete_existing(self, mock_store, monkeypatch):
        _bypass_auth(monkeypatch)
        from fastapi.testclient import TestClient

        from src.web.api import app

        mock_store.create_scheduled_task("t_del", "To delete", "* * * * *", enabled=True)

        client = TestClient(app)
        r = client.delete("/api/scheduled-tasks/t_del")
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert mock_store.get_scheduled_task("t_del") is None

    def test_delete_nonexistent(self, mock_store, monkeypatch):
        _bypass_auth(monkeypatch)
        from fastapi.testclient import TestClient

        from src.web.api import app

        client = TestClient(app)
        r = client.delete("/api/scheduled-tasks/does_not_exist")
        assert r.status_code == 404
