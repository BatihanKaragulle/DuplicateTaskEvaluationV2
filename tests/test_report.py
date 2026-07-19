"""Tests for duptool.candidates, duptool.report and the run CLI.

(An addition to the CLAUDE.md test layout, reason: the report files are the
user-facing contract and the CLI is the only end-to-end integration path.)
"""

import csv
import json
from pathlib import Path

from duptool.__main__ import main
from duptool.candidates import generate_pairs
from duptool.models import ScoredPair, SubScore, Ticket, load_settings
from duptool.report import shown_pairs, write_reports

REPO_ROOT = Path(__file__).parents[1]
FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample_tickets.csv"
REPORT = load_settings(REPO_ROOT / "config.yaml").report


def make_pair(a="T-1", b="T-2", score=0.9, band="possible_duplicate", override=False):
    return ScoredPair(
        ticket_a=a, ticket_b=b, final_score=score, band=band, hard_override=override,
        subscores=[
            SubScore(name="id_overlap", score=1.0, weight=0.45,
                     evidence=["both reference TMS-48213 (A: link, B: written in text)"]),
            SubScore(name="layer_method", score=None, weight=0.15,
                     evidence=["skipped: not enough data on one or both sides"]),
            SubScore(name="lexical", score=0.4, weight=0.25,
                     evidence=["text similarity 0.40 (top shared words: barcode)"]),
        ],
    )


# --- candidates -----------------------------------------------------------------


def test_all_unique_pairs_no_self_pairs():
    from duptool.signals import extract_signals
    settings = load_settings(REPO_ROOT / "config.yaml").signals
    signals = [
        extract_signals(Ticket(id=f"T-{i}", title="t", description=""), settings)
        for i in range(4)
    ]
    pairs = generate_pairs(signals)
    assert len(pairs) == 6  # 4 choose 2
    assert all(p.ticket_a != p.ticket_b for p in pairs)
    assert len(set((p.ticket_a, p.ticket_b) for p in pairs)) == 6


CANDIDATES = load_settings(REPO_ROOT / "config.yaml").candidates


def test_parent_child_pairs_are_suppressed_with_count():
    from duptool.candidates import suppress_known_pairs
    from duptool.models import CandidatePair
    tickets = [
        Ticket(id="US-1", title="Implement FDD Steps", description=""),
        Ticket(id="T-2", title="Implement FDD Steps", description="", parent_id="US-1"),
        Ticket(id="T-3", title="other work", description=""),
    ]
    pairs = [CandidatePair(ticket_a="US-1", ticket_b="T-2"),
             CandidatePair(ticket_a="US-1", ticket_b="T-3"),
             CandidatePair(ticket_a="T-2", ticket_b="T-3")]
    kept, counts = suppress_known_pairs(pairs, tickets, CANDIDATES)
    assert counts == {"parent_child": 1, "already_linked": 0}
    assert [(p.ticket_a, p.ticket_b) for p in kept] == [("US-1", "T-3"), ("T-2", "T-3")]


def test_already_linked_pairs_are_suppressed():
    from duptool.candidates import suppress_known_pairs
    from duptool.models import CandidatePair
    tickets = [
        Ticket(id="T-1", title="a", description="", linked_ids=["T-2"]),
        Ticket(id="T-2", title="b", description=""),
    ]
    kept, counts = suppress_known_pairs(
        [CandidatePair(ticket_a="T-1", ticket_b="T-2")], tickets, CANDIDATES
    )
    assert kept == [] and counts["already_linked"] == 1


def test_suppression_can_be_disabled_by_config():
    from duptool.candidates import suppress_known_pairs
    from duptool.models import CandidatePair
    settings = CANDIDATES.model_copy(
        update={"suppress_parent_child": False, "suppress_already_linked": False}
    )
    tickets = [
        Ticket(id="US-1", title="story", description=""),
        Ticket(id="T-2", title="task", description="", parent_id="US-1"),
    ]
    kept, counts = suppress_known_pairs(
        [CandidatePair(ticket_a="US-1", ticket_b="T-2")], tickets, settings
    )
    assert len(kept) == 1 and counts == {"parent_child": 0, "already_linked": 0}


# --- ranking / shown_pairs --------------------------------------------------------


def test_not_shown_pairs_are_dropped_and_rest_ranked():
    pairs = [
        make_pair("T-1", "T-2", score=0.6, band="possibly_related"),
        make_pair("T-3", "T-4", score=0.9, band="possible_duplicate"),
        make_pair("T-5", "T-6", score=0.2, band="not_shown"),
    ]
    ranked = shown_pairs(pairs)
    assert [(p.ticket_a, p.ticket_b) for p in ranked] == [("T-3", "T-4"), ("T-1", "T-2")]


