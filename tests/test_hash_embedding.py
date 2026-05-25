"""Hash embedding smoke tests."""

import math

import pytest

from src.knowledge.vector_store import HashEmbedding


@pytest.mark.asyncio
async def test_hash_embedding_is_deterministic_and_normalized() -> None:
    embedding = HashEmbedding(dimensions=32)

    first = (await embedding.embed(["MemoX offline smoke"]))[0]
    second = (await embedding.embed(["MemoX offline smoke"]))[0]

    assert first == second
    assert len(first) == 32
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0)


@pytest.mark.asyncio
async def test_hash_embedding_distinguishes_texts() -> None:
    embedding = HashEmbedding(dimensions=32)

    first, second = await embedding.embed(["alpha beta", "gamma delta"])

    assert first != second
