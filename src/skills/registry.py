"""Skill registry search — keyword + embedding similarity hybrid search.

Implements the Karpathy LLM Wiki pattern for skill metadata:
  - Frontmatter fields (created, updated, tags, sources) for provenance
  - Contested/contradictions for explicit conflict tracking
  - Change log for traceability

Registry JSON format (version 3):
{
  "version": 3,
  "skills": [
    {
      "name": "...",
      "description": "...",
      "source_url": "...",
      "keywords": [...],
      "embedding": [...],
      "created": "YYYY-MM-DD",          # ISO date first added
      "updated": "YYYY-MM-DD",          # ISO date last modified
      "tags": [...],                    # from defined taxonomy
      "sources": [...],                 # upstream sources/URLs
      "contradictions": [...],           # skill names this conflicts with
      "contested": false                 # true = has unresolved conflict
    }
  ],
  "log": [
    { "action": "upsert|update|conflict_mark", "name": "...", "at": "YYYY-MM-DD", "note": "..." }
  ]
}
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

# ── helpers ──────────────────────────────────────────────────────────────────


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _today() -> str:
    return date.today().isoformat()


# ── dataclass ────────────────────────────────────────────────────────────────


@dataclass
class RegistryEntry:
    """A skill entry in the registry with wiki-style frontmatter fields."""

    name: str
    description: str
    source_url: str
    keywords: list[str]
    embedding: list[float] | None = None
    score: float = 0.0
    # ── wiki-style frontmatter ────────────────────────────────────────────
    created: str = ""           # ISO date first added to registry
    updated: str = ""           # ISO date last modified
    tags: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # upstream sources
    contradictions: list[str] = field(default_factory=list)  # conflicting skill names
    contested: bool = False     # True when this entry has unresolved conflicts


def _dict_to_entry(s: dict) -> RegistryEntry:
    """Deserialize a JSON dict into a RegistryEntry."""
    return RegistryEntry(
        name=s["name"],
        description=s.get("description", ""),
        source_url=s["source_url"],
        keywords=[k.lower() for k in s.get("keywords", [])],
        embedding=s.get("embedding"),
        created=s.get("created", ""),
        updated=s.get("updated", ""),
        tags=s.get("tags", []),
        sources=s.get("sources", []),
        contradictions=s.get("contradictions", []),
        contested=s.get("contested", False),
    )


# ── contradiction detection ───────────────────────────────────────────────────


def _similarity_ratio(a: str, b: str) -> float:
    """Return 0-1 similarity between two strings."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def detect_contradictions(
    new_name: str,
    new_description: str,
    existing: list[RegistryEntry],
    *,
    name_threshold: float = 0.7,
    desc_threshold: float = 0.5,
) -> list[str]:
    """Return names of existing skills that may conflict with the new one.

    Contradiction is triggered when:
      - name similarity > name_threshold, OR
      - description similarity > desc_threshold AND names are not identical

    This is a heuristic — it flags candidates for human review rather than
    making automatic decisions (the ``contested`` flag is set by the caller).
    """
    conflicting = []
    for e in existing:
        if e.name == new_name:
            continue  # same name → upsert path, handled elsewhere
        name_sim = _similarity_ratio(new_name, e.name)
        desc_sim = _similarity_ratio(new_description, e.description)
        if name_sim >= name_threshold or (desc_sim >= desc_threshold and name_sim > 0.3):
            conflicting.append(e.name)
    return conflicting


# ── registry I/O ─────────────────────────────────────────────────────────────


