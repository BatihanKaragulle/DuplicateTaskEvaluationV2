"""Candidate pair generation: list[TicketSignals] -> list[CandidatePair].

MVP: every unique pair (full pairwise is fine at current dataset sizes).
The module boundary exists so a blocking strategy can replace the inside
without touching scoring, when ticket counts demand it. Any future blocking
MUST preserve the hard rule: pairs sharing at least one item are always
candidates (see CLAUDE.md); same-type buckets and a cheap lexical prefilter
would only thin the remainder.
"""

from __future__ import annotations

from itertools import combinations

from duptool.models import CandidatePair, CandidateSettings, Ticket, TicketSignals


def generate_pairs(signals: list[TicketSignals]) -> list[CandidatePair]:
    """All unique unordered pairs, in deterministic input order."""
    return [
        CandidatePair(ticket_a=a.ticket_id, ticket_b=b.ticket_id)
        for a, b in combinations(signals, 2)
    ]


def suppress_known_pairs(
    pairs: list[CandidatePair],
    tickets: list[Ticket],
    settings: CandidateSettings,
) -> tuple[list[CandidatePair], dict[str, int]]:
    """Drop pairs the tester already knows about; return (kept, counts).

    A User Story and its child task often share near-identical titles --
    they are related by STRUCTURE, not duplicates (owner, 2026-07-19), and
    suggesting them is pure noise. Same for pairs already linked in ADO
    (Related Work). Counts are reported on the console, never silent.
    """
    by_id = {t.id: t for t in tickets}
    kept: list[CandidatePair] = []
    counts = {"parent_child": 0, "already_linked": 0}
    for pair in pairs:
        a, b = by_id[pair.ticket_a], by_id[pair.ticket_b]
        if settings.suppress_parent_child and (
            a.parent_id == b.id or b.parent_id == a.id
        ):
            counts["parent_child"] += 1
            continue
        if settings.suppress_already_linked and (
            b.id in a.linked_ids or a.id in b.linked_ids
        ):
            counts["already_linked"] += 1
            continue
        kept.append(pair)
    return kept, counts
