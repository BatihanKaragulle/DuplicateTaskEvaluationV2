"""Per-ticket signal extraction: Ticket -> TicketSignals.

Two extractors feed one model:

- template/section-aware: anchors on label phrases from config ("Affected
  TMS IDs", "TMS:", "What needs to be done", ...). A kind's section supplies
  the kind for bare numbers, table rows, and shared-shape tracker links
  found inside it, with the most trusted source tag.
- generic: always runs over the WHOLE title+description. Old freeform
  tickets have no labels, and even templated tickets scatter IDs anywhere,
  so the IDs themselves are the primary indicators -- extraction must never
  depend on the labels.

References merge by item_id (the tracker number IS the identity; TMS and
TRQ share one id space). For each item the most trusted source wins
(template_section > link > text > number_list) and a known kind always
beats "unknown". Everything here is deterministic regex/string work: no
statistics, no embeddings, no network.
"""

from __future__ import annotations

import re

from duptool.clean import clean_text
from duptool.models import (
    IdRef,
    IdSettings,
    SignalsSettings,
    TaskType,
    TaskTypeSettings,
    TaskTypeSource,
    TemplateLabels,
    Ticket,
    TicketSignals,
)

# Gherkin keywords are a stable standard, not a tunable -> constant, not
# config. Optional leading "@" accepts step-definition code lines
# (@Then("the(table_name)"...)) that testers paste into tickets.
_GHERKIN_LINE = re.compile(r"^\s*@?(given|when|then|and|but)\b[ \t]*(.*)$", re.IGNORECASE)
_QUOTED_TOKEN = re.compile(r'"([^"\n]{1,60})"')
# snake_case-ish identifier, any case: _expiratation_date_get, Step_Layer,
# Get_function_id, EXIT_CONFIGURATION_MODAL
_SNAKE_IDENT = re.compile(r"\b_?[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+_?\b")
# UPPER-DASH locator tokens (PROCEDURE-OVERVIEW-DESCRIPTION). Every segment
# must contain a letter so TMS-48213 stays an ID, not an identifier.
_UPPER_DASH_IDENT = re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)+\b")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_IMAGE_PATH = re.compile(r"[^\s\"'<>]+\.(?:png|jpe?g|gif|bmp|webp)\b", re.IGNORECASE)

_SOURCE_TRUST = {"template_section": 4, "link": 3, "text": 2, "number_list": 1}

# internal section keys: "what", "layer", or an ID kind name ("tms", "trq")
_WHAT, _LAYER = "what", "layer"


def extract_signals(ticket: Ticket, settings: SignalsSettings) -> TicketSignals:
    title = clean_text(ticket.title)
    desc = clean_text(ticket.description)
    full = (title + "\n" + desc).strip()

    sections, remainders = _split_sections(desc, settings.template_labels, settings.ids)
    template_found = bool(sections)

    refs = _extract_refs(full, sections, remainders, settings.ids)
    quoted, bdd, methods = _parse_tokens(full)

    step_numbers: set[str] = set()
    for pattern in settings.step_patterns:
        for m in re.finditer(pattern, full):
            step_numbers.add(m.group(1).lstrip("0") or "0")

    # URLs are recorded, never fetched. A URL that yielded a reference is
    # already represented by it; the rest go to the (disabled) link-resolver
    # extension point, images to the screenshot placeholder.
    urls = _URL.findall(full)
    image_refs = _dedup(_IMAGE_PATH.findall(full))
    other_urls = _dedup(
        u for u in urls
        if u not in image_refs and not _url_is_a_ref(u, settings.ids)
    )

    # free-text body: the "What needs to be done" section when present
    # (labels/boilerplate would pollute lexical similarity), else everything
    body = "\n".join(sections.get(_WHAT, [])).strip() or desc

    task_type, task_type_source = _classify_task_type(title, body, settings.task_type)

    return TicketSignals(
        ticket_id=ticket.id,
        task_type=task_type,
        task_type_source=task_type_source,
        refs=refs,
        quoted_tokens=quoted,
        bdd_steps=bdd,
        method_names=methods,
        step_numbers=sorted(step_numbers, key=int),
        other_urls=other_urls,
        image_refs=image_refs,
        clean_title=title,
        clean_description=desc,
        body_text=body,
        extractors_used=["template", "generic"] if template_found else ["generic"],
    )


