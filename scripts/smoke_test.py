#!/usr/bin/env python3
"""Run an isolated MemoX smoke test.

Usage:
    uv run --extra dev python scripts/smoke_test.py
    uv run --extra dev python scripts/smoke_test.py --frontend

The script starts a temporary backend with throwaway data, uses a deterministic
in-process embedding function, runs HTTP checks, and then cleans up processes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"
USERNAME = "admin"
PASSWORD = "smoke-pass"


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen
    log_path: Path


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def record(self, name: str, response: httpx.Response, expected: set[int]) -> httpx.Response:
        ok = response.status_code in expected
        self.items.append(
            {
                "name": name,
                "status": response.status_code,
                "expected": sorted(expected),
                "ok": ok,
            }
        )
        if not ok:
            body = response.text[:1000]
            raise RuntimeError(f"{name} failed: status={response.status_code}, body={body}")
        return response


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _tail(path: Path, line_count: int = 120) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-line_count:])


def _assert_port_free(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise RuntimeError(f"{host}:{port} is already in use") from exc


def _wait_for_http(url: str, timeout_seconds: float, log_path: Path | None = None) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=5.0)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0)

    details = f"Timed out waiting for {url}. Last error: {last_error}"
    if log_path is not None:
        details += f"\n\n--- process log tail ---\n{_tail(log_path)}"
    raise RuntimeError(details)


def _popen(name: str, cmd: list[str], cwd: Path, log_path: Path, env: dict[str, str] | None = None) -> ManagedProcess:
    proc_env = os.environ.copy()
    proc_env["PYTHONUNBUFFERED"] = "1"
    if env:
        proc_env.update(env)

    log_file = log_path.open("w", encoding="utf-8")
    try:
        kwargs: dict[str, Any] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        process = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=proc_env,
            **kwargs,
        )
    finally:
        log_file.close()
    return ManagedProcess(name=name, process=process, log_path=log_path)


def _stop_process(managed: ManagedProcess | None) -> None:
    if managed is None or managed.process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(managed.process.pid, signal.SIGTERM)
        else:
            managed.process.terminate()
        managed.process.wait(timeout=10)
    except Exception:
        if managed.process.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(managed.process.pid, signal.SIGKILL)
                else:
                    managed.process.kill()
            finally:
                managed.process.wait(timeout=10)


def _backend_launcher_code(root: Path, data_dir: Path, port: int) -> str:
    return textwrap.dedent(
        f"""
        import sys
        import os
        from pathlib import Path

        root = Path({str(root)!r})
        data_dir = Path({str(data_dir)!r})
        config_path = data_dir / "config.yaml"
        os.environ["MEMOX_CONFIG_PATH"] = str(config_path)
        (data_dir / "data").mkdir(parents=True, exist_ok=True)
        (data_dir / "data" / "smoke.txt").write_text("smoke persistent data\\n", encoding="utf-8")
        (data_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (data_dir / "workspace" / "smoke.txt").write_text("smoke workspace artifact\\n", encoding="utf-8")
        config_path.write_text("app:\\n  name: MemoX Smoke\\n", encoding="utf-8")
        sys.path.insert(0, str(root / "src"))

        from config import Config
        import config as cfg

        cfg._config = Config._from_dict({{
            "app": {{
                "name": "MemoX Smoke",
                "debug": False,
                "log_level": "INFO",
                "workspace": str(data_dir / "workspace"),
            }},
            "server": {{
                "host": "127.0.0.1",
                "port": {port},
                "cors_origins": ["http://127.0.0.1:3000", "http://localhost:3000"],
            }},
            "coordinator": {{
                "model": "smoke-model",
                "provider": "openai",
                "temperature": 0.1,
                "max_tokens": 128,
                "max_workers": 2,
                "task_timeout": 10,
            }},
            "providers": {{
                "openai": {{"api_key": "smoke-key", "base_url": "http://127.0.0.1:9/v1"}},
            }},
            "worker_templates": {{}},
            "knowledge_base": {{
                "vector_store": "chroma",
                "persist_directory": str(data_dir / "chroma"),
                "upload_directory": str(data_dir / "uploads"),
                "skills_dir": str(data_dir / "skills"),
                "embedding_provider": "hash",
                "embedding_model": "hash-smoke",
                "chunk_size": 200,
                "chunk_overlap": 20,
                "top_k": 3,
                "hybrid_search": {{
                    "enabled": True,
                    "bm25_persist_path": str(data_dir / "bm25_index.pkl"),
                    "rrf_k": 60,
                    "chunk_strategy": "size",
                }},
                "enable_graph": False,
                "graph_persist_path": str(data_dir / "knowledge_graph.gml"),
                "manifest_path": str(data_dir / "documents_manifest.json"),
            }},
            "memory": {{
                "enabled": True,
                "max_turns_before_compress": 10,
                "summary_max_chars": 500,
                "recent_messages_to_keep": 4,
            }},
            "auth": {{
                "enabled": True,
                "public_paths": [
                    "/api/auth/login",
                    "/api/health",
                    "/api/docs",
                    "/api/openapi.json",
                    "/api/files/",
                ],
                "users": [
                    {{
                        "username": "{USERNAME}",
                        "password": "{PASSWORD}",
                        "role": "admin",
                        "display_name": "Smoke Admin",
                    }}
                ],
            }},
            "image_generation": {{"enabled": False}},
            "video_generation": {{"enabled": False}},
            "image_to_video": {{"enabled": False}},
        }})

        import uvicorn
        from web.api import app

        uvicorn.run(app, host="127.0.0.1", port={port}, log_level="info")
        """
    )


def start_backend(data_dir: Path, port: int, timeout: float) -> ManagedProcess:
    _assert_port_free("127.0.0.1", port)
    launcher = data_dir / "smoke_backend.py"
    launcher.write_text(_backend_launcher_code(ROOT, data_dir, port), encoding="utf-8")
    managed = _popen(
        "backend",
        [sys.executable, str(launcher)],
        cwd=ROOT,
        log_path=data_dir / "backend.log",
    )
    _wait_for_http(f"http://127.0.0.1:{port}/api/health", timeout, managed.log_path)
    return managed


def start_frontend(data_dir: Path, port: int, timeout: float) -> ManagedProcess:
    _assert_port_free("127.0.0.1", port)
    vite_bin = FRONTEND_DIR / "node_modules" / ".bin" / "vite"
    if not vite_bin.exists():
        raise RuntimeError("frontend dependencies are missing; run `npm ci` in frontend/ first")
    managed = _popen(
        "frontend",
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port)],
        cwd=FRONTEND_DIR,
        log_path=data_dir / "frontend.log",
    )
    _wait_for_http(f"http://127.0.0.1:{port}/", timeout, managed.log_path)
    return managed


def run_operational_checks(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str],
    checks: Checks,
    *,
    prefix: str = "",
) -> dict[str, Any]:
    def check_name(name: str) -> str:
        return f"{prefix}{name}" if prefix else name

    initial_backups = checks.record(
        check_name("system backups"),
        client.get(f"{base_url}/api/system/backups", headers=headers),
        {200},
    ).json()
    if not isinstance(initial_backups.get("backups"), list):
        raise RuntimeError(f"system backups returned unexpected payload: {initial_backups}")

    initial_events = checks.record(
        check_name("system events"),
        client.get(f"{base_url}/api/system/events", params={"limit": 5}, headers=headers),
        {200},
    ).json()
    if not isinstance(initial_events.get("events"), list):
        raise RuntimeError(f"system events returned unexpected payload: {initial_events}")

    maintenance = checks.record(
        check_name("run backup maintenance"),
        client.post(f"{base_url}/api/system/maintenance/backup", params={"force": "true"}, headers=headers),
        {200},
    ).json()
    if not maintenance.get("ok"):
        raise RuntimeError(f"backup maintenance failed: {maintenance}")
    archive_name = Path(str(maintenance.get("archive") or "")).name
    if not archive_name:
        raise RuntimeError(f"backup maintenance did not return an archive: {maintenance}")

    backups = checks.record(
        check_name("system backups after maintenance"),
        client.get(f"{base_url}/api/system/backups", headers=headers),
        {200},
    ).json()
    if not any(backup.get("name") == archive_name for backup in backups.get("backups", [])):
        raise RuntimeError(f"created backup {archive_name} missing from backup list: {backups}")

    verified = checks.record(
        check_name("verify backup archive"),
        client.post(f"{base_url}/api/system/backups/{archive_name}/verify", headers=headers),
        {200},
    ).json()
    if not verified.get("ok") or not verified.get("verified"):
        raise RuntimeError(f"backup verification failed: {verified}")

    preflight = checks.record(
        check_name("restore preflight"),
        client.post(f"{base_url}/api/system/backups/{archive_name}/restore-preflight", headers=headers),
        {200},
    ).json()
    if not preflight.get("ok") or preflight.get("writes_performed") is not False:
        raise RuntimeError(f"restore preflight failed: {preflight}")

    drill = checks.record(
        check_name("restore drill"),
        client.post(f"{base_url}/api/system/backups/{archive_name}/restore-drill", headers=headers),
        {200},
    ).json()
    if not drill.get("ok"):
        raise RuntimeError(f"restore drill failed: {drill}")

    events = checks.record(
        check_name("system events after restore drill"),
        client.get(f"{base_url}/api/system/events", params={"limit": 10}, headers=headers),
        {200},
    ).json()
    event_types = {event.get("event_type") for event in events.get("events", [])}
    if not {"backup_maintenance", "restore_preflight", "restore_drill"}.issubset(event_types):
        raise RuntimeError(f"operational events missing expected types: {events}")

    return {
        "archive_name": archive_name,
        "backup_count": backups.get("count", 0),
        "event_count": events.get("count", 0),
    }


def run_backend_checks(base_url: str) -> dict[str, Any]:
    checks = Checks()
    unique = f"memox-smoke-keyword-{int(time.time())}"
    workflow_yaml = """
workflow:
  name: smoke-workflow
  steps:
    - id: first
      worker: researcher
      input: Smoke validation step
"""
    content = textwrap.dedent(
        f"""
        # MemoX Smoke Document

        MemoX smoke testing verifies document upload, retrieval, and search.
        The unique phrase is {unique}.
        """
    ).strip().encode("utf-8")

    with httpx.Client(timeout=180.0) as client:
        checks.record("health", client.get(f"{base_url}/api/health"), {200})
        checks.record("swagger docs", client.get(f"{base_url}/api/docs"), {200})
        openapi = checks.record("openapi json", client.get(f"{base_url}/api/openapi.json"), {200}).json()
        if openapi.get("info", {}).get("title") != "MemoX API":
            raise RuntimeError(f"unexpected OpenAPI title: {openapi.get('info')}")
        checks.record("documents require auth", client.get(f"{base_url}/api/documents"), {401})

        login = checks.record(
            "login",
            client.post(f"{base_url}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}),
            {200},
        ).json()
        headers = {"Authorization": f"Bearer {login['token']}"}

        me = checks.record("me", client.get(f"{base_url}/api/auth/me", headers=headers), {200}).json()
        if me.get("username") != USERNAME:
            raise RuntimeError(f"/api/auth/me returned unexpected user: {me}")

        system_health = checks.record(
            "system health",
            client.get(f"{base_url}/api/system/health", headers=headers),
            {200},
        ).json()
        if system_health.get("status") == "error":
            raise RuntimeError(f"system health reported error: {system_health}")

        ops_result = run_operational_checks(client, base_url, headers, checks)

        before_docs = checks.record(
            "list documents before upload",
            client.get(f"{base_url}/api/documents", headers=headers),
            {200},
        ).json()

        upload = checks.record(
            "upload markdown document",
            client.post(
                f"{base_url}/api/documents",
                headers=headers,
                files={"file": ("smoke.md", content, "text/markdown")},
            ),
            {200},
        ).json()
        doc_id = upload["id"]
        if upload.get("chunk_count", 0) < 1:
            raise RuntimeError(f"upload returned no chunks: {upload}")

        after_docs = checks.record(
            "list documents after upload",
            client.get(f"{base_url}/api/documents", headers=headers),
            {200},
        ).json()
        if not any(doc.get("id") == doc_id for doc in after_docs):
            raise RuntimeError(f"uploaded doc {doc_id} missing from list: {after_docs}")

        chunks = checks.record(
            "document chunks",
            client.get(f"{base_url}/api/documents/{doc_id}/chunks", headers=headers),
            {200},
        ).json()
        if chunks.get("chunk_count", 0) < 1:
            raise RuntimeError(f"chunks endpoint returned no chunks: {chunks}")

        search = checks.record(
            "document search",
            client.get(f"{base_url}/api/documents/search", params={"q": unique}, headers=headers),
            {200},
        ).json()
        if not search.get("results"):
            raise RuntimeError(f"search returned no results: {search}")

        groups = checks.record("groups", client.get(f"{base_url}/api/groups", headers=headers), {200}).json()
        if not groups:
            raise RuntimeError("groups endpoint returned an empty list")

        checks.record("workers", client.get(f"{base_url}/api/workers", headers=headers), {200})

        validation = checks.record(
            "workflow validate",
            client.post(f"{base_url}/api/workflows/validate", json={"yaml_content": workflow_yaml}, headers=headers),
            {200},
        ).json()
        if not validation.get("valid"):
            raise RuntimeError(f"workflow validation failed: {validation}")

        checks.record("workflow runs list", client.get(f"{base_url}/api/workflows/runs", headers=headers), {200})

        ssrf = checks.record(
            "url import blocks localhost",
            client.post(f"{base_url}/api/documents/url", json={"url": f"{base_url}/api/health"}, headers=headers),
            {400},
        ).json()

    return {
        "checks": checks.items,
        "uploaded_doc_id": doc_id,
        "docs_before": len(before_docs),
        "docs_after": len(after_docs),
        "ops": ops_result,
        "ssrf_detail": ssrf.get("detail"),
    }


def run_frontend_checks(frontend_url: str) -> dict[str, Any]:
    checks = Checks()
    unique = f"frontend-proxy-keyword-{int(time.time())}"

    with httpx.Client(timeout=180.0) as client:
        html = checks.record("vite index", client.get(f"{frontend_url}/"), {200}).text
        if "root" not in html or "script" not in html:
            raise RuntimeError("Vite index did not look like an app shell")

        main = checks.record("vite module", client.get(f"{frontend_url}/src/main.tsx"), {200}).text
        if "ReactDOM" not in main:
            raise RuntimeError("Vite module transform did not return main.tsx content")

        checks.record("proxy health", client.get(f"{frontend_url}/api/health"), {200})
        checks.record("proxy swagger docs", client.get(f"{frontend_url}/api/docs"), {200})
        checks.record("proxy openapi json", client.get(f"{frontend_url}/api/openapi.json"), {200})
        login = checks.record(
            "proxy login",
            client.post(f"{frontend_url}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}),
            {200},
        ).json()
        headers = {"Authorization": f"Bearer {login['token']}"}

        me = checks.record("proxy me", client.get(f"{frontend_url}/api/auth/me", headers=headers), {200}).json()
        if me.get("username") != USERNAME:
            raise RuntimeError(f"proxy /api/auth/me returned unexpected user: {me}")

        proxy_system_health = checks.record(
            "proxy system health",
            client.get(f"{frontend_url}/api/system/health", headers=headers),
            {200},
        ).json()
        if proxy_system_health.get("status") == "error":
            raise RuntimeError(f"proxy system health reported error: {proxy_system_health}")

        ops_result = run_operational_checks(client, frontend_url, headers, checks, prefix="proxy ")

        upload = checks.record(
            "proxy upload document",
            client.post(
                f"{frontend_url}/api/documents",
                headers=headers,
                files={"file": ("frontend-smoke.md", f"# Frontend Smoke\n\n{unique}".encode(), "text/markdown")},
            ),
            {200},
        ).json()

        search = checks.record(
            "proxy document search",
            client.get(f"{frontend_url}/api/documents/search", params={"q": unique}, headers=headers),
            {200},
        ).json()
        if not search.get("results"):
            raise RuntimeError(f"proxy search returned no results: {search}")

    return {"checks": checks.items, "uploaded_doc_id": upload.get("id"), "ops": ops_result}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run isolated MemoX smoke checks.")
    parser.add_argument("--frontend", action="store_true", help="Also start Vite and check frontend proxy behavior.")
    parser.add_argument("--backend-port", type=int, default=None, help="Backend port. Defaults to 8080 with --frontend, else 18080.")
    parser.add_argument("--frontend-port", type=int, default=3000, help="Frontend port for --frontend.")
    parser.add_argument("--timeout", type=float, default=180.0, help="Seconds to wait for each server to become healthy.")
    parser.add_argument("--keep-data", action="store_true", help="Keep the temporary smoke data directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    backend_port = args.backend_port if args.backend_port is not None else (8080 if args.frontend else 18080)
    if args.frontend and backend_port != 8080:
        raise SystemExit("frontend smoke requires backend port 8080 because vite.config.ts proxies /api there")

    data_dir = Path(tempfile.mkdtemp(prefix="memox-smoke-"))
    backend: ManagedProcess | None = None
    frontend: ManagedProcess | None = None

    try:
        backend = start_backend(data_dir, backend_port, args.timeout)
        backend_result = run_backend_checks(f"http://127.0.0.1:{backend_port}")

        result: dict[str, Any] = {
            "ok": True,
            "data_dir": str(data_dir),
            "backend": backend_result,
        }

        if args.frontend:
            frontend = start_frontend(data_dir, args.frontend_port, args.timeout)
            result["frontend"] = run_frontend_checks(f"http://127.0.0.1:{args.frontend_port}")

        _print_json(result)
        return 0
    except Exception as exc:
        failure = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "data_dir": str(data_dir),
            "backend_log_tail": _tail(backend.log_path) if backend else "",
            "frontend_log_tail": _tail(frontend.log_path) if frontend else "",
        }
        _print_json(failure)
        return 1
    finally:
        _stop_process(frontend)
        _stop_process(backend)
        if args.keep_data:
            print(f"Kept smoke data directory: {data_dir}")
        else:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
