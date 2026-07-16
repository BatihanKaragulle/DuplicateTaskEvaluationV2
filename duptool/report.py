"""Report output: scored pairs -> ranked files + per-ticket summaries.

Three artifacts in the output directory, all built from the SAME shown
pairs (band != not_shown), ranked by score:

- pairs.csv     one row per shown pair: scores per signal, band, evidence
- pairs.json    full ScoredPair detail (subscores, weights, evidence)
- summary.txt   human-readable "Top candidates for <ticket>" sections

Confidentiality: reports carry ticket IDs and titles only -- never
descriptions or bodies. Wording is always "possible duplicate" /
"possibly related"; suggestions, not verdicts.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from duptool.models import ReportSettings, ScoredPair, Ticket

_SIGNAL_ORDER = ["id_overlap", "layer_method", "task_type", "steps", "lexical"]

_BAND_TEXT = {
    "possible_duplicate": "possible duplicate",
    "possibly_related": "possibly related",
}


def shown_pairs(scored: list[ScoredPair]) -> list[ScoredPair]:
    """Pairs worth showing, ranked. Deterministic tie-break by ticket IDs."""
    kept = [p for p in scored if p.band != "not_shown"]
    return sorted(kept, key=lambda p: (-p.final_score, p.ticket_a, p.ticket_b))


def write_reports(
    scored: list[ScoredPair],
    tickets: list[Ticket],
    settings: ReportSettings,
    out_dir: str | Path,
) -> list[Path]:
    """Write all three artifacts; returns the written paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ranked = shown_pairs(scored)
    titles = {t.id: t.title for t in tickets}

    paths = [
        _write_csv(ranked, out / "pairs.csv"),
        _write_json(ranked, out / "pairs.json"),
        _write_summary(ranked, titles, settings.top_k_per_ticket, out / "summary.txt"),
    ]
    return paths


def _evidence_lines(pair: ScoredPair) -> list[str]:
    lines: list[str] = []
    for sub in pair.subscores:
        if sub.score is not None:
            lines.extend(sub.evidence)
    return lines


def _write_csv(ranked: list[ScoredPair], path: Path) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["ticket_a", "ticket_b", "score", "band", "hard_override"]
            + _SIGNAL_ORDER
            + ["evidence"]
        )
        for p in ranked:
            by_name = {s.name: s.score for s in p.subscores}
            writer.writerow(
                [p.ticket_a, p.ticket_b, f"{p.final_score:.4f}", p.band, p.hard_override]
                + [
                    "" if by_name.get(name) is None else f"{by_name[name]:.4f}"
                    for name in _SIGNAL_ORDER
                ]
                + [" | ".join(_evidence_lines(p))]
            )
    return path


def _write_json(ranked: list[ScoredPair], path: Path) -> Path:
    payload = [p.model_dump() for p in ranked]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _write_summary(
    ranked: list[ScoredPair],
    titles: dict[str, str],
    top_k: int,
    path: Path,
) -> Path:
    # each shown pair appears under BOTH tickets, capped at top_k per ticket
    per_ticket: dict[str, list[tuple[str, ScoredPair]]] = {}
    for pair in ranked:  # already ranked -> per-ticket lists stay ranked
        per_ticket.setdefault(pair.ticket_a, []).append((pair.ticket_b, pair))
        per_ticket.setdefault(pair.ticket_b, []).append((pair.ticket_a, pair))

    lines: list[str] = []
    if not per_ticket:
        lines.append("No candidate pairs above the configured thresholds.")
    for ticket_id in sorted(per_ticket):
        title = titles.get(ticket_id, "")
        lines.append(f"Top candidates for {ticket_id}" + (f" ({title})" if title else "") + ":")
        for rank, (other_id, pair) in enumerate(per_ticket[ticket_id][:top_k], start=1):
            other_title = titles.get(other_id, "")
            band = _BAND_TEXT[pair.band]
            lines.append(
                f"  {rank}. {other_id}" + (f" ({other_title})" if other_title else "")
                + f" -- {band}, score {pair.final_score:.2f}"
            )
            for evidence in _evidence_lines(pair):
                lines.append(f"       - {evidence}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path