# --- section splitting ---------------------------------------------------------


def _label_regex(alias: str) -> re.Pattern[str]:
    """A line-start regex for one label phrase, tolerant of formatting.

    "affected tms ids" also matches "## Affected TMS IDs:", "**AFFECTED
    TMS-IDS**", and "Affected TMS IDs: TMS-101" (inline content survives as
    the match remainder). Anchored at line start so a mere mention of the
    phrase mid-sentence does not open a section.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", alias.lower()) if w]
    return re.compile(
        r"^[\W_]*" + r"[\W_]+".join(re.escape(w) for w in words) + r"[\W_]*",
        re.IGNORECASE,
    )


def _split_sections(
    description: str, labels: TemplateLabels, ids: IdSettings
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Split a description into labeled sections (lists of lines).

    Anchors on label PHRASES only -- never on markdown/HTML formatting,
    which changes over time. The longest alias is tried first so "affected
    tms ids" wins over a plain "tms" alias on the same line. Returns
    (sections, remainders): remainders are the same-line leftovers of each
    label ("TMS: 1111111 2222222" -> "1111111 2222222"), which get special
    ID harvesting. Empty sections dict = no label matched (freeform ticket).
    """
    aliases: list[tuple[re.Pattern[str], str]] = []
    keyed = [(_WHAT, labels.what_needs_to_be_done), (_LAYER, labels.affected_layer_method)]
    keyed += [(kind, cfg.section_labels) for kind, cfg in ids.kinds.items()]
    for key, phrases in keyed:
        for phrase in phrases:
            aliases.append((_label_regex(phrase), key))
    aliases.sort(key=lambda pair: len(pair[0].pattern), reverse=True)

    sections: dict[str, list[str]] = {}
    remainders: dict[str, list[str]] = {}
    current: str | None = None
    for line in description.split("\n"):
        for regex, key in aliases:
            m = regex.match(line)
            if m:
                current = key
                sections.setdefault(key, [])
                remainder = line[m.end():].strip()
                if remainder:  # content on the same line as the label
                    sections[key].append(remainder)
                    remainders.setdefault(key, []).append(remainder)
                break
        else:
            if current is not None:
                sections[current].append(line)
    return sections, remainders


# --- reference extraction --------------------------------------------------------


# The leading run of numbers directly after a kind label on the same line
# ("TMS: 1111111, 2222222 and 3333333"). Stops at the first word, so
# "TMS 48213 in build 36" yields only 48213. Structural list-parsing, not
# an ID format -- which is why it lives here and not in config.
_LABEL_NUMBER_RUN = re.compile(r"^[\s:,;/\-]*((?:\d{1,8}[\s,;/\-]*(?:and\s+)?)+)")


