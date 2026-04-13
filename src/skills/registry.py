"""Skill registry search — looks up skills by keyword in a curated JSON index."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RegistryEntry:
    name: str
    description: str
    source_url: str
    keywords: list[str]
    score: float = 0.0


def upsert_registry_entry(
    registry_path: Path,
    name: str,
    description: str,
    source_url: str,
    keywords: list[str] | None = None,
) -> bool:
    """Append or replace an entry by name. Returns True if registry changed."""
    if registry_path.is_file():
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    else:
        data = {"version": 2, "skills": []}

    entries = data.setdefault("skills", [])
    new_entry = {
        "name": name,
        "description": description,
        "source_url": source_url,
        "keywords": keywords or [],
    }
    for i, e in enumerate(entries):
        if e.get("name") == name:
            if e == new_entry:
                return False
            entries[i] = new_entry
            registry_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return True
    entries.append(new_entry)
    registry_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def load_registry(registry_path: Path) -> list[RegistryEntry]:
    if not registry_path.is_file():
        return []
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    out: list[RegistryEntry] = []
    for s in data.get("skills", []):
        out.append(RegistryEntry(
            name=s["name"],
            description=s.get("description", ""),
            source_url=s["source_url"],
            keywords=[k.lower() for k in s.get("keywords", [])],
        ))
    return out


def search_registry(
    entries: list[RegistryEntry],
    query: str,
    installed: set[str],
    limit: int = 10,
) -> list[RegistryEntry]:
    """Return entries ranked by relevance to query, excluding already-installed."""
    q = query.strip().lower()
    results: list[RegistryEntry] = []

    for e in entries:
        if e.name in installed:
            continue
        score = _score(e, q)
        if score <= 0 and q:
            continue
        copy = RegistryEntry(
            name=e.name,
            description=e.description,
            source_url=e.source_url,
            keywords=e.keywords,
            score=score,
        )
        results.append(copy)

    results.sort(key=lambda r: (-r.score, r.name))
    return results[:limit]


def _score(entry: RegistryEntry, q: str) -> float:
    if not q:
        return 1.0  # empty query → return all (uninstalled) alphabetically
    score = 0.0
    name = entry.name.lower()
    desc = entry.description.lower()

    if q == name:
        score += 100
    elif name.startswith(q):
        score += 40
    elif q in name:
        score += 20

    for kw in entry.keywords:
        if q == kw:
            score += 15
        elif q in kw or kw in q:
            score += 5

    if q in desc:
        score += 3

    # token-level partial match — split query on whitespace
    for tok in q.split():
        if len(tok) < 2:
            continue
        if tok in name:
            score += 2
        if tok in desc:
            score += 1
        for kw in entry.keywords:
            if tok in kw:
                score += 1

    return score
