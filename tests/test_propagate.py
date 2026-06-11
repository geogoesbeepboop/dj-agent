"""Label-propagation + active-learning math (pure; no DB).

Vectors here are hand-built and L2-normalized, matching the real invariant.
"""

import numpy as np

from dj.taste.propagate import provisional_taste, uncertainty


def test_provisional_taste_follows_the_nearest_neighbor():
    # target sits on neighbor 0; neighbor 1 is orthogonal (zero weight).
    target = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    neighbor_acoustic = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    neighbor_taste = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    ratings = np.array([5.0, 1.0], dtype=np.float32)

    p = provisional_taste(target, neighbor_acoustic, neighbor_taste, ratings, k=2)
    assert p is not None
    assert np.allclose(p.taste_vec, [1.0, 0.0], atol=1e-5)
    assert abs(p.rating - 5.0) < 1e-5
    assert abs(p.confidence - 1.0) < 1e-5


def test_provisional_taste_none_when_no_positive_neighbor():
    target = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    away = np.array([[-1.0, 0.0, 0.0]], dtype=np.float32)  # cosine -1 → clipped to 0
    neighbor_taste = np.array([[0.0, 1.0]], dtype=np.float32)
    ratings = np.array([3.0], dtype=np.float32)
    assert provisional_taste(target, away, neighbor_taste, ratings) is None


def test_provisional_taste_none_when_corpus_empty():
    target = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    empty_a = np.zeros((0, 3), dtype=np.float32)
    empty_t = np.zeros((0, 2), dtype=np.float32)
    empty_r = np.zeros((0,), dtype=np.float32)
    assert provisional_taste(target, empty_a, empty_t, empty_r) is None


def test_uncertainty_is_max_with_no_labels():
    target = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert uncertainty(target, np.zeros((0, 3), dtype=np.float32), np.zeros((0,))) == 1.0


def test_uncertainty_low_for_close_agreeing_neighbors():
    target = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    tagged = np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    ratings = np.array([5.0, 5.0], dtype=np.float32)
    assert uncertainty(target, tagged, ratings, k=2) < 1e-6


def test_uncertainty_high_for_distant_neighbors():
    target = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    tagged = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)  # orthogonal → closeness 0
    ratings = np.array([5.0], dtype=np.float32)
    assert uncertainty(target, tagged, ratings, k=1) == 1.0


def test_uncertainty_rises_when_neighbors_disagree():
    target = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    tagged = np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)  # both close
    ratings = np.array([1.0, 5.0], dtype=np.float32)  # but disagree → std 2 → 0.5
    assert abs(uncertainty(target, tagged, ratings, k=2) - 0.5) < 1e-6
