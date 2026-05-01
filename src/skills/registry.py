"""Skill registry search — keyword + embedding similarity hybrid search."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

# ── cosine similarity ────────────────────────────────────────────────────────


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── dataclass ────────────────────────────────────────────────────────────────


@dataclass
class RegistryEntry:
    name: str
    description: str
    source_url: str
    keywords: list[str]
    embedding: list[float] | None = None
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
            embedding=s.get("embedding"),
        ))
    return out


def search_registry(
    entries: list[RegistryEntry],
    query: str,
    installed: set[str],
    limit: int = 10,
    query_embedding: list[float] | None = None,
) -> list[RegistryEntry]:
    """Return entries ranked by relevance to query, excluding already-installed.

    When query_embedding is provided, blends keyword score (0-1) with
    cosine similarity from the embedding (0-1) using equal weighting.
    """
    q = query.strip().lower()
    results: list[RegistryEntry] = []

    for e in entries:
        if e.name in installed:
            continue
        kw_score = _score(e, q)
        if kw_score <= 0 and q:
            continue

        # Normalise keyword score to [0, 1] — max observed ≈ 100
        norm_kw = min(kw_score / 100.0, 1.0) if kw_score > 0 else 0.0

        # Embedding similarity (when pre-computed)
        emb_sim = 0.0
        if query_embedding is not None and e.embedding is not None:
            try:
                emb_sim = _cosine(query_embedding, e.embedding)
            except Exception:
                emb_sim = 0.0

        # Blend: 50/50 keyword + embedding
        if query_embedding is not None and e.embedding is not None:
            final_score = 0.5 * norm_kw + 0.5 * emb_sim
        else:
            final_score = norm_kw

        copy = RegistryEntry(
            name=e.name,
            description=e.description,
            source_url=e.source_url,
            keywords=e.keywords,
            embedding=e.embedding,
            score=final_score,
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


async def rebuild_embeddings(
    registry_path: Path,
    embed_fn: "EmbeddingFunction",
) -> int:
    """Compute and store embeddings for all skills that don't have one yet.

    Uses the description field as the text to embed.
    Returns the number of embeddings actually computed.
    """
    from knowledge.vector_store import EmbeddingFunction

    entries = load_registry(registry_path)
    needs_embed = [e for e in entries if e.embedding is None]
    if not needs_embed:
        return 0

    texts = [e.description for e in needs_embed]
    vectors = await embed_fn(texts)

    # Reload to avoid concurrent-write races
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    name_to_emb = {e.name: vectors[i] for i, e in enumerate(needs_embed)}

    changed = False
    for skill in data.get("skills", []):
        if skill["name"] in name_to_emb and "embedding" not in skill:
            skill["embedding"] = name_to_emb[skill["name"]]
            changed = True

    if changed:
        registry_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return len(name_to_emb)
