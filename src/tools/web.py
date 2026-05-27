"""Web search and fetch tools for Worker Agents."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.agents.base_agent import BaseTool
from src.config import WebToolPolicyConfig, get_config
from src.tools.net_safety import (
    WebSafetyError,
    configured_internal_host_allowlist,
    validate_public_http_url,
)

DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_RESPONSE_BYTES = 2_000_000
MAX_FETCH_CHARS = 20000
DEFAULT_FETCH_CHARS = 8000
DEFAULT_SEARCH_RESULTS = 5
MAX_SEARCH_RESULTS = 10
USER_AGENT = "MemoX/0.1 (+https://github.com/hiderfong/memoX)"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5


def _web_policy() -> WebToolPolicyConfig:
    try:
        return get_config().tool_policy.web
    except Exception:
        return WebToolPolicyConfig()


def _clean_text(text: str) -> str:
    return " ".join(text.split())


def _duckduckgo_target_url(href: str) -> str:
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return unquote(query["uddg"][0])
    return href


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    return min(max(number, minimum), maximum)


async def _read_limited_response(
    response: httpx.Response,
    *,
    max_response_bytes: int,
) -> httpx.Response:
    try:
        content_length = response.headers.get("content-length", "")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = 0
            if declared_length > max_response_bytes:
                raise WebSafetyError(f"响应体过大，超过 {max_response_bytes} 字节")

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_response_bytes:
                raise WebSafetyError(f"响应体过大，超过 {max_response_bytes} 字节")
            chunks.append(chunk)
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=b"".join(chunks),
            request=response.request,
            extensions=response.extensions,
        )
    finally:
        await response.aclose()


async def _safe_http_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    allow_internal_hosts: list[str] | None = None,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> httpx.Response:
    current_url = validate_public_http_url(url, allow_internal_hosts=allow_internal_hosts)
    for _ in range(MAX_REDIRECTS + 1):
        request = client.build_request("GET", current_url)
        response = await client.send(request, stream=True)
        if response.status_code not in REDIRECT_STATUSES:
            validate_public_http_url(str(response.url), allow_internal_hosts=allow_internal_hosts)
            return await _read_limited_response(response, max_response_bytes=max_response_bytes)
        location = response.headers.get("location")
        if not location:
            return await _read_limited_response(response, max_response_bytes=max_response_bytes)
        await response.aclose()
        current_url = validate_public_http_url(
            urljoin(str(response.url), location),
            allow_internal_hosts=allow_internal_hosts,
        )
    raise WebSafetyError("重定向次数过多")


class WebSearchTool(BaseTool):
    """Search the public web through DuckDuckGo's HTML endpoint."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "搜索公开网页，返回标题、URL 和摘要。适合需要最新资料或外部来源的调研任务。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {
                    "type": "integer",
                    "description": f"返回结果数量，默认 {DEFAULT_SEARCH_RESULTS}，最大 {MAX_SEARCH_RESULTS}",
                },
            },
            "required": ["query"],
        }

    async def execute(self, arguments: dict) -> Any:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return "Error: query 不能为空"
        policy = _web_policy()
        max_results = _bounded_int(
            arguments.get("max_results"),
            default=DEFAULT_SEARCH_RESULTS,
            minimum=1,
            maximum=min(MAX_SEARCH_RESULTS, policy.max_search_results),
        )
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            async with httpx.AsyncClient(
                timeout=policy.request_timeout_seconds,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = await _safe_http_get(
                    client,
                    url,
                    max_response_bytes=policy.max_response_bytes,
                )
                response.raise_for_status()
        except WebSafetyError as exc:
            return f"Web search rejected: {exc}"
        except Exception as exc:
            return f"Error: 搜索失败: {type(exc).__name__}: {exc}"

        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for result in soup.select(".result"):
            anchor = result.select_one("a.result__a")
            if not anchor:
                continue
            title = _clean_text(anchor.get_text(" "))
            href = anchor.get("href") or ""
            if not href:
                continue
            target_url = _duckduckgo_target_url(urljoin("https://duckduckgo.com", href))
            try:
                target_url = validate_public_http_url(target_url)
            except WebSafetyError:
                continue
            snippet_node = result.select_one(".result__snippet")
            snippet = _clean_text(snippet_node.get_text(" ")) if snippet_node else ""
            results.append({"title": title, "url": target_url, "snippet": snippet})
            if len(results) >= max_results:
                break

        return json.dumps({"query": query, "results": results}, ensure_ascii=False, indent=2)


class WebFetchTool(BaseTool):
    """Fetch and extract readable text from a public web page."""

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "抓取公开网页并抽取标题和正文文本。只支持 http/https，禁止访问本机和内网地址。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的公开网页 URL"},
                "max_chars": {
                    "type": "integer",
                    "description": f"正文最大字符数，默认 {DEFAULT_FETCH_CHARS}，最大 {MAX_FETCH_CHARS}",
                },
            },
            "required": ["url"],
        }

    async def execute(self, arguments: dict) -> Any:
        raw_url = str(arguments.get("url", "")).strip()
        if not raw_url:
            return "Error: url 不能为空"
        policy = _web_policy()
        allow_internal_hosts = configured_internal_host_allowlist()
        try:
            safe_url = validate_public_http_url(raw_url, allow_internal_hosts=allow_internal_hosts)
        except WebSafetyError as exc:
            return f"Web fetch rejected: {exc}"

        max_chars = _bounded_int(
            arguments.get("max_chars"),
            default=min(DEFAULT_FETCH_CHARS, policy.max_fetch_chars),
            minimum=1,
            maximum=min(MAX_FETCH_CHARS, policy.max_fetch_chars),
        )
        try:
            async with httpx.AsyncClient(
                timeout=policy.request_timeout_seconds,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = await _safe_http_get(
                    client,
                    safe_url,
                    allow_internal_hosts=allow_internal_hosts,
                    max_response_bytes=policy.max_response_bytes,
                )
                response.raise_for_status()
        except WebSafetyError as exc:
            return f"Web fetch rejected: {exc}"
        except Exception as exc:
            return f"Error: 抓取失败: {type(exc).__name__}: {exc}"

        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            text = response.text[:max_chars]
            return json.dumps(
                {
                    "url": str(response.url),
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "title": "",
                    "text": text,
                    "truncated": len(response.text) > max_chars,
                },
                ensure_ascii=False,
                indent=2,
            )

        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup(["script", "style", "noscript", "svg"]):
            node.decompose()
        title = _clean_text(soup.title.get_text(" ")) if soup.title else ""
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = _clean_text(main.get_text(" "))
        return json.dumps(
            {
                "url": str(response.url),
                "status_code": response.status_code,
                "content_type": content_type,
                "title": title,
                "text": text[:max_chars],
                "truncated": len(text) > max_chars,
            },
            ensure_ascii=False,
            indent=2,
        )
