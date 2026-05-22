"""Web search and fetch tools for Worker Agents."""

from __future__ import annotations

import ipaddress
import json
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.agents.base_agent import BaseTool

DEFAULT_TIMEOUT = 15.0
MAX_FETCH_CHARS = 20000
DEFAULT_FETCH_CHARS = 8000
DEFAULT_SEARCH_RESULTS = 5
MAX_SEARCH_RESULTS = 10
USER_AGENT = "MemoX/0.1 (+https://github.com/hiderfong/memoX)"


class WebSafetyError(ValueError):
    """Raised when a URL is unsafe for server-side fetch."""


def _validate_public_http_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise WebSafetyError("只支持 http/https URL")
    if not parsed.hostname:
        raise WebSafetyError("URL 缺少 hostname")

    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise WebSafetyError("禁止访问本机或 .local 地址")

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast):
        raise WebSafetyError("禁止访问内网、本机或保留 IP 地址")
    return parsed.geturl()


def _clean_text(text: str) -> str:
    return " ".join(text.split())


def _duckduckgo_target_url(href: str) -> str:
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return unquote(query["uddg"][0])
    return href


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
        max_results = min(
            max(int(arguments.get("max_results", DEFAULT_SEARCH_RESULTS)), 1),
            MAX_SEARCH_RESULTS,
        )
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            async with httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
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
        try:
            safe_url = _validate_public_http_url(raw_url)
        except WebSafetyError as exc:
            return f"Error: {exc}"

        max_chars = min(
            max(int(arguments.get("max_chars", DEFAULT_FETCH_CHARS)), 1),
            MAX_FETCH_CHARS,
        )
        try:
            async with httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                response = await client.get(safe_url)
                response.raise_for_status()
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
