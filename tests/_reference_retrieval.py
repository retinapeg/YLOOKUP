"""Test-only oracle and explicit reference-mode evaluation entry point."""
import argparse
from collections import Counter
from contextlib import ExitStack, contextmanager
import math
from unittest.mock import patch

import numpy as np

from app import retrieval


def tfidf_matrix(chunk_token_lists, vocab):
    counts = [Counter(row) for row in chunk_token_lists]
    n = len(counts)
    out = np.zeros((n, len(vocab)))
    if n:
        for col, word in enumerate(vocab):
            df = sum(word in row for row in counts)
            for i, row in enumerate(counts):
                out[i, col] = row[word] * math.log(n / (1 + df))
    return out


def cosine_sim(q, X):
    q, X = np.asarray(q, dtype=float), np.asarray(X, dtype=float)
    if q.ndim != 1 or X.ndim != 2 or q.size != X.shape[1]:
        raise ValueError("incompatible shapes")
    den = np.linalg.norm(q) * np.linalg.norm(X, axis=1)
    return np.divide(X @ q, den, out=np.zeros(len(X)), where=den != 0)


def top_k(scores, k):
    if k < 0:
        raise ValueError("negative k")
    return sorted(range(len(scores)), key=lambda i: (-float(scores[i]), i))[:k]


def recall_at_k(retrieved_pages_per_field, gold_page_per_field, k):
    if k < 0:
        raise ValueError("negative k")
    hits = [page in retrieved_pages_per_field.get(field, [])[:k]
            for field, page in gold_page_per_field.items() if page is not None]
    return sum(hits) / len(hits) if hits else 0.0


@contextmanager
def test_only_reference_maths():
    with ExitStack() as stack:
        for fn in (tfidf_matrix, cosine_sim, top_k, recall_at_k):
            stack.enter_context(patch.object(retrieval, fn.__name__, fn))
        yield


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-only-reference-maths", action="store_true", required=True)
    _, args = parser.parse_known_args()
    from app.evals.retrieval import main
    with test_only_reference_maths():
        raise SystemExit(main(args, maths="test-only reference"))
