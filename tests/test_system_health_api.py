"""System health endpoint tests."""

from __future__ import annotations

import contextlib
import importlib
import json
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient


class EmptyVectorStore:
    def list_documents(self, collection_name: str = "documents") -> list[dict]:
        return []

    def get_chunks_by_doc(self, doc_id: str, collection_name: str = "documents") -> list[dict]:
        return []


def _write_config(root: Path) -> Path:
    data = root / "data"
    for name in ["chroma", "uploads", "skills"]:
        (data / name).mkdir(parents=True, exist_ok=True)
    (root / "workspace").mkdir()
    config = {
        "app": {"workspace": str(root / "workspace")},
        "knowledge_base": {
            "persist_directory": str(data / "chroma"),
            "upload_directory": str(data / "uploads"),
            "skills_dir": str(data / "skills"),
            "embedding_provider": "hash",
            "embedding_model": "hash-test",
            "hybrid_search": {
                "enabled": True,
                "bm25_persist_path": str(data / "bm25_index.pkl"),
            },
            "manifest_path": str(data / "documents_manifest.json"),
        },
        "auth": {
            "enabled": True,
            "public_paths": ["/api/auth/login", "/api/health"],
            "users": [
                {"username": "admin", "password": "pw", "role": "admin", "display_name": "Admin"},
                {"username": "user", "password": "pw", "role": "user", "display_name": "User"},
            ],
        },
        "ops": {"archive_mirror_dir": str(root / "mirror")},
    }
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def test_system_health_requires_admin_and_reports_readiness(monkeypatch, tmp_path: Path) -> None:
    from src.web import api as api_module

    init_auth = importlib.import_module("auth").init_auth
    Config = importlib.import_module("config").Config
    BM25Indexer = importlib.import_module("knowledge.bm25_indexer").BM25Indexer
    storage = importlib.import_module("storage")
    persistence_module = importlib.import_module("storage.persistence")

    config_path = _write_config(tmp_path)
    monkeypatch.setenv("MEMOX_CONFIG_PATH", str(config_path))
    original_lifespan = api_module.app.router.lifespan_context
    store = storage.init_store(tmp_path / "data" / "memox.db")
    store.record_ops_event(
        event_type="backup_maintenance",
        status="ok",
        action="created",
        message="Created and verified backup",
        details={"archive": "backups/memox-backup-test.tar.gz", "verified": True},
    )

    @contextlib.asynccontextmanager
    async def noop_lifespan(app):
        yield

    api_module.app.router.lifespan_context = noop_lifespan
    api_module._config = Config.from_yaml(config_path)
    api_module._rag_engine = SimpleNamespace(
        vector_store=EmptyVectorStore(),
        _hybrid_retriever=SimpleNamespace(bm25_indexer=BM25Indexer(tmp_path / "data" / "bm25_index.pkl")),
    )
    auth = init_auth(
        [
            {"username": "admin", "password": "pw", "role": "admin", "display_name": "Admin"},
            {"username": "user", "password": "pw", "role": "user", "display_name": "User"},
        ],
        app_state=api_module.app.state,
    )
    admin_token = auth.login("admin", "pw")
    user_token = auth.login("user", "pw")

    try:
        with TestClient(api_module.app, raise_server_exceptions=False) as client:
            forbidden = client.get("/api/system/health", headers={"Authorization": f"Bearer {user_token}"})
            assert forbidden.status_code == 403
            manual_forbidden = client.post(
                "/api/system/maintenance/backup?force=true",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            assert manual_forbidden.status_code == 403
            backups_forbidden = client.get("/api/system/backups", headers={"Authorization": f"Bearer {user_token}"})
            assert backups_forbidden.status_code == 403
            drill_forbidden = client.post(
                "/api/system/backups/memox-backup-missing.tar.gz/restore-drill",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            assert drill_forbidden.status_code == 403
            preflight_forbidden = client.post(
                "/api/system/backups/memox-backup-missing.tar.gz/restore-preflight",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            assert preflight_forbidden.status_code == 403
            restore_forbidden = client.post(
                "/api/system/backups/memox-backup-missing.tar.gz/restore",
                headers={"Authorization": f"Bearer {user_token}"},
                json={
                    "confirm_archive_name": "memox-backup-missing.tar.gz",
                    "acknowledge_overwrite": True,
                    "acknowledge_maintenance_mode": True,
                },
            )
            assert restore_forbidden.status_code == 403
            events_forbidden = client.get("/api/system/events", headers={"Authorization": f"Bearer {user_token}"})
            assert events_forbidden.status_code == 403
            repair_forbidden = client.post("/api/system/indexes/repair", headers={"Authorization": f"Bearer {user_token}"})
            assert repair_forbidden.status_code == 403
            diagnostics_forbidden = client.get(
                "/api/system/diagnostics/export",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            assert diagnostics_forbidden.status_code == 403
            lifecycle_forbidden = client.post(
                "/api/system/maintenance/lifecycle?dry_run=true",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            assert lifecycle_forbidden.status_code == 403

            response = client.get("/api/system/health", headers={"Authorization": f"Bearer {admin_token}"})
            assert response.status_code == 200
            payload = response.json()

            manual = client.post(
                "/api/system/maintenance/backup?force=true",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert manual.status_code == 200
            manual_payload = manual.json()

            backup_name = Path(manual_payload["archive"]).name
            backups = client.get("/api/system/backups", headers={"Authorization": f"Bearer {admin_token}"})
            assert backups.status_code == 200
            backups_payload = backups.json()

            repair = client.post("/api/system/indexes/repair", headers={"Authorization": f"Bearer {admin_token}"})
            assert repair.status_code == 200
            repair_payload = repair.json()

            verified = client.post(
                f"/api/system/backups/{backup_name}/verify",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert verified.status_code == 200
            verified_payload = verified.json()

            preflight = client.post(
                f"/api/system/backups/{backup_name}/restore-preflight",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert preflight.status_code == 200
            preflight_payload = preflight.json()

            restore_rejected = client.post(
                f"/api/system/backups/{backup_name}/restore",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "confirm_archive_name": "wrong.tar.gz",
                    "acknowledge_overwrite": True,
                    "acknowledge_maintenance_mode": True,
                },
            )
            assert restore_rejected.status_code == 200
            restore_rejected_payload = restore_rejected.json()

            drill = client.post(
                f"/api/system/backups/{backup_name}/restore-drill",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert drill.status_code == 200
            drill_payload = drill.json()

            diagnostics = client.get(
                "/api/system/diagnostics/export",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert diagnostics.status_code == 200
            diagnostics_content_type = diagnostics.headers.get("content-type")
            diagnostics_disposition = diagnostics.headers.get("content-disposition", "")
            diagnostics_zip = diagnostics.content

            lifecycle_dry_run = client.post(
                "/api/system/maintenance/lifecycle?dry_run=true",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert lifecycle_dry_run.status_code == 200
            lifecycle_dry_run_payload = lifecycle_dry_run.json()
            lifecycle_execute = client.post(
                "/api/system/maintenance/lifecycle?dry_run=false",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert lifecycle_execute.status_code == 200
            lifecycle_execute_payload = lifecycle_execute.json()

            events = client.get("/api/system/events?limit=10", headers={"Authorization": f"Bearer {admin_token}"})
            assert events.status_code == 200
            events_payload = events.json()
            restore_events = client.get(
                "/api/system/events?event_type=restore_drill&limit=5",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert restore_events.status_code == 200
            restore_events_payload = restore_events.json()
            paged_events = client.get(
                "/api/system/events?status=ok&limit=2&offset=1",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert paged_events.status_code == 200
            paged_events_payload = paged_events.json()

            missing = client.post(
                "/api/system/backups/memox-backup-missing.tar.gz/verify",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert missing.status_code == 404
            missing_drill = client.post(
                "/api/system/backups/memox-backup-missing.tar.gz/restore-drill",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert missing_drill.status_code == 404
            missing_preflight = client.post(
                "/api/system/backups/memox-backup-missing.tar.gz/restore-preflight",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert missing_preflight.status_code == 404
            missing_restore = client.post(
                "/api/system/backups/memox-backup-missing.tar.gz/restore",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "confirm_archive_name": "memox-backup-missing.tar.gz",
                    "acknowledge_overwrite": True,
                    "acknowledge_maintenance_mode": True,
                },
            )
            assert missing_restore.status_code == 404

            refreshed = client.get("/api/system/health", headers={"Authorization": f"Bearer {admin_token}"})
            assert refreshed.status_code == 200
            refreshed_payload = refreshed.json()
    finally:
        api_module.app.router.lifespan_context = original_lifespan
        api_module._config = None
        api_module._rag_engine = None
        store.close()
        persistence_module._store = None

    assert payload["ok"] is True
    assert payload["status"] == "warning"
    assert payload["runtime"]["config_loaded"] is True
    assert payload["runtime"]["rag_engine_loaded"] is True
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["config"]["status"] == "ok"
    assert checks["index_consistency"]["status"] == "ok"
    assert checks["sqlite"]["status"] == "warning"
    assert checks["disk"]["status"] == "ok"
    assert checks["latest_backup"]["status"] == "warning"
    assert checks["latest_backup"]["details"]["archive_count"] == 0
    assert payload["ops"]["auto_backup_enabled"] is True
    assert payload["ops"]["archive_mirror_enabled"] is True
    assert payload["ops"]["archive_mirror_dir"] == str(tmp_path / "mirror")
    assert payload["ops"]["last_backup_maintenance"]["action"] == "created"
    assert manual_payload["ok"] is True
    assert manual_payload["action"] == "created"
    assert manual_payload["forced"] is True
    assert Path(manual_payload["archive"]).exists()
    assert manual_payload["mirror"]["ok"] is True
    assert Path(manual_payload["mirror"]["destination"]).exists()
    assert Path(manual_payload["mirror"]["destination"]).name == Path(manual_payload["archive"]).name
    assert backups_payload["count"] == 1
    assert backups_payload["backups"][0]["name"] == Path(manual_payload["archive"]).name
    assert backups_payload["backups"][0]["metadata_valid"] is True
    assert backups_payload["backups"][0]["entry_count"] > 0
    assert repair_payload["ok"] is True
    assert repair_payload["action"] == "index_repair"
    assert repair_payload["after"]["status"] == "ok"
    assert verified_payload["ok"] is True
    assert verified_payload["verified"] is True
    assert verified_payload["name"] == Path(manual_payload["archive"]).name
    assert verified_payload["entry_count"] > 0
    assert preflight_payload["ok"] is True
    assert preflight_payload["status"] == "warning"
    assert preflight_payload["safe_without_overwrite"] is False
    assert preflight_payload["requires_overwrite"] is True
    assert preflight_payload["conflict_count"] > 0
    assert preflight_payload["writes_performed"] is False
    assert restore_rejected_payload["ok"] is False
    assert restore_rejected_payload["status"] == "warning"
    assert restore_rejected_payload["action"] == "rejected"
    assert restore_rejected_payload["writes_performed"] is False
    assert drill_payload["ok"] is True
    assert drill_payload["status"] == "ok"
    assert drill_payload["name"] == Path(manual_payload["archive"]).name
    assert {check["name"]: check["status"] for check in drill_payload["checks"]} == {
        "config.yaml": "ok",
        "data": "ok",
        "workspace": "ok",
    }
    assert diagnostics_content_type == "application/zip"
    assert "memox-diagnostics-" in diagnostics_disposition
    with zipfile.ZipFile(BytesIO(diagnostics_zip)) as bundle:
        names = set(bundle.namelist())
        assert {
            "manifest.json",
            "reports/system_health.json",
            "reports/backups.json",
            "reports/ops_events.json",
            "reports/index_consistency.json",
            "config/redacted_config.json",
            "logs/log_sources.json",
        }.issubset(names)
        manifest = json.loads(bundle.read("manifest.json"))
        redacted_config = json.loads(bundle.read("config/redacted_config.json"))
    assert manifest["log_count"] >= 0
    assert redacted_config["auth"]["users"][0]["password"] == "***REDACTED***"
    assert lifecycle_dry_run_payload["dry_run"] is True
    assert lifecycle_dry_run_payload["summary"]["core_user_data_deleted"] is False
    assert lifecycle_execute_payload["dry_run"] is False
    assert lifecycle_execute_payload["event_id"]
    assert lifecycle_execute_payload["actor"]["username"] == "admin"
    assert refreshed_payload["ops"]["last_backup_maintenance"]["details"]["forced"] is True
    assert refreshed_payload["ops"]["last_backup_maintenance"]["details"]["actor"]["username"] == "admin"
    assert refreshed_payload["ops"]["last_diagnostics_export"]["details"]["filename"].endswith(".zip")
    assert refreshed_payload["ops"]["last_diagnostics_export"]["details"]["actor"]["username"] == "admin"
    assert refreshed_payload["ops"]["last_diagnostics_export"]["details"]["mirror"]["ok"] is True
    assert refreshed_payload["ops"]["retention"]["ops_event_retention_days"] == 90
    assert refreshed_payload["ops"]["last_lifecycle_cleanup"]["details"]["action"] == "executed"
    assert Path(refreshed_payload["ops"]["last_diagnostics_export"]["details"]["mirror"]["destination"]).exists()
    assert refreshed_payload["ops"]["last_index_repair"]["details"]["action"] == "index_repair"
    assert refreshed_payload["ops"]["last_restore_drill"]["details"]["name"] == Path(manual_payload["archive"]).name
    assert refreshed_payload["ops"]["last_restore_execute"]["details"]["action"] == "rejected"
    assert events_payload["count"] >= 6
    assert events_payload["total"] >= events_payload["count"]
    assert events_payload["offset"] == 0
    assert {event["event_type"] for event in events_payload["events"]} >= {
        "backup_maintenance",
        "diagnostics_export",
        "index_repair",
        "restore_preflight",
        "restore_execute",
        "restore_drill",
        "lifecycle_cleanup",
    }
    assert restore_events_payload["count"] == 1
    assert restore_events_payload["total"] == 1
    assert restore_events_payload["events"][0]["event_type"] == "restore_drill"
    assert restore_events_payload["events"][0]["details"]["name"] == Path(manual_payload["archive"]).name
    assert restore_events_payload["events"][0]["details"]["actor"]["username"] == "admin"
    assert paged_events_payload["status"] == "ok"
    assert paged_events_payload["limit"] == 2
    assert paged_events_payload["offset"] == 1
    assert paged_events_payload["total"] >= paged_events_payload["count"]
    assert all(event["status"] == "ok" for event in paged_events_payload["events"])
