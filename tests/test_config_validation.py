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


def test_config_example_is_valid_with_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMOX_ADMIN_PASSWORD", "dev-password")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    cfg = Config.from_yaml(Path(__file__).parents[1] / "config.example.yaml")

    validate_config(cfg)


def test_default_config_path_uses_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "custom.yaml"
    monkeypatch.setenv("MEMOX_CONFIG_PATH", str(config_path))

    assert default_config_path() == config_path
