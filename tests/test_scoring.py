"""Tests for duptool.scoring. Each test name states the rule it proves.

Settings come from the repo's real config.yaml, so weights/thresholds used
here are the shipped ones.
"""

from pathlib import Path

from duptool.models import IdRef, TicketSignals, load_settings
from duptool.scoring import (
    LexicalIndex,
    band_for,
    score_id_overlap,
    score_layer_method,
    score_pair,
    score_steps,
    score_task_type,
)

REPO_ROOT = Path(__file__).parents[1]
SCORING = load_settings(REPO_ROOT / "config.yaml").scoring


def make_signals(
    ticket_id: str = "A",
    refs: tuple = (),           # (item_id, kind, source) triples
    quoted: tuple = (),
    methods: tuple = (),
    bdd: tuple = (),
    steps: tuple = (),
    title: str = "",
    body: str = "",
    task_type: str = "unknown",
    type_source: str = "none",
) -> TicketSignals:
    return TicketSignals(
        ticket_id=ticket_id,
        task_type=task_type,
        task_type_source=type_source,
        refs=[IdRef(item_id=i, kind=k, source=s) for i, k, s in refs],
        quoted_tokens=list(quoted),
        bdd_steps=list(bdd),
        method_names=list(methods),
        step_numbers=list(steps),
        other_urls=[],
        image_refs=[],
        clean_title=title,
        clean_description=body,
        body_text=body,
        extractors_used=["generic"],
    )


def index_for(*signals: TicketSignals) -> LexicalIndex:
    return LexicalIndex(list(signals), SCORING.lexical)


# --- id_overlap -----------------------------------------------------------------


def test_shared_item_scores_at_least_base():
    a = make_signals("A", refs=(("48213", "tms", "template_section"),))
    b = make_signals("B", refs=(("48213", "tms", "text"),))
    score, evidence = score_id_overlap(a, b, SCORING.id_overlap)
    assert score >= SCORING.id_overlap.base
    assert evidence == ["both reference TMS-48213 (A: template section, B: written in text)"]


def test_identical_ref_sets_score_one():
    refs = (("111", "tms", "text"), ("222", "trq", "text"))
    score, _ = score_id_overlap(make_signals("A", refs=refs), make_signals("B", refs=refs), SCORING.id_overlap)
    assert score == 1.0


def test_disjoint_ref_sets_are_negative_evidence_not_neutral():
    a = make_signals("A", refs=(("111", "tms", "text"),))
    b = make_signals("B", refs=(("222", "tms", "text"),))
    score, evidence = score_id_overlap(a, b, SCORING.id_overlap)
    assert score == 0.0
    assert "no shared items" in evidence[0]


def test_missing_refs_on_either_side_is_neutral():
    a = make_signals("A", refs=(("111", "tms", "text"),))
    b = make_signals("B")  # freeform old ticket without any IDs
    assert score_id_overlap(a, b, SCORING.id_overlap) is None


def test_kind_known_on_one_side_is_used_for_the_label():
    a = make_signals("A", refs=(("1231211", "unknown", "link"),))
    b = make_signals("B", refs=(("1231211", "trq", "text"),))
    _, evidence = score_id_overlap(a, b, SCORING.id_overlap)
    assert "TRQ-1231211" in evidence[0]


# --- layer_method ------------------------------------------------------------------


def test_layer_is_neutral_when_either_side_has_no_tokens():
    a = make_signals("A", methods=("standardize_function",))
    b = make_signals("B")
    assert score_layer_method(a, b, SCORING.layer_method) is None


def test_shared_method_names_score_full_pool_weight():
    a = make_signals("A", methods=("standardize_function",))
    b = make_signals("B", methods=("standardize_function",))
    score, evidence = score_layer_method(a, b, SCORING.layer_method)
    assert score == SCORING.layer_method.method_weight
    assert evidence == ["shared method names: standardize_function"]


def test_quoted_token_overlap_scores_below_exact_method_match():
    q_a = make_signals("A", quoted=("left_menu",))
    q_b = make_signals("B", quoted=("left_menu",))
    m_a = make_signals("A", methods=("left_menu",))
    m_b = make_signals("B", methods=("left_menu",))
    quoted_score, _ = score_layer_method(q_a, q_b, SCORING.layer_method)
    method_score, _ = score_layer_method(m_a, m_b, SCORING.layer_method)
    assert quoted_score < method_score


def test_disjoint_tokens_are_negative_evidence():
    a = make_signals("A", methods=("expiration_date_get",))
    b = make_signals("B", methods=("standardize_function",))
    score, evidence = score_layer_method(a, b, SCORING.layer_method)
    assert score == 0.0
    assert "no overlap" in evidence[0]


# --- task_type ------------------------------------------------------------------------


def test_same_type_scores_full_and_names_both_routes():
    a = make_signals("A", task_type="refactor", type_source="title_header")
    b = make_signals("B", task_type="refactor", type_source="keywords")
    score, evidence = score_task_type(a, b, SCORING.task_type)
    assert score == SCORING.task_type.same_type
    assert evidence == ["both are refactor tasks (A: title header, B: keywords)"]


def test_cross_type_gets_partial_credit_never_zero():
    a = make_signals("A", task_type="investigation", type_source="title_header")
    b = make_signals("B", task_type="refactor", type_source="keywords")
    score, evidence = score_task_type(a, b, SCORING.task_type)
    assert 0.0 < score < SCORING.task_type.same_type
    assert "cross-type" in evidence[0]


def test_unknown_type_on_either_side_is_neutral():
    a = make_signals("A", task_type="unknown")
    b = make_signals("B", task_type="refactor", type_source="keywords")
    assert score_task_type(a, b, SCORING.task_type) is None


