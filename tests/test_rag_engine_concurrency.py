"""RAG engine concurrency and manifest consistency tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

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
