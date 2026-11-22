"""Page-local chunking, field queries, and document-scoped retrieval."""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence
from urllib import request

import numpy as np


def tfidf_matrix(chunk_token_lists, vocab):
    """Build a float array (N, V) from N token lists and V ordered unique words.

    X[c,w] = tf[c,w] * ln(N / (1 + n_w)), where tf is the raw count and
    n_w = sum_c 1[w occurs in chunk c]. Ignore tokens outside vocab. An absent
    word or empty chunk has zero entries. A ubiquitous word has negative IDF.
    N=0 returns shape (0,V) without evaluating ln(0); V=0 returns (N,0).
    """
    raise NotImplementedError


def cosine_sim(q, X):
    """Score q of shape (V,) against finite rows X of shape (N,V), returning (N,).

    s[c] = sum_j q[j]*X[c,j] / (sqrt(sum_j q[j]^2)*sqrt(sum_j X[c,j]^2)).
    Return zero when either norm is zero, including V=0. N=0 returns an
    empty vector. Negative scores are valid. Reject incompatible shapes.
    """
    raise NotImplementedError


def top_k(scores, k):
    """Return a list of up to k original row indices from finite scores (N,).

    For permutation p sorted ascending by (-scores[i], i), return
    [p[r] for 0 <= r < min(k,N)]. Lower indices win ties. Empty input or
    k=0 returns []; k>N returns all indices. Negative k raises ValueError.
    """
    raise NotImplementedError


def recall_at_k(retrieved_pages_per_field, gold_page_per_field, k):
    """Score ranked chunk pages against one gold page per field, returning a float.

    Inputs are mappings field -> list of page numbers (ragged, rank order)
    and field -> page number or None. Let G be the fields with non-null gold.
    R@k = sum_{f in G} 1[g_f in pages_f[:k]] / |G|. Repeated pages retain
    their ranks. Missing rankings count as misses; unlabelled fields are
    excluded. No gold or k=0 returns 0.0; k above list length uses all hits.
    Negative k raises ValueError.
    """
    raise NotImplementedError


FIELD_QUERIES = (
    ("fund_name", "fund name partnership"),
    ("investor_name", "investor name limited partner"),
    ("commitment_amount", "commitment amount total commitment"),
    ("capital_call_amount", "amount due capital call total payable"),
    ("call_date", "call date notice date issued"),
    ("due_date", "due date payment deadline"),
    ("currency", "currency denomination settlement currency"),
    ("bank_account_reference", "bank account reference payment reference"),
    ("management_fee", "management fee"),
    ("document_type", "document type capital call notice"),
)


@dataclass(frozen=True)
class TextPage:
    number: int
    text: str


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    page: int
    chunk_id: int
    start: int
    end: int
    text: str
    starts_mid_line: bool
    ends_mid_line: bool

    def complete_lines(self) -> str:
        """Keep complete source lines for the deterministic label parser."""
        lines = self.text.splitlines()
        return "\n".join(lines[int(self.starts_mid_line):len(lines) - int(self.ends_mid_line)])


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


@dataclass
class RetrievalResult:
    backend: str
    fields: dict[str, list[Hit]]
    warnings: list[str]


def chunk_pages(doc_id: str, pages: Sequence[TextPage], size: int = 80, overlap: int = 20) -> list[Chunk]:
    """Return source-ordered windows with half-open Unicode offsets on each page."""
    if size < 1 or not 0 <= overlap < size:
        raise ValueError("require size > overlap >= 0")
    chunks = []
    for page in pages:
        words = list(re.finditer(r"\S+", page.text))
        for first in range(0, len(words), size - overlap):
            stop = min(first + size, len(words))
            start, end = words[first].start(), words[stop - 1].end()
            leading_gap = page.text[words[first - 1].end():start] if first else ""
            trailing_gap = page.text[end:words[stop].start()] if stop < len(words) else ""
            chunks.append(Chunk(doc_id, page.number, len(chunks), start, end, page.text[start:end],
                                first > 0 and not re.search(r"[\r\n]", leading_gap),
                                stop < len(words) and not re.search(r"[\r\n]", trailing_gap)))
            if stop == len(words):
                break
    return chunks


class Retriever(Protocol):
    def retrieve(self, chunks: Sequence[Chunk], queries: Mapping[str, str], k: int) -> RetrievalResult:
        ...


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _rank(chunks, matrix, query_vectors, k, backend):
    fields = {}
    for field, vector in query_vectors.items():
        scores = cosine_sim(vector, matrix)
        fields[field] = [Hit(chunks[i], float(scores[i])) for i in top_k(scores, k)]
    return RetrievalResult(backend, fields, [])


class TfidfRetriever:
    def retrieve(self, chunks, queries, k):
        tokens = [_tokens(chunk.text) for chunk in chunks]
        vocab = sorted({token for row in tokens for token in row})
        matrix = tfidf_matrix(tokens, vocab)
        # Recover the fitted per-word weight from a row that contains it.
        # This keeps query and document weights tied to the same corpus/stub.
        columns = {word: j for j, word in enumerate(vocab)}
        weights = {}
        for i, row in enumerate(tokens):
            for word, count in Counter(row).items():
                if word not in weights:
                    weights[word] = matrix[i, columns[word]] / count
        vectors = {}
        for field, query in queries.items():
            vector = np.zeros(len(vocab))
            for word, count in Counter(_tokens(query)).items():
                if word in columns:
                    vector[columns[word]] = count * weights[word]
            vectors[field] = vector
        return _rank(chunks, matrix, vectors, k, "tfidf")


class EmbeddingRetriever:
    def __init__(self, *, transport=None):
        self.key = os.getenv("OPENAI_API_KEY")
        self.url = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.model = os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small"
        self.transport = transport

    def _batch(self, texts):
        if self.transport is not None:
            payload = self.transport(texts)
        else:
            req = request.Request(
                self.url + "/embeddings",
                data=json.dumps({"model": self.model, "input": texts}).encode(),
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            )
            with request.urlopen(req, timeout=12) as response:
                payload = json.load(response)
        rows = sorted(payload["data"], key=lambda row: row["index"])
        if [row["index"] for row in rows] != list(range(len(texts))):
            raise ValueError("Incomplete embeddings response")
        matrix = np.asarray([row["embedding"] for row in rows], dtype=float)
        if matrix.ndim != 2 or not matrix.shape[1] or not np.isfinite(matrix).all():
            raise ValueError("Invalid embeddings response")
        return matrix

    def retrieve(self, chunks, queries, k):
        if not self.key and self.transport is None:
            result = TfidfRetriever().retrieve(chunks, queries, k)
            result.warnings.append("Embedding retrieval unavailable without OPENAI_API_KEY; used TF-IDF.")
            return result
        if not chunks or not queries:
            return RetrievalResult("embeddings", {f: [] for f in queries}, [])
        texts = [chunk.text for chunk in chunks] + list(queries.values())
        matrix = np.concatenate([self._batch(texts[i:i + 64]) for i in range(0, len(texts), 64)])
        return _rank(chunks, matrix[:len(chunks)], dict(zip(queries, matrix[len(chunks):])), k, "embeddings")
