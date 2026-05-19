#!/usr/bin/env python3
"""Run an end-to-end backup/restore drill against a real MemoX process."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backup_restore import create_backup, restore_backup, verify_backup  # noqa: E402
from scripts.smoke_test import (  # noqa: E402
    Checks,
    ManagedProcess,
    _assert_port_free,
    _popen,
    _stop_process,
    _tail,
    _wait_for_http,
)

USERNAME = "admin"
PASSWORD = "restore-drill-pass"
WORKER_ID = "restore_worker"


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_restore_drill_config(root: Path, port: int) -> Path:
    """Write a deployment-like config that uses root-relative persistent paths."""
    config_path = root / "config.yaml"
    config_path.write_text(
        textwrap.dedent(
            f"""
            app:
              name: "MemoX Restore Drill"
              debug: false
              log_level: "INFO"
              workspace: "./workspace"

            server:
              host: "127.0.0.1"
              port: {port}
              cors_origins: []

            coordinator:
              provider: "openai"
              model: "restore-drill-coordinator"
              temperature: 0.1
              max_tokens: 128
              max_workers: 2
              task_timeout: 10

            providers:
              openai:
                api_key: "restore-drill-key"
                base_url: "http://127.0.0.1:9/v1"

            worker_templates:
              {WORKER_ID}:
                provider: "openai"
                model: "restore-drill-worker"
                temperature: 0.1
                skills: []
                tools: []
                display_name: "Restore Drill Worker"

            knowledge_base:
              vector_store: "chroma"
              persist_directory: "./data/chroma"
              upload_directory: "./data/uploads"
              skills_dir: "./data/skills"
              embedding_provider: "hash"
              embedding_model: "hash-restore-drill"
              chunk_size: 200
              chunk_overlap: 20
              top_k: 3
              hybrid_search:
                enabled: true
                bm25_persist_path: "./data/bm25_index.pkl"
                rrf_k: 60
                chunk_strategy: "size"
              enable_graph: false
              graph_persist_path: "./data/knowledge_graph.gml"
              manifest_path: "./data/documents_manifest.json"

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
                  display_name: "Restore Drill Admin"

            image_generation:
              enabled: false
            video_generation:
              enabled: false
            image_to_video:
              enabled: false
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (root / ".env").write_text("MEMOX_ADMIN_PASSWORD=restore-drill-pass\n", encoding="utf-8")
    return config_path


def start_deployment(root: Path, timeout: float) -> ManagedProcess:
    config_path = root / "config.yaml"
    log_path = root / "server.log"
    port = int(_base_url(config_path).rsplit(":", 1)[1])
    _assert_port_free("127.0.0.1", port)
    env = {
        "MEMOX_CONFIG_PATH": str(config_path),
        "PYTHONPATH": str(ROOT),
    }
    managed = _popen("restore-drill-backend", [sys.executable, "-m", "src.main"], cwd=root, log_path=log_path, env=env)
    _wait_for_http(_base_url(config_path) + "/api/health", timeout, log_path)
    return managed


def _base_url(config_path: Path) -> str:
    port = None
    for line in config_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("port:"):
            port = int(line.split(":", 1)[1].strip())
            break
    if port is None:
        raise RuntimeError(f"Could not read server.port from {config_path}")
    return f"http://127.0.0.1:{port}"


def _login(client: httpx.Client, base_url: str, checks: Checks) -> dict[str, str]:
    login = checks.record(
        "login",
        client.post(f"{base_url}/api/auth/login", json={"username": USERNAME, "password": PASSWORD}),
        {200},
    ).json()
    return {"Authorization": f"Bearer {login['token']}"}


def seed_source_deployment(base_url: str) -> dict[str, Any]:
    checks = Checks()
    unique = f"restore-drill-keyword-{int(time.time())}"
    content = textwrap.dedent(
        f"""
        # Restore Drill Probe

        This document proves MemoX backup restoration keeps searchable knowledge.
        Unique restore phrase: {unique}.
        """
    ).strip()

    with httpx.Client(timeout=180.0) as client:
        checks.record("source health", client.get(f"{base_url}/api/health"), {200})
        headers = _login(client, base_url, checks)
        workers = checks.record("source workers", client.get(f"{base_url}/api/workers", headers=headers), {200}).json()
        if not any(worker.get("id") == WORKER_ID for worker in workers):
            raise RuntimeError(f"configured worker {WORKER_ID!r} missing before backup: {workers}")

        upload = checks.record(
            "source upload document",
            client.post(
                f"{base_url}/api/documents",
                headers=headers,
                files={"file": ("restore-drill.md", content.encode("utf-8"), "text/markdown")},
            ),
            {200},
        ).json()
        doc_id = upload["id"]
        if upload.get("chunk_count", 0) < 1:
            raise RuntimeError(f"source upload produced no chunks: {upload}")

        search = checks.record(
            "source document search",
            client.get(f"{base_url}/api/documents/search", params={"q": unique}, headers=headers),
            {200},
        ).json()
        if not search.get("results"):
            raise RuntimeError(f"source search returned no results: {search}")

    return {
        "checks": checks.items,
        "doc_id": doc_id,
        "search_query": unique,
    }


