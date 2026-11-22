# FundOps retrieval ingestion handoff

Local branch `feature/rag-ingestion` was created from `d462d09`. The tested implementation commit is `99cd9c367877fb2591d4219cb09ef76ac9b6266d`. The following handoff commit contains documentation only.

**Leo’s next step** is to implement the four functions in [app/retrieval.py](../../app/retrieval.py). Each currently raises `NotImplementedError`. The full-document workflow is the startup default so the existing demo and fixture baseline remain usable. Retrieval is opt-in.

| Function | Acceptance criterion |
| --- | --- |
| `tfidf_matrix(chunk_token_lists, vocab)` | Raw term counts times the specified natural-log IDF, in the supplied vocabulary order. A term present in every chunk has a negative weight. |
| `cosine_sim(q, X)` | One score per matrix row, with zero for a zero norm. |
| `top_k(scores, k)` | Descending scores, ties by lower original index, at most the available count. |
| `recall_at_k(retrieved_pages_per_field, gold_page_per_field, k)` | Fraction of fields with a gold page whose first k ranked chunks contain that page. |

In the repository-local virtualenv, the acceptance command is **`pytest -m maths_stub`**. The default pytest configuration deselects those 21 cases. They passed against the test-only oracle. Running them against the current production stubs is expected to fail. Once the hand-written implementations pass, remove the default marker exclusion in `pytest.ini` to include them in CI.

## Verified commands and results

Run from the repository root with the existing `.venv` activated.

| Command | Result |
| --- | --- |
| `python -m pytest -q` | 161 passed (150 original + 11 new), 21 maths cases deselected. Includes AppTest for the evidence ledger, field isolation, literal grounding and the no-key embeddings fallback. |
| `python -m app.evals --mode fixture --fail-on-regression --output .venv/rag-fixture-eval.json` | 27 cases, 267/270 exact fields, 12/12 gold field exceptions found, 13/17 expected human escalations, 4/4 gates passed, zero model calls. |
| `python -m scripts.smoke_demo` | Clean case passed. Northstar had two expected exceptions (+GBP 25,000 and −2 days). Audit append and no-key extraction fallback passed. |
| `python -m tests._reference_retrieval --test-only-reference-maths --output .venv/long-pack-eval.json` | Completed using the four functions in `tests/_reference_retrieval.py` for this process only. TF-IDF results appear below. |
| `python -m app.evals.retrieval --offline` | Current production runner exits 2 with an unfinished-maths message. After implementing the stubs, this command runs the same benchmark using your code. |

The reference runner accepts `--offline` to skip embeddings even if credentials are configured. Without that flag, both benchmark entry points evaluate the optional embeddings endpoint when a key is available. **Embeddings were not run** in the recorded benchmark because `OPENAI_API_KEY` was absent. The adapter was tested with a supplied transport. Model extraction was tested with supplied responses. Live provider behavior remains unverified.

## Long-pack evidence

The 12 synthetic packs contain 363 pages in total (20–40 per pack), including six packs with an archived decoy. Gold values come from the existing labelled notices. Gold pages are remapped when a notice is moved or repaginated. The tests check the source evidence on the labelled target page and the document hashes. Regenerate the text and manifest with `python -m scripts.generate_long_packs`.

The table records the **test-only TF-IDF reference**, with 80-word windows, 20-word overlap and five chunks per field for extraction. Recall counts the rank of chunks, so repeat hits on the same page consume ranks. Its denominator excludes fields without a gold page. Accuracy includes all ten fields per pack, including expected abstentions. The decoy rate counts cases where the first retrieved chunk is on a decoy page, over gold-labelled field queries in decoy packs.

| Field | Recall@1 | Recall@3 | Recall@5 |
| --- | ---: | ---: | ---: |
| fund_name | 8/12 | 12/12 | 12/12 |
| investor_name | 0/12 | 0/12 | 0/12 |
| commitment_amount | 1/11 | 1/11 | 2/11 |
| capital_call_amount | 1/12 | 4/12 | 4/12 |
| call_date | 9/12 | 12/12 | 12/12 |
| due_date | 0/11 | 0/11 | 2/11 |
| currency | 2/12 | 2/12 | 4/12 |
| bank_account_reference | 12/12 | 12/12 | 12/12 |
| management_fee | 1/2 | 2/2 | 2/2 |
| document_type | 8/12 | 12/12 | 12/12 |
| overall | 42/108 | 57/108 | 62/108 |

| Group | Exact fields, retrieval | Exact fields, full document | Top-1 decoy / eligible fields |
| --- | ---: | ---: | ---: |
| all packs | 52/120 | 81/120 | 17/55 |
| decoy packs | 17/60 | 21/60 | 17/55 |
| clean packs | 35/60 | 60/60 | — |

Retrieval reduced exact field accuracy from 81/120 to 52/120 on this corpus. In particular, the target page never appeared in the first five chunks for investor name (0/12), whereas bank references achieved 12/12 even at rank one. The synthetic glossary repeats several field-query terms. Page recall and correct field extraction are separate outcomes. The ordered per-field pages and both accuracy flags are in the JSON produced by the benchmark.

## Changed interfaces

- [Retrieval](../../app/retrieval.py) owns page-local word windows, field queries, the two retrievers and the four maths stubs. `Retriever.retrieve` returns scored chunks, the selected backend and any fallback notice.
- [Extraction](../../app/extraction.py) accepts `retrieval_mode=True` on either extractor, plus optional `retriever`, `chunk_size`, `overlap` and `top_k`. The model and deterministic fallback share each field’s selected windows. The model cannot override a deterministic conflict.
- [Document model](../../app/models.py) adds optional `ExtractedDocument.retrieval`. Hits retain the document digest, original page, chunk ID, half-open character offsets, text and score. Final fields continue to use the existing value, page, evidence, method and abstention contracts.
- [Streamlit](../../streamlit_app.py) adds **Use retrieval for extraction**, an optional embeddings selector, and ranked chunks in each field’s Extraction Ledger expander. Original audit identifiers are preserved for full-document runs.
- [Retrieval benchmark](../../app/evals/retrieval.py) adds the long-pack report alongside the existing fixture CLI. The reference switch is confined to the tests package.
- The runtime now declares NumPy directly. Embeddings read the existing provider key and base URL plus `OPENAI_EMBEDDING_MODEL` (default `text-embedding-3-small`). The existing deterministic controls and human-decision interfaces keep their prior contracts.
