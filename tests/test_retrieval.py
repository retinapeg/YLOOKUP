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


def test_model_and_fallback_cannot_read_outside_field_window(tmp_path):
    from app.extraction import DeterministicExtractor, OpenAICompatibleExtractor
    from app.models import ExtractionMethod

    path = tmp_path / "sample.txt"
    path.write_text("Fund Name: Ember\nDue Date: 2027-02-19\fInvoice text only", encoding="utf-8")

    class ScopedRetriever:
        def retrieve(self, chunks, queries, k):
            # Both labels share a page; only the first three words are visible
            # to the due_date and fund_name requests.
            fields = {name: [r.Hit(chunks[0], 1.0)] for name in queries}
            fields["management_fee"] = [r.Hit(chunks[-1], .6)]
            return r.RetrievalResult("test", fields, [])

    prompts = []
    def model(prompt):
        prompts.append(prompt)
        return {"fields": {
            "fund_name": {"value": "Ember", "page": 1, "confidence": 1, "evidence": "Fund Name: Ember"},
            "due_date": {"value": "2027-02-19", "page": 1, "confidence": 1, "evidence": "Due Date: 2027-02-19"},
            "management_fee": {"value": "10", "page": 2, "confidence": 1, "evidence": "Invoice text only"},
        }}

    config = dict(retrieval_mode=True, retriever=ScopedRetriever(), chunk_size=3, overlap=0)
    offline = DeterministicExtractor(**config).extract(path)
    result = OpenAICompatibleExtractor(transport=model, **config).extract(path)
    assert len(prompts) == len(r.FIELD_QUERIES)
    assert all("2027-02-19" not in p for p in prompts)
    assert all("Invoice text only" not in p for p in prompts if not p.startswith("Extract only management_fee."))
    for doc in (offline, result):
        assert doc.fields["due_date"].value is None and doc.fields["due_date"].abstention_reason
        assert doc.fields["management_fee"].value is None
        assert doc.fields["fund_name"].page == 1
        assert doc.retrieval.fields["fund_name"][0].chunk.text == "Fund Name: Ember"
    assert result.fields["fund_name"].method == ExtractionMethod.OPENAI_COMPATIBLE


def test_scoped_conflict_remains_abstention(tmp_path):
    from app.extraction import OpenAICompatibleExtractor

    path = tmp_path / "conflict.txt"
    path.write_text("Capital Call Amount: GBP 500\fCapital Call Amount: GBP 600")
    prompts = []
    def model(prompt):
        prompts.append(prompt)
        return {}
    with reference_maths():
        doc = OpenAICompatibleExtractor(retrieval_mode=True, transport=model).extract(path)
    assert doc.fields["capital_call_amount"].value is None
    assert "conflicting" in doc.fields["capital_call_amount"].abstention_reason
    assert all(not prompt.startswith("Extract only capital_call_amount.") for prompt in prompts)


def test_retrieval_ui_renders_scored_chunks(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("FUNDOPS_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with reference_maths():
        app = AppTest.from_file("streamlit_app.py", default_timeout=20).run()
        app.toggle(key="retrieval_mode").set_value(True).run()
        app.button(key="load_northstar").click().run()
        assert not app.exception
        assert app.session_state["document"].retrieval.backend == "tfidf"
        assert any("Score" in c.value and "Page" in c.value for c in app.caption)
        assert any("Capital Call Amount:" in block.value for block in app.text)