def check_restored_deployment(base_url: str, *, doc_id: str, search_query: str, workspace_file: Path) -> dict[str, Any]:
    checks = Checks()
    with httpx.Client(timeout=180.0) as client:
        checks.record("restored health", client.get(f"{base_url}/api/health"), {200})
        checks.record("restored openapi json", client.get(f"{base_url}/api/openapi.json"), {200})
        headers = _login(client, base_url, checks)

        docs = checks.record("restored documents", client.get(f"{base_url}/api/documents", headers=headers), {200}).json()
        if not any(doc.get("id") == doc_id for doc in docs):
            raise RuntimeError(f"restored document {doc_id} missing from list: {docs}")

        chunks = checks.record(
            "restored document chunks",
            client.get(f"{base_url}/api/documents/{doc_id}/chunks", headers=headers),
            {200},
        ).json()
        if chunks.get("chunk_count", 0) < 1:
            raise RuntimeError(f"restored chunks endpoint returned no chunks: {chunks}")

        search = checks.record(
            "restored document search",
            client.get(f"{base_url}/api/documents/search", params={"q": search_query}, headers=headers),
            {200},
        ).json()
        if not search.get("results"):
            raise RuntimeError(f"restored search returned no results: {search}")

        workers = checks.record("restored workers", client.get(f"{base_url}/api/workers", headers=headers), {200}).json()
        if not any(worker.get("id") == WORKER_ID for worker in workers):
            raise RuntimeError(f"configured worker {WORKER_ID!r} missing after restore: {workers}")

    if workspace_file.read_text(encoding="utf-8") != "restore drill workspace artifact\n":
        raise RuntimeError(f"workspace artifact was not restored correctly: {workspace_file}")

    return {
        "checks": checks.items,
        "doc_id": doc_id,
        "docs_after_restore": len(docs),
        "workspace_file": str(workspace_file),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Backend port. Defaults to a free local port.")
    parser.add_argument("--timeout", type=float, default=180.0, help="Seconds to wait for each restored server start.")
    parser.add_argument("--keep-data", action="store_true", help="Keep the temporary drill directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    port = args.port or _find_free_port()
    drill_dir = Path(tempfile.mkdtemp(prefix="memox-restore-drill-"))
    source_root = drill_dir / "source"
    restored_root = drill_dir / "restored"
    archive_path = drill_dir / "memox-restore-drill.tar.gz"
    source: ManagedProcess | None = None
    restored: ManagedProcess | None = None

    try:
        source_root.mkdir()
        write_restore_drill_config(source_root, port)
        source = start_deployment(source_root, args.timeout)
        source_seed = seed_source_deployment(_base_url(source_root / "config.yaml"))

        workspace_file = source_root / "workspace" / "task-restore-drill" / "shared" / "artifact.txt"
        workspace_file.parent.mkdir(parents=True, exist_ok=True)
        workspace_file.write_text("restore drill workspace artifact\n", encoding="utf-8")

        _stop_process(source)
        source = None

        backup = create_backup(root=source_root, output=archive_path)
        verified = verify_backup(archive_path)
        restored_backup = restore_backup(archive=archive_path, target=restored_root)

        restored = start_deployment(restored_root, args.timeout)
        restored_result = check_restored_deployment(
            _base_url(restored_root / "config.yaml"),
            doc_id=source_seed["doc_id"],
            search_query=source_seed["search_query"],
            workspace_file=restored_root / "workspace" / "task-restore-drill" / "shared" / "artifact.txt",
        )

        _print_json(
            {
                "ok": True,
                "drill_dir": str(drill_dir),
                "archive": str(archive_path),
                "backup_entries": len(backup["entries"]),
                "verified_entries": len(verified["entries"]),
                "restored_entries": restored_backup["entry_count"],
                "source": source_seed,
                "restored": restored_result,
            }
        )
        return 0
    except Exception as exc:
        _print_json(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "drill_dir": str(drill_dir),
                "source_log_tail": _tail(source.log_path) if source else "",
                "restored_log_tail": _tail(restored.log_path) if restored else "",
            }
        )
        return 1
    finally:
        _stop_process(restored)
        _stop_process(source)
        if args.keep_data:
            print(f"Kept restore drill directory: {drill_dir}")
        else:
            shutil.rmtree(drill_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
