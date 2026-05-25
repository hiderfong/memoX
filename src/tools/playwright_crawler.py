import contextlib
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from src.agents.base_agent import BaseTool
from src.tools.net_safety import (
    WebSafetyError,
    configured_internal_host_allowlist,
    validate_public_http_url,
)


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
        raw_url = str(arguments["url"]).strip()
        wait_for_selector = arguments.get("wait_for_selector")
        extract_text_only = arguments.get("extract_text_only", True)
        allow_internal_hosts = configured_internal_host_allowlist()

        try:
            url = validate_public_http_url(raw_url, allow_internal_hosts=allow_internal_hosts)
        except WebSafetyError as e:
            return f"Error: {e}"

        browser = None
        blocked_errors: list[str] = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

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
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                validate_public_http_url(page.url, allow_internal_hosts=allow_internal_hosts)

                if wait_for_selector:
                    await page.wait_for_selector(wait_for_selector, timeout=10000)
                else:
                    # 如果没有指定 selector，额外等待一下以防网络请求未完成
                    await page.wait_for_timeout(2000)

                html_content = await page.content()

                if extract_text_only:
                    soup = BeautifulSoup(html_content, "html.parser")
                    # 移除不需要的标签
                    for element in soup(["script", "style", "noscript", "iframe", "svg"]):
                        element.extract()
                    text = soup.get_text(separator="\n", strip=True)
                    # 简单截断防止超出 Token 限制
                    return text[:8000] + ("\n...[Truncated]" if len(text) > 8000 else "")
                else:
                    return html_content[:8000] + ("\n...[Truncated]" if len(html_content) > 8000 else "")

        except Exception as e:
            if blocked_errors:
                return f"Error: {blocked_errors[0]}"
            return f"Playwright Crawler failed: {e}"
        finally:
            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()
