# duptool -- Code & Modules Guide

A detailed walk-through of the codebase: what each module does, what each
function is for, and which domain rule each design decision encodes. Read
[README.md](README.md) first for install/run basics and [CLAUDE.md](CLAUDE.md)
for the full requirements this code implements.

---

## 1. What this tool is (and guarantees)

duptool reads a CSV export of Azure DevOps tasks and suggests which tickets
are **possible duplicates** or **possibly related** -- with a human-readable
reason for every suggestion. Hard guarantees, enforced by design:

| Guarantee | How it is enforced |
|---|---|
| Fully offline | No network library is imported anywhere. URLs are extracted as text, never fetched. |
| Read-only | The only outputs are files in `--out` and console text. No code path touches ADO. |
| Deterministic | Regex + counting + BM25 only. No LLM, no randomness. Same input -> same output. |
| Explainable | Every subscore produces its evidence strings at scoring time. A score is never just a number. |
| Config-driven | Every tunable (regexes, labels, keywords, weights, thresholds) lives in `config.yaml`. Changing behavior never requires touching Python. |
| Precision over recall | High default thresholds, top-k caps, conservative wording ("possible", never "is"). |

## 2. Running the tool -- inputs, outputs, where the data lives

**One-time setup** (see README for details):

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

**Input data: the CSV export.** Export your tasks from ADO as CSV. The
file can live ANYWHERE on your machine -- you always pass its path
explicitly via `--input`; nothing is picked up implicitly. The export must
contain at least an ID, a title, and a description column. Their exact
header names are mapped in `config.yaml`:

```yaml
ingest:
  encoding: utf-8-sig     # ADO exports usually carry a UTF-8 BOM
  columns:
    id: "ID"              # <- change these if your export uses
    title: "Title"        #    different column headers
    description: "Description"
```

If the headers do not match, the tool fails immediately and tells you both
what it expected and what it found. Extra columns in the CSV are ignored.

Practical tip: keep exports OUT of the repository (e.g. in a `samples/`
folder, which is gitignored, or anywhere outside the project) -- ticket
contents are confidential and must never be committed.

**Scoring a real export:**

```powershell
python -m duptool run --input C:\path\to\export.csv --config config.yaml --out results\
```

- `--input`  (required) the CSV export
- `--config` (optional) defaults to `config.yaml` in the current directory
- `--out`    (optional) defaults to `results/` -- created if missing

The console prints ingest issues (row numbers + ticket IDs only), how many
pairs were scored, and the top 5. The real output is the three files in
`--out`: `pairs.csv`, `pairs.json`, `summary.txt` -- start with
`summary.txt`, it is written for humans.

**Evaluating against your labels:**

Create a labels CSV anywhere (also keep it out of the repo if it reveals
real ticket IDs you consider sensitive):

```csv
ticket_a,ticket_b,relationship
TASK-101,TASK-102,duplicate
TASK-101,TASK-103,unrelated
```

```powershell
python -m duptool eval --input C:\path\to\export.csv --labels C:\path\to\labels.csv
```

The ticket IDs in the labels must match the ID column values of the export
(pairs referencing unknown tickets are counted and reported as skipped).

**Everything stays local.** Both commands only read the two files you name
and only write into `--out`. No network, no ADO access, ever.

## 3. The pipeline

Data flows one way through small modules:

```
CSV file
   |  ingest.load_tickets           -> list[Ticket] + list[IngestIssue]
   v
Ticket (raw title/description, may contain HTML)
   |  signals.extract_signals       (uses clean.clean_text internally)
   v
TicketSignals (refs, tokens, task type, cleaned text, ...)
   |  candidates.generate_pairs     -> list[CandidatePair]
   |  scoring.LexicalIndex          (one BM25 index over all tickets)
   |  scoring.score_pair            per pair
   v
ScoredPair (final score, band, subscores with evidence)
   |  report.write_reports          -> pairs.csv / pairs.json / summary.txt
   v
Human reads the report and decides.
```

