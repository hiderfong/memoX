"""GitHub repo metadata fetcher with local TTL cache.

Used by the skill search endpoint to enrich results with stars + last-pushed
timestamps, helping users pick between skills with overlapping functionality.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

_REPO_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?(?:/|$)"
)

_TTL_SECONDS = 24 * 3600
_FETCH_TIMEOUT = 3.0


def _repo_key(source_url: str) -> str | None:
    m = _REPO_RE.search(source_url.strip())
    if not m:
        return None
    return f"{m.group('owner')}/{m.group('repo')}"


def _load_cache(cache_path: Path) -> dict[str, dict[str, Any]]:
    if not cache_path.is_file():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache_path: Path, cache: dict[str, dict[str, Any]]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def _fetch_one(client: httpx.AsyncClient, repo_key: str) -> dict[str, Any] | None:
    url = f"https://api.github.com/repos/{repo_key}"
    try:
        resp = await client.get(url, timeout=_FETCH_TIMEOUT)
        if resp.status_code != 200:
            logger.warning(f"github_meta: {repo_key} status={resp.status_code}")
            return None
        data = resp.json()
        return {
            "stars": data.get("stargazers_count", 0),
            "pushed_at": data.get("pushed_at", ""),
            "fetched_at": int(time.time()),
        }
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"github_meta: {repo_key} fetch failed: {e}")
        return None


async def enrich_with_repo_meta(
    source_urls: list[str],
    cache_path: Path,
) -> dict[str, dict[str, Any]]:
    """Return {source_url: {stars, pushed_at}} for all urls whose repo we could resolve.

    Uses a local JSON cache with 24h TTL; only cache-miss/stale repos trigger network
    requests, and all network requests run concurrently with a 3s per-request timeout.
    Missing / failed lookups are simply omitted from the result (caller can decide
    how to render their absence).
    """
    cache = _load_cache(cache_path)
    now = int(time.time())

    # map url → repo_key
    url_to_repo: dict[str, str] = {}
    for url in source_urls:
        key = _repo_key(url)
        if key:
            url_to_repo[url] = key

    unique_repos = set(url_to_repo.values())
    stale = {
        r for r in unique_repos
        if r not in cache or (now - cache[r].get("fetched_at", 0)) > _TTL_SECONDS
    }

    if stale:
        async with httpx.AsyncClient(
            headers={"Accept": "application/vnd.github+json"}
        ) as client:
            results = await asyncio.gather(
                *(_fetch_one(client, r) for r in stale),
                return_exceptions=False,
            )
        for repo, meta in zip(stale, results, strict=False):
            if meta:
                cache[repo] = meta
        _save_cache(cache_path, cache)

    out: dict[str, dict[str, Any]] = {}
    for url, repo in url_to_repo.items():
        meta = cache.get(repo)
        if meta and "stars" in meta:
            out[url] = {"stars": meta["stars"], "pushed_at": meta.get("pushed_at", "")}
    return out
