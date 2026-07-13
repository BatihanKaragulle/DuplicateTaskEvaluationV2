"""Pure text cleaning: str -> str functions. No I/O, no config, no state.

clean_text() is the pipeline entry point and applies, in order:
    strip_html -> fix_encoding_artifacts -> normalize_whitespace
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

# Tags that produce a visual line break when rendered. Line structure is
# preserved on purpose: the template extractor (signals, step 2) anchors on
# section label phrases, which sit on their own line in the rendered ticket.
_BLOCK_TAGS = {
    "br", "p", "div", "li", "ul", "ol", "tr", "table",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
}

# Something that actually looks like markup: "<b>", "</p>", "<!--".
# Plain text such as "a < b" must NOT be run through the HTML parser,
# which would swallow everything after the "<".
_LOOKS_LIKE_HTML = re.compile(r"<[a-zA-Z/!]")


class _TextExtractor(HTMLParser):
    """Collects text content while keeping link and image URLs.

    In ADO descriptions the TMS/TRQ ID often lives only in a link's href
    while the visible anchor text is just "test case" -- so hrefs must
    survive into the cleaned text for the ID extractor to see them.
    Image sources are kept as text references only (recorded, never
    fetched -- see the screenshot-extraction placeholder in CLAUDE.md).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        attr_map = dict(attrs)
        if tag == "a" and attr_map.get("href"):
            self.parts.append(f" {attr_map['href']} ")
        elif tag == "img" and attr_map.get("src"):
            self.parts.append(f" {attr_map['src']} ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(text: str) -> str:
    """Remove HTML tags; keep text content, link/image URLs, line structure."""
    if not _LOOKS_LIKE_HTML.search(text):
        # Not markup -- but stray entities (&amp;, &nbsp;) still occur in
        # plain-text exports and must be decoded either way.
        return html.unescape(text)
    parser = _TextExtractor()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


# Common artifacts in ADO exports: UTF-8 text that was decoded as
# Windows-1252 somewhere upstream (classic mojibake: a right single quote
# U+2019 arrives as the three characters U+00E2 U+20AC U+2122), plus
# typographic characters that would break naive token comparison. Repaired
# here once so everything downstream sees plain ASCII quotes/dashes.
# This is a fixed repair table, not a tunable -- it does not belong in
# config.yaml. Written entirely as \u escapes: nothing in this table is
# invisible or editor-mangled in source.
#
# Dict order is the apply order. Mojibake sequences contain typographic
# characters themselves (e.g. the en-dash mojibake ends in U+201C), so they
# must be repaired before the single-character typographic rules run.
_ENCODING_FIXES = {
    "\u00e2\u20ac\u2122": "'",  # mojibake of right single quote U+2019
    "\u00e2\u20ac\u02dc": "'",  # mojibake of left single quote U+2018
    "\u00e2\u20ac\u0153": "\"",  # mojibake of left double quote U+201C
    "\u00e2\u20ac\u009d": "\"",  # mojibake of right double quote U+201D
    "\u00e2\u20ac\u201c": "-",  # mojibake of en dash U+2013
    "\u00e2\u20ac\u201d": "-",  # mojibake of em dash U+2014
    "\u00e2\u20ac\u00a6": "...",  # mojibake of ellipsis U+2026
    "\u00c2\u00a0": " ",  # mojibake of non-breaking space U+00A0
    "\u2018": "'",  # left single quote
    "\u2019": "'",  # right single quote
    "\u201c": "\"",  # left double quote
    "\u201d": "\"",  # right double quote
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2026": "...",  # ellipsis
    "\u00a0": " ",  # non-breaking space
    "\u200b": "",  # zero-width space
    "\ufeff": "",  # stray BOM inside text
}


def fix_encoding_artifacts(text: str) -> str:
    """Replace known mojibake sequences and typographic characters."""
    for bad, good in _ENCODING_FIXES.items():
        text = text.replace(bad, good)
    return text


def normalize_whitespace(text: str) -> str:
    """Collapse spaces/tabs within lines; keep line breaks meaningful.

    At most one blank line survives between paragraphs. Newlines are NOT
    collapsed away entirely because the template extractor needs them.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_text(text: str) -> str:
    """Full cleaning pipeline for a raw title or description."""
    return normalize_whitespace(fix_encoding_artifacts(strip_html(text)))