`__main__.py` is the only place that wires these together (`_score_all`).

## 4. Project structure

```
DuplicateTaskEvaluationV2/
|-- CLAUDE.md          # requirements + domain knowledge (the contract)
|-- README.md          # install, run, read the output
|-- GUIDE.md           # this file
|-- pyproject.toml     # core deps; [semantic] and [dev] optional extras
|-- config.yaml        # ALL tunables, validated strictly at load
|-- duptool/
|   |-- __init__.py
|   |-- __main__.py    # CLI: run / eval + metric computation
|   |-- models.py      # every typed model + Settings + load_settings
|   |-- ingest.py      # CSV -> Tickets; labels CSV -> LabelledPairs
|   |-- clean.py       # HTML strip, encoding repair, whitespace (pure str->str)
|   |-- signals.py     # per-ticket extraction -> TicketSignals
|   |-- candidates.py  # pair generation (all pairs for MVP)
|   |-- scoring.py     # subscores + evidence + BM25 + banding
|   `-- report.py      # ranked outputs + per-ticket summaries
`-- tests/
    |-- test_clean.py    # cleaning rules
    |-- test_ingest.py   # ingest + config loading rules
    |-- test_signals.py  # the largest: every ID/label/token format
    |-- test_scoring.py  # every subscore + override + banding
    |-- test_report.py   # candidates, report files, run CLI end-to-end
    |-- test_eval.py     # labels file + metric definitions
    `-- fixtures/        # sample_tickets.csv, labelled_pairs.csv
