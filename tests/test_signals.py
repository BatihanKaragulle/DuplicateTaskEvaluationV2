"""Tests for duptool.signals -- the largest test file on purpose.

Extraction quality determines everything downstream, so every ID format
variant, label tolerance, and parsing rule is documented here as a test.
Settings come from the repo's real config.yaml: these tests prove the
shipped patterns, not synthetic ones. Ticket texts are invented lookalikes
of real-world shapes (the real samples stay out of the repo by request).
"""

from pathlib import Path

from duptool.models import Ticket, load_settings
from duptool.signals import extract_signals

REPO_ROOT = Path(__file__).parents[1]
SIG = load_settings(REPO_ROOT / "config.yaml").signals


def sig(description: str = "", title: str = "task"):
    return extract_signals(
        Ticket(id="T-1", title=title, description=description), SIG
    )


def labels(signals) -> list[str]:
    return [r.label() for r in signals.refs]


def ref(signals, item_id: str):
    return next(r for r in signals.refs if r.item_id == item_id)


TEMPLATE_DESC = (
    "What needs to be done?\n"
    "Update the expiry checks after the layout change.\n"
    "Affected Layer and Method\n"
    'Given the user clicks "LEFT_MENU" "button"\n'
    "AND the user sees dropdown menu\n"
    "_expiratation_date_get\n"
    "Affected TMS IDs\n"
    "TMS-48213\n"
    "https://tms.example.local/case/50001\n"
    "Affected TRQs\n"
    "TRQ-1042\n"
)


# --- labeled ID extraction and normalization -----------------------------------


def test_all_bare_tms_variants_normalize_to_one_item():
    s = sig("TMS-48213 TMS 48213 tms48213 TMS_48213 tms:48213 TMS#48213")
    assert labels(s) == ["TMS-48213"]


def test_spaces_around_the_dash_are_tolerated():
    s = sig("Covers TRQ - 4578459 and TMS - 142415.")
    assert labels(s) == ["TMS-142415", "TRQ-4578459"]


def test_leading_zeros_normalize_away():
    s = sig("tms 048213")
    assert labels(s) == ["TMS-48213"]


def test_id_in_kind_named_url_is_extracted_with_link_source():
    s = sig("See https://tms.example.local/case/48213 for details.")
    assert labels(s) == ["TMS-48213"]
    assert ref(s, "48213").source == "link"


def test_bare_number_and_link_to_same_item_are_equal():
    from_text = sig("Refactor tms 48213 after the change.")
    from_link = sig("See https://tms.example.local/case/48213")
    assert labels(from_text) == labels(from_link)


def test_two_different_looking_links_to_same_item_yield_one_reference():
    s = sig(
        "https://tms.example.local/case/48213 and "
        "https://tms.example.local/browse/case/48213?tab=steps"
    )
    assert labels(s) == ["TMS-48213"]


def test_id_inside_html_link_href_is_found():
    s = sig('<a href="https://tms.example.local/case/48213">the case</a>')
    assert labels(s) == ["TMS-48213"]


def test_id_in_title_counts_too():
    s = sig(description="left menu moved", title="Refactor TMS 48213")
    assert labels(s) == ["TMS-48213"]


def test_unrelated_short_numbers_are_not_ids():
    s = sig("increase timeout to 48213 ms; retry 1042 times")
    assert labels(s) == []


def test_word_containing_tms_is_not_an_id_prefix():
    s = sig("the items 48213 were checked")
    assert labels(s) == []


# --- ID lists after one label (real-world shape) --------------------------------


def test_space_separated_number_list_after_label():
    s = sig("TMS: 1111111 2222222 3333333")
    assert labels(s) == ["TMS-1111111", "TMS-2222222", "TMS-3333333"]
    assert ref(s, "2222222").source == "template_section"


def test_comma_separated_number_list_after_label():
    s = sig("TMS: 1234567, 2345678, 3456789")
    assert labels(s) == ["TMS-1234567", "TMS-2345678", "TMS-3456789"]


def test_tms_id_label_with_word_id_between():
    # real-world form: "TMS ID: 6627195" mid-sentence
    s = sig("Check if the implementation matches figma. TMS ID: 6627195")
    assert labels(s) == ["TMS-6627195"]


