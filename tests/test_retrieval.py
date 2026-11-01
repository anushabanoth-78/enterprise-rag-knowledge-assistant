import uuid

from app.services.retrieval import Passage, filter_by_score, reciprocal_rank_fusion


def test_rrf_rewards_agreement_between_rankers():
    scores = reciprocal_rank_fusion([[1, 2, 3], [3, 1, 4]])
    assert max(scores, key=scores.get) in (1, 3)
    assert scores[1] > scores[2] and scores[3] > scores[4]


def test_rrf_handles_empty_rankings():
    assert reciprocal_rank_fusion([[], []]) == {}


def _p(score, reranked=True):
    return Passage(1, uuid.uuid4(), "a.pdf", 1, "x", score, reranked)


def test_threshold_drops_weak_reranked_passages():
    kept = filter_by_score([_p(4.0), _p(-3.0), _p(-9.0)], min_score=-3.0)
    assert [p.score for p in kept] == [4.0, -3.0]  # boundary is inclusive


def test_threshold_never_applies_to_rrf_scores():
    # RRF scores (~0.03) and cross-encoder logits are different scales; comparing them would be a bug.
    assert len(filter_by_score([_p(0.03, reranked=False)], min_score=-3.0)) == 1
