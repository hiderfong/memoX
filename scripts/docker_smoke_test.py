#!/usr/bin/env python3
"""Build and smoke-test the Docker Compose deployment with offline config."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USERNAME = "admin"
PASSWORD = "smoke-password"


def _json_string(value: str | Path) -> str:
    return json.dumps(str(value))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_smoke_config(path: Path) -> None:
    path.write_text(
        f"""app:
  name: "MemoX Docker Smoke"
  debug: false
  log_level: "INFO"
  workspace: "/app/workspace"

server:
  host: "0.0.0.0"
  port: 8080
  cors_origins: []

coordinator:
  provider: "openai"
  model: "smoke-model"
  temperature: 0.1
  max_tokens: 128
  max_workers: 1
  task_timeout: 10

providers:
  openai:
    api_key: "smoke-key"
    base_url: "http://127.0.0.1:9/v1"

worker_templates: {{}}

knowledge_base:
  vector_store: "chroma"
  persist_directory: "/app/data/chroma"
  upload_directory: "/app/data/uploads"
  skills_dir: "/app/data/skills"
  embedding_provider: "hash"
  embedding_model: "hash-smoke"
  chunk_size: 200
  chunk_overlap: 20
  top_k: 3
  hybrid_search:
    enabled: true
    bm25_persist_path: "/app/data/bm25_index.pkl"
    rrf_k: 60
    chunk_strategy: "size"
  enable_graph: false
  graph_persist_path: "/app/data/knowledge_graph.gml"
  manifest_path: "/app/data/documents_manifest.json"

memory:
  enabled: true
  max_turns_before_compress: 10
  summary_max_chars: 500
  recent_messages_to_keep: 4

auth:
  enabled: true
  public_paths:
    - "/api/auth/login"
    - "/api/health"
    - "/api/docs"
    - "/api/redoc"
    - "/api/openapi.json"
    - "/api/files/"
  users:
    - username: "{USERNAME}"
      password: "{PASSWORD}"
      role: "admin"
      display_name: "Smoke Admin"

image_generation:
  enabled: false
video_generation:
  enabled: false
image_to_video:
  enabled: false
""",
        encoding="utf-8",
    )


def _write_compose_file(path: Path, config_path: Path, data_dir: Path, workspace_dir: Path, port: int) -> None:
    path.write_text(
        f"""services:
  memox:
    build:
      context: {_json_string(ROOT)}
      dockerfile: Dockerfile
    image: memox:local
    container_name: "memox-smoke-{port}"
    restart: "no"
    environment:
      MEMOX_CONFIG_PATH: /app/config.yaml
    ports:
      - "{port}:8080"
    volumes:
      - {_json_string(f"{config_path}:/app/config.yaml:ro")}
      - {_json_string(f"{data_dir}:/app/data")}
      - {_json_string(f"{workspace_dir}:/app/workspace")}
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/api/health"]
      interval: 30s
      timeout: 5s
      start_period: 60s
      retries: 3
""",
        encoding="utf-8",
    )


def _run(
    cmd: list[str],
    *,
    cwd: Path = ROOT,
    check: bool = True,
    capture: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture,
        timeout=timeout,
    )


def _request(method: str, url: str, *, body: dict | None = None, token: str | None = None) -> tuple[int, dict | str]:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read().decode("utf-8")
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return response.status, json.loads(payload)
        return response.status, payload


def _wait_for_health(base_url: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            status, data = _request("GET", f"{base_url}/api/health")
            if status == 200 and isinstance(data, dict):
                return data
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for container health: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Host port to bind. Defaults to a free port.")
    parser.add_argument("--timeout", type=float, default=180.0, help="Seconds to wait for health.")
    parser.add_argument("--build-timeout", type=float, default=1200.0, help="Seconds to allow for image build.")
    args = parser.parse_args()

    port = args.port or _find_free_port()
    project_name = f"memox-smoke-{port}"
    base_url = f"http://127.0.0.1:{port}"

    temp_parent = ROOT / ".docker-smoke"
    temp_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="memox-docker-smoke-", dir=temp_parent) as temp:
        temp_dir = Path(temp)
        config_path = temp_dir / "config.yaml"
        compose_path = temp_dir / "compose.yml"
        data_dir = temp_dir / "data"
        workspace_dir = temp_dir / "workspace"
        data_dir.mkdir()
        workspace_dir.mkdir()
        _write_smoke_config(config_path)
        _write_compose_file(compose_path, config_path, data_dir, workspace_dir, port)

        compose = [
            "docker",
            "compose",
            "--progress",
            "plain",
            "-p",
            project_name,
            "-f",
            str(compose_path),
        ]

        checks: list[dict] = []
        try:
            _run([*compose, "build"], capture=False, timeout=args.build_timeout)
            _run([*compose, "up", "-d", "--no-build"], capture=False, timeout=60)
            health = _wait_for_health(base_url, args.timeout)
            checks.append({"name": "health", "ok": health.get("status") == "healthy", "data": health})

            docs_status, _ = _request("GET", f"{base_url}/api/docs")
            checks.append({"name": "swagger docs", "ok": docs_status == 200, "status": docs_status})

            openapi_status, openapi = _request("GET", f"{base_url}/api/openapi.json")
            checks.append({
                "name": "openapi json",
                "ok": openapi_status == 200 and isinstance(openapi, dict) and openapi.get("info", {}).get("title") == "MemoX API",
                "status": openapi_status,
            })

            login_status, login = _request(
                "POST",
                f"{base_url}/api/auth/login",
                body={"username": USERNAME, "password": PASSWORD},
            )
            token = login.get("token") if isinstance(login, dict) else None
            checks.append({"name": "login", "ok": login_status == 200 and bool(token), "status": login_status})

            me_status, me = _request("GET", f"{base_url}/api/auth/me", token=token)
            checks.append({
                "name": "me",
                "ok": me_status == 200 and isinstance(me, dict) and me.get("username") == USERNAME,
                "status": me_status,
            })

            ok = all(item["ok"] for item in checks)
            print(json.dumps({"ok": ok, "base_url": base_url, "checks": checks}, ensure_ascii=False, indent=2))
            return 0 if ok else 1
        except Exception as exc:
            logs = _run([*compose, "logs", "--no-color", "--tail", "160", "memox"], check=False)
            command_output: dict[str, str] = {}
            if isinstance(exc, subprocess.CalledProcessError):
                command_output = {
                    "stdout": (exc.stdout or "")[-12000:],
                    "stderr": (exc.stderr or "")[-12000:],
                }
            print(json.dumps({
                "ok": False,
                "base_url": base_url,
                "error": str(exc),
                **command_output,
                "logs": logs.stdout[-12000:],
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        finally:
            _run([*compose, "down", "--remove-orphans"], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