```

Conventions used everywhere: Python 3.11+, full type hints, Pydantic models
at every module boundary, pure functions where possible, stdlib over new
dependencies, tests named after the rule they prove.

## 5. config.yaml

One file, one `Settings` object. Misspelled or unknown keys are **rejected
at load time** (`extra="forbid"` on every settings model) so a typo cannot
silently fall back to a default.

| Section | Controls |
|---|---|
| `ingest` | CSV encoding; column-header mapping (exports change; fix here, not in code) |
| `signals.ids.kinds.<kind>` | per-kind regex patterns (text + URL) and section labels for `tms` / `trq` |
| `signals.ids.shared_url_patterns` | the tracker link shape all kinds share (`/cb/issue/{number}`) |
| `signals.ids.section_number_pattern` | bare numbers accepted on ID rows inside a labeled section (5-8 digits) |
| `signals.ids.unlabeled_run_*` | unlabeled ID dumps: 2+ standalone 7-digit numbers on one line |
| `signals.ids.neutral_line_labels` | words like `bug:` that disown a line's refs to kind "unknown" |
| `signals.template_labels` | section label phrases ("what needs to be done", "affected layer and method") |
| `signals.task_type` | title-header aliases and keyword lists per task type |
| `signals.step_patterns` | "step 3" / "Step #12" extraction |
| `scoring.weights` | relative signal weights (renormalized per pair over fired signals) |
| `scoring.id_overlap.base` | floor score for any shared item (0.6) |
| `scoring.task_type` | same-type (1.0) and cross-type (0.35) credit |
| `scoring.layer_method` | per-pool credit: methods > quoted tokens > BDD text |
| `scoring.lexical` | BM25 k1 / b |
| `scoring.bands` | possible_duplicate (0.80) / possibly_related (0.55) thresholds |
| `report.top_k_per_ticket` | max candidates listed per ticket in summary.txt |

Rule of the house: weights and bands are tuned ONLY by running
`duptool eval` against labelled pairs, never by feel.

## 6. models.py -- the typed boundaries

Every piece of data that crosses a module boundary has a Pydantic model.

**Data models**

- `Ticket` -- one CSV row, kept raw (description may contain HTML) so any
  extraction bug can be traced back to the original text. Frozen.
- `IngestIssue` / `IngestResult` -- every row-level problem found while
  reading the CSV. `skipped=True` means the row did not become a Ticket.
  Nothing is ever dropped silently.
- `IdRef` -- one tracker item reference: `item_id` (the number -- THE
  identity for matching), `kind` (`tms`/`trq`/`unknown`, metadata only),
  `source` (where it was seen). `label()` renders "TMS-142415" or
  "ITEM-1231211" for reports. Why identity-by-number: the tracker assigns
  one global number per item and links look identical for TMS/TRQ/bugs
  (`.../cb/issue/{number}`), so "TMS-142415" in one ticket and a bare link
  in another MUST compare equal.
- `RefSource` -- trust ladder for where a ref was found:
  `template_section` (deliberate) > `link` (pasted URL) > `text` (labeled
  prose mention) > `number_list` (unlabeled digit run).
- `TicketSignals` -- everything extraction learned about one ticket: task
  type + classification route, refs, quoted tokens, BDD steps, method
  names, step numbers, other URLs / image refs (recorded, never fetched),
  cleaned title/description, `body_text` (the "what needs to be done"
  section when present), and which extractors ran.
- `CandidatePair` -- an unordered pair selected for scoring.
- `SubScore` -- one signal's contribution: `score` (None = skipped),
  configured `weight`, and its `evidence` strings.
- `ScoredPair` -- the final verdict for a pair: blended `final_score`,
  `band`, `hard_override` flag, and all subscores.
- `LabelledPair` / `EvalMetrics` -- ground-truth labels and the eval
  results (matrix + ratios; ratios are None when there is no data).

**Settings models** mirror config.yaml one section per pipeline stage
(`IngestSettings`, `SignalsSettings` with `IdSettings`/`TemplateLabels`/
`TaskTypeSettings`, `ScoringSettings` with weights/bands/etc.,
`ReportSettings`). `load_settings(path)` is the single entry point for
config; `BandSettings` additionally validates that the duplicate threshold
is not below the related threshold.

## 7. clean.py -- pure text cleaning

Three `str -> str` functions composed by `clean_text()`:

- `strip_html(text)` -- removes tags via a stdlib `HTMLParser` subclass
  (`_TextExtractor`). Two deliberate behaviors:
  - **link hrefs and image srcs survive** as text, because the TMS/TRQ ID
    often lives only in the URL while the anchor text says "the case";
  - **block tags become newlines**, because the template extractor anchors
    on section labels that sit on their own line.
  Plain text containing `<` (like `count < 5`) is detected via
  `_LOOKS_LIKE_HTML` and NOT parsed (the parser would swallow it).
- `fix_encoding_artifacts(text)` -- repairs known mojibake (UTF-8 read as
  cp1252, e.g. the 3-character sequence that should be a right single
  quote) and normalizes typographic characters (smart quotes, dashes,
  NBSP, zero-width chars) to plain ASCII. The table `_ENCODING_FIXES` is
  written entirely as `\u` escapes so nothing in source is invisible;
  order matters (mojibake first) and is documented inline.
- `normalize_whitespace(text)` -- collapses spaces/tabs within lines,
  keeps line breaks (max one blank line), trims. Newlines stay meaningful
  on purpose.

## 8. ingest.py -- CSV to typed models

- `load_tickets(csv_path, settings) -> IngestResult`
  - Column names come from config. Missing columns raise an error that
    names both the expected and the found columns.
  - `dtype=str` + `keep_default_na=False`: IDs like "00123" stay strings,
    empty cells become "" (never NaN).
  - Row rules: empty ID -> skipped AND reported with its CSV row number;
    duplicate ID -> first occurrence kept, later ones reported; empty
    title/description -> ticket kept AND flagged. Nothing silent.
- `load_labelled_pairs(csv_path) -> list[LabelledPair]`
  - Strict where ticket ingest is lenient: bad relationship value,
    self-pair, or repeated pair RAISES. These labels referee all tuning;
    silently skewed metrics would be worse than a crash.

## 9. signals.py -- extraction (the heart of the tool)

`extract_signals(ticket, settings) -> TicketSignals` orchestrates:

1. `clean_text` on title and description.
2. `_split_sections` -- template-aware splitting.
3. `_extract_refs` -- all tracker item references.
4. `_parse_tokens` -- BDD steps, quoted tokens, identifiers.
5. step-number extraction, URL/image recording.
6. `_classify_task_type`.

**Section splitting** (`_label_regex`, `_split_sections`)

- Each label phrase from config becomes a line-start regex tolerant of ANY
  punctuation/markdown around and between its words: "affected tms ids"
  matches `## Affected TMS IDs:`, `**AFFECTED TMS-IDS**`, and
  `Affected TMS IDs: TMS-101` (inline content is kept as the remainder).
