from __future__ import annotations

import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser_e2e,
    pytest.mark.skipif(
        os.getenv("MEMOX_BROWSER_E2E") != "1",
        reason="set MEMOX_BROWSER_E2E=1 to run browser UI E2E tests",
    ),
]

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend_wip"
BACKEND_PORT = 8080
FRONTEND_PORT = 3000
USERNAME = "admin"
PASSWORD = "pw"


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    log_path: Path


def _tail(path: Path, line_count: int = 100) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-line_count:])


def _assert_port_free(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            pytest.skip(f"{host}:{port} is already in use: {exc}")


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
            env=proc_env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
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
            if os.name != "nt":
                os.killpg(managed.process.pid, signal.SIGKILL)
            else:
                managed.process.kill()
            managed.process.wait(timeout=10)


def _wait_for_http(url: str, managed: ManagedProcess, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        if managed.process.poll() is not None:
            raise RuntimeError(
                f"{managed.name} exited before {url} became available.\n\n--- log tail ---\n{_tail(managed.log_path)}"
            )
        try:
            response = httpx.get(url, timeout=5.0)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}\n\n--- log tail ---\n{_tail(managed.log_path)}")


def _write_browser_config(root: Path, *, enable_graph: bool = False) -> Path:
    data = root / "data"
    for name in ["chroma", "uploads", "skills"]:
        (data / name).mkdir(parents=True, exist_ok=True)
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    config = {
        "app": {
            "name": "MemoX Browser E2E",
            "debug": True,
            "log_level": "INFO",
            "workspace": str(root / "workspace"),
        },
        "server": {
            "host": "127.0.0.1",
            "port": BACKEND_PORT,
            "cors_origins": ["http://127.0.0.1:3000", "http://localhost:3000"],
        },
        "coordinator": {
            "provider": "openai",
            "model": "smoke-model",
            "temperature": 0,
            "max_tokens": 512,
            "max_workers": 1,
            "task_timeout": 30,
            "task_auto_retry_enabled": False,
            "task_auto_retry_max_attempts": 0,
            "task_auto_retry_initial_delay_seconds": 1,
            "task_auto_retry_max_delay_seconds": 1,
            "task_auto_retry_backoff_multiplier": 1,
        },
        "providers": {
            "openai": {"api_key": "smoke-key", "base_url": "http://127.0.0.1:9/v1"},
            "dashscope": {"api_key": "", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        },
        "worker_templates": {},
        "tool_policy": {
            "network": {"allow_internal_hosts": []},
            "web": {
                "request_timeout_seconds": 15,
                "max_response_bytes": 2_000_000,
                "max_fetch_chars": 20_000,
                "max_search_results": 10,
            },
            "playwright_crawler": {
                "max_concurrency": 2,
                "queue_timeout_seconds": 10,
                "total_timeout_seconds": 45,
                "navigation_timeout_ms": 30_000,
                "selector_timeout_ms": 10_000,
                "idle_wait_ms": 2_000,
                "max_pages": 1,
                "max_response_bytes": 5_000_000,
                "max_output_chars": 8_000,
            },
            "database": {
                "default_access_mode": "read_only",
                "allow_raw_connection_strings": True,
                "allow_write": True,
                "allow_ddl": False,
                "allow_multiple_statements": False,
                "max_result_rows": 200,
                "data_sources": {},
            },
        },
        "knowledge_base": {
            "vector_store": "chroma",
            "persist_directory": str(data / "chroma"),
            "upload_directory": str(data / "uploads"),
            "skills_dir": str(data / "skills"),
            "embedding_provider": "hash",
            "embedding_model": "hash-browser-e2e",
            "chunk_size": 500,
            "chunk_overlap": 50,
            "top_k": 5,
            "hybrid_search": {
                "enabled": True,
                "bm25_persist_path": str(data / "bm25_index.pkl"),
                "rrf_k": 60,
                "chunk_strategy": "size",
            },
            "enable_graph": enable_graph,
            "graph_persist_path": str(data / "knowledge_graph.gml"),
            "manifest_path": str(data / "documents_manifest.json"),
            "graph_llm_provider": "dashscope",
            "graph_llm_api_key": "",
        },
        "memory": {
            "enabled": True,
            "max_turns_before_compress": 10,
            "summary_max_chars": 500,
            "recent_messages_to_keep": 4,
        },
        "ops": {
            "auto_backup_enabled": False,
            "auto_backup_interval_hours": 24,
            "auto_backup_startup_delay_seconds": 300,
            "auto_backup_include": ["config.yaml", "data", "workspace"],
            "max_backups": 14,
            "archive_mirror_dir": "",
            "ops_event_retention_days": 90,
            "audit_log_retention_days": 180,
            "task_job_retention_days": 30,
            "diagnostic_retention_days": 30,
            "max_diagnostic_bundles": 20,
        },
        "auth": {
            "enabled": True,
            "public_paths": ["/api/auth/login", "/api/health", "/api/docs", "/api/redoc", "/api/openapi.json"],
            "users": [
                {
                    "username": USERNAME,
                    "password": PASSWORD,
                    "role": "admin",
                    "display_name": "管理员",
                }
            ],
        },
        "file_access": {"signing_secret": "browser-e2e-file-secret", "signed_url_ttl_seconds": 300},
        "image_generation": {"enabled": False, "api_key": ""},
        "video_generation": {"enabled": False, "api_key": ""},
        "image_to_video": {"enabled": False, "api_key": ""},
    }
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return config_path


def _seed_knowledge_graph_quality_issue(root: Path) -> None:
    from src.knowledge.knowledge_graph import NetworkXKnowledgeGraph, Triple

    graph_path = root / "data" / "knowledge_graph.gml"
    kg = NetworkXKnowledgeGraph(persist_path=str(graph_path), enabled=True)
    kg.add_triple(Triple("MemoX", "支持", "长期记忆", "doc_ok_chunk_0", 0.92))
    kg.add_triple(Triple("MemoX", "包含", "知识图谱", "doc_ok_chunk_1", 0.88))
    kg.add_triple(Triple("噪声实体", "关联", "偶发对象", "doc_bad_chunk_0", 0.25))
    kg.save()


def _start_backend(tmp_path: Path, config_path: Path) -> ManagedProcess:
    _assert_port_free("127.0.0.1", BACKEND_PORT)
    managed = _popen(
        "backend",
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.web.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(BACKEND_PORT),
        ],
        cwd=ROOT,
        log_path=tmp_path / "backend.log",
        env={"MEMOX_CONFIG_PATH": str(config_path)},
    )
    _wait_for_http(f"http://127.0.0.1:{BACKEND_PORT}/api/health", managed)
    return managed


def _start_frontend(tmp_path: Path) -> ManagedProcess:
    _assert_port_free("127.0.0.1", FRONTEND_PORT)
    vite_bin = FRONTEND_DIR / "node_modules" / ".bin" / "vite"
    if not vite_bin.exists():
        pytest.skip("frontend dependencies are missing; run `npm ci` in frontend_wip/ first")
    managed = _popen(
        "frontend",
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(FRONTEND_PORT)],
        cwd=FRONTEND_DIR,
        log_path=tmp_path / "frontend.log",
    )
    _wait_for_http(f"http://127.0.0.1:{FRONTEND_PORT}/", managed)
    return managed


