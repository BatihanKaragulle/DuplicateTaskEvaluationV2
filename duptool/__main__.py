"""CLI entry point: python -m duptool {run,eval}

run   score a CSV export and write the report files
eval  score a CSV export, compare against a labelled pair file, print metrics

Only argparse, only the console. Log lines reference tickets by ID; ticket
bodies never reach the console or the logs (confidentiality rule).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from duptool.candidates import generate_pairs, suppress_known_pairs
from duptool.ingest import load_labelled_pairs, load_tickets
from duptool.models import EvalMetrics, LabelledPair, ScoredPair, Settings, Ticket, load_settings
from duptool.report import shown_pairs, write_reports
from duptool.scoring import LexicalIndex, score_pair
from duptool.signals import extract_signals

# the "top-3 accuracy" metric from CLAUDE.md -- a named metric definition,
# not a tunable (the report's own list length is config: report.top_k_per_ticket)
_TOP_K_ACCURACY = 3

_BANDS = ["possible_duplicate", "possibly_related", "not_shown"]
_LABELS = ["duplicate", "related", "unrelated"]


def _score_all(tickets: list[Ticket], settings: Settings) -> list[ScoredPair]:
    """The pipeline core shared by run and eval: signals -> pairs -> scores.

    Known-related pairs (parent-child from the tree export, already-linked
    work items) are suppressed before scoring, with counts on the console.
    """
    signals = [extract_signals(t, settings.signals) for t in tickets]
    index = LexicalIndex(signals, settings.scoring.lexical)
    by_id = {s.ticket_id: s for s in signals}
    pairs, suppressed = suppress_known_pairs(
        generate_pairs(signals), tickets, settings.candidates
    )
    if any(suppressed.values()):
        print(
            f"suppressed known-related pairs: {suppressed['parent_child']} "
            f"parent-child, {suppressed['already_linked']} already-linked"
        )
    return [
        score_pair(by_id[p.ticket_a], by_id[p.ticket_b], settings.scoring, index)
        for p in pairs
    ]


def _load_and_report(args: argparse.Namespace, settings: Settings) -> list[Ticket]:
    result = load_tickets(args.input, settings.ingest)
    for issue in result.issues:
        ref = f" [{issue.ticket_id}]" if issue.ticket_id else ""
        print(f"ingest: row {issue.row}{ref}: {issue.problem}")
    print(f"loaded {len(result.tickets)} tickets ({len(result.issues)} issue(s) reported)")
    return result.tickets


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    tickets = _load_and_report(args, settings)
    if not tickets:
        print("error: no usable tickets in the CSV", file=sys.stderr)
        return 1

    scored = _score_all(tickets, settings)
    ranked = shown_pairs(scored)
    duplicates = sum(1 for p in ranked if p.band == "possible_duplicate")
    print(
        f"scored {len(scored)} pairs: {duplicates} possible duplicate(s), "
        f"{len(ranked) - duplicates} possibly related"
    )
    for p in ranked[:5]:
        print(f"  {p.ticket_a} <-> {p.ticket_b}  {p.final_score:.2f}  {p.band}")

    for path in write_reports(scored, tickets, settings.report, args.out):
        print(f"wrote {path}")
    return 0


# --- eval ----------------------------------------------------------------------


def compute_eval_metrics(
    all_scored: list[ScoredPair], labels: list[LabelledPair]
) -> EvalMetrics:
    """Pure metric computation -- the referee for all tuning.

    top-3 accuracy: a labelled duplicate counts as a hit when the partner
    appears among the first _TOP_K_ACCURACY surfaced candidates of either
    ticket (a tester would see the suggestion on one of the two).
    """
    by_key = {frozenset((p.ticket_a, p.ticket_b)): p for p in all_scored}

    top: dict[str, list[str]] = {}
    for p in shown_pairs(all_scored):  # ranked -> per-ticket lists stay ranked
        top.setdefault(p.ticket_a, []).append(p.ticket_b)
        top.setdefault(p.ticket_b, []).append(p.ticket_a)

    matrix = {band: {label: 0 for label in _LABELS} for band in _BANDS}
    used = skipped = top3_hits = top3_total = 0
    for lab in labels:
        pair = by_key.get(frozenset((lab.ticket_a, lab.ticket_b)))
        if pair is None:
            skipped += 1
            continue
        used += 1
        matrix[pair.band][lab.relationship] += 1
        if lab.relationship == "duplicate":
            top3_total += 1
            if (
                lab.ticket_b in top.get(lab.ticket_a, [])[:_TOP_K_ACCURACY]
                or lab.ticket_a in top.get(lab.ticket_b, [])[:_TOP_K_ACCURACY]
            ):
                top3_hits += 1

    def ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    flagged_duplicate = sum(matrix["possible_duplicate"].values())
    true_duplicates = sum(matrix[band]["duplicate"] for band in _BANDS)
    true_unrelated = sum(matrix[band]["unrelated"] for band in _BANDS)
    return EvalMetrics(
        total_labelled=len(labels),
        used=used,
        skipped_missing_ticket=skipped,
        matrix=matrix,
        duplicate_precision=ratio(matrix["possible_duplicate"]["duplicate"], flagged_duplicate),
        duplicate_recall=ratio(matrix["possible_duplicate"]["duplicate"], true_duplicates),
        top3_accuracy=ratio(top3_hits, top3_total),
        unrelated_flagged_duplicate=ratio(matrix["possible_duplicate"]["unrelated"], true_unrelated),
        unrelated_surfaced=ratio(
            matrix["possible_duplicate"]["unrelated"] + matrix["possibly_related"]["unrelated"],
            true_unrelated,
        ),
    )


def _print_metrics(m: EvalMetrics) -> None:
    print(
        f"labelled pairs: {m.total_labelled} total, {m.used} used, "
        f"{m.skipped_missing_ticket} skipped (ticket not in export)"
    )
    print()
    print(f"{'predicted band':<20}" + "".join(f"{label:>11}" for label in _LABELS))
    for band in _BANDS:
        print(f"{band:<20}" + "".join(f"{m.matrix[band][label]:>11}" for label in _LABELS))
    print()

    def fmt(value: float | None) -> str:
        return "n/a (no data)" if value is None else f"{value:.2f}"

    print(f"possible_duplicate precision: {fmt(m.duplicate_precision)}   <- PRIMARY")
    print(f"duplicate recall:             {fmt(m.duplicate_recall)}")
    print(f"top-3 accuracy:               {fmt(m.top3_accuracy)}")
    print(f"unrelated flagged duplicate:  {fmt(m.unrelated_flagged_duplicate)}")
    print(f"unrelated surfaced at all:    {fmt(m.unrelated_surfaced)}")


def cmd_eval(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    tickets = _load_and_report(args, settings)
    if not tickets:
        print("error: no usable tickets in the CSV", file=sys.stderr)
        return 1

    labels = load_labelled_pairs(args.labels)
    metrics = compute_eval_metrics(_score_all(tickets, settings), labels)
    print()
    _print_metrics(metrics)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="duptool",
        description="Offline, read-only duplicate-task suggestion tool. "
        "Suggestions only -- a human decides.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="score a CSV export and write reports")
    run.add_argument("--input", required=True, type=Path, help="CSV export of tasks")
    run.add_argument("--config", default=Path("config.yaml"), type=Path)
    run.add_argument("--out", default=Path("results"), type=Path)
    run.set_defaults(func=cmd_run)

    ev = sub.add_parser("eval", help="print metrics against a labelled pair file")
    ev.add_argument("--input", required=True, type=Path, help="CSV export of tasks")
    ev.add_argument("--labels", required=True, type=Path,
                    help="CSV: ticket_a,ticket_b,relationship (duplicate/related/unrelated)")
    ev.add_argument("--config", default=Path("config.yaml"), type=Path)
    ev.set_defaults(func=cmd_eval)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
