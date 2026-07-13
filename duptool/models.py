"""Typed data models at every module boundary, plus config loading.

Models for later pipeline stages (TicketSignals, CandidatePair, ScoredPair)
are added in the development step that introduces them, so each shape is
reviewed next to the code that fills it — nothing speculative lives here.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


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


class Settings(_StrictModel):
    config_version: int
    ingest: IngestSettings


def load_settings(path: str | Path) -> Settings:
    """Read the YAML config into a typed, validated Settings object."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Settings.model_validate(raw)
