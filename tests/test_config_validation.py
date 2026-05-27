"""Runtime configuration validation tests."""

from pathlib import Path

import pytest

from src.config import Config, ConfigError, default_config_path, validate_config


def _base_config(auth: dict) -> Config:
    return Config._from_dict(
        {
            "app": {},
            "server": {},
            "coordinator": {},
            "providers": {},
            "worker_templates": {},
            "knowledge_base": {},
            "auth": auth,
        }
    )


def test_validate_config_rejects_empty_auth_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMOX_ADMIN_PASSWORD", raising=False)
    cfg = _base_config(
        {
            "enabled": True,
            "users": [
                {
                    "username": "admin",
                    "password": "${MEMOX_ADMIN_PASSWORD}",
                    "role": "admin",
                }
            ],
        }
    )

    with pytest.raises(ConfigError, match="密码为空"):
        validate_config(cfg)


def test_validate_config_accepts_auth_password_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMOX_ADMIN_PASSWORD", "dev-password")
    cfg = _base_config(
        {
            "enabled": True,
            "users": [
                {
                    "username": "admin",
                    "password": "${MEMOX_ADMIN_PASSWORD}",
                    "role": "admin",
                }
            ],
        }
    )

    validate_config(cfg)


def test_validate_config_allows_disabled_auth_without_users() -> None:
    cfg = _base_config({"enabled": False, "users": []})

    validate_config(cfg)


def test_validate_config_rejects_invalid_ops_backup_settings() -> None:
    cfg = Config._from_dict(
        {
            "app": {},
            "server": {},
            "coordinator": {},
            "providers": {},
            "worker_templates": {},
            "knowledge_base": {},
            "auth": {"enabled": False, "users": []},
            "ops": {
                "auto_backup_enabled": True,
                "auto_backup_interval_hours": 0,
                "max_backups": 0,
            },
        }
    )

    with pytest.raises(ConfigError, match="auto_backup_interval_hours"):
        validate_config(cfg)


def test_validate_config_rejects_invalid_database_policy() -> None:
    cfg = Config._from_dict(
        {
            "app": {},
            "server": {},
            "coordinator": {},
            "providers": {},
            "worker_templates": {},
            "knowledge_base": {},
            "auth": {"enabled": False, "users": []},
            "tool_policy": {
                "database": {
                    "default_access_mode": "owner",
                    "max_result_rows": 0,
                }
            },
        }
    )

    with pytest.raises(ConfigError, match="tool_policy.database.default_access_mode"):
        validate_config(cfg)


def test_validate_config_rejects_invalid_playwright_crawler_policy() -> None:
    cfg = Config._from_dict(
        {
            "app": {},
            "server": {},
            "coordinator": {},
            "providers": {},
            "worker_templates": {},
            "knowledge_base": {},
            "auth": {"enabled": False, "users": []},
            "tool_policy": {
                "playwright_crawler": {
                    "max_concurrency": 0,
                    "total_timeout_seconds": 0,
                }
            },
        }
    )

    with pytest.raises(ConfigError, match="tool_policy.playwright_crawler.max_concurrency"):
        validate_config(cfg)


def test_validate_config_rejects_invalid_web_tool_policy() -> None:
    cfg = Config._from_dict(
        {
            "app": {},
            "server": {},
            "coordinator": {},
            "providers": {},
            "worker_templates": {},
            "knowledge_base": {},
            "auth": {"enabled": False, "users": []},
            "tool_policy": {
                "web": {
                    "request_timeout_seconds": 0,
                    "max_response_bytes": 100,
                    "max_fetch_chars": 50,
                    "max_search_results": 0,
                }
            },
        }
    )

    with pytest.raises(ConfigError, match="tool_policy.web.request_timeout_seconds"):
        validate_config(cfg)


def test_validate_config_rejects_invalid_file_access_ttl() -> None:
    cfg = Config._from_dict(
        {
            "app": {},
            "server": {},
            "coordinator": {},
            "providers": {},
            "worker_templates": {},
            "knowledge_base": {},
            "auth": {"enabled": False, "users": []},
            "file_access": {"signed_url_ttl_seconds": 0},
        }
    )

    with pytest.raises(ConfigError, match="file_access.signed_url_ttl_seconds"):
        validate_config(cfg)


def test_config_example_is_valid_with_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMOX_ADMIN_PASSWORD", "dev-password")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    cfg = Config.from_yaml(Path(__file__).parents[1] / "config.example.yaml")

    validate_config(cfg)
    assert cfg.ops.auto_backup_enabled is True
    assert cfg.ops.auto_backup_include == ["config.yaml", "data", "workspace"]
    assert cfg.ops.archive_mirror_dir == ""


def test_default_config_path_uses_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "custom.yaml"
    monkeypatch.setenv("MEMOX_CONFIG_PATH", str(config_path))

    assert default_config_path() == config_path
