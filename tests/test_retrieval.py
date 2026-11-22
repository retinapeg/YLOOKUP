import numpy as np
import pytest

from app import retrieval as r
from tests._reference_retrieval import test_only_reference_maths as reference_maths


def test_chunk_offsets_and_stable_order():
    pages = [r.TextPage(1, " é  b\nc d  e "), r.TextPage(2, " "), r.TextPage(3, "one")]
    chunks = r.chunk_pages("doc", pages, 3, 1)
    assert [(c.chunk_id, c.page, c.text) for c in chunks] == [(0, 1, "é  b\nc"), (1, 1, "c d  e"), (2, 3, "one")]
    for chunk in chunks:
        assert chunk.doc_id == "doc"
        assert pages[chunk.page-1].text[chunk.start:chunk.end] == chunk.text
    assert [c.complete_lines() for c in chunks] == ["é  b", "c d  e", "one"]
    with pytest.raises(ValueError):
        r.chunk_pages("doc", pages, 2, 2)


def test_backend_fallback_and_embedding_response_order(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    chunks = r.chunk_pages("doc", [r.TextPage(i, t) for i, t in enumerate(["alpha", "beta", "gamma"], 1)])
    queries = {"f": "beta"}
    with reference_maths():
        result = r.EmbeddingRetriever().retrieve(chunks, queries, 2)
        assert result.warnings and result.backend == "tfidf"
        assert result.fields["f"][0].chunk.page == 2
        def transport(texts):
            return {"data": [{"index": i, "embedding": [1, int(text == "beta")]} for i, text in reversed(list(enumerate(texts)))]}
        embedded = r.EmbeddingRetriever(transport=transport).retrieve(chunks, queries, 2)
        assert embedded.backend == "embeddings" and embedded.fields["f"][0].chunk.page == 2
        assert np.isclose(embedded.fields["f"][0].score, 1)


def test_reference_switch_is_scoped():
    with reference_maths():
        assert r.top_k([0, 1], 1) == [1]
    # The context manager restores the original function, even after Leo implements it.
    assert r.top_k.__module__ == "app.retrieval"
