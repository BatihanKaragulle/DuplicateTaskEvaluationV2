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

from duptool.models import CandidatePair, TicketSignals


def generate_pairs(signals: list[TicketSignals]) -> list[CandidatePair]:
    """All unique unordered pairs, in deterministic input order."""
    return [
        CandidatePair(ticket_a=a.ticket_id, ticket_b=b.ticket_id)
        for a, b in combinations(signals, 2)
    ]
