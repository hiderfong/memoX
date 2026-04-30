import asyncio
import re
import sys
sys.path.insert(0, '.')
from src.knowledge.semantic_chunker import SemanticChunker, _estimate_tokens

class OrthogonalEmbed:
    async def embed(self, texts):
        vecs = []
        for i, _ in enumerate(texts):
            v = [0.0] * 4
            v[i % 4] = 1.0
            vecs.append(v)
        return vecs

async def main():
    chunker = SemanticChunker(
        embedding_fn=OrthogonalEmbed(),
        chunk_size=500,
        chunk_overlap=1,
        similarity_threshold=0.5,
    )

    text = "今天天气好。XYZ unrelated text here."
    sents = chunker._split_sentences(text)
    print('Sentences:', [s.text for s in sents])

    async def cosine_sim(a, b):
        return sum(x*y for x,y in zip(a,b))

    embs = await chunker.embedding_fn.embed([s.text for s in sents])
    topic_vec = [sum(dim)/len(embs) for dim in zip(*embs)]

    print(f'Sentence 0 embed: {embs[0]}')
    print(f'Sentence 1 embed: {embs[1]}')
    print(f'Topic vec: {topic_vec}')

    sim0 = sum(a*b for a,b in zip(embs[0], topic_vec))
    sim1 = sum(a*b for a,b in zip(embs[1], topic_vec))
    print(f'Sim0 to topic: {sim0}')
    print(f'Sim1 to topic: {sim1}')

    chunks = await chunker.chunk(text)
    print(f'\nTotal chunks: {len(chunks)}')
    for i, c in enumerate(chunks):
        print(f'  Chunk {i}: {len(c.sentences)} sentences, score={c.topic_score:.3f}, content={c.content}')

asyncio.run(main())
