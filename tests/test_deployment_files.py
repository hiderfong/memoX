"""Deployment file regression tests."""

from pathlib import Path

import yaml

from scripts.run_external_e2e import extract_media_asset

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
    api_py = (ROOT / "src" / "web" / "api.py").read_text(encoding="utf-8")
    frontend_api = (ROOT / "frontend_wip" / "src" / "shared.tsx").read_text(encoding="utf-8")

    assert "AS frontend-build" in dockerfile
    assert "npm run build" in dockerfile
    assert "COPY --from=frontend-build /app/frontend_wip/dist ./frontend_wip/dist" in dockerfile
    assert '"frontend_wip" / "dist"' in api_py
    assert "enqueueI2VJob" in frontend_api
    assert "enqueueI2VBatchJobs" in frontend_api
    assert "generateI2V:" not in frontend_api
    assert "generateI2VBatch:" not in frontend_api
    assert "mkdir -p /app/data /app/workspace /app/backups" in dockerfile
    assert 'CMD ["memox"]' in dockerfile


def test_frontend_source_has_no_backup_files() -> None:
    assert not list((ROOT / "frontend_wip" / "src").rglob("*.bak"))


def test_streamlit_entry_is_diagnostic_compat_only() -> None:
    run_script = (ROOT / "run_streamlit.sh").read_text(encoding="utf-8")
    streamlit_app = (ROOT / "src" / "ui" / "streamlit_app.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    development = (ROOT / "DEVELOPMENT.md").read_text(encoding="utf-8")

    assert "诊断/兼容界面" in run_script
    assert "非主 UI" in run_script
    assert "frontend_wip React" in run_script
    assert "MEMOX_STREAMLIT_USERNAME" in run_script
    assert "MEMOX_STREAMLIT_PASSWORD" in run_script
    assert 'page_title="MemoX Diagnostics"' in streamlit_app
    assert "诊断/兼容入口；主 UI 为 React Web UI" in streamlit_app
    assert "MEMOX_STREAMLIT_USERNAME" in streamlit_app
    assert "MEMOX_STREAMLIT_PASSWORD" in streamlit_app
    assert "admin123" not in streamlit_app
    assert "DEFAULT_PASSWORD" not in streamlit_app
    assert "Streamlit 诊断/兼容界面" in readme
    assert "不作为主 UI" in readme
    assert "不会内置默认密码" in readme
    assert "启动 React 主 UI" in readme
    assert "Streamlit 已降级为诊断/兼容入口" in development
    assert "React 主 Web UI" in development
    assert "frontend_wip/src/pages/ChatPage.tsx" in development
    assert "frontend/src/pages/Chat.tsx" not in development
    assert "Streamlit 出一个管理界面" not in development


def test_config_example_is_container_friendly() -> None:
    config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    local_config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    api_py = (ROOT / "src" / "web" / "api.py").read_text(encoding="utf-8")

    assert config["app"]["debug"] is False
    assert config["server"]["host"] == "0.0.0.0"
    assert "http://localhost:3000" in config["server"]["cors_origins"]
    assert "https://127.0.0.1:8080" in config["server"]["cors_origins"]
    assert "23.236.66.33" not in api_py
    assert "23.236.66.33" not in "\n".join(local_config["server"]["cors_origins"])
    assert "ConfigurableCORSMiddleware" in api_py
    assert config["ops"]["archive_mirror_dir"] == ""
    assert config["ops"]["ops_event_retention_days"] == 90
    assert config["ops"]["audit_log_retention_days"] == 180
    assert config["ops"]["task_job_retention_days"] == 30
    assert config["ops"]["diagnostic_retention_days"] == 30
    assert config["ops"]["max_diagnostic_bundles"] == 20
    assert config["tool_policy"]["web"]["request_timeout_seconds"] == 15
    assert config["tool_policy"]["web"]["max_response_bytes"] == 2000000
    assert config["tool_policy"]["web"]["max_fetch_chars"] == 20000
    assert config["tool_policy"]["web"]["max_search_results"] == 10
    assert "/api/docs" in config["auth"]["public_paths"]
    assert "/api/redoc" in config["auth"]["public_paths"]
    assert "/api/openapi.json" in config["auth"]["public_paths"]
    assert "/api/files/" not in config["auth"]["public_paths"]
    assert config["file_access"]["signing_secret"] == "${MEMOX_FILE_SIGNING_SECRET:-}"
    assert config["file_access"]["signed_url_ttl_seconds"] == 300
    assert "MEMOX_FILE_SIGNING_SECRET=" in env_example


def test_backup_artifacts_are_documented_and_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    readiness = (ROOT / "docs" / "RELEASE_READINESS.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "backups/" in gitignore
    assert "backups" in dockerignore
    assert "docs/RELEASE_READINESS.md" in readme
    assert "docs/RECOVERY_RUNBOOK.md" in readme
    assert "RELEASE_READINESS.md" in deployment
    assert "RECOVERY_RUNBOOK.md" in deployment
    assert "Release Gate" in readiness
    assert "Tool Permission Review" in readiness
    assert "Background Task Checks" in readiness
    assert "Browser Smoke Path" in readiness
    assert "Go/No-Go Decision" in readiness
    assert "scripts/backup_restore.py create" in deployment
    assert "scripts/backup_restore.py restore" in deployment
    assert "scripts/backup_restore.py prune" in deployment
    assert "scripts/restore_drill.py" in deployment
    assert "scripts/index_consistency.py" in deployment
    assert "scripts/ops_check.py" in deployment
    assert "/api/system/health" in deployment
    assert "/api/system/backups" in deployment
    assert "/api/system/events" in deployment
    assert "/api/system/tool-audit" in deployment
    assert "/api/system/tool-policy" in deployment
    assert "/api/system/diagnostics/export" in deployment
    assert "/api/system/indexes/repair" in deployment
    assert "/api/system/maintenance/lifecycle" in deployment
    assert "dry_run=true" in deployment
    assert "lifecycle_cleanup" in deployment
    assert "ops.task_job_retention_days" in deployment
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


def test_release_gate_requires_external_smoke_without_secret_skips() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release-gate.yml").read_text(encoding="utf-8")
    external_script = (ROOT / "scripts" / "run_external_e2e.py").read_text(encoding="utf-8")
    readiness = (ROOT / "docs" / "RELEASE_READINESS.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "EXTERNAL_AGENT_E2E_RUNBOOK.md").read_text(encoding="utf-8")

    assert "name: Release Gate" in workflow
    assert 'tags:\n      - "v*"' in workflow
    assert "scripts/run_external_e2e.py" in workflow
    assert "--phases smoke" in workflow
    assert "--allow-missing-secrets" not in workflow
    assert "Missing required release gate secret" in workflow
    assert "release-gate-e2e-report.md" in workflow
    assert "release-gate-e2e-report" in workflow
    for env_name in (
        "MINIMAX_API_KEY",
        "DEEPSEEK_API_KEY",
        "QWEN_API_KEY",
        "DASHSCOPE_API_KEY",
        "MEMOX_FILE_SIGNING_SECRET",
    ):
        assert env_name in workflow

    assert "Release Gate" in readiness
    assert "--phases smoke" in readiness
    assert "--allow-missing-secrets" in readiness
    assert "must fail the gate" in readiness
    assert "release-gate-e2e-report" in readiness
    assert ".github/workflows/release-gate.yml" in runbook
    assert "缺少真实 provider secret 时必须失败" in runbook
    assert "发布前选择 `Release Gate`" in runbook
    assert '"full-sweep",' in external_script
    assert '"tests/e2e", "-q", "-s", "--tb=short", "-ra"' in external_script
    assert '"tests/e2e", "-m", "e2e"' not in external_script
    assert "pytest tests/e2e -q -s --tb=short -ra" in runbook


def test_external_media_job_smoke_accepts_direct_asset_poll_response() -> None:
    wrapped = {"asset": {"id": "asset_1", "status": "queued"}}
    direct = {"id": "asset_1", "status": "success", "url": "https://cdn/video.mp4"}

    assert extract_media_asset(wrapped) == {"id": "asset_1", "status": "queued"}
    assert extract_media_asset(direct) == direct


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