- Longest alias wins, so "affected tms ids" beats a bare "tms" alias.
- Returns `(sections, remainders)`: lines per section key, plus the
  same-line leftovers of each label (they get special ID harvesting).
- Anchoring is on the words only -- never on markdown -- because ticket
  formatting changes over time.

**Reference extraction** (`_extract_refs`, `_add`, `_norm`)

Three passes feed one merge dict keyed by `item_id`:

1. *ID sections (most trusted).* Inside a kind's section:
   - the label line's remainder gets `_LABEL_NUMBER_RUN` harvesting: the
     leading run of numbers after the label ("TMS: 1111111, 2222222")
     becomes that kind's IDs; the run stops at the first word so
     "TMS 48213 in build 36" yields only 48213;
   - lines starting with a digit or `|` are ID rows (tables): standalone
     5-8 digit numbers are harvested; prose lines never are, so
     "verify version 0.36" cannot become an ID;
   - URLs matching the kind's or the shared pattern become refs with the
     section's kind;
   - a line starting with a neutral label ("bug:", "see", ...) keeps its
     refs but disowns the kind to "unknown".
2. *Generic pass over the whole ticket* (title + description): labeled
   text mentions (`TMS-48213`, `tms 48213`, `TMS_48213`, ...), kind-named
   URLs, and shared-shape URLs (kind "unknown"). Runs on EVERY ticket --
   extraction never depends on the template.
3. *Unlabeled ID dumps*: a line containing 2+ standalone 7-digit numbers
   ("days: 1325121 4353414 ...") is an ID list, kind "unknown", source
   `number_list`. Single bare numbers are never extracted.

Merging (`_add`): for each item the higher-trust source wins, and a known
kind always upgrades "unknown" (a prose "TRQ-2222222" names the kind of an
anonymous `cb/issue/2222222` link). `_norm` strips leading zeros so
"048213" == "48213".

**Token parsing** (`_parse_tokens`, `_identifier_shaped`)

Per line:
- Gherkin lines (`Given/When/Then/And/But`, optional leading `@` for
  pasted step definitions like `@Then("...")`) contribute ALL their quoted
  tokens plus the normalized step text to `bdd_steps`.
- Non-Gherkin lines contribute quoted tokens only when identifier-shaped
  (`"barcode_layout_table"`, `"(Value)"`, `"LEFT_MENU"`) -- quoted plain
  words ("Save", "less", "30") are prose, not signals.
- Identifiers are scanned on every line after masking quoted spans and
  URLs (nothing lands in two buckets; path fragments are not identifiers):
  snake_case in any case (`Step_Layer`, `_expiration_date_get` -- leading/
  trailing underscores stripped, lowercased) and UPPER-DASH locators
  (`PROCEDURE-OVERVIEW-DESCRIPTION`; every segment must contain a letter
  so `TMS-48213` stays an ID).

**Task type** (`_classify_task_type`)