def upsert_registry_entry(
    registry_path: Path,
    name: str,
    description: str,
    source_url: str,
    keywords: list[str] | None = None,
    tags: list[str] | None = None,
    sources: list[str] | None = None,
    contradictions: list[str] | None = None,
    contested: bool = False,
    auto_detect_contradictions: bool = True,
) -> tuple[bool, str]:
    """Append or replace a registry entry by name.

    Returns (changed, action):
      changed=False, action="unchanged"   → entry identical, no-op
      changed=True,  action="upserted"   → new entry added
      changed=True,  action="updated"    → existing entry modified
      changed=True,  action="conflicted" → contradictions auto-detected

    New fields (tags, sources, contradictions, contested) are merged with
    any existing values — setting contradictions/contested does not erase
    prior values unless explicitly cleared.
    """
    if registry_path.is_file():
        raw = registry_path.read_text(encoding="utf-8")
        # Gracefully handle both version 2 (plain list) and version 3 (dict)
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                data = {"version": 2, "skills": data, "log": []}
        except json.JSONDecodeError:
            data = {"version": 3, "skills": [], "log": []}
    else:
        data = {"version": 3, "skills": [], "log": []}

    entries = data.setdefault("skills", [])
    today = _today()
    new_tags = tags or []
    new_sources = sources or []
    new_contradictions = contradictions or []

    # Load existing entries for contradiction detection
    existing = [_dict_to_entry(e) for e in entries]

    # Detect contradictions if this is a new or significantly changed entry
    detected_conflicts: list[str] = []
    if auto_detect_contradictions and existing:
        existing_desc = next((e.description for e in existing if e.name == name), "")
        if description != existing_desc:  # only check on real changes
            detected_conflicts = detect_contradictions(name, description, existing)
            for cname in detected_conflicts:
                if cname not in new_contradictions:
                    new_contradictions.append(cname)

    # Build the new/updated entry — merge frontmatter fields
    for i, e in enumerate(entries):
        if e.get("name") == name:
            # Merge: preserve created, extend lists, overwrite scalars
            old = _dict_to_entry(e)
            merged_tags = list(dict.fromkeys(old.tags + new_tags))      # dedupe preserve order
            merged_sources = list(dict.fromkeys(old.sources + new_sources))
            merged_contradictions = list(dict.fromkeys(old.contradictions + new_contradictions))

            updated_entry = {
                "name": name,
                "description": description,
                "source_url": source_url,
                "keywords": keywords or old.keywords,
                "embedding": e.get("embedding"),  # preserve existing embedding
                "created": old.created or today,
                "updated": today,
                "tags": merged_tags,
                "sources": merged_sources,
                "contradictions": merged_contradictions,
                "contested": contested or old.contested or bool(detected_conflicts),
            }
            if updated_entry == dict(e):
                return False, "unchanged"
            entries[i] = updated_entry
            _append_log(data, "update", name, f"detected_conflicts={detected_conflicts}" if detected_conflicts else "")
            registry_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            action = "conflicted" if detected_conflicts else "updated"
            return True, action

    # New entry
    new_entry = {
        "name": name,
        "description": description,
        "source_url": source_url,
        "keywords": keywords or [],
        "embedding": None,
        "created": today,
        "updated": today,
        "tags": new_tags,
        "sources": new_sources or [],
        "contradictions": new_contradictions,
        "contested": contested or bool(detected_conflicts),
    }
    entries.append(new_entry)
    _append_log(data, "upsert", name, f"detected_conflicts={detected_conflicts}" if detected_conflicts else "")
    registry_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    action = "conflicted" if detected_conflicts else "upserted"
    return True, action


def _append_log(data: dict, action: str, name: str, note: str = "") -> None:
    """Append an entry to the change log within data. Creates log if absent."""
    log = data.setdefault("log", [])
    entry = {
        "action": action,
        "name": name,
        "at": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }
    # Keep last 500 entries to prevent unbounded growth
    if len(log) >= 500:
        log[:] = log[-499:]
    log.append(entry)


def load_registry(registry_path: Path) -> list[RegistryEntry]:
    """Load all registry entries. Missing file → empty list."""
    if not registry_path.is_file():
        return []
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    skills = data if isinstance(data, list) else data.get("skills", [])
    return [_dict_to_entry(s) for s in skills]


def get_change_log(registry_path: Path, limit: int = 20) -> list[dict]:
    """Return the last ``limit`` log entries, newest last."""
    if not registry_path.is_file():
        return []
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return []
    log: list[dict] = data.get("log", [])
    return log[-limit:]


def get_contested(registry_path: Path) -> list[RegistryEntry]:
    """Return all entries marked as contested (have unresolved conflicts)."""
    return [e for e in load_registry(registry_path) if e.contested]


def get_all_tags(registry_path: Path) -> list[str]:
    """Return sorted list of all tags in use across the registry."""
    tags: set[str] = set()
    for e in load_registry(registry_path):
        tags.update(e.tags)
    return sorted(tags)


# ── search ───────────────────────────────────────────────────────────────────


def search_registry(
    entries: list[RegistryEntry],
    query: str,
    installed: set[str],
    limit: int = 10,
    query_embedding: list[float] | None = None,
    include_contested: bool = True,
) -> list[RegistryEntry]:
    """Return entries ranked by relevance to query, excluding already-installed.

    By default contested entries are included in results (they are still valid
    skills — ``contested`` is informational, not a filter).  Set
    ``include_contested=False`` to suppress them.

    When ``query_embedding`` is provided, blends keyword score (0-1) with
    cosine similarity from the embedding (0-1) using equal weighting.
    """
    q = query.strip().lower()
    results: list[RegistryEntry] = []

    for e in entries:
        if e.name in installed:
            continue
        if not include_contested and e.contested:
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

        # Penalise contested entries slightly so non-contested matches rank first
        if e.contested:
            final_score *= 0.95

        copy = RegistryEntry(
            name=e.name,
            description=e.description,
            source_url=e.source_url,
            keywords=e.keywords,
            embedding=e.embedding,
            score=final_score,
            created=e.created,
            updated=e.updated,
            tags=e.tags,
            sources=e.sources,
            contradictions=e.contradictions,
            contested=e.contested,
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
