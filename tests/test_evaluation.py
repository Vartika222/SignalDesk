import math

from signaldesk.evaluation import GoldSpan, bootstrap_ci, find_span, relevant_gold, score_ranking
from signaldesk.retrieval import Hit, rrf


def test_find_span_tolerates_whitespace_quotes_dashes_and_case():
    hay = "Intro.\n\nWe now  expect revenue of\n$5.0 – $5.2 billion, “strong” demand."
    sp = find_span(hay, 'we now expect revenue of $5.0 - $5.2 billion, "strong"')
    assert sp is not None
    s, e = sp
    assert hay[s:e].startswith("We now")
    assert hay[s:e].endswith("“strong”")


def test_find_span_missing():
    assert find_span("abc", "xyz") is None
    assert find_span("abc", "   ") is None


def test_relevance_needs_same_section_and_half_coverage():
    spans = [GoldSpan(0, 1, 10, 100, 200)]
    assert relevant_gold((10, 0, 160), spans) == {0}  # covers 60%
    assert relevant_gold((10, 160, 400), spans) == set()  # covers 40%
    assert relevant_gold((11, 0, 1000), spans) == set()  # other section


def test_score_ranking():
    ranked = [set(), {0}, set(), set(), set(), set(), {1}]  # gold 0 at rank 2, gold 1 at rank 7
    m = score_ranking(ranked, n_gold=2)
    assert m["recall@1"] == 0
    assert m["recall@5"] == 0.5
    assert m["recall@10"] == 1.0
    assert m["mrr"] == 0.5
    assert 0 < m["ndcg@10"] < 1


def test_bootstrap_ci_brackets_mean():
    mean, lo, hi = bootstrap_ci([0, 1] * 25)
    assert mean == 0.5 and lo < 0.5 < hi
    assert hi - lo < 0.35
    assert all(math.isnan(x) for x in bootstrap_ci([]))


def test_rrf_rewards_agreement():
    a = [Hit(1, 1, 0), Hit(2, 2, 0), Hit(3, 3, 0)]
    b = [Hit(3, 1, 0), Hit(1, 2, 0), Hit(9, 3, 0)]
    fused = [h.chunk_id for h in rrf(a, b)]
    assert fused[0] == 1
    assert set(fused) == {1, 2, 3, 9}
