import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import PlaywrightCrawlerPolicyConfig
from src.tools import playwright_crawler as crawler_module
from src.tools.playwright_crawler import PlaywrightCrawlerTool


@pytest.fixture
def crawler_tool():
    return PlaywrightCrawlerTool()


def _mock_browser_stack(mock_async_playwright):
    mock_playwright_context = AsyncMock()
    mock_playwright = MagicMock()
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_async_playwright.return_value = mock_playwright_context
    mock_playwright_context.__aenter__.return_value = mock_playwright
    mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()
    mock_context.set_default_timeout = MagicMock()
    mock_context.set_default_navigation_timeout = MagicMock()
    mock_context.on = MagicMock()
    mock_page.route = AsyncMock()
    mock_page.goto = AsyncMock(return_value=None)
    mock_page.wait_for_selector = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()
    mock_page.content = AsyncMock()
    mock_page.url = "http://example.com"

    return mock_browser, mock_context, mock_page


def test_crawler_tool_properties(crawler_tool):
    assert crawler_tool.name == "playwright_crawler"
    assert "Playwright" in crawler_tool.description
    assert "url" in crawler_tool.input_schema["properties"]


@pytest.mark.asyncio
@patch("src.tools.playwright_crawler.async_playwright")
async def test_crawler_blocks_localhost_before_browser_launch(mock_async_playwright, crawler_tool):
    result = await crawler_tool.execute({"url": "http://127.0.0.1:8080/private"})

    assert "Playwright crawler rejected" in result
    assert "内网" in result or "本机" in result
    mock_async_playwright.assert_not_called()


@pytest.mark.asyncio
@patch("src.tools.playwright_crawler.async_playwright")
async def test_crawler_execute_text_only(mock_async_playwright, crawler_tool):
    mock_browser, mock_context, mock_page = _mock_browser_stack(mock_async_playwright)
    mock_page.content.return_value = (
        "<html><body><h1>Test Page</h1><script>alert(1)</script>"
        "<p>Content here.</p></body></html>"
    )

    result = await crawler_tool.execute({
        "url": "http://example.com",
        "extract_text_only": True,
    })

    mock_page.route.assert_called_once()
    mock_page.goto.assert_called_once_with("http://example.com", wait_until="domcontentloaded", timeout=30000)
    mock_context.set_default_timeout.assert_called_once_with(10000)
    mock_context.set_default_navigation_timeout.assert_called_once_with(30000)
    mock_context.close.assert_called_once()
    mock_browser.close.assert_called_once()
    assert "Test Page" in result
    assert "Content here." in result
    assert "alert(1)" not in result


@pytest.mark.asyncio
@patch("src.tools.playwright_crawler.async_playwright")
async def test_crawler_execute_html(mock_async_playwright, crawler_tool):
    mock_browser, mock_context, mock_page = _mock_browser_stack(mock_async_playwright)

    long_content = "<html><body><h1>Test Page</h1>" + "p" * 9000 + "</body></html>"
    mock_page.content.return_value = long_content

    result = await crawler_tool.execute({
        "url": "http://example.com",
        "wait_for_selector": "h1",
        "extract_text_only": False,
    })

    mock_page.wait_for_selector.assert_called_once_with("h1", timeout=10000)
    mock_context.close.assert_called_once()
    mock_browser.close.assert_called_once()
    assert "<h1>Test Page</h1>" in result
    assert "...[Truncated]" in result


@pytest.mark.asyncio
@patch("src.tools.playwright_crawler.async_playwright")
async def test_crawler_rejects_oversized_main_response(mock_async_playwright, crawler_tool):
    mock_browser, mock_context, mock_page = _mock_browser_stack(mock_async_playwright)
    mock_response = MagicMock()
    mock_response.headers = {"content-length": "5001"}
    mock_page.goto.return_value = mock_response

    policy = PlaywrightCrawlerPolicyConfig(max_response_bytes=5000)
    with patch("src.tools.playwright_crawler._crawler_policy", return_value=policy):
        result = await crawler_tool.execute({"url": "http://example.com"})

    assert "Playwright crawler rejected" in result
    assert "响应体过大" in result
    mock_context.close.assert_called_once()
    mock_browser.close.assert_called_once()


@pytest.mark.asyncio
@patch("src.tools.playwright_crawler.async_playwright")
async def test_crawler_rejects_when_concurrency_queue_is_full(mock_async_playwright, crawler_tool):
    crawler_module._crawl_semaphore = None
    crawler_module._crawl_semaphore_capacity = 0
    policy = PlaywrightCrawlerPolicyConfig(max_concurrency=1, queue_timeout_seconds=0)
    slot = await crawler_module._acquire_crawl_slot(policy)

    try:
        with patch("src.tools.playwright_crawler._crawler_policy", return_value=policy):
            result = await crawler_tool.execute({"url": "http://example.com"})
    finally:
        await slot.__aexit__(None, None, None)
        crawler_module._crawl_semaphore = None
        crawler_module._crawl_semaphore_capacity = 0

    assert "Playwright crawler rejected" in result
    assert "并发已满" in result
    mock_async_playwright.assert_not_called()


@pytest.mark.asyncio
@patch("src.tools.playwright_crawler.async_playwright")
async def test_crawler_total_timeout_closes_browser(mock_async_playwright, crawler_tool):
    mock_browser, mock_context, mock_page = _mock_browser_stack(mock_async_playwright)

    async def slow_goto(*args, **kwargs):
        await asyncio.sleep(1)

    mock_page.goto.side_effect = slow_goto
    policy = PlaywrightCrawlerPolicyConfig(total_timeout_seconds=0.01)

    with patch("src.tools.playwright_crawler._crawler_policy", return_value=policy):
        result = await crawler_tool.execute({"url": "http://example.com"})

    assert "Playwright crawler rejected" in result
    assert "总超时" in result
    mock_context.close.assert_called_once()
    mock_browser.close.assert_called_once()


@pytest.mark.asyncio
@patch("src.tools.playwright_crawler.async_playwright")
async def test_crawler_execute_error(mock_async_playwright, crawler_tool):
    mock_playwright_context = AsyncMock()
    mock_async_playwright.return_value = mock_playwright_context
    mock_playwright_context.__aenter__.side_effect = Exception("Browser launch failed")

    result = await crawler_tool.execute({"url": "http://example.com"})

    assert "Playwright Crawler failed: Browser launch failed" in result