def _extract_refs(
    full: str,
    sections: dict[str, list[str]],
    remainders: dict[str, list[str]],
    ids: IdSettings,
) -> list[IdRef]:
    # item_id -> (kind, source); merged by trust as evidence accumulates
    seen: dict[str, tuple[str, str]] = {}

    # 1) ID sections (most trusted). The section supplies the kind for bare
    # numbers on ID rows and for shared-shape tracker links.
    for kind, cfg in ids.kinds.items():
        # numbers immediately following the label on its own line
        # ("TMS: 1111111 2222222" / "TMS-7") are that kind's IDs
        for remainder in remainders.get(kind, []):
            m = _LABEL_NUMBER_RUN.match(remainder)
            if m:
                for num in re.findall(r"\d{1,8}", m.group(1)):
                    _add(seen, _norm(num), kind, "template_section")
        for line in sections.get(kind, []):
            if _is_neutral_line(line, ids.neutral_line_labels):
                # "bug: <link>" inside the TRQs section: keep the ref (the
                # number still matches across tickets) but disown the kind.
                for num in _ids_in_urls(_URL.findall(line), cfg, ids):
                    _add(seen, num, "unknown", "link")
                continue
            for pattern in cfg.text_patterns:
                for m in re.finditer(pattern, line):
                    _add(seen, _norm(m.group(1)), kind, "template_section")
            for num in _ids_in_urls(_URL.findall(line), cfg, ids):
                _add(seen, num, kind, "template_section")
            # ID rows: lines starting with a number or a table pipe
            # ("1312312 | covered | ..."). Prose lines are never harvested.
            if re.match(r"^\s*[|\d]", line):
                for m in re.finditer(ids.section_number_pattern, line):
                    _add(seen, _norm(m.group(1)), kind, "template_section")

    # 2) generic pass over the whole ticket (fallback + supplement)
    urls = _URL.findall(full)
    for kind, cfg in ids.kinds.items():
        for pattern in cfg.text_patterns:
            for m in re.finditer(pattern, full):
                _add(seen, _norm(m.group(1)), kind, "text")
        for pattern in cfg.url_patterns:
            for url in urls:
                m = re.search(pattern, url)
                if m:
                    _add(seen, _norm(m.group(1)), kind, "link")
    for pattern in ids.shared_url_patterns:
        for url in urls:
            m = re.search(pattern, url)
            if m:
                _add(seen, _norm(m.group(1)), "unknown", "link")

    # 3) unlabeled ID dumps: a line holding >= min_count standalone numbers
    # of the typical ID length is an ID list even without any label.
    for line in full.split("\n"):
        found = [m.group(1) for m in re.finditer(ids.unlabeled_run_pattern, line)]
        if len(found) >= ids.unlabeled_run_min_count:
            for num in found:
                _add(seen, _norm(num), "unknown", "number_list")

    return [
        IdRef(item_id=num, kind=kind, source=source)  # type: ignore[arg-type]
        for num, (kind, source) in sorted(seen.items(), key=lambda kv: int(kv[0]))
    ]


def _norm(number: str) -> str:
    # int() strips leading zeros so "048213" and "48213" compare equal
    return str(int(number))


def _ids_in_urls(urls: list[str], cfg, ids: IdSettings) -> set[str]:
    found: set[str] = set()
    for url in urls:
        for pattern in cfg.url_patterns + ids.shared_url_patterns:
            m = re.search(pattern, url)
            if m:
                found.add(_norm(m.group(1)))
    return found


def _url_is_a_ref(url: str, ids: IdSettings) -> bool:
    patterns = list(ids.shared_url_patterns)
    for cfg in ids.kinds.values():
        patterns += cfg.url_patterns
    return any(re.search(p, url) for p in patterns)


def _is_neutral_line(line: str, neutral_labels: list[str]) -> bool:
    m = re.match(r"^\s*([A-Za-z]+)\b", line)
    return bool(m) and m.group(1).lower() in neutral_labels


def _add(seen: dict[str, tuple[str, str]], item_id: str, kind: str, source: str) -> None:
    """Merge one sighting of an item. Higher-trust source wins; a known
    kind always beats "unknown", whatever the source trust."""
    if item_id not in seen:
        seen[item_id] = (kind, source)
        return
    old_kind, old_source = seen[item_id]
    best_source = source if _SOURCE_TRUST[source] > _SOURCE_TRUST[old_source] else old_source
    best_kind = old_kind if old_kind != "unknown" else kind
    if kind != "unknown" and _SOURCE_TRUST[source] > _SOURCE_TRUST[old_source]:
        best_kind = kind
    seen[item_id] = (best_kind, best_source)


# --- BDD steps, quoted tokens, identifiers ----------------------------------------


