"""Index consistency audit and repair tests."""

from __future__ import annotations

import json
from pathlib import Path

from src.knowledge.bm25_indexer import BM25Indexer, ChunkEntry
from src.ops.index_consistency import audit_indexes, repair_indexes


class FakeVectorStore:
    def __init__(self, chunks: dict[str, list[dict]]) -> None:
        self._chunks = chunks

    def list_documents(self, collection_name: str = "documents") -> list[dict]:
        return [
            {
                "doc_id": doc_id,
                "filename": chunks[0]["metadata"].get("filename", "unknown") if chunks else "unknown",
                "type": chunks[0]["metadata"].get("type", "text") if chunks else "text",
                "created_at": chunks[0]["metadata"].get("created_at", "") if chunks else "",
                "file_size": chunks[0]["metadata"].get("file_size", 0) if chunks else 0,
                "group_id": chunks[0]["metadata"].get("group_id", "ungrouped") if chunks else "ungrouped",
                "chunk_count": len(chunks),
            }
            for doc_id, chunks in self._chunks.items()
        ]

    def get_chunks_by_doc(self, doc_id: str, collection_name: str = "documents") -> list[dict]:
        return list(self._chunks.get(doc_id, []))


def _chunk(chunk_id: str, doc_id: str, content: str = "searchable content") -> dict:
    return {
        "id": chunk_id,
        "content": content,
        "metadata": {
            "doc_id": doc_id,
            "filename": f"{doc_id}.md",
            "type": "markdown",
            "created_at": "2026-05-19T00:00:00",
            "file_size": len(content),
            "group_id": "ungrouped",
        },
    }


def _write_manifest(path: Path, entries: dict[str, dict]) -> None:
    path.write_text(json.dumps({"version": 1, "documents": entries}, ensure_ascii=False), encoding="utf-8")


def test_audit_reports_consistent_indexes(tmp_path: Path) -> None:
    vector_store = FakeVectorStore({"doc1": [_chunk("doc1_chunk_0", "doc1")]})
    bm25 = BM25Indexer(tmp_path / "bm25.pkl")
    bm25.add_chunks([ChunkEntry("doc1_chunk_0", "doc1", "searchable content", {"doc_id": "doc1"})])
    manifest = tmp_path / "documents_manifest.json"
    _write_manifest(
        manifest,
        {
            "doc1.md::18": {
                "doc_id": "doc1",
                "content_hash": "hash",
                "chunk_count": 1,
                "created_at": "2026-05-19T00:00:00",
            }
        },
    )

    report = audit_indexes(vector_store=vector_store, bm25_indexer=bm25, manifest_path=manifest)

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert report["issue_counts"] == {}
    assert report["issues"] == []


def test_audit_reports_bm25_and_manifest_drift(tmp_path: Path) -> None:
    vector_store = FakeVectorStore({"doc1": [_chunk("doc1_chunk_0", "doc1")]})
    bm25 = BM25Indexer(tmp_path / "bm25.pkl")
    bm25.add_chunks([ChunkEntry("orphan_chunk_0", "ghost", "old content", {"doc_id": "ghost"})])
    manifest = tmp_path / "documents_manifest.json"
    _write_manifest(
        manifest,
        {
            "ghost.md::10": {
                "doc_id": "ghost",
                "content_hash": "hash",
                "chunk_count": 1,
                "created_at": "2026-05-19T00:00:00",
            }
        },
    )

    report = audit_indexes(vector_store=vector_store, bm25_indexer=bm25, manifest_path=manifest)
    codes = {issue["code"] for issue in report["issues"]}

    assert report["ok"] is False
    assert report["issue_counts"] == {
        "bm25_missing_chunk": 1,
        "bm25_orphan_chunk": 1,
        "chroma_doc_missing_manifest": 1,
        "manifest_stale_doc": 1,
    }
    assert "bm25_missing_chunk" in codes
    assert "bm25_orphan_chunk" in codes
    assert "manifest_stale_doc" in codes
    assert "chroma_doc_missing_manifest" in codes


def test_repair_rebuilds_bm25_and_removes_stale_manifest_entries(tmp_path: Path) -> None:
    vector_store = FakeVectorStore({"doc1": [_chunk("doc1_chunk_0", "doc1")]})
    bm25 = BM25Indexer(tmp_path / "bm25.pkl")
    bm25.add_chunks([ChunkEntry("orphan_chunk_0", "ghost", "old content", {"doc_id": "ghost"})])
    manifest = tmp_path / "documents_manifest.json"
    _write_manifest(
        manifest,
        {
            "doc1.md::18": {
                "doc_id": "doc1",
                "content_hash": "hash",
                "chunk_count": 1,
                "created_at": "2026-05-19T00:00:00",
            },
            "ghost.md::10": {
                "doc_id": "ghost",
                "content_hash": "hash",
                "chunk_count": 1,
                "created_at": "2026-05-19T00:00:00",
            },
        },
    )

    result = repair_indexes(vector_store=vector_store, bm25_indexer=bm25, manifest_path=manifest)
    manifest_after = json.loads(manifest.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["repair_actions"] == [
        {"action": "rebuild_bm25_from_chroma", "chunks": 1},
        {"action": "remove_stale_manifest_entries", "entries": 1},
    ]
    assert set(bm25._corpus) == {"doc1_chunk_0"}
    assert set(manifest_after["documents"]) == {"doc1.md::18"}
