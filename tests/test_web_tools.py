import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tools.web import WebFetchTool, WebSearchTool


def test_web_fetch_blocks_localhost():
    tool = WebFetchTool()

    result = asyncio.run(tool.execute({"url": "http://127.0.0.1:8080/private"}))

    assert "Error" in result
    assert "内网" in result or "本机" in result


def test_web_fetch_extracts_html_text():
    tool = WebFetchTool()
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.status_code = 200
    response.url = "https://example.com/page"
    response.headers = {"content-type": "text/html; charset=utf-8"}
    response.text = """
    <html>
      <head><title>Example Page</title><script>ignore()</script></head>
      <body><main><h1>Hello</h1><p>Readable content</p></main></body>
    </html>
    """

    with patch("httpx.AsyncClient") as mock_cls:
        instance = mock_cls.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=response)
        result = asyncio.run(tool.execute({"url": "https://example.com/page", "max_chars": 200}))

    data = json.loads(result)
    assert data["title"] == "Example Page"
    assert "Readable content" in data["text"]
    assert "ignore" not in data["text"]


def test_web_search_parses_duckduckgo_results():
    tool = WebSearchTool()
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.text = """
    <div class="result">
      <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Result A</a>
      <a class="result__snippet">Snippet A</a>
    </div>
    """

    with patch("httpx.AsyncClient") as mock_cls:
        instance = mock_cls.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=response)
        result = asyncio.run(tool.execute({"query": "memoX", "max_results": 1}))

    data = json.loads(result)
    assert data["query"] == "memoX"
    assert data["results"] == [
        {
            "title": "Result A",
            "url": "https://example.com/a",
            "snippet": "Snippet A",
        }
    ]
