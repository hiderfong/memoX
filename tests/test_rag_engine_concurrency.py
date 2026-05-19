"""RAG engine concurrency and manifest consistency tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.knowledge.bm25_indexer import ChunkEntry
from src.knowledge.document_parser import Document, TextChunk
from src.knowledge.rag_engine import RAGEngine


class SlowParser:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls = 0

    async def parse_and_chunk(
        self,
        file_path: Path,
        doc_id: str,
        chunk_size: int = 500,
        overlap: int = 50,
        chunk_strategy: str = "size",
        embedding_fn=None,
    ) -> tuple[Document, list[TextChunk]]:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.05)
            content = file_path.read_text(encoding="utf-8")
            document = Document(
                id=doc_id,
                filename=file_path.name,
                content=content,
                metadata={"type": "markdown"},
            )
            return document, [
                TextChunk(
                    id=f"{doc_id}_chunk_0",
                    content=content,
                    metadata={"chunk_index": 0, "chunk_strategy": chunk_strategy},
                    index=0,
                )
            ]
        finally:
            self.active -= 1


class InMemoryVectorStore:
    def __init__(self) -> None:
        self.chunks: dict[str, TextChunk] = {}

    async def add_chunks(self, chunks: list[TextChunk], collection_name: str = "documents") -> list[str]:
        await asyncio.sleep(0)
        for chunk in chunks:
            self.chunks[chunk.id] = chunk
        return [chunk.id for chunk in chunks]

    async def delete_by_document_id(self, doc_id: str, collection_name: str = "documents") -> int:
        await asyncio.sleep(0)
        deleted = [chunk_id for chunk_id, chunk in self.chunks.items() if chunk.metadata.get("doc_id") == doc_id]
        for chunk_id in deleted:
            self.chunks.pop(chunk_id)
        return len(deleted)

    def list_documents(self, collection_name: str = "documents") -> list[dict]:
        documents: dict[str, dict] = {}
        for chunk in self.chunks.values():
            metadata = chunk.metadata
            doc_id = metadata["doc_id"]
            documents.setdefault(
                doc_id,
                {
                    "doc_id": doc_id,
                    "filename": metadata["filename"],
                    "type": metadata["type"],
                    "created_at": metadata["created_at"],
                    "file_size": metadata["file_size"],
                    "group_id": metadata["group_id"],
                    "chunk_count": 0,
                },
            )
            documents[doc_id]["chunk_count"] += 1
        return list(documents.values())


class FailableBM25:
    def __init__(self) -> None:
        self.fail_on_add = False
        self.entries: dict[str, ChunkEntry] = {}

    def add_chunks(self, chunks: list[ChunkEntry]) -> None:
        if self.fail_on_add:
            raise RuntimeError("bm25 unavailable")
        for chunk in chunks:
            self.entries[chunk.chunk_id] = chunk

    def delete_by_doc_id(self, doc_id: str) -> list[str]:
        deleted = [chunk_id for chunk_id, chunk in self.entries.items() if chunk.doc_id == doc_id]
        for chunk_id in deleted:
            self.entries.pop(chunk_id)
        return deleted


@pytest.mark.asyncio
async def test_concurrent_identical_uploads_are_serialized_and_deduplicated(tmp_path: Path) -> None:
    parser = SlowParser()
    vector_store = InMemoryVectorStore()
    manifest_path = tmp_path / "documents_manifest.json"
    engine = RAGEngine(
        vector_store=vector_store,
        document_parser=parser,
        hybrid_search_enabled=False,
        manifest_path=str(manifest_path),
    )

    file_a = tmp_path / "upload-a.md"
    file_b = tmp_path / "upload-b.md"
    content = "# Concurrent Upload\n\nsame content should index once"
    file_a.write_text(content, encoding="utf-8")
    file_b.write_text(content, encoding="utf-8")

    first, second = await asyncio.gather(
        engine.add_document(file_a, original_filename="same-name.md"),
        engine.add_document(file_b, original_filename="same-name.md"),
    )

    actions = sorted([first.action, second.action])
    assert actions == ["indexed", "skipped"]
    assert first.doc_info.id == second.doc_info.id
    assert parser.calls == 1
    assert parser.max_active == 1
    assert len(vector_store.list_documents()) == 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["documents"]) == 1
    only_entry = next(iter(manifest["documents"].values()))
    assert only_entry["doc_id"] == first.doc_info.id


@pytest.mark.asyncio
async def test_new_upload_rolls_back_vector_chunks_when_bm25_fails(tmp_path: Path) -> None:
    vector_store = InMemoryVectorStore()
    bm25 = FailableBM25()
    bm25.fail_on_add = True
    engine = RAGEngine(
        vector_store=vector_store,
        document_parser=SlowParser(),
        hybrid_search_enabled=False,
        manifest_path=str(tmp_path / "documents_manifest.json"),
    )
    engine._hybrid_retriever = SimpleNamespace(bm25_indexer=bm25)
    upload = tmp_path / "upload.md"
    upload.write_text("# Failure\n\nthis should roll back", encoding="utf-8")

    with pytest.raises(RuntimeError, match="bm25 unavailable"):
        await engine.add_document(upload, original_filename="rollback.md")

    assert vector_store.list_documents() == []
    assert bm25.entries == {}
    assert not (tmp_path / "documents_manifest.json").exists()


@pytest.mark.asyncio
async def test_new_upload_rolls_back_when_manifest_save_fails(tmp_path: Path) -> None:
    vector_store = InMemoryVectorStore()
    manifest_path = tmp_path / "documents_manifest.json"
    engine = RAGEngine(
        vector_store=vector_store,
        document_parser=SlowParser(),
        hybrid_search_enabled=False,
        manifest_path=str(manifest_path),
    )
    upload = tmp_path / "upload.md"
    content = "# Manifest Failure\n\nthis should not remain indexed"
    upload.write_text(content, encoding="utf-8")

    def fail_save() -> None:
        raise OSError("disk full")

    engine._manifest.save = fail_save

    with pytest.raises(OSError, match="disk full"):
        await engine.add_document(upload, original_filename="manifest-fail.md")

    assert vector_store.list_documents() == []
    assert engine._manifest.get("manifest-fail.md", len(content.encode("utf-8"))) is None
    assert not manifest_path.exists()


@pytest.mark.asyncio
async def test_failed_update_keeps_previous_document_and_manifest(tmp_path: Path) -> None:
    vector_store = InMemoryVectorStore()
    bm25 = FailableBM25()
    manifest_path = tmp_path / "documents_manifest.json"
    engine = RAGEngine(
        vector_store=vector_store,
        document_parser=SlowParser(),
        hybrid_search_enabled=False,
        manifest_path=str(manifest_path),
    )
    engine._hybrid_retriever = SimpleNamespace(bm25_indexer=bm25)
    upload = tmp_path / "upload.md"
    upload.write_text("version one", encoding="utf-8")

    first = await engine.add_document(upload, original_filename="stable.md")
    first_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    bm25.fail_on_add = True
    upload.write_text("version two", encoding="utf-8")
    with pytest.raises(RuntimeError, match="bm25 unavailable"):
        await engine.add_document(upload, original_filename="stable.md")

    assert vector_store.list_documents() == [
        {
            "doc_id": first.doc_info.id,
            "filename": "stable.md",
            "type": "markdown",
            "created_at": first.doc_info.created_at,
            "file_size": len("version one"),
            "group_id": "ungrouped",
            "chunk_count": 1,
        }
    ]
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == first_manifest
    assert {entry.doc_id for entry in bm25.entries.values()} == {first.doc_info.id}