def test_equal_scores_tie_break_deterministically():
    pairs = [
        make_pair("T-9", "T-8", score=0.7, band="possibly_related"),
        make_pair("T-1", "T-2", score=0.7, band="possibly_related"),
    ]
    ranked = shown_pairs(pairs)
    assert [(p.ticket_a, p.ticket_b) for p in ranked] == [("T-1", "T-2"), ("T-9", "T-8")]


# --- report files -------------------------------------------------------------------


def test_csv_contains_only_shown_pairs_with_evidence(tmp_path):
    pairs = [make_pair(), make_pair("T-5", "T-6", score=0.1, band="not_shown")]
    tickets = [Ticket(id="T-1", title="a", description=""),
               Ticket(id="T-2", title="b", description="")]
    write_reports(pairs, tickets, REPORT, tmp_path)
    rows = list(csv.DictReader(open(tmp_path / "pairs.csv", encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["ticket_a"] == "T-1"
    assert rows[0]["layer_method"] == ""  # skipped signal stays visibly empty
    assert "TMS-48213" in rows[0]["evidence"]


def test_json_round_trips_to_scored_pairs(tmp_path):
    tickets = [Ticket(id="T-1", title="a", description="")]
    write_reports([make_pair()], tickets, REPORT, tmp_path)
    payload = json.loads((tmp_path / "pairs.json").read_text(encoding="utf-8"))
    restored = [ScoredPair.model_validate(item) for item in payload]
    assert restored[0].ticket_a == "T-1"
    assert restored[0].subscores[1].score is None


def test_summary_lists_each_ticket_with_titles_and_evidence(tmp_path):
    tickets = [Ticket(id="T-1", title="expiry checks", description="body A"),
               Ticket(id="T-2", title="menu update", description="body B")]
    write_reports([make_pair()], tickets, REPORT, tmp_path)
    text = (tmp_path / "summary.txt").read_text(encoding="utf-8")
    assert "Top candidates for T-1 (expiry checks):" in text
    assert "Top candidates for T-2 (menu update):" in text
    assert "possible duplicate" in text            # suggestion wording, not verdict
    assert "both reference TMS-48213" in text
    assert "body A" not in text                    # never ticket bodies in reports


def test_summary_respects_top_k(tmp_path):
    pairs = [
        make_pair("T-1", f"T-{i}", score=0.9 - i / 100, band="possibly_related")
        for i in range(2, 10)
    ]
    tickets = [Ticket(id=f"T-{i}", title="", description="") for i in range(1, 10)]
    write_reports(pairs, tickets, REPORT, tmp_path)
    text = (tmp_path / "summary.txt").read_text(encoding="utf-8")
    section = text.split("Top candidates for T-1:")[1].split("Top candidates for T-2")[0]
    listed = [line for line in section.splitlines() if line.strip().startswith(tuple("123456789"))]
    assert len(listed) == REPORT.top_k_per_ticket


def test_empty_results_still_produce_a_readable_summary(tmp_path):
    write_reports([], [], REPORT, tmp_path)
    text = (tmp_path / "summary.txt").read_text(encoding="utf-8")
    assert "No candidate pairs" in text


# --- CLI end to end -------------------------------------------------------------------


def test_run_command_end_to_end(tmp_path, capsys):
    out_dir = tmp_path / "results"
    code = main([
        "run",
        "--input", str(FIXTURE_CSV),
        "--config", str(REPO_ROOT / "config.yaml"),
        "--out", str(out_dir),
    ])
    assert code == 0
    assert (out_dir / "pairs.csv").exists()
    assert (out_dir / "pairs.json").exists()
    assert (out_dir / "summary.txt").exists()

    # TASK-101 and TASK-102 share TMS-48213 and TRQ-1042 -> must be surfaced
    text = (out_dir / "summary.txt").read_text(encoding="utf-8")
    assert "TASK-101" in text and "TASK-102" in text
    assert "both reference TMS-48213" in text

    console = capsys.readouterr().out
    assert "loaded 4 tickets" in console
    # fixture TASK-103 has an empty description -> reported, ticket kept
    assert "TASK-103" in console and "empty description" in console
    # TASK-104 is TASK-103's child (same title!) -> suppressed, not suggested
    assert "suppressed known-related pairs: 1 parent-child" in console
    assert "TASK-104" not in (out_dir / "summary.txt").read_text(encoding="utf-8")
