"""文档 chunk 检索测试"""
import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from knowledge.document_parser import TextChunk


@pytest.fixture
def vector_store(tmp_path):
    from knowledge.vector_store import ChromaVectorStore, SentenceTransformerEmbedding
    embedding = SentenceTransformerEmbedding()
    store = ChromaVectorStore(persist_directory=str(tmp_path / "chroma"), embedding_function=embedding)
    return store


@pytest.mark.asyncio
async def test_get_chunks_by_doc(vector_store):
    """按 doc_id 检索所有 chunk"""
    chunks = [
        TextChunk(id="d1_chunk_0", content="第一段内容", metadata={"doc_id": "d1", "filename": "test.md", "chunk_index": 0}),
        TextChunk(id="d1_chunk_1", content="第二段内容", metadata={"doc_id": "d1", "filename": "test.md", "chunk_index": 1}),
        TextChunk(id="d2_chunk_0", content="另一个文档", metadata={"doc_id": "d2", "filename": "other.md", "chunk_index": 0}),
    ]
    await vector_store.add_chunks(chunks)

    result = vector_store.get_chunks_by_doc("d1")
    assert len(result) == 2
    assert result[0]["chunk_index"] == 0
    assert result[1]["chunk_index"] == 1
    assert "第一段内容" in result[0]["content"]


@pytest.mark.asyncio
async def test_get_chunks_by_doc_not_found(vector_store):
    """不存在的 doc_id 返回空列表"""
    result = vector_store.get_chunks_by_doc("nonexistent")
    assert result == []