Two routes, in priority order, result recorded in `task_type_source`:
1. explicit header at the START of the title (`[Refactor] ...`,
   `(Investigation) ...`, or a bare leading type word) -- strongest;
2. keyword vote on title + body -- classifies only when EXACTLY one type
   hits; both or neither -> "unknown". Never guess.

Task types are soft context (owner rule): scoring gives same-type only a
small boost and NEVER excludes a cross-type pair.

## 10. candidates.py -- pair generation

`generate_pairs(signals)` returns every unique unordered pair. Full
pairwise is fine at current sizes; the module boundary exists so a
blocking strategy can replace the internals without touching scoring.
Documented constraint for that future: blocking MUST keep every pair that
shares an item (the hard rule).

## 11. scoring.py -- deterministic scoring with evidence

**The core semantic: None vs 0.0.** Every subscore returns
`(score in [0,1], evidence)` or `None`. `None` = skipped (missing data on
either side): its weight drops out and the remaining weights renormalize.
`0.0` = genuine negative evidence: both sides have the signal and it
disagrees. A ticket is never punished for not filling an optional field.

- `score_id_overlap(a, b, cfg)` -- None if either side has no refs;
  0.0 with evidence if both have refs but share none (they anchor to
  different items); otherwise `base + (1-base) * containment` so one
  shared item starts at 0.6 and identical ref sets reach 1.0. Evidence
  names each shared item and both sources:
  `both reference TMS-48213 (A: template section, B: written in text)`.
- `_containment(a, b)` = `|A n B| / min(|A|,|B|)` -- chosen over Jaccard
  so a ticket touching a SUBSET of another's items still scores high.
- `score_layer_method(a, b, cfg)` -- three pools (method names, quoted
  tokens, BDD steps); only pools populated on BOTH sides can judge; the
  best pool wins with its configured credit (methods 1.0 > quoted 0.9 >
  BDD 0.7). None when no pool can judge -- this field is optional and
  usually empty, absence must stay neutral.
- `score_task_type(a, b, cfg)` -- None if either is "unknown"; same type
  -> 1.0; cross type -> 0.35 (partial, never 0 -- soft context). Evidence
  names the classification route of each side.
- `score_steps(a, b)` -- containment over mentioned step numbers; None
  when either side mentions none.
- `LexicalIndex` -- hand-rolled Okapi BM25 (the formula is ~20 lines;
  that beats a dependency). Built once over all tickets (term rarity is
  corpus-aware). Pair similarity is made symmetric and bounded:
  `sim(A,B) = (bm25(A->B) + bm25(B->A)) / (bm25(A->A) + bm25(B->B))`,
  clamped to [0,1]. Tokens are words only -- pure digit tokens are item
  numbers, already counted by id_overlap, and must not double-count.
  Evidence includes the rarest shared words (what a human would point at).
- `band_for(score, bands)` -- threshold banding from config.
- `score_pair(a, b, settings, lexical)` -- runs all five, renormalizes
  weights over the fired ones, applies the **hard rule**: a pair sharing
  at least one item is ALWAYS surfaced -- if the blended score lands in
  not_shown it is promoted to possibly_related with an explicit
  "hard rule" evidence line and `hard_override=True`.

## 12. report.py -- the user-facing contract

`write_reports(scored, tickets, settings, out_dir)` writes three files
from the same ranked, shown-only pairs (`shown_pairs`: band != not_shown,
sorted by score desc, deterministic tie-break by IDs):

- `pairs.csv` -- one row per pair: score, band, override flag, one column
  per signal (skipped signals stay visibly empty), evidence joined.
- `pairs.json` -- full `ScoredPair` dumps; round-trips back into models.
- `summary.txt` -- "Top candidates for <ID> (<title>)" sections; each
  pair appears under BOTH tickets (a tester looks up their own ticket),
  capped at `top_k_per_ticket`, every evidence string listed.

Confidentiality: IDs and titles only -- descriptions never appear in any
report. Wording is always "possible duplicate" / "possibly related".

