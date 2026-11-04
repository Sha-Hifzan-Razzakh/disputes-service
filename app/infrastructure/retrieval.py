"""Policy retrieval: deterministic hashing embedder + Postgres-backed chunk store.

Vectors are stored as float8[] and ranked in Python (corpus per tenant is small). A pgvector
adapter can replace SqlPolicyStore behind the PolicyStore port without touching callers.
"""

from __future__ import annotations

import hashlib
import math
import re
from itertools import pairwise

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports import PolicyHit
from app.infrastructure.db.models import PolicyChunkRow, PolicyDocumentRow
from app.infrastructure.db.session import transaction

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_STOP = frozenset(
    "the a an of to and or in is for on be with by if it this that are as at from must may should was were has".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class HashingEmbedder:
    """Feature-hashed bag of unigrams and bigrams, L2-normalised. Deterministic and offline."""

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _bucket(self, token: str) -> tuple[int, float]:
        h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
        return h % self.dim, (1.0 if (h >> 63) & 1 else -1.0)

    def embed(self, text: str) -> list[float]:
        toks = tokenize(text)
        feats = toks + [f"{a}_{b}" for a, b in pairwise(toks)]
        vec = [0.0] * self.dim
        for f in feats:
            i, sign = self._bucket(f)
            vec[i] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def chunk_text(text: str, max_words: int = 90, overlap: int = 15) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    for p in paras:
        words = p.split()
        if len(words) <= max_words:
            chunks.append(p)
            continue
        step = max_words - overlap
        for start in range(0, len(words), step):
            chunks.append(" ".join(words[start : start + max_words]))
            if start + max_words >= len(words):
                break
    return chunks


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


class SqlPolicyStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], embedder: HashingEmbedder) -> None:
        self.sessions = sessions
        self.embedder = embedder

    async def add_document(self, *, tenant_id: str | None, title: str, source: str, text: str) -> int:
        async with self.sessions.begin() as s:
            doc = PolicyDocumentRow(tenant_id=tenant_id, title=title, source=source)
            s.add(doc)
            await s.flush()
            chunks = chunk_text(text)
            for i, c in enumerate(chunks):
                s.add(
                    PolicyChunkRow(
                        document_id=doc.id,
                        tenant_id=tenant_id,
                        ordinal=i,
                        text=c,
                        embedding=self.embedder.embed(f"{title}. {c}"),
                    )
                )
        return len(chunks)

    async def search(self, *, tenant_id: str, query: str, k: int) -> list[PolicyHit]:
        qv = self.embedder.embed(query)
        async with transaction(self.sessions) as s:
            rows = (
                await s.execute(
                    select(PolicyChunkRow, PolicyDocumentRow.title)
                    .join(PolicyDocumentRow, PolicyDocumentRow.id == PolicyChunkRow.document_id)
                    .where(or_(PolicyChunkRow.tenant_id == tenant_id, PolicyChunkRow.tenant_id.is_(None)))
                )
            ).all()
        scored = sorted(
            ((_cosine(qv, r.PolicyChunkRow.embedding), r) for r in rows),
            key=lambda x: (-x[0], str(x[1].PolicyChunkRow.id)),
        )
        return [
            PolicyHit(
                chunk_id=str(r.PolicyChunkRow.id),
                document_title=r.title,
                text=r.PolicyChunkRow.text,
                score=round(score, 4),
                scope="tenant" if r.PolicyChunkRow.tenant_id else "network",
            )
            for score, r in scored[:k]
        ]
