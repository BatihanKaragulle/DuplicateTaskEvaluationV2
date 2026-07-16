"""CSV export -> list[Ticket].

Column names come from config (exports change; the mapping is fixed in
config.yaml, not in code). Every row-level problem is recorded as an
IngestIssue: rows that cannot become a Ticket are skipped AND reported,
weak-but-usable rows (empty title/description) are kept AND flagged.
Nothing is ever dropped silently.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from duptool.models import (
    IngestIssue,
    IngestResult,
    IngestSettings,
    LabelledPair,
    Ticket,
)


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

    missing = [c for c in (cols.id, cols.title, cols.description) if c not in df.columns]
    if missing:
        raise ValueError(
            f"CSV is missing expected column(s) {missing}. "
            f"Found columns: {list(df.columns)}. "
            "Fix the 'ingest.columns' mapping in config.yaml."
        )

    tickets: list[Ticket] = []
    issues: list[IngestIssue] = []
    seen_ids: set[str] = set()

    for pos, row in df.iterrows():
        row_num = pos + 2  # header is row 1, first data row is row 2
        ticket_id = row[cols.id].strip()
        title = row[cols.title].strip()
        description = row[cols.description]

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

        tickets.append(Ticket(id=ticket_id, title=title, description=description))

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