## 13. __main__.py -- CLI and eval metrics

- `_score_all(tickets, settings)` -- the shared pipeline core:
  signals -> BM25 index -> pairs -> scores.
- `cmd_run` -- `python -m duptool run --input export.csv --out results/`:
  prints ingest issues (row + ID only), pair counts, top 5, writes the
  three report files.
- `cmd_eval` -- `python -m duptool eval --input export.csv --labels
  labels.csv`: scores everything, compares against the labels, prints the
  band-vs-label matrix and the metrics.
- `compute_eval_metrics(all_scored, labels) -> EvalMetrics` -- pure and
  unit-tested. Definitions:
  - *possible_duplicate precision* (PRIMARY): of pairs the tool put in the
    duplicate band, the fraction actually labelled duplicate;
  - *duplicate recall*: of labelled duplicates, the fraction the tool put
    in the duplicate band;
  - *top-3 accuracy*: a labelled duplicate counts as a hit when the
    partner appears in the first 3 surfaced candidates of either ticket;
  - *unrelated flagged duplicate / surfaced*: false-positive rates on
    pairs labelled unrelated.
  Labelled pairs whose tickets are missing from the export are counted as
  skipped and reported, never silently ignored.

## 14. Tests -- documentation that runs

Tests are named after the rule they prove; reading a test file top to
bottom is reading the spec of its module.

| File | Proves |
|---|---|
| `test_clean.py` | tag stripping, href/src survival, line-break preservation, mojibake repair, whitespace rules |
| `test_ingest.py` | column mapping errors, report-don't-drop, string IDs, strict config loading |
| `test_signals.py` | every ID format (bare, list, table, link, unlabeled run), label tolerance, source trust, kind upgrade, token/BDD/type rules |
| `test_scoring.py` | each subscore, None-vs-0.0, renormalization, hard override, banding, determinism |
| `test_report.py` | ranking, tie-breaks, file formats, top-k, no-bodies rule, run CLI end to end |
| `test_eval.py` | labels file strictness, every metric definition, reversed-order matching |

Run them: `python -m pytest tests -q` (all should pass, currently 135).

## 15. How to change things safely

- **New ID format seen in the wild** -> add a pattern under
  `signals.ids.kinds.<kind>.text_patterns` (one capture group: the
  number), add a test in `test_signals.py`, done. No code.
- **Template wording changed** -> add the phrase to
  `signals.template_labels.*` or the kind's `section_labels`.
- **Tune weights or bands** -> change `scoring.*` in config, then run
  `duptool eval` before and after against your labelled pairs. Never tune
  without the referee.
- **Bigger exports get slow** -> implement blocking inside
  `candidates.generate_pairs` only; scoring must not change.
- **Do NOT** add network calls, LLM calls, or write-back of any kind --
  these are contractual (CLAUDE.md). The four extension points (LLM final
  checker, ADO live hook, screenshot OCR, link resolver) exist as future
  interfaces only and ship disabled.

## 16. Glossary

| Term | Meaning |
|---|---|
| TMS | a test case item in the tracker |
| TRQ | a requirement item in the tracker |
| item_id | the tracker's global number for an item -- THE identity for matching |
| kind | tms / trq / unknown -- metadata about an item_id, never used for matching |
| source | where a ref was seen: template_section > link > text > number_list |
| signal / subscore | one comparable aspect of a pair (ids, tokens, type, steps, text) |
| neutral / skipped | a signal that cannot judge this pair; excluded from the weighted sum |
| band | the classification of a final score: possible_duplicate / possibly_related / not_shown |
| hard override | the rule that any pair sharing an item is always surfaced |
| containment | overlap measure `|A n B| / min(|A|,|B|)` |
| BM25 | the lexical similarity formula (corpus-aware word rarity) |
| labelled pair | human ground truth used by `duptool eval` to referee tuning |
