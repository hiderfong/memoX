import asyncio
import contextlib
import threading
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from src.agents.base_agent import BaseTool
from src.config import PlaywrightCrawlerPolicyConfig, get_config
from src.tools.net_safety import (
    WebSafetyError,
    configured_internal_host_allowlist,
    validate_public_http_url,
)


class CrawlerPolicyError(Exception):
    """Raised when a crawl is denied by resource or network policy."""


_semaphore_lock = threading.Lock()
_crawl_semaphore: asyncio.Semaphore | None = None
_crawl_semaphore_capacity = 0


def _crawler_policy() -> PlaywrightCrawlerPolicyConfig:
    try:
        return get_config().tool_policy.playwright_crawler
    except Exception:
        return PlaywrightCrawlerPolicyConfig()


def _crawl_limiter(max_concurrency: int) -> asyncio.Semaphore:
    global _crawl_semaphore, _crawl_semaphore_capacity
    capacity = max(1, int(max_concurrency))
    with _semaphore_lock:
        if _crawl_semaphore is None or _crawl_semaphore_capacity != capacity:
            _crawl_semaphore = asyncio.Semaphore(capacity)
            _crawl_semaphore_capacity = capacity
        return _crawl_semaphore


class _CrawlerSlot:
    def __init__(self, semaphore: asyncio.Semaphore):
        self._semaphore = semaphore

    async def __aenter__(self) -> "_CrawlerSlot":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._semaphore.release()


async def _acquire_crawl_slot(policy: PlaywrightCrawlerPolicyConfig) -> _CrawlerSlot:
    semaphore = _crawl_limiter(policy.max_concurrency)
    timeout = float(policy.queue_timeout_seconds)
    try:
        if timeout <= 0:
            if semaphore.locked():
                raise TimeoutError
            await semaphore.acquire()
        else:
            await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
    except TimeoutError as exc:
        raise CrawlerPolicyError(
            f"Playwright crawler rejected: 当前浏览器抓取并发已满，等待超过 {timeout:g} 秒"
        ) from exc
    return _CrawlerSlot(semaphore)


def _response_size(headers: dict[str, str]) -> int | None:
    value = headers.get("content-length") or headers.get("Content-Length")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _ensure_response_within_limit(headers: dict[str, str], policy: PlaywrightCrawlerPolicyConfig) -> None:
    content_length = _response_size(headers)
    if content_length is not None and content_length > policy.max_response_bytes:
        raise CrawlerPolicyError(
            "Playwright crawler rejected: "
            f"响应体过大 ({content_length} bytes)，超过限制 {policy.max_response_bytes} bytes"
        )


def _truncate_output(value: str, max_chars: int) -> str:
    return value[:max_chars] + ("\n...[Truncated]" if len(value) > max_chars else "")


class PlaywrightCrawlerTool(BaseTool):
    """Playwright 动态网页抓取工具。"""

    @property
    def name(self) -> str:
        return "playwright_crawler"

    @property
    def description(self) -> str:
        return "使用无头浏览器 (Playwright) 抓取动态渲染的网页内容。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的网页 URL"
                },
                "wait_for_selector": {
                    "type": "string",
                    "description": "可选: 等待特定元素出现后再提取"
                },
                "extract_text_only": {
                    "type": "boolean",
                    "description": "是否仅提取纯文本",
                    "default": True
                }
            },
            "required": ["url"]
        }

    async def execute(self, arguments: dict) -> Any:
        policy = _crawler_policy()
        try:
            async with await _acquire_crawl_slot(policy):
                return await asyncio.wait_for(
                    self._execute_with_browser(arguments, policy),
                    timeout=float(policy.total_timeout_seconds),
                )
        except CrawlerPolicyError as e:
            return str(e)
        except TimeoutError:
            return (
                "Playwright crawler rejected: "
                f"抓取超过总超时 {float(policy.total_timeout_seconds):g} 秒"
            )

    async def _execute_with_browser(self, arguments: dict, policy: PlaywrightCrawlerPolicyConfig) -> Any:
        raw_url = str(arguments["url"]).strip()
        wait_for_selector = arguments.get("wait_for_selector")
        extract_text_only = arguments.get("extract_text_only", True)
        allow_internal_hosts = configured_internal_host_allowlist()

        try:
            url = validate_public_http_url(raw_url, allow_internal_hosts=allow_internal_hosts)
        except WebSafetyError as e:
            return f"Playwright crawler rejected: {e}"

        browser = None
        context = None
        blocked_errors: list[str] = []
        page_count = 1
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                context.set_default_timeout(policy.selector_timeout_ms)
                context.set_default_navigation_timeout(policy.navigation_timeout_ms)
                page = await context.new_page()

                async def _guard_extra_page(extra_page):
                    nonlocal page_count
                    page_count += 1
                    if page_count > policy.max_pages:
                        blocked_errors.append(
                            f"页面数超过限制 {policy.max_pages}，已关闭额外页面"
                        )
                        with contextlib.suppress(Exception):
                            await extra_page.close()

                context.on("page", lambda extra_page: asyncio.create_task(_guard_extra_page(extra_page)))

                async def _guard_request(route):
                    request_url = route.request.url
                    scheme = urlparse(request_url).scheme
                    if scheme in {"http", "https"}:
                        try:
                            validate_public_http_url(
                                request_url,
                                allow_internal_hosts=allow_internal_hosts,
                            )
                        except WebSafetyError as exc:
                            blocked_errors.append(str(exc))
                            await route.abort()
                            return
                    await route.continue_()

                await page.route("**/*", _guard_request)
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=policy.navigation_timeout_ms,
                )
                if response is not None:
                    _ensure_response_within_limit(response.headers, policy)
                validate_public_http_url(page.url, allow_internal_hosts=allow_internal_hosts)

                if wait_for_selector:
                    await page.wait_for_selector(wait_for_selector, timeout=policy.selector_timeout_ms)
                else:
                    # 如果没有指定 selector，额外等待一下以防网络请求未完成
                    await page.wait_for_timeout(policy.idle_wait_ms)

                if blocked_errors:
                    raise CrawlerPolicyError(f"Playwright crawler rejected: {blocked_errors[0]}")

                html_content = await page.content()
                html_size = len(html_content.encode("utf-8"))
                if html_size > policy.max_response_bytes:
                    raise CrawlerPolicyError(
                        "Playwright crawler rejected: "
                        f"页面内容过大 ({html_size} bytes)，超过限制 {policy.max_response_bytes} bytes"
                    )

                if extract_text_only:
                    soup = BeautifulSoup(html_content, "html.parser")
                    # 移除不需要的标签
                    for element in soup(["script", "style", "noscript", "iframe", "svg"]):
                        element.extract()
                    text = soup.get_text(separator="\n", strip=True)
                    # 简单截断防止超出 Token 限制
                    return _truncate_output(text, policy.max_output_chars)
                else:
                    return _truncate_output(html_content, policy.max_output_chars)

        except CrawlerPolicyError:
            raise
        except Exception as e:
            if blocked_errors:
                return f"Playwright crawler rejected: {blocked_errors[0]}"
            return f"Playwright Crawler failed: {e}"
        finally:
            if context is not None:
                with contextlib.suppress(Exception):
                    await context.close()
            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()
