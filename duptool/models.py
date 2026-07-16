"""Typed data models at every module boundary, plus config loading.

Models for later pipeline stages (TicketSignals, CandidatePair, ScoredPair)
are added in the development step that introduces them, so each shape is
reviewed next to the code that fills it — nothing speculative lives here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class Ticket(BaseModel):
    """One task from the ADO CSV export, kept as exported.

    The description may contain HTML — it is stored raw here and cleaned
    later, so extraction bugs can always be traced back to the original text.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    description: str


class IngestIssue(BaseModel):
    """A problem found while reading the CSV.

    Issues are always reported, never hidden: the owner must be able to see
    exactly which rows were skipped and which tickets are weak (e.g. empty
    description) and will lean on IDs only.
    """

    row: int  # 1-based line number in the CSV file (header is row 1)
    ticket_id: str | None = None
    problem: str
    skipped: bool  # True = the row did not become a Ticket


class IngestResult(BaseModel):
    tickets: list[Ticket]
    issues: list[IngestIssue]


# --- Signals (step 2) --------------------------------------------------------

TaskType = Literal["implementation", "refactor", "investigation", "unknown"]

# How the type was decided. A "[Refactor] ..." title header is explicit and
# strongest; keywords are a guess; "none" means nothing matched. Task types
# are soft context either way -- scoring must never use them to exclude a
# pair (an Investigation ticket can duplicate a Refactor ticket).
TaskTypeSource = Literal["title_header", "keywords", "none"]

# Where a reference was found, in decreasing order of trust. IDs inside an
# "Affected TMS IDs" template section are the most deliberate; a pasted URL
# is next; a labeled number in prose follows; an unlabeled run of numbers
# ("days: 1325121 4353414 ...") is the weakest.
RefSource = Literal["template_section", "link", "text", "number_list"]


class IdRef(BaseModel):
    """One tracker item reference.

    The tracker assigns one global number per item (TMS and TRQ share the
    id space -- links all look like .../cb/issue/{number}), so item_id IS
    the identity: 'TMS-142415' in one ticket and a cb/issue/142415 link in
    another refer to the same item and must compare equal. 'kind' is
    metadata for evidence text and the type signal, never for matching;
    "unknown" means the ticket never said which kind it is.
    """

    model_config = ConfigDict(frozen=True)

    item_id: str   # the number -- the identity used for overlap/matching
    kind: str      # config kind key ("tms", "trq") or "unknown"
    source: RefSource

    def label(self) -> str:
        """Human-readable form for reports, e.g. 'TMS-142415' / 'ITEM-1231211'."""
        prefix = self.kind.upper() if self.kind != "unknown" else "ITEM"
        return f"{prefix}-{self.item_id}"


class TicketSignals(BaseModel):
    """Everything extraction learned about one ticket.

    Downstream code must not know or care whether the template-aware or the
    generic extractor produced a value -- 'extractors_used' records it for
    debugging and eval only.
    """

    ticket_id: str
    task_type: TaskType
    task_type_source: TaskTypeSource
    # every tracker item this ticket references, sorted by item_id
    refs: list[IdRef]
    # from BDD/Gherkin lines: quoted tokens ("LEFT_MENU") are the strongest
    # comparable units; bdd_steps are the normalized step texts
    quoted_tokens: list[str]
    bdd_steps: list[str]
    # bare snake_case identifiers, e.g. expiration_date_get
    method_names: list[str]
    # mentioned test step numbers, e.g. ["3", "12"]
    step_numbers: list[str]
    # recorded, NEVER fetched (extension points: link resolver / screenshots)
    other_urls: list[str]
    image_refs: list[str]
    # cleaned text for lexical scoring; body_text is the "What needs to be
    # done" section when the template is present, else the full description
    clean_title: str
    clean_description: str
    body_text: str
    extractors_used: list[str]  # ["template", "generic"] or ["generic"]


# --- Candidates (step 4) ------------------------------------------------------


class CandidatePair(BaseModel):
    """An unordered ticket pair selected for scoring. MVP selects all pairs;
    blocking strategies would thin this list without touching scoring."""

    model_config = ConfigDict(frozen=True)

    ticket_a: str
    ticket_b: str


# --- Scoring (step 3) --------------------------------------------------------

Band = Literal["possible_duplicate", "possibly_related", "not_shown"]


class SubScore(BaseModel):
    """One signal's contribution to a pair's score, with its evidence.

    score=None means the signal was SKIPPED (not enough data on one or both
    sides): its weight drops out and the remaining weights renormalize. A
    missing optional field must never punish a pair -- only a 0.0 (both
    sides have the signal and it disagrees) is negative evidence.
    """

    name: str
    score: float | None
    weight: float  # configured weight, before renormalization
    evidence: list[str]


class ScoredPair(BaseModel):
    ticket_a: str
    ticket_b: str
    final_score: float
    band: Band
    # True when the shared-item hard rule forced this pair to be surfaced
    # even though the blended score alone would have hidden it
    hard_override: bool
    subscores: list[SubScore]


# --- Settings ---------------------------------------------------------------
# One section per pipeline stage; grows as stages are built. All tunables
# enter the program through these models — no magic numbers in code.


class _StrictModel(BaseModel):
    # extra="forbid": a misspelled key in config.yaml must fail loudly at
    # load time, not be silently ignored while the default takes effect.
    model_config = ConfigDict(extra="forbid")


class ColumnMapping(_StrictModel):
    id: str
    title: str
    description: str


class IngestSettings(_StrictModel):
    encoding: str = "utf-8-sig"  # ADO exports usually carry a UTF-8 BOM
    columns: ColumnMapping


