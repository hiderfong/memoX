#!/usr/bin/env python3
"""Run a simulated manual browser patrol against a deployed MemoX instance.

The patrol is intentionally read-only: it logs in, visits core admin pages,
performs harmless UI interactions, records screenshots, and writes Markdown/JSON
reports with browser issues and route-level results.

中文：该脚本模拟人工验收人员在浏览器里的只读巡检路径，适合测试服务器、
预发布环境和外部 Agent 自动化验收。它不会提交任务或修改配置。
English: This script simulates a human QA patrol in the browser for test,
staging, or external-agent validation. It avoids task submission and
configuration mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Request, Response, TimeoutError, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts" / "manual-browser-tests"

ERROR_TEXT_RE = re.compile(
    r"(Internal Server Error|Application error|Something went wrong|"
    r"Traceback|Unhandled Runtime Error|Uncaught Error|Cannot GET|404 Not Found|500 Internal)",
    re.IGNORECASE,
)
SECRET_NAME_RE = re.compile(r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
KEY_PREFIX = "s" "k"
SECRET_SHAPE_RE = re.compile(
    rf"\b(?:{KEY_PREFIX}|{KEY_PREFIX}-[A-Za-z0-9_-]{{8,}}|Bearer\s+[A-Za-z0-9._-]{{12,}})"
    r"[A-Za-z0-9._-]{8,}\b"
)
URL_WITH_QUERY_RE = re.compile(r"https?://[^\s)'\"]+\?[^\s)'\"]+")
IMPORTANT_RESOURCE_TYPES = {"document", "fetch", "script", "xhr"}
IGNORED_REQUEST_FAILURE_FRAGMENTS = ("net::ERR_ABORTED", "NS_BINDING_ABORTED", "cancelled", "canceled")


@dataclass(frozen=True)
class ViewportSpec:
    name: str
    width: int
    height: int
    is_mobile: bool = False


@dataclass(frozen=True)
class RouteSpec:
    name: str
    path: str
    probe: Callable[[Page, int], None]
    interact: Callable[[Page, int], None] | None = None


@dataclass
class CheckResult:
    viewport: str
    route: str
    path: str
    status: str
    duration_s: float
    message: str = ""
    screenshot: str = ""


@dataclass
class BrowserIssue:
    viewport: str
    issue_type: str
    url: str
    message: str


@dataclass
class RunReport:
    status: str
    base_url: str
    username: str
    started_at: str
    finished_at: str
    duration_s: float
    output_dir: str
    checks: list[CheckResult] = field(default_factory=list)
    issues: list[BrowserIssue] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def normalize_base_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise SystemExit(f"--base-url must be an absolute HTTP(S) URL, got: {value!r}")
    if parsed.scheme not in {"http", "https"}:
        raise SystemExit(f"--base-url must use http or https, got: {parsed.scheme!r}")
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/", "", "", ""))


def absolute_url(base_url: str, path: str) -> str:
    return urljoin(base_url, path.lstrip("/"))


def secret_values(extra_env_names: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, value in os.environ.items():
        if value and len(value) >= 8 and (SECRET_NAME_RE.search(name) or name in extra_env_names):
            values[name] = value
    return values


def redact(text: str, extra_env_names: list[str] | None = None) -> str:
    redacted = text
    for name, value in sorted(secret_values(extra_env_names or []).items(), key=lambda item: len(item[1]), reverse=True):
        redacted = redacted.replace(value, f"<redacted:{name}>")
    redacted = URL_WITH_QUERY_RE.sub("<redacted-url>", redacted)
    return SECRET_SHAPE_RE.sub("<redacted-secret>", redacted)


def wait_text(page: Page, text: str, timeout_ms: int) -> None:
    page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=timeout_ms)


def wait_heading(page: Page, pattern: str, timeout_ms: int) -> None:
    page.get_by_role("heading", name=re.compile(pattern)).first.wait_for(state="visible", timeout=timeout_ms)


def wait_placeholder(page: Page, pattern: str, timeout_ms: int) -> None:
    page.get_by_placeholder(re.compile(pattern)).first.wait_for(state="visible", timeout=timeout_ms)


def probe_projects(page: Page, timeout_ms: int) -> None:
    wait_heading(page, r"^项目$", timeout_ms)


def probe_documents(page: Page, timeout_ms: int) -> None:
    wait_text(page, "知识库管理", timeout_ms)
    wait_placeholder(page, r"搜索文档内容", timeout_ms)


def interact_documents(page: Page, timeout_ms: int) -> None:
    search = page.get_by_placeholder(re.compile(r"搜索文档内容")).first
    search.fill("MemoX", timeout=timeout_ms)
    search.press("Enter", timeout=timeout_ms)
    page.wait_for_timeout(300)


def probe_chat(page: Page, timeout_ms: int) -> None:
    wait_heading(page, r"开始对话", timeout_ms)
    wait_placeholder(page, r"输入问题", timeout_ms)


def interact_chat(page: Page, timeout_ms: int) -> None:
    box = page.get_by_placeholder(re.compile(r"输入问题")).first
    box.fill("请简要说明当前知识库状态", timeout=timeout_ms)
    page.wait_for_timeout(200)
    box.clear(timeout=timeout_ms)


def probe_tasks(page: Page, timeout_ms: int) -> None:
    wait_text(page, "任务执行", timeout_ms)
    wait_placeholder(page, r"输入任务描述", timeout_ms)


def interact_tasks(page: Page, timeout_ms: int) -> None:
    box = page.get_by_placeholder(re.compile(r"输入任务描述")).first
    box.fill("模拟人工巡检：检查任务编排页面可输入", timeout=timeout_ms)
    page.wait_for_timeout(200)
    box.clear(timeout=timeout_ms)


def probe_scheduled_tasks(page: Page, timeout_ms: int) -> None:
    wait_text(page, "新建定时任务", timeout_ms)
    wait_text(page, "尚未创建定时任务", timeout_ms)


def probe_workflows(page: Page, timeout_ms: int) -> None:
    wait_heading(page, r"工作流可视化编排", timeout_ms)


def probe_media(page: Page, timeout_ms: int) -> None:
    wait_heading(page, r"媒体创作", timeout_ms)


def probe_workers(page: Page, timeout_ms: int) -> None:
    wait_text(page, "Agent Worker 状态", timeout_ms)
    wait_text(page, "Worker Agent 池", timeout_ms)


def probe_system(page: Page, timeout_ms: int) -> None:
    wait_heading(page, r"系统状态", timeout_ms)
    page.get_by_test_id("tool-audit-card").first.wait_for(state="visible", timeout=timeout_ms)


def probe_settings(page: Page, timeout_ms: int) -> None:
    wait_text(page, "设置", timeout_ms)
    page.get_by_test_id("tool-policy-card").first.wait_for(state="visible", timeout=timeout_ms)


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec("projects", "/projects", probe_projects),
    RouteSpec("documents", "/documents", probe_documents, interact_documents),
    RouteSpec("chat", "/chat", probe_chat, interact_chat),
    RouteSpec("tasks", "/tasks", probe_tasks, interact_tasks),
    RouteSpec("scheduled-tasks", "/scheduled-tasks", probe_scheduled_tasks),
    RouteSpec("workflows", "/workflows", probe_workflows),
    RouteSpec("media", "/media", probe_media),
    RouteSpec("workers", "/workers", probe_workers),
    RouteSpec("system", "/system", probe_system),
    RouteSpec("settings", "/settings", probe_settings),
)
ROUTE_BY_NAME = {route.name: route for route in ROUTES}

VIEWPORTS = {
    "desktop": ViewportSpec("desktop", 1440, 1000),
    "mobile": ViewportSpec("mobile", 390, 844, is_mobile=True),
}


def check_page_health(page: Page, timeout_ms: int) -> None:
    body = page.locator("body").inner_text(timeout=timeout_ms).strip()
    compact = " ".join(body.split())
    if len(compact) < 20:
        raise AssertionError("page body is unexpectedly sparse")
    match = ERROR_TEXT_RE.search(compact)
    if match:
        raise AssertionError(f"page shows error text: {match.group(0)}")


def important_request_failure(request: Request) -> str | None:
    if request.resource_type not in IMPORTANT_RESOURCE_TYPES:
        return None
    failure = request.failure
    if callable(failure):
        failure = failure()
    message = str(failure or "request failed")
    # 中文：SPA 快速跳转会主动取消上一页请求，这不代表后端或页面失败。
    # English: SPA navigation can abort in-flight requests; that is not a
    # backend or page failure.
    if any(fragment.lower() in message.lower() for fragment in IGNORED_REQUEST_FAILURE_FRAGMENTS):
        return None
    return message


def important_response_failure(response: Response) -> str | None:
    if response.request.resource_type not in {"document", "fetch", "xhr"}:
        return None
    if response.status >= 500:
        return f"HTTP {response.status}"
    return None


def attach_issue_collectors(page: Page, viewport: str, issues: list[BrowserIssue]) -> None:
    def on_console(message: object) -> None:
        msg_type = getattr(message, "type", "")
        if msg_type != "error":
            return
        issues.append(
            BrowserIssue(
                viewport=viewport,
                issue_type="console.error",
                url=getattr(page, "url", ""),
                message=str(getattr(message, "text", "")),
            )
        )

    def on_page_error(exc: object) -> None:
        issues.append(BrowserIssue(viewport=viewport, issue_type="pageerror", url=page.url, message=str(exc)))

    def on_request_failed(request: Request) -> None:
        failure = important_request_failure(request)
        if failure:
            issues.append(BrowserIssue(viewport=viewport, issue_type="requestfailed", url=request.url, message=failure))

    def on_response(response: Response) -> None:
        failure = important_response_failure(response)
        if failure:
            issues.append(BrowserIssue(viewport=viewport, issue_type="http-error", url=response.url, message=failure))

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)


def login(page: Page, base_url: str, username: str, password: str, timeout_ms: int) -> None:
    page.goto(absolute_url(base_url, "/login"), wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.get_by_placeholder("用户名").fill(username, timeout=timeout_ms)
        page.get_by_placeholder("密码").fill(password, timeout=timeout_ms)
        page.get_by_role("button", name=re.compile(r"登\s*录")).click(timeout=timeout_ms)
    except TimeoutError as exc:
        raise AssertionError("login form is not usable") from exc
    page.wait_for_url(re.compile(r".*/projects(?:[?#].*)?$"), timeout=timeout_ms)
    probe_projects(page, timeout_ms)


def run_route(page: Page, route: RouteSpec, viewport: str, base_url: str, output_dir: Path, args: argparse.Namespace) -> CheckResult:
    start = time.monotonic()
    screenshot_path = output_dir / f"{viewport}-{route.name}.png"
    try:
        page.goto(absolute_url(base_url, route.path), wait_until="domcontentloaded", timeout=args.navigation_timeout_ms)
        route.probe(page, args.timeout_ms)
        if route.interact:
            route.interact(page, args.timeout_ms)
        check_page_health(page, args.timeout_ms)
        page.screenshot(path=str(screenshot_path), full_page=True)
        return CheckResult(
            viewport=viewport,
            route=route.name,
            path=route.path,
            status="PASS",
            duration_s=time.monotonic() - start,
            screenshot=str(screenshot_path),
        )
    except Exception as exc:
        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception:
            screenshot_path = Path("")
        return CheckResult(
            viewport=viewport,
            route=route.name,
            path=route.path,
            status="FAIL",
            duration_s=time.monotonic() - start,
            message=f"{type(exc).__name__}: {exc}",
            screenshot=str(screenshot_path) if screenshot_path else "",
        )


def selected_routes(raw: str) -> list[RouteSpec]:
    if raw.strip().lower() in {"all", "*"}:
        return list(ROUTES)
    names = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = sorted(set(names) - set(ROUTE_BY_NAME))
    if unknown:
        raise SystemExit(f"Unknown route(s): {', '.join(unknown)}. Available: {', '.join(ROUTE_BY_NAME)}")
    return [ROUTE_BY_NAME[name] for name in names]


def selected_viewports(raw: list[str] | None) -> list[ViewportSpec]:
    names = raw or ["desktop", "mobile"]
    unknown = sorted(set(names) - set(VIEWPORTS))
    if unknown:
        raise SystemExit(f"Unknown viewport(s): {', '.join(unknown)}. Available: {', '.join(VIEWPORTS)}")
    return [VIEWPORTS[name] for name in names]


def write_reports(report: RunReport, output_dir: Path, password_env: str) -> tuple[Path, Path]:
    json_path = output_dir / "manual-browser-report.json"
    markdown_path = output_dir / "manual-browser-report.md"
    payload = asdict(report)
    payload["checks"] = [asdict(check) for check in report.checks]
    payload["issues"] = [asdict(issue) for issue in report.issues]
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# MemoX Simulated Manual Browser Test",
        "",
        f"- Status: **{report.status}**",
        f"- Base URL: `{redact(report.base_url, [password_env])}`",
        f"- Username: `{report.username}`",
        f"- Started: `{report.started_at}`",
        f"- Finished: `{report.finished_at}`",
        f"- Duration: `{report.duration_s:.1f}s`",
        "",
        "## Route Results",
        "",
        "| Viewport | Route | Status | Duration | Screenshot | Notes |",
        "|---|---|---:|---:|---|---|",
    ]
    for check in report.checks:
        screenshot = f"`{check.screenshot}`" if check.screenshot else "-"
        message = redact(check.message, [password_env]).replace("\n", " ") or "-"
        lines.append(
            f"| `{check.viewport}` | `{check.path}` | **{check.status}** | "
            f"{check.duration_s:.1f}s | {screenshot} | {message} |"
        )

    if report.issues:
        lines.extend(["", "## Browser Issues", "", "| Viewport | Type | URL | Message |", "|---|---|---|---|"])
        for issue in report.issues:
            issue_url = redact(issue.url, [password_env])
            issue_message = redact(issue.message, [password_env]).replace("|", r"\|")
            lines.append(
                f"| `{issue.viewport}` | `{issue.issue_type}` | `{issue_url}` | {issue_message} |"
            )
    else:
        lines.extend(["", "## Browser Issues", "", "No console errors, page errors, network failures, or HTTP 5xx responses were captured."])

    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return markdown_path, json_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("MEMOX_MANUAL_TEST_BASE_URL", "http://127.0.0.1:18080"))
    parser.add_argument("--username", default=os.getenv("MEMOX_ADMIN_USERNAME", "admin"))
    parser.add_argument("--password-env", default=os.getenv("MEMOX_MANUAL_TEST_PASSWORD_ENV", "MEMOX_ADMIN_PASSWORD"))
    parser.add_argument("--output-dir", default=None, help="Directory for reports, screenshots, and optional traces.")
    parser.add_argument("--routes", default="all", help=f"Comma-separated route names, or all. Available: {', '.join(ROUTE_BY_NAME)}")
    parser.add_argument("--viewport", action="append", choices=sorted(VIEWPORTS), help="Repeat to select viewports. Defaults to desktop+mobile.")
    parser.add_argument("--timeout-ms", type=int, default=15_000)
    parser.add_argument("--navigation-timeout-ms", type=int, default=30_000)
    parser.add_argument("--headful", action="store_true", help="Run Chromium with a visible browser window.")
    parser.add_argument("--slow-mo-ms", type=int, default=0)
    parser.add_argument("--trace", choices=["off", "on-failure", "always"], default="on-failure")
    parser.add_argument(
        "--fail-on-browser-issues",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail when console errors, page errors, request failures, or HTTP 5xx responses are captured.",
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    base_url = normalize_base_url(args.base_url)
    password = os.getenv(args.password_env)
    if not password:
        raise SystemExit(f"Missing password env var: {args.password_env}")

    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT / timestamp_slug()
    output_dir.mkdir(parents=True, exist_ok=True)

    routes = selected_routes(args.routes)
    viewports = selected_viewports(args.viewport)
    started_at = utc_now()
    start = time.monotonic()
    checks: list[CheckResult] = []
    issues: list[BrowserIssue] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headful, slow_mo=args.slow_mo_ms or None)
        try:
            for viewport in viewports:
                context = browser.new_context(
                    viewport={"width": viewport.width, "height": viewport.height},
                    is_mobile=viewport.is_mobile,
                    has_touch=viewport.is_mobile,
                )
                trace_path = output_dir / f"{viewport.name}-trace.zip"
                if args.trace != "off":
                    context.tracing.start(screenshots=True, snapshots=True, sources=False)
                page = context.new_page()
                attach_issue_collectors(page, viewport.name, issues)
                viewport_checks_start = len(checks)
                viewport_failed = False
                try:
                    login(page, base_url, args.username, password, args.timeout_ms)
                    for route in routes:
                        result = run_route(page, route, viewport.name, base_url, output_dir, args)
                        checks.append(result)
                        if result.status == "FAIL":
                            viewport_failed = True
                except Exception as exc:
                    viewport_failed = True
                    checks.append(
                        CheckResult(
                            viewport=viewport.name,
                            route="login",
                            path="/login",
                            status="FAIL",
                            duration_s=0.0,
                            message=f"{type(exc).__name__}: {exc}",
                        )
                    )
                finally:
                    if args.trace == "always" or (args.trace == "on-failure" and viewport_failed):
                        context.tracing.stop(path=str(trace_path))
                    elif args.trace != "off":
                        context.tracing.stop()
                    context.close()
                if len(checks) == viewport_checks_start:
                    checks.append(
                        CheckResult(
                            viewport=viewport.name,
                            route="viewport",
                            path="",
                            status="FAIL",
                            duration_s=0.0,
                            message="viewport produced no checks",
                        )
                    )
        finally:
            browser.close()

    if args.fail_on_browser_issues:
        for issue in issues:
            checks.append(
                CheckResult(
                    viewport=issue.viewport,
                    route=issue.issue_type,
                    path=issue.url,
                    status="FAIL",
                    duration_s=0.0,
                    message=issue.message,
                )
            )

    status = "PASS" if checks and all(check.status == "PASS" for check in checks) else "FAIL"
    report = RunReport(
        status=status,
        base_url=base_url,
        username=args.username,
        started_at=started_at,
        finished_at=utc_now(),
        duration_s=time.monotonic() - start,
        output_dir=str(output_dir),
        checks=checks,
        issues=issues,
    )
    markdown_path, json_path = write_reports(report, output_dir, args.password_env)
    print(f"Status: {status}")
    print(f"Markdown report: {markdown_path}")
    print(f"JSON report: {json_path}")
    return 0 if status == "PASS" else 1


def main() -> int:
    args = parse_args()
    try:
        return run(args)
    except PlaywrightError as exc:
        message = str(exc)
        hint = ""
        if "Executable doesn't exist" in message or "playwright install" in message:
            hint = "\nHint: run `uv run --extra dev playwright install chromium`."
        print(redact(f"Playwright failed: {message}{hint}", [args.password_env]), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
