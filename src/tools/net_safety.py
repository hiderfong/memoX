"""Network safety checks shared by web-capable tools."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlparse


class WebSafetyError(ValueError):
    """Raised when a URL is unsafe for server-side fetch."""


BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "host.docker.internal",
    "metadata.google.internal",
}


def configured_internal_host_allowlist() -> list[str]:
    """Return explicit internal host allowlist from runtime config, if available."""
    try:
        from config import get_config

        config = get_config()
        return list(getattr(config.tool_policy.network, "allow_internal_hosts", []) or [])
    except Exception:
        return []


def _default_port(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def _effective_port(parsed) -> int | None:
    return parsed.port or _default_port(parsed.scheme)


def _normalise_allow_entry(entry: str):
    value = str(entry).strip()
    if not value:
        return None
    return urlparse(value if "://" in value else f"//{value}")


def _matches_allowlist(parsed, allow_internal_hosts: Iterable[str] | None) -> bool:
    hostname = (parsed.hostname or "").lower()
    port = _effective_port(parsed)
    for entry in allow_internal_hosts or []:
        allowed = _normalise_allow_entry(entry)
        if not allowed or not allowed.hostname:
            continue
        if allowed.hostname.lower() != hostname:
            continue
        allowed_port = allowed.port
        if allowed_port is None or allowed_port == port:
            return True
    return False


def _is_blocked_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return not ip.is_global


def _resolved_ips(hostname: str, port: int | None) -> set[str]:
    try:
        infos = socket.getaddrinfo(hostname, port or 0, type=socket.SOCK_STREAM)
    except OSError:
        return set()
    return {info[4][0] for info in infos if info and info[4]}


def validate_public_http_url(
    url: str,
    *,
    allow_internal_hosts: Iterable[str] | None = None,
) -> str:
    """Validate a URL before server-side network access.

    The default policy allows public http/https targets only. Internal hosts can
    be re-enabled explicitly through exact host or host:port allowlist entries.
    """
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"}:
        raise WebSafetyError("只支持 http/https URL")
    if not parsed.hostname:
        raise WebSafetyError("URL 缺少 hostname")

    hostname = parsed.hostname.lower()
    if _matches_allowlist(parsed, allow_internal_hosts):
        return parsed.geturl()

    if hostname in BLOCKED_HOSTNAMES or hostname.endswith(".local"):
        raise WebSafetyError("禁止访问本机、内网或保留主机名")

    try:
        direct_ip = ipaddress.ip_address(hostname)
    except ValueError:
        for resolved_ip in _resolved_ips(hostname, _effective_port(parsed)):
            try:
                if _is_blocked_ip(resolved_ip):
                    raise WebSafetyError("禁止访问解析到内网、本机或保留 IP 的主机")
            except ValueError:
                continue
    else:
        if _is_blocked_ip(str(direct_ip)):
            raise WebSafetyError("禁止访问内网、本机或保留 IP 地址")

    return parsed.geturl()
