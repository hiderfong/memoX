#!/usr/bin/env python3
"""Run MemoX external E2E release checks and write a redacted report.

This script is meant for trusted external runners or manual CI dispatches with
real provider secrets injected through environment variables.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend_wip"

DEFAULT_PHASES = [
    "preflight",
    "baseline",
    "frontend-build",
    "browser-e2e",
    "mixed",
    "collab",
    "qwen-smoke",
    "i2v-direct",
    "media-job",
]
PHASE_ALIASES = {
    "all": DEFAULT_PHASES,
    "smoke": DEFAULT_PHASES,
    "quick": ["preflight", "baseline", "frontend-build", "browser-e2e", "mixed", "collab", "qwen-smoke"],
}
PROVIDER_HOSTS = ["api.deepseek.com", "api.minimaxi.com", "dashscope.aliyuncs.com"]
PHASE_SECRETS = {
    "mixed": ["DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "QWEN_API_KEY"],
    "collab": ["MINIMAX_API_KEY"],
    "qwen-smoke": ["QWEN_API_KEY"],
    "i2v-direct": ["DASHSCOPE_API_KEY"],
    "media-job": ["DASHSCOPE_API_KEY", "MEMOX_FILE_SIGNING_SECRET"],
}
SECRET_NAME_RE = re.compile(r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
KEY_PREFIX = "s" "k"
SECRET_SHAPE_RE = re.compile(
    rf"\b(?:{KEY_PREFIX}|{KEY_PREFIX}-[A-Za-z0-9_-]{{8,}}|Bearer\s+[A-Za-z0-9._-]{{12,}})"
    r"[A-Za-z0-9._-]{8,}\b"
)
URL_WITH_QUERY_RE = re.compile(r"https?://[^\s)'\"]+\?[^\s)'\"]+")


@dataclass
class PhaseResult:
    name: str
    status: str
    duration_s: float = 0.0
    notes: list[str] = field(default_factory=list)
    output_tail: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def secret_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in os.environ.items():
        if value and len(value) >= 8 and SECRET_NAME_RE.search(name):
            values[name] = value
    return values


def redact(text: str) -> str:
    redacted = text
    for name, value in secret_values().items():
        if value:
            redacted = redacted.replace(value, f"<redacted:{name}>")
    redacted = URL_WITH_QUERY_RE.sub("<redacted-url>", redacted)
    redacted = SECRET_SHAPE_RE.sub("<redacted-secret>", redacted)
    return redacted


def redacted_url(value: str) -> str:
    if not value.startswith(("http://", "https://")):
        return "<non-http-url>"
    return "<redacted-http-url>"


def command_text(cmd: list[str]) -> str:
    return " ".join(cmd)


def tail(text: str, max_chars: int = 4_000) -> str:
    text = redact(text.strip())
    if len(text) <= max_chars:
        return text
    return "...<truncated>...\n" + text[-max_chars:]


def run_command(
    name: str,
    cmd: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout_s: int | None = None,
    dry_run: bool = False,
) -> PhaseResult:
    start = time.monotonic()
    if dry_run:
        return PhaseResult(name=name, status="DRY-RUN", notes=[f"Would run: {command_text(cmd)}"])
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=merged_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        return PhaseResult(
            name=name,
            status="FAIL",
            duration_s=duration,
            notes=[f"Timed out after {timeout_s}s: {command_text(cmd)}"],
            output_tail=tail(exc.stdout or ""),
        )
    duration = time.monotonic() - start
    status = "PASS" if proc.returncode == 0 else "FAIL"
    return PhaseResult(
        name=name,
        status=status,
        duration_s=duration,
        notes=[f"Command: {command_text(cmd)}", f"Exit code: {proc.returncode}"],
        output_tail=tail(proc.stdout or ""),
    )


def missing_secrets(phases: list[str]) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for phase in phases:
        needed = PHASE_SECRETS.get(phase, [])
        phase_missing = [name for name in needed if not os.environ.get(name)]
        if phase_missing:
            missing[phase] = phase_missing
    return missing


def ensure_secrets(phase: str, allow_missing: bool) -> PhaseResult | None:
    missing = [name for name in PHASE_SECRETS.get(phase, []) if not os.environ.get(name)]
    if not missing:
        return None
    status = "SKIP" if allow_missing else "FAIL"
    return PhaseResult(phase, status, notes=[f"Missing required env vars: {', '.join(missing)}"])


def resolve_phases(raw: str) -> list[str]:
    values: list[str] = []
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        if name in PHASE_ALIASES:
            values.extend(PHASE_ALIASES[name])
        else:
            values.append(name)
    allowed = set(DEFAULT_PHASES) | {"full-sweep"}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise SystemExit(f"Unknown phase(s): {', '.join(unknown)}")
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def phase_preflight(args: argparse.Namespace) -> PhaseResult:
    if args.dry_run:
        return PhaseResult("preflight", "DRY-RUN", notes=[f"Would resolve: {', '.join(PROVIDER_HOSTS)}"])
    start = time.monotonic()
    notes: list[str] = []
    for host in PROVIDER_HOSTS:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        notes.append(f"{host}: OK ({infos[0][4][0]})")
    return PhaseResult("preflight", "PASS", time.monotonic() - start, notes=notes)


def phase_baseline(args: argparse.Namespace) -> PhaseResult:
    return run_command(
        "baseline",
        ["uv", "run", "--extra", "dev", "pytest", "tests", "--ignore=tests/e2e", "-q", "--tb=short"],
        timeout_s=args.command_timeout,
        dry_run=args.dry_run,
    )


def phase_frontend_build(args: argparse.Namespace) -> PhaseResult:
    return run_command(
        "frontend-build",
        ["npm", "run", "build"],
        cwd=FRONTEND_DIR,
        timeout_s=args.command_timeout,
        dry_run=args.dry_run,
    )


def phase_browser_e2e(args: argparse.Namespace) -> PhaseResult:
    return run_command(
        "browser-e2e",
        [
            "uv",
            "run",
            "--extra",
            "dev",
            "pytest",
            "tests/e2e/test_admin_ui_browser_flow.py",
            "-q",
            "--tb=short",
            "-s",
        ],
        env={"MEMOX_BROWSER_E2E": "1"},
        timeout_s=args.browser_timeout,
        dry_run=args.dry_run,
    )


def phase_mixed(args: argparse.Namespace) -> PhaseResult:
    skipped = ensure_secrets("mixed", args.allow_missing_secrets)
    if skipped:
        return skipped
    return run_command(
        "mixed",
        [
            "uv",
            "run",
            "--extra",
            "dev",
            "pytest",
            "tests/e2e/test_deepseek_mixed_orchestration.py",
            "-q",
            "-s",
            "--tb=short",
        ],
        timeout_s=args.real_e2e_timeout,
        dry_run=args.dry_run,
    )


def phase_collab(args: argparse.Namespace) -> PhaseResult:
    skipped = ensure_secrets("collab", args.allow_missing_secrets)
    if skipped:
        return skipped
    target = "tests/e2e/test_e2e_collab.py"
    if not args.full_collab:
        target += "::test_calculator_collaboration"
    return run_command(
        "collab",
        ["uv", "run", "--extra", "dev", "pytest", target, "-q", "-s", "--tb=short"],
        timeout_s=args.real_e2e_timeout,
        dry_run=args.dry_run,
    )


async def qwen_smoke() -> str:
    sys.path.insert(0, str(ROOT))
    from src.agents.base_agent import create_provider

    model = os.environ.get("QWEN_MODEL", "qwen-plus")
    provider = create_provider(
        "dashscope",
        os.environ["QWEN_API_KEY"],
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    response = await provider.chat(
        messages=[
            {"role": "system", "content": "Reply with exactly QWEN_OK."},
            {"role": "user", "content": "Provider smoke test."},
        ],
        model=model,
        temperature=0,
        max_tokens=32,
    )
    content = (response.content or "").strip()
    if "QWEN_OK" not in content:
        raise AssertionError(f"Qwen smoke response did not contain QWEN_OK: {content[:200]}")
    return f"model={model}; response={content[:80]}"


def run_async_phase(name: str, func: Callable[[], object], args: argparse.Namespace) -> PhaseResult:
    start = time.monotonic()
    if args.dry_run:
        return PhaseResult(name, "DRY-RUN", notes=[f"Would run {name}"])
    try:
        detail = asyncio.run(func())
    except Exception as exc:
        return PhaseResult(name, "FAIL", time.monotonic() - start, notes=[f"{type(exc).__name__}: {redact(str(exc))}"])
    return PhaseResult(name, "PASS", time.monotonic() - start, notes=[str(detail)])


def phase_qwen_smoke(args: argparse.Namespace) -> PhaseResult:
    skipped = ensure_secrets("qwen-smoke", args.allow_missing_secrets)
    if skipped:
        return skipped
    return run_async_phase("qwen-smoke", qwen_smoke, args)


def write_png(path: Path, width: int = 512, height: int = 512, colors: tuple[int, int, int] = (40, 100, 180)) -> None:
    raw = bytearray()
    r0, g0, b0 = colors
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(
                (
                    min(255, int(r0 + 150 * x / max(1, width - 1))),
                    min(255, int(g0 + 120 * y / max(1, height - 1))),
                    b0,
                )
            )

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def extract_media_asset(payload: Mapping[str, object]) -> dict[str, object]:
    """Accept both wrapped and direct media asset API response shapes."""
    wrapped = payload.get("asset")
    if isinstance(wrapped, dict):
        return wrapped
    if isinstance(payload.get("id"), str) and isinstance(payload.get("status"), str):
        return dict(payload)
    raise KeyError("asset")


async def i2v_direct() -> str:
    sys.path.insert(0, str(ROOT))
    from src.imaging.i2v_client import DashScopeImageToVideoClient

    with tempfile.TemporaryDirectory(prefix="memox-i2v-direct-") as tmp:
        image_path = Path(tmp) / "i2v-smoke.png"
        write_png(image_path)
        client = DashScopeImageToVideoClient(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model=os.environ.get("I2V_TEST_MODEL", "wan2.7-i2v"),
            edit_model=os.environ.get("I2V_TEST_EDIT_MODEL", "wan2.7-videoedit"),
            poll_interval=float(os.environ.get("I2V_TEST_POLL_INTERVAL", "5")),
            timeout_s=float(os.environ.get("I2V_TEST_TIMEOUT_SECONDS", "900")),
        )
        video_url = await client.generate_from_file(
            image_path,
            prompt="A slow cinematic push-in over a simple blue and green gradient card, smooth motion, no text.",
            resolution=os.environ.get("I2V_TEST_RESOLUTION", "720P"),
            duration=int(os.environ.get("I2V_TEST_DURATION", "5")),
            prompt_extend=True,
            watermark=False,
            seed=20260530,
        )
        if not video_url.startswith(("http://", "https://")):
            raise AssertionError(f"I2V result is not an HTTP URL: {video_url[:120]}")
        return f"generated={redacted_url(video_url)}"


def phase_i2v_direct(args: argparse.Namespace) -> PhaseResult:
    skipped = ensure_secrets("i2v-direct", args.allow_missing_secrets)
    if skipped:
        return skipped
    return run_async_phase("i2v-direct", i2v_direct, args)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def wait_for_http(url: str, proc: subprocess.Popen[str], log_path: Path, timeout_s: float = 90) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early\n{tail(log_path.read_text(encoding='utf-8', errors='replace'))}")
        try:
            response = httpx.get(url, timeout=5)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(1)
    raise TimeoutError(f"timed out waiting for {url}: {last_error}")


async def media_job_smoke() -> str:
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="memox-media-e2e-") as tmp:
        root = Path(tmp)
        data = root / "data"
        upload_dir = data / "uploads"
        for path in (data / "chroma", upload_dir, data / "skills", root / "workspace"):
            path.mkdir(parents=True, exist_ok=True)
        config = {
            "app": {"name": "MemoX External Media E2E", "debug": False, "log_level": "INFO", "workspace": str(root / "workspace")},
            "server": {"host": "127.0.0.1", "port": port},
            "coordinator": {
                "model": os.environ.get("QWEN_MODEL", "qwen-plus"),
                "provider": "dashscope",
                "temperature": 0,
                "max_tokens": 512,
                "max_workers": 1,
                "task_timeout": 300,
            },
            "providers": {
                "dashscope": {
                    "api_key": "${DASHSCOPE_API_KEY}",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                }
            },
            "worker_templates": {},
            "knowledge_base": {
                "persist_directory": str(data / "chroma"),
                "upload_directory": str(upload_dir),
                "skills_dir": str(data / "skills"),
                "embedding_provider": "hash",
                "embedding_model": "hash-e2e",
                "chunk_size": 200,
                "chunk_overlap": 20,
                "top_k": 3,
                "hybrid_search": {"enabled": True, "bm25_persist_path": str(data / "bm25_index.pkl")},
                "enable_graph": False,
                "manifest_path": str(data / "documents_manifest.json"),
            },
            "auth": {"enabled": False, "users": []},
            "file_access": {
                "signing_secret": "${MEMOX_FILE_SIGNING_SECRET}",
                "signed_url_ttl_seconds": 300,
            },
            "ops": {"auto_backup_enabled": False},
            "image_to_video": {
                "enabled": True,
                "provider": "dashscope",
                "model": os.environ.get("I2V_TEST_MODEL", "wan2.7-i2v"),
                "edit_model": os.environ.get("I2V_TEST_EDIT_MODEL", "wan2.7-videoedit"),
                "api_key": "${DASHSCOPE_API_KEY}",
                "default_resolution": os.environ.get("I2V_TEST_RESOLUTION", "720P"),
                "default_duration": int(os.environ.get("I2V_TEST_DURATION", "5")),
            },
        }
        config_path = root / "config.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        log_path = root / "server.log"
        env = os.environ.copy()
        env["MEMOX_CONFIG_PATH"] = str(config_path)
        with log_path.open("w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "src.web.api:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=str(ROOT),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=(os.name != "nt"),
            )
        try:
            await wait_for_http(f"http://127.0.0.1:{port}/api/health", proc, log_path)
            image_name = "memox-media-job-smoke.png"
            write_png(upload_dir / image_name, colors=(120, 70, 110))
            async with httpx.AsyncClient(timeout=30) as client:
                enqueue = await client.post(
                    f"http://127.0.0.1:{port}/api/videos/i2v/jobs",
                    json={
                        "image_url": image_name,
                        "prompt": "A gentle animated camera move over a colorful abstract gradient card, no text.",
                        "resolution": os.environ.get("I2V_TEST_RESOLUTION", "720P"),
                        "duration": int(os.environ.get("I2V_TEST_DURATION", "5")),
                        "prompt_extend": True,
                        "watermark": False,
                        "seed": 20260530,
                    },
                )
                enqueue.raise_for_status()
                asset = extract_media_asset(enqueue.json())
                asset_id = asset["id"]
                for _attempt in range(int(os.environ.get("MEDIA_JOB_POLL_ATTEMPTS", "180"))):
                    item_response = await client.get(f"http://127.0.0.1:{port}/api/videos/assets/{asset_id}")
                    item_response.raise_for_status()
                    item = extract_media_asset(item_response.json())
                    if item["status"] == "success":
                        if not item.get("url", "").startswith(("http://", "https://")):
                            raise AssertionError(f"media job URL is invalid: {item.get('url', '')[:120]}")
                        status = await client.get(f"http://127.0.0.1:{port}/api/videos/jobs/status")
                        status.raise_for_status()
                        return f"asset_id={asset_id}; status=success; queue={json.dumps(status.json(), ensure_ascii=False)}"
                    if item["status"] == "failed":
                        raise AssertionError(f"media job failed: {item.get('error', '')}")
                    await asyncio.sleep(float(os.environ.get("MEDIA_JOB_POLL_SECONDS", "5")))
                raise TimeoutError(f"media asset {asset_id} did not finish")
        finally:
            if proc.poll() is None:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                    proc.wait(timeout=10)


def phase_media_job(args: argparse.Namespace) -> PhaseResult:
    skipped = ensure_secrets("media-job", args.allow_missing_secrets)
    if skipped:
        return skipped
    return run_async_phase("media-job", media_job_smoke, args)


def phase_full_sweep(args: argparse.Namespace) -> PhaseResult:
    return run_command(
        "full-sweep",
        ["uv", "run", "--extra", "dev", "pytest", "tests/e2e", "-q", "-s", "--tb=short", "-ra"],
        timeout_s=args.real_e2e_timeout,
        dry_run=args.dry_run,
    )


PHASE_RUNNERS: dict[str, Callable[[argparse.Namespace], PhaseResult]] = {
    "preflight": phase_preflight,
    "baseline": phase_baseline,
    "frontend-build": phase_frontend_build,
    "browser-e2e": phase_browser_e2e,
    "mixed": phase_mixed,
    "collab": phase_collab,
    "qwen-smoke": phase_qwen_smoke,
    "i2v-direct": phase_i2v_direct,
    "media-job": phase_media_job,
    "full-sweep": phase_full_sweep,
}


def write_report(path: Path, phases: list[str], results: list[PhaseResult], started: str, finished: str) -> None:
    lines = [
        "# MemoX External E2E Report",
        "",
        f"- Repo: `{ROOT}`",
        f"- Commit: `{subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, text=True).strip()}`",
        f"- Started: `{started}`",
        f"- Finished: `{finished}`",
        f"- Requested phases: `{', '.join(phases)}`",
        "",
        "## Secret Handling",
        "",
        "- Secrets are read only from environment variables.",
        "- Known secret values, key-shaped strings, and signed URLs are redacted from this report.",
        "",
        "## Results",
        "",
        "| Phase | Status | Duration | Notes |",
        "|---|---:|---:|---|",
    ]
    for result in results:
        notes = "<br>".join(redact(note) for note in result.notes) or "-"
        lines.append(f"| `{result.name}` | **{result.status}** | {result.duration_s:.1f}s | {notes} |")

    failed_outputs = [result for result in results if result.output_tail and result.status != "PASS"]
    if failed_outputs:
        lines.extend(["", "## Failure Output Tails", ""])
        for result in failed_outputs:
            lines.extend([f"### {result.name}", "", "```text", result.output_tail, "```", ""])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phases", default="smoke", help="Comma-separated phases, or alias: quick, smoke, all.")
    parser.add_argument("--report-path", default="external-e2e-report.md", help="Markdown report path.")
    parser.add_argument("--allow-missing-secrets", action="store_true", help="Skip secret-backed phases instead of failing.")
    parser.add_argument("--full-collab", action="store_true", help="Run all MiniMax collaboration tests instead of the minimal scenario.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would run without executing commands or provider calls.")
    parser.add_argument("--command-timeout", type=int, default=900)
    parser.add_argument("--browser-timeout", type=int, default=600)
    parser.add_argument("--real-e2e-timeout", type=int, default=1_800)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    phases = resolve_phases(args.phases)
    started = utc_now()
    results: list[PhaseResult] = []

    if not args.allow_missing_secrets:
        missing = missing_secrets(phases)
        if missing:
            for phase, names in missing.items():
                results.append(PhaseResult(phase, "FAIL", notes=[f"Missing required env vars: {', '.join(names)}"]))
            write_report(Path(args.report_path), phases, results, started, utc_now())
            return 1

    for phase in phases:
        runner = PHASE_RUNNERS[phase]
        print(f"==> {phase}", flush=True)
        result = runner(args)
        results.append(result)
        print(f"{phase}: {result.status} ({result.duration_s:.1f}s)", flush=True)
        if result.status == "FAIL":
            break

    finished = utc_now()
    write_report(Path(args.report_path), phases, results, started, finished)
    if any(result.status == "FAIL" for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
