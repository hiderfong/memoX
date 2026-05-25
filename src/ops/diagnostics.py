"""Build downloadable operational diagnostic bundles."""

from __future__ import annotations

import json
import zipfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from .redaction import redact_mapping, redact_text

MAX_LOG_BYTES = 96 * 1024
LOG_CANDIDATES = (
    "server.log",
    "backend.log",
    "memox.log",
    "logs/server.log",
    "logs/backend.log",
    "logs/memox.log",
)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def redact_config(config: Any) -> dict[str, Any]:
    """Return a config snapshot with obvious secrets removed."""

    raw = _jsonable(config)
    redacted = redact_mapping(raw)
    return redacted if isinstance(redacted, dict) else {"config": redacted}


def _write_json(bundle: zipfile.ZipFile, name: str, payload: Any) -> None:
    text = json.dumps(_jsonable(redact_mapping(payload)), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    bundle.writestr(name, text)


def _safe_log_name(root: Path, path: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = path.name
    return rel.replace("/", "__")


def _tail_text(path: Path, max_bytes: int = MAX_LOG_BYTES) -> str:
    data = path.read_bytes()
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="replace")


def collect_log_tails(root: Path) -> dict[str, dict[str, Any]]:
    """Collect tail snippets from common local log files."""
    candidates: list[Path] = []
    for rel in LOG_CANDIDATES:
        path = root / rel
        if path.is_file():
            candidates.append(path)
    for pattern in ("*.log", "logs/*.log"):
        candidates.extend(path for path in root.glob(pattern) if path.is_file())

    seen: set[Path] = set()
    logs: dict[str, dict[str, Any]] = {}
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            stat = resolved.stat()
            name = _safe_log_name(root, resolved)
            logs[name] = {
                "path": str(resolved),
                "size_bytes": stat.st_size,
                "tail_truncated": stat.st_size > MAX_LOG_BYTES,
                "tail": redact_text(_tail_text(resolved)),
            }
        except OSError as exc:
            logs[path.name] = {"path": str(path), "error": f"{type(exc).__name__}: {exc}"}
    return logs


def build_diagnostic_bundle(
    *,
    root: str | Path,
    config_path: str | Path,
    config: Any,
    system_health: dict[str, Any],
    backups: dict[str, Any],
    ops_events: dict[str, Any],
    index_report: dict[str, Any],
) -> tuple[bytes, str, dict[str, Any]]:
    """Return zip bytes, filename, and event-safe details."""
    root_path = Path(root).resolve()
    config_path = Path(config_path).resolve()
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    filename = f"memox-diagnostics-{_utc_stamp()}.zip"
    log_tails = collect_log_tails(root_path)

    manifest = {
        "generated_at": generated_at,
        "root": str(root_path),
        "config": str(config_path),
        "files": [
            "manifest.json",
            "reports/system_health.json",
            "reports/backups.json",
            "reports/ops_events.json",
            "reports/index_consistency.json",
            "config/redacted_config.json",
            "logs/log_sources.json",
        ],
        "redaction": (
            "Sensitive JSON keys plus common bearer/api-key/password patterns in log tails are redacted."
        ),
        "log_count": len(log_tails),
    }

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        _write_json(bundle, "manifest.json", manifest)
        _write_json(bundle, "reports/system_health.json", system_health)
        _write_json(bundle, "reports/backups.json", backups)
        _write_json(bundle, "reports/ops_events.json", ops_events)
        _write_json(bundle, "reports/index_consistency.json", index_report)
        _write_json(bundle, "config/redacted_config.json", redact_config(config))
        _write_json(
            bundle,
            "logs/log_sources.json",
            {
                name: {
                    "path": item.get("path"),
                    "size_bytes": item.get("size_bytes"),
                    "tail_truncated": item.get("tail_truncated"),
                    "error": item.get("error"),
                }
                for name, item in log_tails.items()
            },
        )
        for name, item in log_tails.items():
            if "tail" in item:
                bundle.writestr(f"logs/{name}.txt", item["tail"])

    details = {
        "ok": True,
        "status": "ok",
        "action": "diagnostics_export",
        "message": "Diagnostic bundle generated",
        "filename": filename,
        "size_bytes": len(buffer.getvalue()),
        "log_count": len(log_tails),
    }
    return buffer.getvalue(), filename, details