def _identifier_shaped(token: str) -> bool:
    """True for quoted tokens that look like code/locator identifiers:
    "barcode_layout_table", "(Value)", "LEFT_MENU". Plain words ("Save",
    "less") and pure numbers ("30") are prose, not identifiers."""
    t = token.strip()
    if not t or any(ch.isspace() for ch in t):
        return False
    if re.fullmatch(r"[\d\W_]+", t):
        return False
    return "_" in t or "-" in t or "(" in t or t.isupper()


def _parse_tokens(text: str) -> tuple[list[str], list[str], list[str]]:
    """Extract (quoted_tokens, bdd_steps, method_names) from the ticket text.

    Quoted tokens count from Gherkin lines always; from other lines only
    when identifier-shaped (testers quote identifiers in prose: 'extend the
    step with "barcode_layout_table"'). Identifiers are scanned on every
    line, with quoted spans and URLs masked first so nothing lands in two
    buckets and path fragments don't read as identifiers.
    """
    quoted: list[str] = []
    bdd: list[str] = []
    methods: list[str] = []
    for line in text.split("\n"):
        line_quoted = _QUOTED_TOKEN.findall(line)
        g = _GHERKIN_LINE.match(line)
        if g:
            quoted.extend(t.strip().lower() for t in line_quoted)
            step = re.sub(r"\s+", " ", g.group(2)).strip().lower()
            if step:
                bdd.append(step)
        else:
            quoted.extend(
                t.strip().lower() for t in line_quoted if _identifier_shaped(t)
            )
        masked = _QUOTED_TOKEN.sub(" ", line)
        masked = _IMAGE_PATH.sub(" ", _URL.sub(" ", masked))
        # strip('_'): "_expiration_date_get" and "expiration_date_get" are
        # the same method; the underscore is a privacy convention.
        methods.extend(m.group(0).strip("_").lower() for m in _SNAKE_IDENT.finditer(masked))
        methods.extend(m.group(0).lower() for m in _UPPER_DASH_IDENT.finditer(masked))
    return _dedup(quoted), _dedup(bdd), _dedup(methods)


# --- task type ---------------------------------------------------------------------


def _classify_task_type(
    title: str, body: str, cfg: TaskTypeSettings
) -> tuple[TaskType, TaskTypeSource]:
    """Three routes, in priority order; "unknown" whenever none is sure.

    1. A type word inside ANY bracketed title tag. Testers stack tags --
       "[IMP Refactor][REL: v.X] :", "[Refinement][v0.38.0][Refactor]" --
       so the type bracket is often not the first one. A bracketed type
       word is deliberate labeling wherever it sits.
    2. A bare leading type word ("Refactor the login test").
    3. Keyword vote on title + body; only an unambiguous vote (exactly one
       type hits) classifies -- never guess.
    Either way the result is soft context for scoring, never a separator.
    """
    for content in re.findall(r"\[([^\]]{1,60})\]", title):
        for task_type, aliases in cfg.title_headers.items():
            for alias in aliases:
                words = r"[\W_]+".join(re.escape(w) for w in alias.split())
                if re.search(rf"\b{words}\b", content, re.IGNORECASE):
                    return task_type, "title_header"  # type: ignore[return-value]

    for task_type, aliases in cfg.title_headers.items():
        for alias in aliases:
            if re.match(rf"^\s*[\({{]?\s*{re.escape(alias)}\b", title, re.IGNORECASE):
                return task_type, "title_header"  # type: ignore[return-value]

    lowered = (title + "\n" + body).lower()
    hits = [t for t, kws in cfg.keywords.items() if any(k in lowered for k in kws)]
    if len(hits) == 1:
        return hits[0], "keywords"  # type: ignore[return-value]
    return "unknown", "none"


def _dedup(items) -> list[str]:
    """Order-preserving dedup (first occurrence wins)."""
    return list(dict.fromkeys(items))