class IdKindSettings(_StrictModel):
    """Patterns and labels for one ID kind (tms, trq, ...).

    Every regex must have exactly one capture group: the number. New
    real-world format variants are added HERE, never hardcoded. The kind
    name "unknown" is reserved for refs whose kind was never stated.
    """

    text_patterns: list[str]    # labeled mentions in prose ("TMS-48213")
    url_patterns: list[str]     # URLs that name the kind ("...tms.../48213")
    section_labels: list[str]   # lines that open this kind's ID context
                                # ("Affected TMS IDs", "TMS:", "TMS Action summary")


class IdSettings(_StrictModel):
    kinds: dict[str, IdKindSettings]
    # URL shape shared by ALL kinds (tracker links look identical for
    # TMS/TRQ/bugs). Kind comes from the surrounding section, else "unknown".
    shared_url_patterns: list[str]
    # bare numbers harvested inside an ID section (number-leading/table lines)
    section_number_pattern: str
    # unlabeled runs anywhere: a line with >= min_count standalone matches
    unlabeled_run_pattern: str
    unlabeled_run_min_count: int
    # a section line starting with one of these words disowns its refs to
    # kind "unknown" (e.g. "bug: <link>" inside the TRQs section)
    neutral_line_labels: list[str]


class TemplateLabels(_StrictModel):
    """Section label phrases (and aliases) of the ticket template.

    Matching is case-insensitive and tolerant of punctuation/markdown around
    the words -- parsing anchors on these phrases, never on formatting.
    (ID section labels live per-kind in signals.ids.kinds.<kind>.)
    """

    what_needs_to_be_done: list[str]
    affected_layer_method: list[str]


class TaskTypeSettings(_StrictModel):
    """Task-type classification, two routes in priority order.

    title_headers: explicit header at the START of the title
    ("[Refactor] update login") -- strongest signal when present.
    keywords: lowercase substring vote on title + body -- fallback; only an
    unambiguous vote (exactly one type hits) classifies.
    """

    title_headers: dict[str, list[str]]  # task type -> header aliases
    keywords: dict[str, list[str]]       # task type -> keyword list

    @field_validator("title_headers", "keywords")
    @classmethod
    def _only_known_task_types(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        allowed = {"implementation", "refactor", "investigation"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(
                f"unknown task type(s) {sorted(unknown)}; allowed: {sorted(allowed)}"
            )
        return value


class SignalsSettings(_StrictModel):
    ids: IdSettings
    template_labels: TemplateLabels
    task_type: TaskTypeSettings
    step_patterns: list[str]  # capture group = the step number


class WeightSettings(_StrictModel):
    """Relative signal weights. They need not sum to 1: the final score is
    always renormalized over the signals that actually fired for a pair."""

    id_overlap: float
    layer_method: float
    task_type: float
    steps: float
    lexical: float


class IdOverlapScoring(_StrictModel):
    # any shared item starts at `base`; the rest scales with the overlap
    # ratio, so identical ref sets reach 1.0
    base: float


class TaskTypeScoring(_StrictModel):
    same_type: float
    cross_type: float  # types are soft context: cross-type is partial, never 0


class LayerMethodScoring(_StrictModel):
    # per-pool credit: exact identifier match > quoted-token > step text
    method_weight: float
    quoted_weight: float
    bdd_weight: float


class LexicalScoring(_StrictModel):
    k1: float  # BM25 term-frequency saturation
    b: float   # BM25 length normalization


class BandSettings(_StrictModel):
    possible_duplicate: float
    possibly_related: float

    @model_validator(mode="after")
    def _bands_ordered(self) -> "BandSettings":
        if self.possible_duplicate < self.possibly_related:
            raise ValueError("possible_duplicate threshold must be >= possibly_related")
        return self


class ScoringSettings(_StrictModel):
    weights: WeightSettings
    id_overlap: IdOverlapScoring
    task_type: TaskTypeScoring
    layer_method: LayerMethodScoring
    lexical: LexicalScoring
    bands: BandSettings


# --- Evaluation (step 5) -------------------------------------------------------

Relationship = Literal["duplicate", "related", "unrelated"]


class LabelledPair(BaseModel):
    """One human-labelled ticket pair -- the ground truth for eval.

    The labelled set referees ALL weight/threshold tuning: thresholds are
    never adjusted by feel, only against these labels.
    """

    model_config = ConfigDict(frozen=True)

    ticket_a: str
    ticket_b: str
    relationship: Relationship


class EvalMetrics(BaseModel):
    """duptool eval results. Ratio fields are None when their denominator
    is empty (no data is not the same as a perfect or zero score)."""

    total_labelled: int
    used: int
    skipped_missing_ticket: int
    # predicted band -> true label -> count
    matrix: dict[str, dict[str, int]]
    duplicate_precision: float | None   # PRIMARY: precision of possible_duplicate
    duplicate_recall: float | None
    top3_accuracy: float | None
    unrelated_flagged_duplicate: float | None
    unrelated_surfaced: float | None


class ReportSettings(_StrictModel):
    # per-ticket summary shows at most this many candidates (precision over
    # recall: few strong suggestions beat many weak ones)
    top_k_per_ticket: int


class Settings(_StrictModel):
    config_version: int
    ingest: IngestSettings
    signals: SignalsSettings
    scoring: ScoringSettings
    report: ReportSettings


def load_settings(path: str | Path) -> Settings:
    """Read the YAML config into a typed, validated Settings object."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Settings.model_validate(raw)
