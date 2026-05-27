import asyncio
import json
import os
import sys
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.config import WebToolPolicyConfig
from tools.net_safety import validate_public_http_url
from tools.web import WebFetchTool, WebSearchTool


def _response(url: str, status_code: int = 200, *, headers: dict | None = None, content: bytes = b"") -> httpx.Response:
    request = httpx.Request("GET", url)
    return httpx.Response(status_code, headers=headers or {}, content=content, request=request)


class _FakeAsyncClient:
    def __init__(self, responses: tuple[httpx.Response, ...]) -> None:
        self._responses = list(responses)
        self.send_calls: list[tuple[httpx.Request, bool]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def build_request(self, method: str, url: str) -> httpx.Request:
        return httpx.Request(method, url)

    async def send(self, request: httpx.Request, *, stream: bool = False) -> httpx.Response:
        self.send_calls.append((request, stream))
        return self._responses.pop(0)


def _patch_client(*responses: httpx.Response):
    client = _FakeAsyncClient(responses)
    return patch("httpx.AsyncClient", return_value=client), client


def test_web_fetch_blocks_localhost():
    tool = WebFetchTool()

    result = asyncio.run(tool.execute({"url": "http://127.0.0.1:8080/private"}))

    assert "Web fetch rejected" in result
    assert "内网" in result or "本机" in result


def test_network_safety_allows_explicit_internal_host():
    url = validate_public_http_url(
        "http://127.0.0.1:3000/status",
        allow_internal_hosts=["127.0.0.1:3000"],
    )

    assert url == "http://127.0.0.1:3000/status"


def test_web_fetch_blocks_redirect_to_internal_host():
    tool = WebFetchTool()
    redirect = _response(
        "https://example.com/start",
        302,
        headers={"location": "http://127.0.0.1:8080/private"},
    )

    client_patch, client = _patch_client(redirect)
    with client_patch:
        result = asyncio.run(tool.execute({"url": "https://example.com/start"}))

    assert "Web fetch rejected" in result
    assert "内网" in result or "本机" in result
    assert len(client.send_calls) == 1
    assert client.send_calls[0][1] is True


def test_web_fetch_rejects_oversized_content_length():
    tool = WebFetchTool()
    response = _response(
        "https://example.com/page",
        headers={"content-type": "text/html", "content-length": "5001"},
        content=b"",
    )

    with patch("tools.web._web_policy", return_value=WebToolPolicyConfig(max_response_bytes=5000)):
        client_patch, _client = _patch_client(response)
        with client_patch:
            result = asyncio.run(tool.execute({"url": "https://example.com/page"}))

    assert result.startswith("Web fetch rejected:")
    assert "响应体过大" in result


def test_web_fetch_rejects_oversized_streamed_body():
    tool = WebFetchTool()
    response = _response(
        "https://example.com/page",
        headers={"content-type": "text/html"},
        content=b"x" * 5001,
    )

    with patch("tools.web._web_policy", return_value=WebToolPolicyConfig(max_response_bytes=5000)):
        client_patch, _client = _patch_client(response)
        with client_patch:
            result = asyncio.run(tool.execute({"url": "https://example.com/page"}))

    assert result.startswith("Web fetch rejected:")
    assert "响应体过大" in result


def test_web_fetch_extracts_html_text():
    tool = WebFetchTool()
    response = _response(
        "https://example.com/page",
        headers={"content-type": "text/html; charset=utf-8"},
        content=b"""
    <html>
      <head><title>Example Page</title><script>ignore()</script></head>
      <body><main><h1>Hello</h1><p>Readable content</p></main></body>
    </html>
    """,
    )

    client_patch, _client = _patch_client(response)
    with client_patch:
        result = asyncio.run(tool.execute({"url": "https://example.com/page", "max_chars": 200}))

    data = json.loads(result)
    assert data["title"] == "Example Page"
    assert "Readable content" in data["text"]
    assert "ignore" not in data["text"]


def test_web_search_parses_duckduckgo_results():
    tool = WebSearchTool()
    response = _response(
        "https://duckduckgo.com/html/?q=memoX",
        headers={"content-type": "text/html; charset=utf-8"},
        content=b"""
    <div class="result">
      <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Result A</a>
      <a class="result__snippet">Snippet A</a>
    </div>
    """,
    )

    client_patch, _client = _patch_client(response)
    with client_patch:
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
