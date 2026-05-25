"""Diagnostics redaction regression tests."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from src.ops.diagnostics import build_diagnostic_bundle
from src.ops.redaction import REDACTED, is_sensitive_key, redact_mapping, redact_text


def test_redact_mapping_removes_secrets_without_hiding_token_usage() -> None:
    payload = {
        "providers": {
            "openai": {
                "api_key": "sk-config-secret",
                "headers": {
                    "Authorization": "Bearer header-secret",
                    "X-API-Key": "header-api-key",
                    "User-Agent": "MemoX",
                },
            }
        },
        "auth": {"users": [{"username": "admin", "password": "admin-password"}]},
        "usage": {
            "input_tokens": 12,
            "output_tokens": 7,
            "total_tokens": 19,
            "max_tokens": 4096,
        },
        "access_token": "session-secret",
        "empty_secret": "",
        "none_secret": None,
    }

    redacted = redact_mapping(payload)

    assert redacted["providers"]["openai"]["api_key"] == REDACTED
    assert redacted["providers"]["openai"]["headers"]["Authorization"] == REDACTED
    assert redacted["providers"]["openai"]["headers"]["X-API-Key"] == REDACTED
    assert redacted["providers"]["openai"]["headers"]["User-Agent"] == "MemoX"
    assert redacted["auth"]["users"][0]["password"] == REDACTED
    assert redacted["access_token"] == REDACTED
    assert redacted["empty_secret"] == ""
    assert redacted["none_secret"] is None
    assert redacted["usage"] == payload["usage"]
    assert is_sensitive_key("access_token") is True
    assert is_sensitive_key("total_tokens") is False


def test_redact_text_removes_common_secret_patterns() -> None:
    text = """
Authorization: Bearer bearer-secret
OPENAI_API_KEY=sk-env-secret
password="plain-password"
token=url-token-secret
{"api_key": "sk-json-secret", "input_tokens": 12}
-----BEGIN PRIVATE KEY-----
private-key-secret
-----END PRIVATE KEY-----
total_tokens=12
"""

    redacted = redact_text(text)

    for secret in [
        "bearer-secret",
        "sk-env-secret",
        "plain-password",
        "url-token-secret",
        "sk-json-secret",
        "private-key-secret",
    ]:
        assert secret not in redacted
    assert REDACTED in redacted
    assert '"input_tokens": 12' in redacted
    assert "total_tokens=12" in redacted


def test_diagnostic_bundle_redacts_reports_config_and_log_tails(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "server.log").write_text(
        "Authorization: Bearer log-bearer-secret\n"
        "OPENAI_API_KEY=log-env-secret\n"
        "input_tokens=12\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text("providers: {}\n", encoding="utf-8")

    zip_bytes, filename, details = build_diagnostic_bundle(
        root=tmp_path,
        config_path=config_path,
        config={
            "providers": {
                "openai": {
                    "api_key": "config-api-secret",
                    "headers": {"Authorization": "Bearer config-header-secret"},
                }
            },
            "auth": {"users": [{"username": "admin", "password": "config-password"}]},
        },
        system_health={
            "checks": [
                {
                    "name": "llm",
                    "details": {
                        "Authorization": "Bearer report-bearer-secret",
                        "input_tokens": 33,
                    },
                }
            ]
        },
        backups={"backups": [{"name": "memox-backup.tar.gz", "metadata_valid": True}]},
        ops_events={"events": [{"details": {"access_token": "event-token-secret", "total_tokens": 44}}]},
        index_report={"status": "ok", "private_key": "index-private-key-secret"},
    )

    assert filename.startswith("memox-diagnostics-")
    assert details["ok"] is True

    with zipfile.ZipFile(BytesIO(zip_bytes)) as bundle:
        names = set(bundle.namelist())
        redacted_config = json.loads(bundle.read("config/redacted_config.json"))
        system_health = json.loads(bundle.read("reports/system_health.json"))
        ops_events = json.loads(bundle.read("reports/ops_events.json"))
        index_report = json.loads(bundle.read("reports/index_consistency.json"))
        log_name = next(name for name in names if name.startswith("logs/") and name.endswith("server.log.txt"))
        log_tail = bundle.read(log_name).decode("utf-8")
        combined = "\n".join(bundle.read(name).decode("utf-8") for name in names if name.endswith(".json")) + log_tail

    for secret in [
        "config-api-secret",
        "config-header-secret",
        "config-password",
        "report-bearer-secret",
        "event-token-secret",
        "index-private-key-secret",
        "log-bearer-secret",
        "log-env-secret",
    ]:
        assert secret not in combined
    assert redacted_config["providers"]["openai"]["api_key"] == REDACTED
    assert redacted_config["auth"]["users"][0]["password"] == REDACTED
    assert system_health["checks"][0]["details"]["Authorization"] == REDACTED
    assert system_health["checks"][0]["details"]["input_tokens"] == 33
    assert ops_events["events"][0]["details"]["access_token"] == REDACTED
    assert ops_events["events"][0]["details"]["total_tokens"] == 44
    assert index_report["private_key"] == REDACTED
    assert "input_tokens=12" in log_tail
