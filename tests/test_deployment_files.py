"""Deployment file regression tests."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_docker_compose_mounts_persistent_paths() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    service = compose["services"]["memox"]
    assert service["ports"] == ["8080:8080"]
    assert service["env_file"] == [{"path": ".env", "required": False}]
    assert service["environment"]["MEMOX_CONFIG_PATH"] == "/app/config.yaml"
    assert "./config.yaml:/app/config.yaml:rw" in service["volumes"]
    assert "./data:/app/data" in service["volumes"]
    assert "./workspace:/app/workspace" in service["volumes"]
    assert "./backups:/app/backups" in service["volumes"]
    assert service["healthcheck"]["test"] == ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/api/health"]


def test_dockerfile_builds_frontend_and_runs_memox() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "AS frontend-build" in dockerfile
    assert "npm run build" in dockerfile
    assert "COPY --from=frontend-build /app/frontend/dist ./frontend/dist" in dockerfile
    assert "mkdir -p /app/data /app/workspace /app/backups" in dockerfile
    assert 'CMD ["memox"]' in dockerfile


def test_config_example_is_container_friendly() -> None:
    config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))

    assert config["app"]["debug"] is False
    assert config["server"]["host"] == "0.0.0.0"
    assert config["ops"]["archive_mirror_dir"] == ""
    assert config["ops"]["ops_event_retention_days"] == 90
    assert config["ops"]["audit_log_retention_days"] == 180
    assert config["ops"]["diagnostic_retention_days"] == 30
    assert config["ops"]["max_diagnostic_bundles"] == 20
    assert "/api/docs" in config["auth"]["public_paths"]
    assert "/api/redoc" in config["auth"]["public_paths"]
    assert "/api/openapi.json" in config["auth"]["public_paths"]


def test_backup_artifacts_are_documented_and_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "backups/" in gitignore
    assert "backups" in dockerignore
    assert "docs/RECOVERY_RUNBOOK.md" in readme
    assert "RECOVERY_RUNBOOK.md" in deployment
    assert "scripts/backup_restore.py create" in deployment
    assert "scripts/backup_restore.py restore" in deployment
    assert "scripts/backup_restore.py prune" in deployment
    assert "scripts/restore_drill.py" in deployment
    assert "scripts/index_consistency.py" in deployment
    assert "scripts/ops_check.py" in deployment
    assert "/api/system/health" in deployment
    assert "/api/system/backups" in deployment
    assert "/api/system/events" in deployment
    assert "/api/system/diagnostics/export" in deployment
    assert "/api/system/indexes/repair" in deployment
    assert "/api/system/maintenance/lifecycle" in deployment
    assert "dry_run=true" in deployment
    assert "lifecycle_cleanup" in deployment
    assert "SQLite schema version/migration records" in deployment
    assert "redacted config" in deployment
    assert "redacted tails" in deployment
    assert "bearer tokens" in deployment
    assert "ops.archive_mirror_dir" in deployment
    assert "<mirror>/diagnostics/" in deployment
    assert "restore-preflight" in deployment
    assert "restart the service" in deployment
    assert "/restore" in deployment
    assert "confirm_archive_name" in deployment
    assert "backup verification" in deployment
    assert "temporary restore drill" in deployment
    assert "ops.auto_backup_enabled" in deployment
    assert "Repeated failed logins" in deployment
    assert "Retry-After" in deployment


def test_recovery_runbook_documents_guarded_restore_flow() -> None:
    runbook = (ROOT / "docs" / "RECOVERY_RUNBOOK.md").read_text(encoding="utf-8")

    assert "Recovery Priorities" in runbook
    assert "Incident Triage" in runbook
    assert "Backup Selection" in runbook
    assert "API Restore Path" in runbook
    assert "Offline Restore Path" in runbook
    assert "Post-Restore Validation" in runbook
    assert "Rollback From A Bad Restore" in runbook
    assert "scripts/ops_check.py --create-backup --restore-drill" in runbook
    assert "scripts/backup_restore.py verify" in runbook
    assert "scripts/backup_restore.py restore" in runbook
    assert "/api/system/diagnostics/export" in runbook
    assert "/api/system/backups/$BACKUP_NAME/restore-preflight" in runbook
    assert "/api/system/backups/$BACKUP_NAME/restore" in runbook
    assert "confirm_archive_name" in runbook
    assert "acknowledge_overwrite" in runbook
    assert "acknowledge_maintenance_mode" in runbook
    assert "safety_backup.archive" in runbook
    assert "ops.archive_mirror_dir" in runbook
    assert "<mirror>/backups/" in runbook
