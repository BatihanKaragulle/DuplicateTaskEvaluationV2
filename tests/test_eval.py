"""Tests for the labelled-pair workflow and eval metrics.

(An addition to the CLAUDE.md test layout, reason: these metrics referee
ALL weight/threshold tuning -- if they are wrong, every tuning decision
is wrong with them.)
"""

from pathlib import Path

import pytest

from duptool.__main__ import compute_eval_metrics, main
from duptool.ingest import load_labelled_pairs
from duptool.models import ScoredPair, SubScore

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parents[1]


def make_pair(a, b, score, band):
    return ScoredPair(
        ticket_a=a, ticket_b=b, final_score=score, band=band, hard_override=False,
        subscores=[SubScore(name="lexical", score=score, weight=1.0, evidence=["e"])],
    )


def write_labels(tmp_path, text):
    path = tmp_path / "labels.csv"
    path.write_text(text, encoding="utf-8")
    return path


# --- loading the labelled pair file --------------------------------------------


def test_fixture_labels_load():
    pairs = load_labelled_pairs(FIXTURES / "labelled_pairs.csv")
    assert len(pairs) == 3
    assert pairs[0].relationship == "duplicate"


def test_bad_relationship_fails_loudly(tmp_path):
    path = write_labels(tmp_path, "ticket_a,ticket_b,relationship\nT-1,T-2,dupe\n")
    with pytest.raises(ValueError, match="dupe"):
        load_labelled_pairs(path)


def test_missing_columns_fail_loudly(tmp_path):
    path = write_labels(tmp_path, "a,b\nT-1,T-2\n")
    with pytest.raises(ValueError, match="relationship"):
        load_labelled_pairs(path)


def test_repeated_pair_fails_loudly(tmp_path):
    path = write_labels(
        tmp_path,
        "ticket_a,ticket_b,relationship\nT-1,T-2,duplicate\nT-2,T-1,related\n",
    )
    with pytest.raises(ValueError, match="more than once"):
        load_labelled_pairs(path)


def test_self_pair_fails_loudly(tmp_path):
    path = write_labels(tmp_path, "ticket_a,ticket_b,relationship\nT-1,T-1,duplicate\n")
    with pytest.raises(ValueError, match="different ticket"):
        load_labelled_pairs(path)


# --- metric computation -----------------------------------------------------------


def scored_fixture():
    return [
        make_pair("T-1", "T-2", 0.9, "possible_duplicate"),   # labelled duplicate
        make_pair("T-3", "T-4", 0.6, "possibly_related"),     # labelled duplicate
        make_pair("T-5", "T-6", 0.7, "possibly_related"),     # labelled unrelated
        make_pair("T-7", "T-8", 0.2, "not_shown"),            # labelled unrelated
    ]


def labels_fixture():
    from duptool.models import LabelledPair
    return [
        LabelledPair(ticket_a="T-1", ticket_b="T-2", relationship="duplicate"),
        LabelledPair(ticket_a="T-3", ticket_b="T-4", relationship="duplicate"),
        LabelledPair(ticket_a="T-5", ticket_b="T-6", relationship="unrelated"),
        LabelledPair(ticket_a="T-7", ticket_b="T-8", relationship="unrelated"),
        LabelledPair(ticket_a="T-9", ticket_b="T-10", relationship="duplicate"),  # not in export
    ]


def test_matrix_counts_and_skipped():
    m = compute_eval_metrics(scored_fixture(), labels_fixture())
    assert m.total_labelled == 5 and m.used == 4 and m.skipped_missing_ticket == 1
    assert m.matrix["possible_duplicate"]["duplicate"] == 1
    assert m.matrix["possibly_related"]["duplicate"] == 1
    assert m.matrix["possibly_related"]["unrelated"] == 1
    assert m.matrix["not_shown"]["unrelated"] == 1


def test_primary_precision_and_recall():
    m = compute_eval_metrics(scored_fixture(), labels_fixture())
    assert m.duplicate_precision == 1.0        # 1 flagged duplicate, correctly
    assert m.duplicate_recall == 0.5           # 1 of 2 labelled duplicates flagged


def test_top3_counts_surfaced_partners_only():
    m = compute_eval_metrics(scored_fixture(), labels_fixture())
    # both used duplicates are surfaced (dup band and related band) -> 2/2
    assert m.top3_accuracy == 1.0


def test_hidden_duplicate_misses_top3():
    scored = [make_pair("T-1", "T-2", 0.2, "not_shown")]
    labels = [labels_fixture()[0]]
    m = compute_eval_metrics(scored, labels)
    assert m.top3_accuracy == 0.0


def test_unrelated_false_positive_rates():
    m = compute_eval_metrics(scored_fixture(), labels_fixture())
    assert m.unrelated_flagged_duplicate == 0.0
    assert m.unrelated_surfaced == 0.5         # T-5/T-6 surfaced as related


def test_empty_denominators_are_none_not_zero():
    scored = [make_pair("T-1", "T-2", 0.2, "not_shown")]
    labels = [labels_fixture()[2]]  # only an unrelated label
    m = compute_eval_metrics(scored, labels)
    assert m.duplicate_precision is None
    assert m.duplicate_recall is None
    assert m.top3_accuracy is None


def test_reversed_ticket_order_still_matches():
    scored = [make_pair("T-2", "T-1", 0.9, "possible_duplicate")]
    labels = [labels_fixture()[0]]  # labelled as T-1,T-2
    m = compute_eval_metrics(scored, labels)
    assert m.used == 1 and m.duplicate_precision == 1.0


# --- CLI end to end -----------------------------------------------------------------


def test_eval_command_end_to_end(capsys):
    code = main([
        "eval",
        "--input", str(FIXTURES / "sample_tickets.csv"),
        "--labels", str(FIXTURES / "labelled_pairs.csv"),
        "--config", str(REPO_ROOT / "config.yaml"),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "labelled pairs: 3 total, 3 used, 0 skipped" in out
    assert "possible_duplicate precision" in out and "PRIMARY" in out
    assert "top-3 accuracy" in out
