"""CSV export -> list[Ticket].

Column names come from config (exports change; the mapping is fixed in
config.yaml, not in code). Every row-level problem is recorded as an
IngestIssue: rows that cannot become a Ticket are skipped AND reported,
weak-but-usable rows (empty title/description) are kept AND flagged.
Nothing is ever dropped silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from duptool.models import (
    IngestIssue,
    IngestResult,
    IngestSettings,
    LabelledPair,
    Ticket,
)


def _as_list(spec: str | list[str]) -> list[str]:
    return [spec] if isinstance(spec, str) else list(spec)


def load_tickets(csv_path: str | Path, settings: IngestSettings) -> IngestResult:
    cols = settings.columns
    try:
        # dtype=str: ticket IDs like "00123" must stay strings, not become 123.
        # keep_default_na=False: empty cells become "" instead of NaN, so all
        # downstream code deals in plain strings only.
        df = pd.read_csv(
            csv_path,
            dtype=str,
            keep_default_na=False,
            encoding=settings.encoding,
        )
    except pd.errors.EmptyDataError:
        raise ValueError(f"CSV file is empty: {csv_path}") from None

    title_cols = _as_list(cols.title)
    desc_cols = _as_list(cols.description)
    wanted = [cols.id] + title_cols + desc_cols
    wanted += [c for c in (cols.state, cols.links) if c]
    missing = [c for c in wanted if c not in df.columns]
    if missing:
        raise ValueError(
            f"CSV is missing expected column(s) {missing}. "
            f"Found columns: {list(df.columns)}. "
            "Fix the 'ingest.columns' mapping in config.yaml."
        )

    excluded_states = {s.lower() for s in settings.exclude_states}
    derive_hierarchy = settings.hierarchy_from_title_columns and len(title_cols) > 1
    tickets: list[Ticket] = []
    issues: list[IngestIssue] = []
    seen_ids: set[str] = set()
    current_parent: str | None = None

    for pos, row in df.iterrows():
        row_num = pos + 2  # header is row 1, first data row is row 2
        ticket_id = row[cols.id].strip()
        # ADO tree exports fill exactly one of the title columns; WHICH one
        # encodes the hierarchy level (first column = parent row)
        title, title_level = "", 0
        for level, column in enumerate(title_cols):
            if row[column].strip():
                title, title_level = row[column].strip(), level
                break
        # every text column carries extraction signal (template sections in
        # Description, BDD steps often in Repro Steps) -> concatenate
        description = "\n\n".join(row[c] for c in desc_cols if row[c].strip())

        if not ticket_id:
            issues.append(IngestIssue(
                row=row_num, problem="empty ID - row skipped", skipped=True,
            ))
            continue
        if ticket_id in seen_ids:
            issues.append(IngestIssue(
                row=row_num, ticket_id=ticket_id,
                problem="duplicate ID - first occurrence kept, this row skipped",
                skipped=True,
            ))
            continue
        seen_ids.add(ticket_id)

        if cols.state and excluded_states:
            state = row[cols.state].strip()
            if state.lower() in excluded_states:
                issues.append(IngestIssue(
                    row=row_num, ticket_id=ticket_id,
                    problem=f"state '{state}' excluded by config - row skipped",
                    skipped=True,
                ))
                continue

        if not title:
            issues.append(IngestIssue(
                row=row_num, ticket_id=ticket_id,
                problem="empty title - ticket kept", skipped=False,
            ))
        if not description.strip():
            issues.append(IngestIssue(
                row=row_num, ticket_id=ticket_id,
                problem="empty description - ticket kept, will rely on title/IDs only",
                skipped=False,
            ))

        # hierarchy: a level-1 row opens a parent; deeper rows below it are
        # its children (parent-child pairs get suppressed, not suggested)
        parent_id: str | None = None
        if derive_hierarchy:
            if title_level == 0:
                current_parent = ticket_id
            else:
                parent_id = current_parent

        linked_ids: list[str] = []
        if cols.links:
            # tolerant of unknown cell formats: harvest ID-looking tokens
            # ("1234567; 2345678", "TASK-101, TASK-102", pasted URLs). Only
            # tokens matching another loaded ticket's ID ever matter.
            cell = row[cols.links]
            linked_ids = sorted(set(re.findall(r"[A-Za-z]+-\d+|\d+", cell)))

        tickets.append(Ticket(
            id=ticket_id, title=title, description=description,
            parent_id=parent_id, linked_ids=linked_ids,
        ))

    return IngestResult(tickets=tickets, issues=issues)


def load_labelled_pairs(csv_path: str | Path) -> list[LabelledPair]:
    """Read the labelled pair file (ticket_a,ticket_b,relationship).

    Strict on purpose: a bad label or duplicated pair RAISES instead of
    being skipped -- these labels referee all tuning, and silently skewed
    metrics are worse than a crash.
    """
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    missing = [c for c in ("ticket_a", "ticket_b", "relationship") if c not in df.columns]
    if missing:
        raise ValueError(
            f"labels CSV is missing column(s) {missing}. "
            f"Found columns: {list(df.columns)}."
        )

    pairs: list[LabelledPair] = []
    seen: set[frozenset[str]] = set()
    for pos, row in df.iterrows():
        row_num = pos + 2
        a, b = row["ticket_a"].strip(), row["ticket_b"].strip()
        rel = row["relationship"].strip().lower()
        if not a or not b or a == b:
            raise ValueError(f"labels row {row_num}: needs two different ticket IDs")
        if rel not in ("duplicate", "related", "unrelated"):
            raise ValueError(
                f"labels row {row_num}: relationship {rel!r} is not one of "
                "duplicate/related/unrelated"
            )
        key = frozenset((a, b))
        if key in seen:
            raise ValueError(f"labels row {row_num}: pair {a},{b} appears more than once")
        seen.add(key)
        pairs.append(LabelledPair(ticket_a=a, ticket_b=b, relationship=rel))
    return pairs
