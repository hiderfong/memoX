#!/usr/bin/env python3
"""Audit and repair MemoX Chroma/BM25/manifest index consistency."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from config import default_config_path, load_config  # noqa: E402
from knowledge.bm25_indexer import BM25Indexer, ChunkEntry  # noqa: E402
from knowledge.vector_store import ChromaVectorStore  # noqa: E402


@dataclass(frozen=True)
class IndexIssue:
    code: str
    severity: str
    message: str
    doc_id: str | None = None
    chunk_id: str | None = None
    repair: str | None = None


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "documents": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"version": 1, "documents": {}, "_load_error": str(exc)}
    if not isinstance(raw, dict):
        return {"version": 1, "documents": {}, "_load_error": "manifest root is not an object"}
    documents = raw.get("documents", {})
    if not isinstance(documents, dict):
        raw["documents"] = {}
        raw["_load_error"] = "manifest documents is not an object"
    return raw


def _collect_chroma_state(vector_store: Any, collection_name: str) -> tuple[list[dict], dict[str, list[dict]], dict[str, dict]]:
    docs = vector_store.list_documents(collection_name) or []
    chunks_by_doc: dict[str, list[dict]] = {}
    chunks_by_id: dict[str, dict] = {}
    for doc in docs:
        doc_id = doc.get("doc_id")
        if not doc_id:
            continue
        chunks = vector_store.get_chunks_by_doc(doc_id, collection_name) or []
        chunks_by_doc[doc_id] = chunks
        for chunk in chunks:
            chunk_id = chunk.get("id")
            if chunk_id:
                chunks_by_id[chunk_id] = chunk
    return docs, chunks_by_doc, chunks_by_id


def _collect_bm25_state(bm25_indexer: BM25Indexer) -> dict[str, ChunkEntry]:
    return dict(getattr(bm25_indexer, "_corpus", {}))


def _status_for_issues(issues: list[IndexIssue]) -> str:
    severities = {issue.severity for issue in issues}
    if "error" in severities:
        return "error"
    if "warning" in severities:
        return "warning"
    return "ok"


def _issue_counts(issues: list[IndexIssue]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.code] = counts.get(issue.code, 0) + 1
    return dict(sorted(counts.items()))


def audit_indexes(
    *,
    vector_store: Any,
    bm25_indexer: BM25Indexer,
    manifest_path: Path,
    collection_name: str = "documents",
) -> dict:
    docs, chunks_by_doc, chroma_chunks = _collect_chroma_state(vector_store, collection_name)
    bm25_chunks = _collect_bm25_state(bm25_indexer)
    manifest = load_manifest(manifest_path)
    manifest_docs = manifest.get("documents", {})
    issues: list[IndexIssue] = []

    if manifest.get("_load_error"):
        issues.append(
            IndexIssue(
                code="manifest_load_error",
                severity="error",
                message=f"Manifest could not be loaded: {manifest['_load_error']}",
            )
        )

    chroma_doc_ids = {doc.get("doc_id") for doc in docs if doc.get("doc_id")}
    manifest_doc_ids: set[str] = set()
    for key, entry in manifest_docs.items():
        if not isinstance(entry, dict):
            issues.append(
                IndexIssue(
                    code="manifest_entry_invalid",
                    severity="error",
                    message=f"Manifest entry {key!r} is not an object",
                    repair=None,
                )
            )
            continue
        doc_id = entry.get("doc_id")
        if not doc_id:
            issues.append(
                IndexIssue(
                    code="manifest_doc_id_missing",
                    severity="error",
                    message=f"Manifest entry {key!r} has no doc_id",
                )
            )
            continue
        manifest_doc_ids.add(doc_id)
        if doc_id not in chroma_doc_ids:
            issues.append(
                IndexIssue(
                    code="manifest_stale_doc",
                    severity="error",
                    message=f"Manifest entry {key!r} points to missing Chroma doc {doc_id!r}",
                    doc_id=doc_id,
                    repair="remove stale manifest entry",
                )
            )
            continue
        expected_count = entry.get("chunk_count")
        actual_count = len(chunks_by_doc.get(doc_id, []))
        if isinstance(expected_count, int) and expected_count != actual_count:
            issues.append(
                IndexIssue(
                    code="manifest_chunk_count_mismatch",
                    severity="warning",
                    message=f"Manifest doc {doc_id!r} has chunk_count={expected_count}, Chroma has {actual_count}",
                    doc_id=doc_id,
                )
            )

    for doc_id in sorted(chroma_doc_ids - manifest_doc_ids):
        issues.append(
            IndexIssue(
                code="chroma_doc_missing_manifest",
                severity="warning",
                message=f"Chroma doc {doc_id!r} has no manifest entry; URL imports and legacy docs may be expected",
                doc_id=doc_id,
            )
        )

    chroma_chunk_ids = set(chroma_chunks)
    bm25_chunk_ids = set(bm25_chunks)
    for chunk_id in sorted(chroma_chunk_ids - bm25_chunk_ids):
        chunk = chroma_chunks[chunk_id]
        issues.append(
            IndexIssue(
                code="bm25_missing_chunk",
                severity="error",
                message=f"BM25 is missing Chroma chunk {chunk_id!r}",
                doc_id=(chunk.get("metadata") or {}).get("doc_id"),
                chunk_id=chunk_id,
                repair="rebuild BM25 from Chroma",
            )
        )
    for chunk_id in sorted(bm25_chunk_ids - chroma_chunk_ids):
        entry = bm25_chunks[chunk_id]
        issues.append(
            IndexIssue(
                code="bm25_orphan_chunk",
                severity="error",
                message=f"BM25 chunk {chunk_id!r} is not present in Chroma",
                doc_id=entry.doc_id,
                chunk_id=chunk_id,
                repair="rebuild BM25 from Chroma",
            )
        )

    issue_dicts = [asdict(issue) for issue in issues]
    return {
        "ok": not any(issue.severity == "error" for issue in issues),
        "status": _status_for_issues(issues),
        "collection": collection_name,
        "summary": {
            "chroma_documents": len(chroma_doc_ids),
            "chroma_chunks": len(chroma_chunk_ids),
            "bm25_chunks": len(bm25_chunk_ids),
            "manifest_entries": len(manifest_docs),
        },
        "issue_counts": _issue_counts(issues),
        "issues": issue_dicts,
    }


def rebuild_bm25_from_chroma(
    *,
    vector_store: Any,
    bm25_indexer: BM25Indexer,
    collection_name: str = "documents",
) -> int:
    _, chunks_by_doc, _ = _collect_chroma_state(vector_store, collection_name)
    entries: list[ChunkEntry] = []
    for doc_id, chunks in chunks_by_doc.items():
        for chunk in chunks:
            chunk_id = chunk.get("id")
            content = chunk.get("content") or ""
            metadata = dict(chunk.get("metadata") or {})
            if not chunk_id or not content:
                continue
            metadata.setdefault("doc_id", doc_id)
            entries.append(
                ChunkEntry(
                    chunk_id=chunk_id,
                    doc_id=metadata.get("doc_id", doc_id),
                    content=content,
                    metadata=metadata,
                )
            )
    bm25_indexer.rebuild_from_entries(entries)
    return len(entries)


def remove_stale_manifest_entries(*, manifest_path: Path, chroma_doc_ids: set[str]) -> int:
    manifest = load_manifest(manifest_path)
    documents = manifest.get("documents", {})
    if not isinstance(documents, dict):
        return 0
    stale_keys = [
        key
        for key, entry in documents.items()
        if isinstance(entry, dict) and entry.get("doc_id") and entry.get("doc_id") not in chroma_doc_ids
    ]
    for key in stale_keys:
        documents.pop(key, None)
    if stale_keys:
        manifest.pop("_load_error", None)
        _atomic_write_json(manifest_path, manifest)
    return len(stale_keys)


def repair_indexes(
    *,
    vector_store: Any,
    bm25_indexer: BM25Indexer,
    manifest_path: Path,
    collection_name: str = "documents",
) -> dict:
    before = audit_indexes(
        vector_store=vector_store,
        bm25_indexer=bm25_indexer,
        manifest_path=manifest_path,
        collection_name=collection_name,
    )
    docs, _, _ = _collect_chroma_state(vector_store, collection_name)
    chroma_doc_ids = {doc.get("doc_id") for doc in docs if doc.get("doc_id")}

    repair_actions: list[dict[str, Any]] = []
    bm25_needs_rebuild = any(
        issue["code"] in {"bm25_missing_chunk", "bm25_orphan_chunk"} for issue in before["issues"]
    )
    if bm25_needs_rebuild:
        rebuilt = rebuild_bm25_from_chroma(
            vector_store=vector_store,
            bm25_indexer=bm25_indexer,
            collection_name=collection_name,
        )
        repair_actions.append({"action": "rebuild_bm25_from_chroma", "chunks": rebuilt})

    removed = remove_stale_manifest_entries(manifest_path=manifest_path, chroma_doc_ids=chroma_doc_ids)
    if removed:
        repair_actions.append({"action": "remove_stale_manifest_entries", "entries": removed})

    after = audit_indexes(
        vector_store=vector_store,
        bm25_indexer=bm25_indexer,
        manifest_path=manifest_path,
        collection_name=collection_name,
    )
    return {
        "ok": after["ok"],
        "before": before,
        "repair_actions": repair_actions,
        "after": after,
    }


def _build_runtime(config_path: Path) -> tuple[ChromaVectorStore, BM25Indexer, Path]:
    cfg = load_config(config_path)
    kb = cfg.knowledge_base
    hybrid_cfg = kb.hybrid_search or {}
    vector_store = ChromaVectorStore(kb.persist_directory)
    bm25_path = hybrid_cfg.get("bm25_persist_path", "./data/bm25_index.pkl")
    bm25_indexer = BM25Indexer(Path(bm25_path))
    return vector_store, bm25_indexer, Path(kb.manifest_path)


def _print_human(result: dict, *, repaired: bool) -> None:
    report = result["after"] if repaired else result
    print(f"Status: {report['status']}")
    print(
        "Summary: "
        f"Chroma docs={report['summary']['chroma_documents']}, "
        f"Chroma chunks={report['summary']['chroma_chunks']}, "
        f"BM25 chunks={report['summary']['bm25_chunks']}, "
        f"manifest entries={report['summary']['manifest_entries']}"
    )
    if repaired:
        actions = result.get("repair_actions", [])
        if actions:
            print("Repairs:")
            for action in actions:
                print(f"- {action}")
        else:
            print("Repairs: none")
    if report.get("issue_counts"):
        print("Issue counts:")
        for code, count in report["issue_counts"].items():
            print(f"- {code}: {count}")
    issues = report.get("issues", [])
    if not issues:
        print("Issues: none")
        return
    print("Issues:")
    for issue in issues:
        print(f"- [{issue['severity']}] {issue['code']}: {issue['message']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(default_config_path()), help="Path to config.yaml")
    parser.add_argument("--collection", default="documents", help="Chroma collection name")
    parser.add_argument("--repair", action="store_true", help="Rebuild BM25 and remove stale manifest entries")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    vector_store, bm25_indexer, manifest_path = _build_runtime(Path(args.config))
    if args.repair:
        result = repair_indexes(
            vector_store=vector_store,
            bm25_indexer=bm25_indexer,
            manifest_path=manifest_path,
            collection_name=args.collection,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_human(result, repaired=True)
        return 0 if result["ok"] else 1

    result = audit_indexes(
        vector_store=vector_store,
        bm25_indexer=bm25_indexer,
        manifest_path=manifest_path,
        collection_name=args.collection,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result, repaired=False)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