def test_tms_id_list_line_harvests_all_numbers():
    # real-world form: a bullet line "TMS ID List: n, n, n" (nbsp-separated
    # in the raw HTML; cleaned to spaces before extraction)
    s = sig(
        "## Affected TMS IDs\n"
        "TMS ID List: 6996741, 6996742, 6996743, 7078688\n"
    )
    assert labels(s) == ["TMS-6996741", "TMS-6996742", "TMS-6996743", "TMS-7078688"]
    assert ref(s, "6996742").source == "template_section"


def test_trq_list_line_harvests_all_numbers():
    s = sig("TRQ List: 4728281, 4744549, 4728956")
    assert labels(s) == ["TRQ-4728281", "TRQ-4728956", "TRQ-4744549"]


def test_id_table_rows_are_harvested():
    s = sig(
        "TMS Action summary\n"
        "|TMS ID | STATUS | CURRENT ACTION |\n"
        "1111111 | covered | something\n"
        "2222222 | blocked | no migration\n"
    )
    assert labels(s) == ["TMS-1111111", "TMS-2222222"]
    assert ref(s, "1111111").kind == "tms"


def test_prose_lines_inside_id_sections_are_never_harvested():
    s = sig(
        "Affected TMS IDs\n"
        "please verify build 12345 is released first\n"
        "1111111 | ok |\n"
    )
    assert labels(s) == ["TMS-1111111"]  # 12345 sits in prose -> not an ID


# --- shared tracker-link shape (cb/issue/{number}) --------------------------------


def test_shared_link_inside_tms_section_gets_tms_kind():
    s = sig("## Affected TMS IDs\nhttps://tracker.example.local/cb/issue/6115121\n")
    assert labels(s) == ["TMS-6115121"]
    assert ref(s, "6115121").source == "template_section"


def test_shared_link_outside_sections_keeps_unknown_kind():
    s = sig("relates to https://tracker.example.local/cb/issue/1231211")
    assert labels(s) == ["ITEM-1231211"]
    assert ref(s, "1231211").source == "link"


def test_bug_labeled_line_inside_a_section_disowns_the_kind():
    s = sig(
        "Affected TRQs\n"
        "TRQ-1111111\n"
        "bug: https://tracker.example.local/cb/issue/2222222\n"
    )
    assert ref(s, "1111111").kind == "trq"
    assert ref(s, "2222222").kind == "unknown"  # it's a bug link, not a TRQ


def test_label_link_and_bare_number_all_match_by_item_id():
    a = sig("TMS - 3333333 needs new steps")
    b = sig("see https://tracker.example.local/cb/issue/3333333")
    c = sig("check these: 3333333 4444444")
    assert [r.item_id for r in a.refs] == ["3333333"]
    assert "3333333" in [r.item_id for r in b.refs]
    assert "3333333" in [r.item_id for r in c.refs]


def test_prose_kind_upgrades_an_unknown_link_ref():
    s = sig("see https://tracker.example.local/cb/issue/2222222 about TRQ-2222222")
    assert labels(s) == ["TRQ-2222222"]  # one ref: link identity + prose kind
    assert ref(s, "2222222").source == "link"


# --- unlabeled ID dumps -------------------------------------------------------------


def test_run_of_seven_digit_numbers_is_an_id_list():
    s = sig('the certification days: 1325121 4353414 8532578 1223472')
    assert labels(s) == ["ITEM-1223472", "ITEM-1325121", "ITEM-4353414", "ITEM-8532578"]
    assert ref(s, "1325121").source == "number_list"


def test_single_unlabeled_number_is_ignored():
    s = sig("error code 1234567 appeared once")
    assert labels(s) == []


def test_unlabeled_runs_of_other_lengths_are_ignored():
    s = sig("ran 123456 234567 checks and 12345678 90123456 loops")
    assert labels(s) == []


# --- template-aware extraction --------------------------------------------------


def test_ids_in_their_template_section_get_template_source():
    s = sig(TEMPLATE_DESC)
    assert labels(s) == ["TRQ-1042", "TMS-48213", "TMS-50001"]
    assert ref(s, "48213").source == "template_section"
    assert ref(s, "50001").source == "template_section"  # from URL in section
    assert ref(s, "1042").kind == "trq"


def test_section_labels_tolerate_case_punctuation_and_markdown():
    for label in ("## AFFECTED TMS IDS:", "**Affected Tms Ids**", "Affected  TMS - IDs ..."):
        s = sig(label + "\nTMS-7")
        assert ref(s, "7").source == "template_section", label


