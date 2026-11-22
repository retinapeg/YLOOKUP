"""Contract for Leo's maths implementations. Run pytest -m maths_stub."""
import math

import numpy as np
import pytest

from app import retrieval as maths

pytestmark = pytest.mark.maths_stub


def test_tfidf_raw_counts_document_frequency_and_order():
    rows = [["a", "b", "b", "outside"], ["a"], ["a"], []]
    # b occurs in one chunk; a in three (IDF = ln(4/4)).
    actual = maths.tfidf_matrix(rows, ["b", "absent", "a"])
    assert actual.shape == (4, 3)
    np.testing.assert_allclose(actual, [[2 * math.log(2), 0, 0], [0, 0, 0],
                                        [0, 0, 0], [0, 0, 0]])


def test_ubiquitous_term_has_negative_weight():
    np.testing.assert_allclose(maths.tfidf_matrix([["x"], ["x"]], ["x"]),
                               [[math.log(2/3)], [math.log(2/3)]])


@pytest.mark.parametrize("rows,vocab,shape", [([], [], (0, 0)), ([], ["x"], (0, 1)),
                                                ([["x"]], [], (1, 0))])
def test_empty_matrix_dimensions(rows, vocab, shape):
    assert maths.tfidf_matrix(rows, vocab).shape == shape


def test_cosine_known_angles_and_zero_row():
    result = maths.cosine_sim(np.array([3., 4.]), np.array([[6, 8], [-4, 3], [-3, -4], [0, 0], [1, 0]]))
    assert result.shape == (5,)
    np.testing.assert_allclose(result, [1, 0, -1, 0, .6], atol=1e-12)


@pytest.mark.parametrize("q,X,out", [(np.zeros(2), np.ones((2, 2)), [0, 0]),
                                      (np.ones(2), np.empty((0, 2)), []),
                                      (np.empty(0), np.empty((3, 0)), [0, 0, 0])])
def test_degenerate_cosines(q, X, out):
    assert maths.cosine_sim(q, X).shape == (len(out),)
    np.testing.assert_array_equal(maths.cosine_sim(q, X), out)


def test_cosine_rejects_mismatched_width():
    with pytest.raises(ValueError):
        maths.cosine_sim(np.ones(3), np.ones((2, 4)))


@pytest.mark.parametrize("scores,k,expected", [([-.5, 1, 1, 0], 2, [1, 2]),
    ([-2, -1, -1], 9, [1, 2, 0]), ([1], 0, []), ([], 3, []), ([0, 0, 0], 1, [0])])
def test_top_k(scores, k, expected):
    assert maths.top_k(np.array(scores), k) == expected


@pytest.mark.parametrize("k,expected", [(0, 0.), (1, 1/3), (2, 1/3), (5, 2/3)])
def test_recall_denominator_and_repeated_ranked_pages(k, expected):
    retrieved = {"a": [9, 9, 2], "b": [4], "unlabelled": [3]}
    gold = {"a": 2, "b": 4, "missing_ranking": 1, "unlabelled": None}
    assert maths.recall_at_k(retrieved, gold, k) == pytest.approx(expected)


def test_no_gold_fields():
    assert maths.recall_at_k({"a": [3]}, {"a": None}, 5) == 0.


def test_negative_cutoffs():
    with pytest.raises(ValueError):
        maths.top_k([1], -1)
    with pytest.raises(ValueError):
        maths.recall_at_k({}, {}, -1)
