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
    assert service["healthcheck"]["test"] == ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/api/health"]


def test_dockerfile_builds_frontend_and_runs_memox() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "AS frontend-build" in dockerfile
    assert "npm run build" in dockerfile
    assert "COPY --from=frontend-build /app/frontend/dist ./frontend/dist" in dockerfile
    assert 'CMD ["memox"]' in dockerfile


def test_config_example_is_container_friendly() -> None:
    config = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))

    assert config["app"]["debug"] is False
    assert config["server"]["host"] == "0.0.0.0"
    assert "/api/docs" in config["auth"]["public_paths"]
    assert "/api/redoc" in config["auth"]["public_paths"]
    assert "/api/openapi.json" in config["auth"]["public_paths"]


def test_backup_artifacts_are_documented_and_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "backups/" in gitignore
    assert "backups" in dockerignore
    assert "scripts/backup_restore.py create" in deployment
    assert "scripts/backup_restore.py restore" in deployment
    assert "scripts/restore_drill.py" in deployment
    assert "scripts/index_consistency.py" in deployment