def test_ids_on_the_same_line_as_the_label_are_found():
    s = sig("Affected TMS IDs: TMS-101, TMS-102")
    assert labels(s) == ["TMS-101", "TMS-102"]
    assert ref(s, "101").source == "template_section"


def test_freeform_ticket_uses_generic_extractor_only():
    s = sig("Refactor the menu tests for tms 48213. See TRQ-1042.")
    assert s.extractors_used == ["generic"]
    assert labels(s) == ["TRQ-1042", "TMS-48213"]


def test_templated_ticket_still_gets_generic_supplement():
    desc = (
        "What needs to be done?\n"
        "Align with tms 222 behaviour.\n"  # ID outside any ID section
        "Affected TMS IDs\nTMS-111\n"
    )
    s = sig(desc)
    assert s.extractors_used == ["template", "generic"]
    assert labels(s) == ["TMS-111", "TMS-222"]
    assert ref(s, "222").source == "text"


def test_ref_seen_in_section_and_prose_keeps_the_template_source():
    desc = (
        "What needs to be done?\n"
        "Extend tms 333 coverage.\n"
        "Affected TMS IDs\nTMS-333\n"
    )
    s = sig(desc)
    assert labels(s) == ["TMS-333"]
    assert ref(s, "333").source == "template_section"


def test_body_text_is_the_what_section_when_template_present():
    s = sig(TEMPLATE_DESC)
    assert s.body_text == "Update the expiry checks after the layout change."


def test_body_text_is_full_description_without_template():
    s = sig("Just a freeform note about the login flow.")
    assert s.body_text == "Just a freeform note about the login flow."


# --- task type -------------------------------------------------------------------


def test_bracketed_title_header_is_the_strongest_type_signal():
    for title, expected in (
        ("[Implementation] cover the new field", "implementation"),
        ("[Refactor] login test after menu change", "refactor"),
        ("[Investigation] is the wait step still needed", "investigation"),
        ("( Investigation ) menu behaviour", "investigation"),
    ):
        s = sig("neutral body text", title=title)
        assert s.task_type == expected, title
        assert s.task_type_source == "title_header"


def test_type_word_in_any_stacked_bracket_classifies():
    # real-world titles stack bracket tags; the type is rarely the first one
    for title, expected in (
        ("[Refinement][v0.38.0][Refactor] UI Mismatch: teaching vs Figma", "refactor"),
        ("[IMP Refactor][REL: v.X Unknown] : Refactorization of the step", "refactor"),
        ("[Refinement][v0.38.1][Investigation] flaky wait step", "investigation"),
    ):
        s = sig("neutral body text", title=title)
        assert s.task_type == expected, title
        assert s.task_type_source == "title_header"


def test_non_type_brackets_do_not_classify():
    s = sig("neutral body text", title="[Refinement][v0.38.1] some cleanup work")
    assert s.task_type_source != "title_header"


def test_title_header_beats_conflicting_body_keywords():
    s = sig("update the flow and implement new checks", title="[Investigation] flaky step")
    assert s.task_type == "investigation"
    assert s.task_type_source == "title_header"


def test_bare_leading_type_word_counts_as_header():
    s = sig("The left menu moved to the top bar.", title="Refactor the login test")
    assert s.task_type == "refactor"
    assert s.task_type_source == "title_header"


def test_type_word_mid_title_is_not_a_header_but_still_a_keyword():
    s = sig("neutral body", title="Planned refactor of the login test")
    assert s.task_type == "refactor"
    assert s.task_type_source == "keywords"


def test_implementation_keywords_classify_implementation():
    s = sig("We must implement checks for the new field.", title="Cover the expiry flow")
    assert s.task_type == "implementation"
    assert s.task_type_source == "keywords"


def test_investigation_keywords_classify_investigation():
    s = sig("We need to check if this step is still needed or not.")
    assert s.task_type == "investigation"
    assert s.task_type_source == "keywords"


def test_conflicting_keywords_yield_unknown():
    s = sig("Update the flow and add test coverage for it.")
    assert s.task_type == "unknown"
    assert s.task_type_source == "none"


def test_no_keywords_yield_unknown():
    s = sig("The left menu behaves oddly on small screens.")
    assert s.task_type == "unknown"


# --- BDD steps, quoted tokens, identifiers -------------------------------------------


def test_quoted_tokens_are_extracted_from_gherkin_lines():
    s = sig(TEMPLATE_DESC)
    assert s.quoted_tokens == ["left_menu", "button"]


