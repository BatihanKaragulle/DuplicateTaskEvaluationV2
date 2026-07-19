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

# simple single-column mapping for the tmp-CSV edge-case tests
SETTINGS = IngestSettings(
    columns=ColumnMapping(id="ID", title="Title", description="Description"),
)
# the fixture CSV mirrors the REAL export shape and is read with the
# repo config, so config and fixture can never drift apart
FIXTURE_SETTINGS = load_settings(REPO_ROOT / "config.yaml").ingest


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
    result = load_tickets(FIXTURES / "sample_tickets.csv", FIXTURE_SETTINGS)
    assert [t.id for t in result.tickets] == ["TASK-101", "TASK-102", "TASK-103", "TASK-104"]
    # TASK-103 has no text in any description column: kept, but flagged.
    flagged = [i for i in result.issues if i.ticket_id == "TASK-103"]
    assert len(flagged) == 1 and not flagged[0].skipped


def test_title_taken_from_first_nonempty_title_column():
    # TASK-101 fills "Title1", TASK-104 fills "Title 2" (ADO tree export)
    result = load_tickets(FIXTURES / "sample_tickets.csv", FIXTURE_SETTINGS)
    by_id = {t.id: t for t in result.tickets}
    assert by_id["TASK-101"].title == "Add expiry date checks to checkout tests"
    assert by_id["TASK-104"].title == "Smoke test placeholder"


def test_child_rows_get_the_preceding_level1_row_as_parent():
    # TASK-104 (Title 2 filled) sits below TASK-103 (Title1 filled): a User
    # Story and its child task -- near-identical titles, NOT duplicates.
    result = load_tickets(FIXTURES / "sample_tickets.csv", FIXTURE_SETTINGS)
    by_id = {t.id: t for t in result.tickets}
    assert by_id["TASK-104"].parent_id == "TASK-103"
    assert by_id["TASK-103"].parent_id is None
    assert by_id["TASK-101"].parent_id is None


def test_hierarchy_derivation_can_be_disabled():
    settings = FIXTURE_SETTINGS.model_copy(update={"hierarchy_from_title_columns": False})
    result = load_tickets(FIXTURES / "sample_tickets.csv", settings)
    assert all(t.parent_id is None for t in result.tickets)


def test_links_column_is_parsed_into_linked_ids(tmp_path):
    path = write_csv(
        tmp_path,
        "ID,Title 1,Title 2,State,Repro Steps,Description,Related Item\n"
        'T-1,a,,Active,,x,"T-2; 1234567"\n'
        "T-2,b,,Active,,y,\n",
    )
    result = load_tickets(path, FIXTURE_SETTINGS)
    assert result.tickets[0].linked_ids == ["1234567", "T-2"]
    assert result.tickets[1].linked_ids == []


def test_repro_steps_column_feeds_the_description():
    # TASK-102's text lives in "Repro Steps", not "Description"
    result = load_tickets(FIXTURES / "sample_tickets.csv", FIXTURE_SETTINGS)
    task_102 = next(t for t in result.tickets if t.id == "TASK-102")
    assert "left menu moved" in task_102.description
    assert "TRQ-1042" in task_102.description  # text after the embedded newline


def test_all_text_columns_are_concatenated(tmp_path):
    path = write_csv(
        tmp_path,
        "ID,Title 1,Title 2,State,Related Item,Repro Steps,Description\n"
        'T-1,both,,Active,,steps text here,"main description"\n',
    )
    result = load_tickets(path, FIXTURE_SETTINGS)
    desc = result.tickets[0].description
    assert "main description" in desc and "steps text here" in desc


def test_state_filter_skips_and_reports(tmp_path):
    settings = FIXTURE_SETTINGS.model_copy(update={"exclude_states": ["Closed"]})
    result = load_tickets(FIXTURES / "sample_tickets.csv", settings)
    assert [t.id for t in result.tickets] == ["TASK-101", "TASK-102", "TASK-104"]
    skipped = [i for i in result.issues if i.skipped]
    assert len(skipped) == 1 and skipped[0].ticket_id == "TASK-103"
    assert "Closed" in skipped[0].problem


def test_description_html_is_kept_raw_at_ingest():
    # Cleaning happens later in the pipeline; ingest must not touch content.
    result = load_tickets(FIXTURES / "sample_tickets.csv", FIXTURE_SETTINGS)
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