def _seed_tool_audit(db_path: Path) -> None:
    details = {
        "tool_name": "web_fetch",
        "status": "success",
        "duration_ms": 42,
        "worker_id": "browser-ui-worker",
        "worker_name": "Browser UI Worker",
        "task_id": "browser-ui-task",
        "arguments": {"url": "https://example.com"},
        "result": {"title": "Example Domain"},
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO audit_log
               (timestamp, username, user_role, action, resource, resource_id, details, ip_address)
               VALUES (strftime('%Y-%m-%dT%H:%M:%f', 'now'), ?, ?, ?, ?, ?, ?, ?)""",
            (
                USERNAME,
                "admin",
                "tool_call",
                "tool",
                "web_fetch",
                json.dumps(details, ensure_ascii=False),
                "127.0.0.1",
            ),
        )
        conn.commit()


def _login_and_token(base_url: str) -> str:
    response = httpx.post(
        f"{base_url}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=10,
    )
    response.raise_for_status()
    return str(response.json()["token"])


def _assert_saved_policy(base_url: str, token: str, expected_max_results: int) -> None:
    response = httpx.get(
        f"{base_url}/api/system/tool-policy",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    response.raise_for_status()
    assert response.json()["web"]["max_search_results"] == expected_max_results


def test_admin_settings_web_policy_and_tool_audit_browser_flow(tmp_path: Path) -> None:
    config_path = _write_browser_config(tmp_path)
    backend: ManagedProcess | None = None
    frontend: ManagedProcess | None = None
    backend_base_url = f"http://127.0.0.1:{BACKEND_PORT}"
    frontend_base_url = f"http://127.0.0.1:{FRONTEND_PORT}"

    try:
        backend = _start_backend(tmp_path, config_path)
        frontend = _start_frontend(tmp_path)
        _seed_tool_audit(tmp_path / "data" / "memox.db")

        console_errors: list[str] = []
        playwright = None
        browser = None
        try:
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            if playwright is not None:
                playwright.stop()
            pytest.skip(f"Playwright browser is not available in this environment: {str(exc).splitlines()[0]}")

        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.on(
                "console",
                lambda message: console_errors.append(message.text) if message.type == "error" else None,
            )

            page.goto(f"{frontend_base_url}/login")
            expect(page.get_by_role("heading", name="MemoX")).to_be_visible()
            page.get_by_placeholder("用户名").fill(USERNAME)
            page.get_by_placeholder("密码").fill(PASSWORD)
            page.get_by_role("button", name=re.compile(r"登\s*录")).click()
            expect(page).to_have_url(re.compile(r".*/documents$"))

            page.goto(f"{frontend_base_url}/settings")
            tool_policy_card = page.get_by_test_id("tool-policy-card")
            expect(tool_policy_card).to_contain_text("Web 搜索与抓取资源")
            max_results = page.locator(
                '[data-testid="web-max-search-results"] input, input[data-testid="web-max-search-results"]'
            )
            expect(max_results).to_have_value("10")
            max_results.fill("13")
            page.get_by_test_id("tool-policy-save").click()
            expect(page.get_by_text("工具策略已保存")).to_be_visible()

            _assert_saved_policy(backend_base_url, _login_and_token(backend_base_url), expected_max_results=13)

            page.goto(f"{frontend_base_url}/system")
            tool_audit_card = page.get_by_test_id("tool-audit-card")
            tool_audit_card.scroll_into_view_if_needed()
            expect(tool_audit_card).to_contain_text("web_fetch")
            expect(tool_audit_card).to_contain_text("成功 1")
            expect(tool_audit_card).to_contain_text("Browser UI Worker")
            expect(tool_audit_card).to_contain_text("browser-ui-task")
            tool_audit_card.locator('[data-testid^="tool-audit-detail-"]').click()
            expect(page.get_by_text("工具调用详情")).to_be_visible()
            expect(page.get_by_text("Example Domain")).to_be_visible()
            expect(page.get_by_text("https://example.com")).to_be_visible()

            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(f"{frontend_base_url}/settings")
            mobile_card = page.get_by_test_id("tool-policy-card")
            mobile_card.scroll_into_view_if_needed()
            expect(mobile_card).to_contain_text("Web 搜索与抓取资源")
            expect(
                page.locator(
                    '[data-testid="web-max-search-results"] input, input[data-testid="web-max-search-results"]'
                )
            ).to_have_value("13")

            assert console_errors == []
        finally:
            if browser is not None:
                browser.close()
            if playwright is not None:
                playwright.stop()
    finally:
        _stop_process(frontend)
        _stop_process(backend)


def test_knowledge_graph_governance_status_deeplink_and_resolution_browser_flow(tmp_path: Path) -> None:
    config_path = _write_browser_config(tmp_path, enable_graph=True)
    _seed_knowledge_graph_quality_issue(tmp_path)
    backend: ManagedProcess | None = None
    frontend: ManagedProcess | None = None
    backend_base_url = f"http://127.0.0.1:{BACKEND_PORT}"
    frontend_base_url = f"http://127.0.0.1:{FRONTEND_PORT}"

    try:
        backend = _start_backend(tmp_path, config_path)
        frontend = _start_frontend(tmp_path)
        token = _login_and_token(backend_base_url)
        auth_headers = {"Authorization": f"Bearer {token}"}
        quality = httpx.get(
            f"{backend_base_url}/api/knowledge/graph/quality",
            params={"confidence_threshold": 0.6, "status": "all"},
            headers=auth_headers,
            timeout=10,
        )
        quality.raise_for_status()
        assert quality.json()["summary"]["quality_gate"]["passed"] is False
        health = httpx.get(f"{backend_base_url}/api/system/health", headers=auth_headers, timeout=10)
        health.raise_for_status()
        assert health.json()["ops"]["last_knowledge_graph_governance_task"]["status"] in {"warning", "error"}

        console_errors: list[str] = []
        playwright = None
        browser = None
        try:
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            if playwright is not None:
                playwright.stop()
            pytest.skip(f"Playwright browser is not available in this environment: {str(exc).splitlines()[0]}")

        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.on(
                "console",
                lambda message: console_errors.append(message.text) if message.type == "error" else None,
            )

            page.goto(f"{frontend_base_url}/login")
            expect(page.get_by_role("heading", name="MemoX")).to_be_visible()
            page.get_by_placeholder("用户名").fill(USERNAME)
            page.get_by_placeholder("密码").fill(PASSWORD)
            page.get_by_role("button", name=re.compile(r"登\s*录")).click()
            expect(page).to_have_url(re.compile(r".*/documents$"))

            page.goto(f"{frontend_base_url}/system")
            expect(page.get_by_text("知识图谱治理任务待处理")).to_be_visible()
            expect(page.get_by_text(re.compile(r"低置信度\s*1"))).to_be_visible()
            expect(page.get_by_text(re.compile(r"孤立关系\s*1"))).to_be_visible()
            page.get_by_role("button", name="处理图谱").click()
            expect(page).to_have_url(re.compile(r".*/documents\?view=graph&quality=open#graph-quality-queue"))

            quality_queue = page.get_by_test_id("graph-quality-queue")
            quality_queue.scroll_into_view_if_needed()
            expect(quality_queue).to_contain_text("质量审核队列")
            candidate_title = "低置信度关系：噪声实体 关联 偶发对象"
            expect(quality_queue.get_by_text(candidate_title)).to_be_visible()

            candidate_row = page.locator("li").filter(has_text=candidate_title).first
            candidate_row.get_by_label("删除关系").click()
            page.get_by_role("button", name="删除").click()
            expect(page.get_by_text("关系已删除")).to_be_visible()
            expect(quality_queue.get_by_text(candidate_title)).not_to_be_visible(timeout=15_000)

            resolved = httpx.get(f"{backend_base_url}/api/system/health", headers=auth_headers, timeout=10)
            resolved.raise_for_status()
            assert resolved.json()["ops"]["last_knowledge_graph_governance_task"]["status"] == "ok"

            page.goto(f"{frontend_base_url}/system")
            expect(page.get_by_text("知识图谱治理任务待处理")).not_to_be_visible()
            expect(page.get_by_text("已恢复")).to_be_visible()

            assert console_errors == []
        finally:
            if browser is not None:
                browser.close()
            if playwright is not None:
                playwright.stop()
    finally:
        _stop_process(frontend)
        _stop_process(backend)
