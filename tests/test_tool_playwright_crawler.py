from unittest.mock import AsyncMock, patch

import pytest

from src.tools.playwright_crawler import PlaywrightCrawlerTool


@pytest.fixture
def crawler_tool():
    return PlaywrightCrawlerTool()


def test_crawler_tool_properties(crawler_tool):
    assert crawler_tool.name == "playwright_crawler"
    assert "Playwright" in crawler_tool.description
    assert "url" in crawler_tool.input_schema["properties"]


@pytest.mark.asyncio
@patch("src.tools.playwright_crawler.async_playwright")
async def test_crawler_blocks_localhost_before_browser_launch(mock_async_playwright, crawler_tool):
    result = await crawler_tool.execute({"url": "http://127.0.0.1:8080/private"})

    assert "Error" in result
    assert "内网" in result or "本机" in result
    mock_async_playwright.assert_not_called()


@pytest.mark.asyncio
@patch("src.tools.playwright_crawler.async_playwright")
async def test_crawler_execute_text_only(mock_async_playwright, crawler_tool):
    mock_playwright_context = AsyncMock()
    mock_playwright = AsyncMock()
    mock_browser = AsyncMock()
    mock_page = AsyncMock()

    mock_async_playwright.return_value = mock_playwright_context
    mock_playwright_context.__aenter__.return_value = mock_playwright
    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_page.return_value = mock_page
    mock_page.url = "http://example.com"
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
    mock_browser.close.assert_called_once()
    assert "Test Page" in result
    assert "Content here." in result
    assert "alert(1)" not in result


@pytest.mark.asyncio
@patch("src.tools.playwright_crawler.async_playwright")
async def test_crawler_execute_html(mock_async_playwright, crawler_tool):
    mock_playwright_context = AsyncMock()
    mock_playwright = AsyncMock()
    mock_browser = AsyncMock()
    mock_page = AsyncMock()

    mock_async_playwright.return_value = mock_playwright_context
    mock_playwright_context.__aenter__.return_value = mock_playwright
    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_page.return_value = mock_page
    mock_page.url = "http://example.com"

    long_content = "<html><body><h1>Test Page</h1>" + "p" * 9000 + "</body></html>"
    mock_page.content.return_value = long_content

    result = await crawler_tool.execute({
        "url": "http://example.com",
        "wait_for_selector": "h1",
        "extract_text_only": False,
    })

    mock_page.wait_for_selector.assert_called_once_with("h1", timeout=10000)
    mock_browser.close.assert_called_once()
    assert "<h1>Test Page</h1>" in result
    assert "...[Truncated]" in result


@pytest.mark.asyncio
@patch("src.tools.playwright_crawler.async_playwright")
async def test_crawler_execute_error(mock_async_playwright, crawler_tool):
    mock_playwright_context = AsyncMock()
    mock_async_playwright.return_value = mock_playwright_context
    mock_playwright_context.__aenter__.side_effect = Exception("Browser launch failed")

    result = await crawler_tool.execute({"url": "http://example.com"})

    assert "Playwright Crawler failed: Browser launch failed" in result