def test_gherkin_keywords_match_case_insensitively():
    s = sig("Affected Layer and Method\nAND the user sees dropdown menu\n")
    assert "the user sees dropdown menu" in s.bdd_steps


def test_step_definition_code_lines_count_as_gherkin():
    s = sig('@Then("the(table_name)""(expectation)"contain"(search_text)"element")')
    assert s.quoted_tokens == ["the(table_name)", "(expectation)", "(search_text)"]


def test_quoted_identifier_in_plain_prose_is_captured():
    s = sig('Extend the step below with "barcode_layout_table".')
    assert s.quoted_tokens == ["barcode_layout_table"]


def test_quoted_parenthesized_placeholders_are_captured():
    s = sig('Step I set time factor to "(Value)" to make system "(speed)"')
    assert s.quoted_tokens == ["(value)", "(speed)"]


def test_quoted_plain_words_in_prose_are_not_tokens():
    s = sig('The "Save" button is broken "sometimes", they added a field "body".')
    assert s.quoted_tokens == []


def test_snake_case_method_names_are_detected_and_underscore_stripped():
    s = sig(TEMPLATE_DESC)
    assert s.method_names == ["expiratation_date_get"]


def test_mixed_case_identifiers_are_detected_and_lowercased():
    s = sig("Affected layer and method\nStep_Layer\nBusiness_logic_layer\nGet_function_id()\n")
    assert s.method_names == ["step_layer", "business_logic_layer", "get_function_id"]


def test_upper_dash_locator_tokens_are_detected():
    s = sig("PROCEDURE-OVERVIEW-DESCRIPTION\neither remove it or fix the locator\n")
    assert s.method_names == ["procedure-overview-description"]


def test_id_references_are_not_locator_tokens():
    s = sig("TMS-1111111 needs a fix")
    assert s.method_names == []


def test_quoted_tokens_are_not_duplicated_into_method_names():
    s = sig('And the user clicks "LEFT_MENU_WORK_CYCLES""button"')
    assert s.quoted_tokens == ["left_menu_work_cycles", "button"]
    assert s.method_names == []


def test_method_names_are_not_harvested_from_urls():
    s = sig("see https://api.example.local/get_user_data/list?a=1")
    assert s.method_names == []


def test_bdd_lines_anywhere_in_description_are_found():
    s = sig('steps changed:\nWhen the user opens "SETTINGS"\n')
    assert s.quoted_tokens == ["settings"]
    assert s.extractors_used == ["generic"]


# --- steps, urls, images ------------------------------------------------------------


def test_mentioned_step_numbers_are_extracted():
    s = sig("Update step 3 and Step #12 accordingly (see test step 4).")
    assert s.step_numbers == ["3", "4", "12"]


def test_non_id_urls_are_recorded_not_dropped():
    s = sig("Design doc: https://wiki.example.local/page/123")
    assert s.other_urls == ["https://wiki.example.local/page/123"]


def test_ref_bearing_urls_are_not_duplicated_into_other_urls():
    s = sig("See https://tracker.example.local/cb/issue/1231211")
    assert s.other_urls == []


def test_image_references_are_recorded_never_fetched():
    s = sig('Broken layout: <img src="attachments/screen_1.png"> as shown')
    assert s.image_refs == ["attachments/screen_1.png"]
    assert s.method_names == []  # path fragments are not identifiers


# --- robustness ----------------------------------------------------------------------


def test_empty_description_yields_valid_empty_signals():
    s = sig("")
    assert s.refs == []
    assert s.quoted_tokens == [] and s.method_names == []
    assert s.extractors_used == ["generic"]


def test_fixture_tickets_extract_end_to_end():
    from duptool.ingest import load_tickets

    ingest_settings = load_settings(REPO_ROOT / "config.yaml").ingest
    result = load_tickets(
        Path(__file__).parent / "fixtures" / "sample_tickets.csv", ingest_settings
    )
    by_id = {t.id: extract_signals(t, SIG) for t in result.tickets}

    # templated ticket: IDs from link and section text, both most-trusted
    s101 = by_id["TASK-101"]
    assert labels(s101) == ["TRQ-1042", "TMS-48213"]
    assert ref(s101, "48213").source == "template_section"

    # freeform ticket: same references found generically -> same item_ids
    s102 = by_id["TASK-102"]
    assert [r.item_id for r in s102.refs] == [r.item_id for r in s101.refs]
    assert s102.task_type == "refactor"
    assert s102.extractors_used == ["generic"]
