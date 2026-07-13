"""Tests for duptool.clean. Each test name states the rule it proves.

Non-ASCII test inputs are built with chr() codepoints so this file stays
pure ASCII and cannot be corrupted by editor/encoding round-trips.
"""

from duptool.clean import (
    clean_text,
    fix_encoding_artifacts,
    normalize_whitespace,
    strip_html,
)

# mojibake of a right single quote (U+2019 seen through cp1252)
MOJI_RSQUOTE = chr(0x00E2) + chr(0x20AC) + chr(0x2122)
# mojibake of an en dash (U+2013 seen through cp1252)
MOJI_ENDASH = chr(0x00E2) + chr(0x20AC) + chr(0x201C)
LSQ, RSQ = chr(0x2018), chr(0x2019)      # curly single quotes
LDQ, RDQ = chr(0x201C), chr(0x201D)      # curly double quotes
NBSP = chr(0x00A0)
ZWSP, BOM = chr(0x200B), chr(0xFEFF)

# --- strip_html --------------------------------------------------------------


def test_tags_are_removed_and_text_kept():
    assert strip_html("<b>update</b> the <i>login</i> test").strip() == "update the login test"


def test_plain_text_without_html_is_unchanged():
    text = "Refactor TMS 48213 after the menu change."
    assert strip_html(text) == text


def test_less_than_sign_in_plain_text_is_not_eaten():
    # "a < b" is not markup; the HTML parser must not swallow the rest.
    text = "expect count < 5 after cleanup"
    assert strip_html(text) == text


def test_block_tags_become_line_breaks():
    out = strip_html("<div>Affected TMS IDs</div><div>TMS-48213</div>")
    # the two sections must not run together into one line
    assert "IDsTMS" not in out
    lines = [line for line in out.split("\n") if line]
    assert lines == ["Affected TMS IDs", "TMS-48213"]


def test_br_becomes_line_break():
    assert strip_html("line one<br>line two") == "line one\nline two"


def test_link_href_survives_stripping():
    # The TMS ID often lives only in the URL; the anchor text is useless.
    out = strip_html('<a href="https://tms.example.local/case/48213">the test case</a>')
    assert "https://tms.example.local/case/48213" in out
    assert "the test case" in out


def test_img_src_survives_stripping():
    out = strip_html('see <img src="attachments/screen1.png"> above')
    assert "attachments/screen1.png" in out


def test_entities_are_unescaped_inside_html():
    assert "A & B" in strip_html("<p>A &amp; B</p>")


def test_entities_are_unescaped_in_plain_text_too():
    assert strip_html("A &amp; B") == "A & B"


# --- fix_encoding_artifacts ---------------------------------------------------


def test_mojibake_right_single_quote_is_fixed():
    assert fix_encoding_artifacts("It" + MOJI_RSQUOTE + "s done") == "It's done"


def test_mojibake_en_dash_is_not_half_eaten_by_quote_rule():
    # The en-dash mojibake ends in U+201C (a left double quote). The fix
    # table must repair the full 3-char sequence, not just its tail.
    assert fix_encoding_artifacts("x " + MOJI_ENDASH + " y") == "x - y"


def test_smart_quotes_become_ascii_quotes():
    raw = LDQ + "LEFT_MENU" + RDQ + " " + LSQ + "button" + RSQ
    assert fix_encoding_artifacts(raw) == "\"LEFT_MENU\" 'button'"


def test_non_breaking_space_becomes_space():
    assert fix_encoding_artifacts("TMS" + NBSP + "48213") == "TMS 48213"


def test_zero_width_characters_are_removed():
    assert fix_encoding_artifacts("TMS" + ZWSP + "-48213" + BOM) == "TMS-48213"


# --- normalize_whitespace ------------------------------------------------------


def test_spaces_collapse_within_lines_but_newlines_survive():
    out = normalize_whitespace("Affected  TMS \t IDs\nTMS-48213")
    assert out == "Affected TMS IDs\nTMS-48213"


def test_many_blank_lines_collapse_to_one_blank_line():
    assert normalize_whitespace("para one\n\n\n\n\npara two") == "para one\n\npara two"


def test_windows_line_endings_are_normalized():
    assert normalize_whitespace("a\r\nb\rc") == "a\nb\nc"


def test_empty_string_is_fine():
    assert normalize_whitespace("") == ""
    assert clean_text("") == ""


# --- clean_text (full pipeline) -----------------------------------------------


def test_full_pipeline_on_a_realistic_ado_description():
    raw = (
        "<div>What needs to be done?</div>"
        "<div>Update the login test " + MOJI_RSQUOTE + " the left&nbsp;menu moved.</div>"
        "<div>Affected TMS IDs</div>"
        '<div><a href="https://tms.example.local/case/48213">TMS case</a></div>'
    )
    out = clean_text(raw)
    assert "What needs to be done?" in out
    assert "'" in out and chr(0x00E2) not in out           # mojibake repaired
    assert "left menu" in out                              # &nbsp; became a plain space
    assert "https://tms.example.local/case/48213" in out   # href kept for ID extraction
    assert "<" not in out                                  # no tags left
