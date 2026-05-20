"""Utilities for removing secrets from diagnostic payloads."""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

REDACTED = "***REDACTED***"

_SENSITIVE_EXACT_KEYS = {
    "api_key",
    "apikey",
    "api-key",
    "x-api-key",
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "auth_token",
    "api_token",
    "session_token",
    "authorization",
    "cookie",
    "set-cookie",
    "private_key",
}
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "api-key",
    "password",
    "passwd",
    "secret",
    "private_key",
    "credential",
)
_TEXT_SECRET_KEY = (
    r"api[_-]?key|x-api-key|password|passwd|pwd|secret|client_secret|"
    r"token|access_token|refresh_token|id_token|auth_token|api_token|session_token|"
    r"authorization|cookie|set-cookie|private_key|credential"
)
_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_AUTH_HEADER_RE = re.compile(r"(?im)(\bAuthorization\s*[:=]\s*)([^\r\n]+)")
_QUOTED_KEY_VALUE_RE = re.compile(
    rf"(?im)((?:[\"'])?\b(?:{_TEXT_SECRET_KEY})\b(?:[\"'])?\s*[:=]\s*)([\"'])(.*?)\2"
)
_UNQUOTED_KEY_VALUE_RE = re.compile(
    rf"(?im)((?:[\"'])?\b(?:{_TEXT_SECRET_KEY})\b(?:[\"'])?\s*[:=]\s*)([^\"'\s,;&}}]+)"
)
_ENV_VALUE_RE = re.compile(
    r"(?im)(\b[A-Z0-9_]*(?:API_KEY|PASSWORD|SECRET|TOKEN(?!S))[A-Z0-9_]*\s*=\s*)"
    r"([\"']?)([^\"'\s,;&]+)([\"']?)"
)


def _normalize_key(key: str) -> str:
    return key.strip().lower()


def is_sensitive_key(key: str) -> bool:
    """Return whether a mapping key is likely to contain a secret value."""
    normalized = _normalize_key(key)
    underscore_key = normalized.replace("-", "_")

    if normalized in _SENSITIVE_EXACT_KEYS or underscore_key in _SENSITIVE_EXACT_KEYS:
        return True
    if any(marker in normalized or marker in underscore_key for marker in _SENSITIVE_KEY_MARKERS):
        return True
    return underscore_key.endswith("_token") and not underscore_key.endswith("_tokens")


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


def _redacted_scalar(value: Any) -> Any:
    if value is None:
        return None
    if value == "":
        return ""
    return REDACTED


def redact_mapping(value: Any, *, key_name: str = "") -> Any:
    """Recursively redact sensitive keys from JSON-compatible structures."""
    jsonable = _jsonable(value)
    if key_name and is_sensitive_key(key_name):
        return _redacted_scalar(jsonable)
    if isinstance(jsonable, dict):
        return {str(key): redact_mapping(item, key_name=str(key)) for key, item in jsonable.items()}
    if isinstance(jsonable, list):
        return [redact_mapping(item) for item in jsonable]
    return jsonable


def _replace_quoted_secret_match(match: re.Match[str]) -> str:
    prefix, quote, value = match.groups()
    if not value or value == REDACTED:
        return match.group(0)
    return f"{prefix}{quote}{REDACTED}{quote}"


def _replace_unquoted_secret_match(match: re.Match[str]) -> str:
    prefix, value = match.groups()
    if not value or value == REDACTED:
        return match.group(0)
    return f"{prefix}{REDACTED}"


def _replace_env_secret_match(match: re.Match[str]) -> str:
    prefix, quote, value, trailing_quote = match.groups()
    if not value or value == REDACTED:
        return match.group(0)
    return f"{prefix}{quote}{REDACTED}{trailing_quote}"


def redact_text(text: str) -> str:
    """Redact common secret patterns from plain-text logs."""
    redacted = _PEM_PRIVATE_KEY_RE.sub(REDACTED, text)
    redacted = _AUTH_HEADER_RE.sub(rf"\1{REDACTED}", redacted)
    redacted = _QUOTED_KEY_VALUE_RE.sub(_replace_quoted_secret_match, redacted)
    redacted = _UNQUOTED_KEY_VALUE_RE.sub(_replace_unquoted_secret_match, redacted)
    return _ENV_VALUE_RE.sub(_replace_env_secret_match, redacted)