# --- steps -------------------------------------------------------------------------------


def test_shared_step_numbers_score_with_evidence():
    a = make_signals("A", steps=("3", "12"))
    b = make_signals("B", steps=("3",))
    score, evidence = score_steps(a, b)
    assert score == 1.0  # smaller set fully contained
    assert evidence == ["both mention step(s) 3"]


def test_no_step_mentions_is_neutral():
    assert score_steps(make_signals("A"), make_signals("B", steps=("3",))) is None


# --- lexical (BM25) --------------------------------------------------------------------


def test_similar_texts_score_higher_than_dissimilar():
    a = make_signals("A", body="update the barcode layout table checks")
    b = make_signals("B", body="the barcode layout table needs updated checks")
    c = make_signals("C", body="investigate login timeout on slow networks")
    idx = index_for(a, b, c)
    sim_ab, _ = idx.similarity("A", "B")
    sim_ac, _ = idx.similarity("A", "C")
    assert sim_ab > sim_ac


def test_lexical_is_neutral_when_text_is_empty():
    a = make_signals("A", body="some text here")
    b = make_signals("B")  # no title, no body
    idx = index_for(a, b)
    assert idx.similarity("A", "B") is None


def test_identical_texts_score_close_to_one():
    a = make_signals("A", body="update the barcode layout table")
    b = make_signals("B", body="update the barcode layout table")
    idx = index_for(a, b)
    sim, _ = idx.similarity("A", "B")
    assert sim > 0.99


def test_evidence_names_the_rarest_shared_words():
    a = make_signals("A", body="update the barcode layout table checks")
    b = make_signals("B", body="new barcode layout column added")
    c = make_signals("C", body="update the login checks")
    idx = index_for(a, b, c)
    _, evidence = idx.similarity("A", "B")
    assert "barcode" in evidence[0]


def test_pure_numbers_do_not_inflate_text_similarity():
    a = make_signals("A", body="1234567 8901234")
    b = make_signals("B", body="1234567 8901234")
    idx = index_for(a, b)
    assert idx.similarity("A", "B") is None  # digits are not lexical tokens


# --- score_pair: renormalization, hard override, banding ---------------------------------


def test_weights_renormalize_over_active_signals():
    # Only lexical can fire for this pair -> final == lexical subscore.
    a = make_signals("A", body="update the barcode layout table")
    b = make_signals("B", body="update the barcode layout table")
    pair = score_pair(a, b, SCORING, index_for(a, b))
    lexical = next(s for s in pair.subscores if s.name == "lexical")
    assert abs(pair.final_score - round(lexical.score, 4)) < 1e-9


def test_skipped_signals_are_reported_not_hidden():
    a = make_signals("A", body="some text")
    b = make_signals("B", body="other words entirely")
    pair = score_pair(a, b, SCORING, index_for(a, b))
    skipped = {s.name for s in pair.subscores if s.score is None}
    assert skipped == {"id_overlap", "layer_method", "task_type", "steps"}


def test_hard_override_surfaces_low_scoring_shared_item_pair():
    # one shared item among many, everything else pulling the score down
    a = make_signals(
        "A",
        refs=tuple((str(n), "tms", "text") for n in range(1111111, 1111116)),
        body="update the barcode layout table checks",
        task_type="implementation", type_source="keywords",
    )
    b = make_signals(
        "B",
        refs=(("1111111", "tms", "text"), ("9999999", "trq", "text"),
              ("8888888", "trq", "text"), ("7777777", "trq", "text"),
              ("6666666", "trq", "text")),
        body="investigate login timeout on slow networks",
        task_type="refactor", type_source="keywords",
    )
    pair = score_pair(a, b, SCORING, index_for(a, b))
    assert pair.hard_override is True
    assert pair.band != "not_shown"
    id_evidence = next(s for s in pair.subscores if s.name == "id_overlap").evidence
    assert any("hard rule" in e for e in id_evidence) or pair.final_score >= SCORING.bands.possibly_related


def test_no_override_without_shared_items():
    a = make_signals("A", refs=(("111", "tms", "text"),), body="barcode checks")
    b = make_signals("B", refs=(("222", "tms", "text"),), body="login timeout")
    pair = score_pair(a, b, SCORING, index_for(a, b))
    assert pair.hard_override is False


def test_band_thresholds_come_from_config():
    assert band_for(SCORING.bands.possible_duplicate, SCORING.bands) == "possible_duplicate"
    assert band_for(SCORING.bands.possibly_related, SCORING.bands) == "possibly_related"
    assert band_for(SCORING.bands.possibly_related - 0.01, SCORING.bands) == "not_shown"


def test_final_score_stays_within_bounds():
    refs = (("111", "tms", "template_section"),)
    a = make_signals("A", refs=refs, methods=("f_one",), steps=("3",),
                     body="update the barcode layout table",
                     task_type="refactor", type_source="title_header")
    b = make_signals("B", refs=refs, methods=("f_one",), steps=("3",),
                     body="update the barcode layout table",
                     task_type="refactor", type_source="title_header")
    pair = score_pair(a, b, SCORING, index_for(a, b))
    assert 0.0 <= pair.final_score <= 1.0
    assert pair.band == "possible_duplicate"


def test_scoring_is_deterministic():
    a = make_signals("A", refs=(("111", "tms", "text"),), body="barcode layout checks")
    b = make_signals("B", refs=(("111", "trq", "link"),), body="barcode table update")
    idx = index_for(a, b)
    first = score_pair(a, b, SCORING, idx)
    second = score_pair(a, b, SCORING, idx)
    assert first.model_dump() == second.model_dump()
