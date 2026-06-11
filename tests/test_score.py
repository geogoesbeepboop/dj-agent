"""Blended-scoring logic (pure; no DB, no model)."""

from dj.taste.score import BlendWeights, Candidate, rank, score

W = BlendWeights(acoustic=0.5, taste=0.4, rating=0.1)


def test_cold_start_untagged_is_acoustic_only():
    # No taste vector and no rating → only the acoustic term contributes.
    c = Candidate(path="a", acoustic_sim=0.8)
    assert abs(score(c, W) - 0.5 * 0.8) < 1e-9


def test_manual_taste_outranks_higher_acoustic_when_taste_weighted():
    weights = BlendWeights(acoustic=0.3, taste=0.7, rating=0.0)
    acoustic_fav = Candidate(path="loud", acoustic_sim=0.9)            # great acoustically
    taste_fav = Candidate(                                            # weaker sound, my vibe
        path="mine", acoustic_sim=0.6, taste_sim=0.95, taste_source="manual"
    )
    ordered = rank([acoustic_fav, taste_fav], weights)
    assert ordered[0].path == "mine"


def test_manual_beats_propagated_at_equal_taste_sim():
    manual = Candidate(path="m", acoustic_sim=0.5, taste_sim=0.9, taste_source="manual")
    propagated = Candidate(path="p", acoustic_sim=0.5, taste_sim=0.9, taste_source="propagated")
    assert score(manual, W) > score(propagated, W)


def test_rating_breaks_a_tie():
    rated = Candidate(path="r", acoustic_sim=0.5, rating=5)
    unrated = Candidate(path="u", acoustic_sim=0.5)
    assert score(rated, W) > score(unrated, W)


def test_propagated_uses_stored_confidence_when_present():
    low = Candidate(path="lo", acoustic_sim=0.0, taste_sim=1.0,
                    taste_source="propagated", taste_confidence=0.1)
    high = Candidate(path="hi", acoustic_sim=0.0, taste_sim=1.0,
                     taste_source="propagated", taste_confidence=0.9)
    assert score(high, W) > score(low, W)
    # no stored confidence → falls back to the 0.5 default (β·0.5·taste_sim)
    default = Candidate(path="d", acoustic_sim=0.0, taste_sim=1.0, taste_source="propagated")
    assert abs(score(default, W) - 0.4 * 0.5 * 1.0) < 1e-9


def test_rank_is_best_first():
    cs = [
        Candidate(path="lo", acoustic_sim=0.1),
        Candidate(path="hi", acoustic_sim=0.9),
        Candidate(path="mid", acoustic_sim=0.5),
    ]
    assert [c.path for c in rank(cs, W)] == ["hi", "mid", "lo"]
