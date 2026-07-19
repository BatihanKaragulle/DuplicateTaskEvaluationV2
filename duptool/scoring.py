"""Deterministic pair scoring: (TicketSignals, TicketSignals) -> ScoredPair.

Every signal returns (subscore in [0,1], evidence strings) or None when it
cannot judge. None means SKIPPED: the weight drops out and the remaining
weights renormalize -- a ticket is never punished for not filling an
optional field. A 0.0, by contrast, is real negative evidence: both sides
have the signal and it disagrees.

Evidence strings are produced HERE, at scoring time, from the same data the
score came from -- never reconstructed afterwards.

The only corpus-level state is the BM25 lexical index (term rarity needs
the whole ticket list once); everything else is a pure per-pair function.
No LLM, no network, no randomness: same input -> same output, every run.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from duptool.models import (
    Band,
    BandSettings,
    IdOverlapScoring,
    LayerMethodScoring,
    LexicalScoring,
    ScoredPair,
    ScoringSettings,
    SubScore,
    TaskTypeScoring,
    TicketSignals,
)

_SOURCE_TEXT = {
    "template_section": "template section",
    "link": "link",
    "text": "written in text",
    "number_list": "bare number list",
}

_ROUTE_TEXT = {"title_header": "title header", "keywords": "keywords", "none": "unclassified"}


def _containment(a: set, b: set) -> float:
    """|A n B| / min(|A|, |B|): 1.0 when the smaller set is fully contained.
    Chosen over Jaccard so a ticket touching a subset of another's items
    still scores high -- callers guarantee both sets are non-empty."""
    return len(a & b) / min(len(a), len(b))


# --- subscores -----------------------------------------------------------------


def score_id_overlap(
    a: TicketSignals, b: TicketSignals, cfg: IdOverlapScoring
) -> tuple[float, list[str]] | None:
    if not a.refs or not b.refs:
        return None  # a ticket without IDs is not evidence against anything
    refs_a = {r.item_id: r for r in a.refs}
    refs_b = {r.item_id: r for r in b.refs}
    shared = sorted(set(refs_a) & set(refs_b), key=int)
    if not shared:
        return 0.0, [
            f"no shared items (A references {len(refs_a)}, B references {len(refs_b)})"
        ]
    evidence = []
    for item_id in shared:
        ra, rb = refs_a[item_id], refs_b[item_id]
        # either side may know the kind ("TMS-...") while the other saw
        # only an anonymous link -- prefer the labeled form for display
        label = ra.label() if ra.kind != "unknown" else rb.label()
        evidence.append(
            f"both reference {label} "
            f"(A: {_SOURCE_TEXT[ra.source]}, B: {_SOURCE_TEXT[rb.source]})"
        )
    score = cfg.base + (1.0 - cfg.base) * _containment(set(refs_a), set(refs_b))
    return score, evidence


def score_layer_method(
    a: TicketSignals, b: TicketSignals, cfg: LayerMethodScoring
) -> tuple[float, list[str]] | None:
    pools = [
        ("method names", cfg.method_weight, set(a.method_names), set(b.method_names)),
        ("quoted tokens", cfg.quoted_weight, set(a.quoted_tokens), set(b.quoted_tokens)),
        ("BDD steps", cfg.bdd_weight, set(a.bdd_steps), set(b.bdd_steps)),
    ]
    # only pools populated on BOTH sides can judge; if none can, the whole
    # signal is neutral (the layer/method field is optional and often empty)
    usable = [(name, w, sa, sb) for name, w, sa, sb in pools if sa and sb]
    if not usable:
        return None
    best = 0.0
    evidence: list[str] = []
    for name, weight, sa, sb in usable:
        shared = sorted(sa & sb)
        if shared:
            evidence.append(f"shared {name}: {', '.join(shared[:5])}")
            best = max(best, weight * _containment(sa, sb))
    if not evidence:
        return 0.0, ["no overlap in method names / quoted tokens / BDD steps"]
    return min(best, 1.0), evidence


def score_task_type(
    a: TicketSignals, b: TicketSignals, cfg: TaskTypeScoring
) -> tuple[float, list[str]] | None:
    if a.task_type == "unknown" or b.task_type == "unknown":
        return None  # never guess: an unclassified ticket is neutral
    route_a = _ROUTE_TEXT[a.task_type_source]
    route_b = _ROUTE_TEXT[b.task_type_source]
    if a.task_type == b.task_type:
        return cfg.same_type, [
            f"both are {a.task_type} tasks (A: {route_a}, B: {route_b})"
        ]
    # soft context, never a separator: cross-type work can still be the
    # same underlying task (owner rule, see CLAUDE.md)
    return cfg.cross_type, [
        f"cross-type: {a.task_type} (A: {route_a}) vs {b.task_type} (B: {route_b})"
    ]


def score_steps(a: TicketSignals, b: TicketSignals) -> tuple[float, list[str]] | None:
    sa, sb = set(a.step_numbers), set(b.step_numbers)
    if not sa or not sb:
        return None  # step mentions are optional -- absence is neutral
    shared = sorted(sa & sb, key=int)
    if not shared:
        return 0.0, ["no shared step numbers"]
    return _containment(sa, sb), [f"both mention step(s) {', '.join(shared)}"]


# --- lexical similarity (hand-rolled Okapi BM25) -----------------------------------

# words only: pure-digit tokens are item numbers, which already carry the
# dominant id_overlap signal and must not inflate text similarity too
_WORD = re.compile(r"\b[a-z_][a-z0-9_]*\b")


def _tokenize(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if len(t) >= 2]


class LexicalIndex:
    """Okapi BM25 over title + body text of all tickets.

    Hand-rolled on purpose: the formula is ~20 lines, which beats adding a
    dependency (rank-bm25) for it. Built once per run so term rarity is
    corpus-aware. Pair similarity is made symmetric and bounded by
    self-score normalization:

        sim(A,B) = (bm25(A->B) + bm25(B->A)) / (bm25(A->A) + bm25(B->B))

    clamped to [0,1]. Deterministic for a fixed ticket list.
    """

    def __init__(self, all_signals: list[TicketSignals], cfg: LexicalScoring) -> None:
        self._k1, self._b = cfg.k1, cfg.b
        self._min_tokens = cfg.min_tokens
        self._tokens: dict[str, list[str]] = {
            s.ticket_id: _tokenize(s.clean_title + "\n" + s.body_text)
            for s in all_signals
        }
        lengths = [len(t) for t in self._tokens.values()]
        self._avgdl = (sum(lengths) / len(lengths)) if lengths else 1.0
        if self._avgdl == 0:
            self._avgdl = 1.0
        n_docs = max(len(self._tokens), 1)
        df: dict[str, int] = {}
        for tokens in self._tokens.values():
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1
        self._idf = {
            term: math.log((n_docs - d + 0.5) / (d + 0.5) + 1.0)
            for term, d in df.items()
        }

    def _bm25(self, query: list[str], doc: list[str]) -> float:
        tf: dict[str, int] = {}
        for term in doc:
            tf[term] = tf.get(term, 0) + 1
        norm = self._k1 * (1.0 - self._b + self._b * len(doc) / self._avgdl)
        total = 0.0
        for term in set(query):
            f = tf.get(term, 0)
            if f:
                total += self._idf.get(term, 0.0) * f * (self._k1 + 1.0) / (f + norm)
        return total

    def similarity(self, id_a: str, id_b: str) -> tuple[float, list[str]] | None:
        ta = self._tokens.get(id_a, [])
        tb = self._tokens.get(id_b, [])
        # too little text cannot judge -- neutral, not negative. Blank
        # tickets and bare template skeletons would otherwise "match"
        # each other at 1.00 (owner feedback, 2026-07-19).
        if len(ta) < self._min_tokens or len(tb) < self._min_tokens:
            return None
        self_score = self._bm25(ta, ta) + self._bm25(tb, tb)
        if self_score == 0.0:
            return None
        sim = (self._bm25(ta, tb) + self._bm25(tb, ta)) / self_score
        sim = max(0.0, min(1.0, sim))
        # rarest shared words are the ones a human would point at; the
        # alphabetical tie-break keeps evidence deterministic across runs
        # (set order would vary with hash randomization)
        shared = sorted(set(ta) & set(tb), key=lambda t: (-self._idf.get(t, 0.0), t))
        note = f"text similarity {sim:.2f}"
        if shared:
            note += " (top shared words: " + ", ".join(shared[:4]) + ")"
        evidence = [note]
        if Counter(ta) == Counter(tb):
            # a perfect text match usually means a copy-pasted or unfilled
            # template, not the same work -- say so instead of implying strength
            evidence.append(
                "warning: texts are identical after cleaning - possibly a "
                "copy-pasted or blank template; structural signals must confirm"
            )
        return sim, evidence


# --- final score, hard override, banding ---------------------------------------------

# The DEFINITION of "structural evidence" -- the signals grounded in
# extracted structure rather than raw text. This is a semantic boundary,
# not a tunable, so it lives here and not in config (the config flag
# require_structural_evidence turns the gate on/off).
_STRUCTURAL_SIGNALS = ("id_overlap", "layer_method", "steps")


def band_for(score: float, bands: BandSettings) -> Band:
    if score >= bands.possible_duplicate:
        return "possible_duplicate"
    if score >= bands.possibly_related:
        return "possibly_related"
    return "not_shown"


def score_pair(
    a: TicketSignals,
    b: TicketSignals,
    settings: ScoringSettings,
    lexical: LexicalIndex,
) -> ScoredPair:
    w = settings.weights
    results: list[tuple[str, float, tuple[float, list[str]] | None]] = [
        ("id_overlap", w.id_overlap, score_id_overlap(a, b, settings.id_overlap)),
        ("layer_method", w.layer_method, score_layer_method(a, b, settings.layer_method)),
        ("task_type", w.task_type, score_task_type(a, b, settings.task_type)),
        ("steps", w.steps, score_steps(a, b)),
        ("lexical", w.lexical, lexical.similarity(a.ticket_id, b.ticket_id)),
    ]

    active_weight = sum(weight for _, weight, r in results if r is not None)
    final = 0.0
    subscores: list[SubScore] = []
    for name, weight, result in results:
        if result is None:
            subscores.append(SubScore(
                name=name, score=None, weight=weight,
                evidence=["skipped: not enough data on one or both sides"],
            ))
            continue
        score, evidence = result
        if active_weight > 0:
            final += weight * score / active_weight
        subscores.append(SubScore(name=name, score=score, weight=weight, evidence=evidence))

    # Hard rule (CLAUDE.md): a pair sharing at least one item is ALWAYS
    # surfaced, whatever the blended score says.
    shared_items = {r.item_id for r in a.refs} & {r.item_id for r in b.refs}
    hard_override = bool(shared_items)
    band = band_for(final, settings.bands)
    if hard_override and band == "not_shown":
        band = "possibly_related"
        subscores[0].evidence.append(
            "hard rule: pairs sharing an item are always surfaced"
        )

    # Structural gate (owner rule, 2026-07-19): text similarity and task
    # type alone must never surface a pair -- blank or copy-pasted template
    # tickets match each other perfectly without being related. At least
    # one structural signal has to agree. The hard rule above is itself
    # structural, so it is naturally exempt.
    if (
        settings.require_structural_evidence
        and band != "not_shown"
        and not hard_override
        and not any(
            result is not None and result[0] > 0.0
            for name, _, result in results
            if name in _STRUCTURAL_SIGNALS
        )
    ):
        band = "not_shown"
        subscores[-1].evidence.append(
            "hidden: text/type similarity alone cannot surface a pair "
            "(no shared items, identifiers, or steps)"
        )

    return ScoredPair(
        ticket_a=a.ticket_id,
        ticket_b=b.ticket_id,
        final_score=round(final, 4),
        band=band,
        hard_override=hard_override,
        subscores=subscores,
    )
