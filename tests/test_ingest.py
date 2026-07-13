"""Tests for duptool.ingest and config loading.

Core rule under test: validate, report problem rows, never silently drop data.
(test_ingest.py is an addition to the CLAUDE.md test layout - ingest rules
deserve the same documented-by-tests treatment as cleaning and signals.)
"""

from pathlib import Path

import pytest

from duptool.ingest import load_tickets
from duptool.models import ColumnMapping, IngestSettings, load_settings

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parents[1]

SETTINGS = IngestSettings(
    columns=ColumnMapping(id="ID", title="Title", description="Description"),
)


def write_csv(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "export.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_config_yaml_is_valid():
    # The checked-in config must always load into the typed Settings object.
    settings = load_settings(REPO_ROOT / "config.yaml")
    assert settings.ingest.columns.id == "ID"


def test_config_with_unknown_key_fails_loudly(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text(
        "config_version: 1\n"
        "ingest:\n"
        "  encodings: utf-8\n"  # typo: 'encodings' instead of 'encoding'
        "  columns: {id: ID, title: Title, description: Description}\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        load_settings(bad)


def test_happy_path_reads_all_fixture_rows():
    result = load_tickets(FIXTURES / "sample_tickets.csv", SETTINGS)
    assert [t.id for t in result.tickets] == ["TASK-101", "TASK-102", "TASK-103"]
    # TASK-103 has an empty description: kept, but flagged.
    flagged = [i for i in result.issues if i.ticket_id == "TASK-103"]
    assert len(flagged) == 1 and not flagged[0].skipped


def test_multiline_quoted_description_survives():
    result = load_tickets(FIXTURES / "sample_tickets.csv", SETTINGS)
    task_102 = next(t for t in result.tickets if t.id == "TASK-102")
    assert "left menu moved" in task_102.description
    assert "TRQ-1042" in task_102.description  # text after the embedded newline


def test_description_html_is_kept_raw_at_ingest():
    # Cleaning happens later in the pipeline; ingest must not touch content.
    result = load_tickets(FIXTURES / "sample_tickets.csv", SETTINGS)
    task_101 = next(t for t in result.tickets if t.id == "TASK-101")
    assert "<div>" in task_101.description


def test_missing_column_fails_loudly_and_names_the_columns(tmp_path):
    path = write_csv(tmp_path, "Id,Name\n1,x\n")
    with pytest.raises(ValueError) as err:
        load_tickets(path, SETTINGS)
    assert "ID" in str(err.value) and "config.yaml" in str(err.value)


def test_empty_id_row_is_skipped_and_reported(tmp_path):
    path = write_csv(tmp_path, "ID,Title,Description\n,orphan row,text\nT-2,ok,text\n")
    result = load_tickets(path, SETTINGS)
    assert [t.id for t in result.tickets] == ["T-2"]
    skipped = [i for i in result.issues if i.skipped]
    assert len(skipped) == 1 and skipped[0].row == 2


def test_duplicate_id_keeps_first_and_reports_second(tmp_path):
    path = write_csv(
        tmp_path,
        "ID,Title,Description\nT-1,first,alpha\nT-1,second,beta\n",
    )
    result = load_tickets(path, SETTINGS)
    assert len(result.tickets) == 1
    assert result.tickets[0].title == "first"
    dup = [i for i in result.issues if i.skipped]
    assert len(dup) == 1 and dup[0].ticket_id == "T-1" and dup[0].row == 3


def test_ids_stay_strings_and_keep_leading_zeros(tmp_path):
    path = write_csv(tmp_path, "ID,Title,Description\n00123,padded,text\n")
    result = load_tickets(path, SETTINGS)
    assert result.tickets[0].id == "00123"


def test_empty_csv_fails_with_clear_message(tmp_path):
    path = write_csv(tmp_path, "")
    with pytest.raises(ValueError) as err:
        load_tickets(path, SETTINGS)
    assert "empty" in str(err.value).lower()


def test_utf8_bom_is_handled(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text("ID,Title,Description\nT-1,a,b\n", encoding="utf-8-sig")
    result = load_tickets(path, SETTINGS)
    assert result.tickets[0].id == "T-1"
