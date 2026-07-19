# CLAUDE.md — Duplicate Task Suggestion Tool

Project context for Claude Code. Read this fully before writing or changing any code.

## What This Project Is

A **read-only, offline duplicate-task suggestion tool** for a large testing team. Testers create tasks in Azure DevOps (ADO); multiple testers often describe the same work in different words. This tool reads a **CSV export of tasks**, extracts structured signals from each ticket, scores ticket pairs with a deterministic hybrid formula, and reports which tickets are likely **duplicates of** or **related to** each other — with human-readable reasons.

The tool **only produces results**. It never writes back to ADO, never merges, links, closes, tags, or modifies anything. A human reads the output and decides.

## Non-Negotiable Rules

1. **Confidentiality first — the data must never leave the machine.**
   - Ticket contents are confidential. The tool must run fully offline on the CSV input.
   - **No network calls of any kind, at any time, by this tool.** No external APIs, no hosted embeddings or LLMs, no telemetry, and no downloading models at runtime — the optional local embedding model's files are obtained manually out-of-band and loaded in local-files-only mode.
   - Do not log or print full ticket bodies by default. Logs reference tickets by ID; verbose content logging only behind an explicit debug flag.
   - Any future component that could send data anywhere (LLM checker, link fetcher) exists **only as a disabled placeholder interface** (see "Extension Points"). Its config flag defaults to OFF and the MVP must not implement its internals.

2. **Suggestion-only, read-only.** No code path may take an action on tickets or any external system. Output is files/console/report only.

3. **Precision over recall.** It is better to miss some duplicates than to spam the user with wrong suggestions. Default thresholds err high; show few, strong candidates (top 3–5 per ticket, configurable). Wording in output is always "possible duplicate" / "possibly related" — never "duplicate" as a fact.

4. **No LLM anywhere in the core path.** The core scoring path is boring, deterministic Python: regex, string normalization, set overlap, TF-IDF/BM25-style lexical similarity. Same input → same output, every run. An LLM "final checker" is a future, optional, post-scoring step — **placeholder interface only in this codebase, no implementation.**

5. **Every score must be explainable.** A result is never just a number. For every candidate pair the output must include:
   - the final score,
   - each subscore (per signal),
   - the concrete evidence behind each subscore in plain language (e.g. `"both reference TMS-48213 (A: link in description, B: bare number)"`, `"shared TRQs: TRQ-1042"`, `"both are Refactor tasks"`, `"title token overlap: 0.71"`).
   The explanation strings are produced by the scoring code itself at scoring time, not reconstructed afterwards.

6. **All tunables live in config, not code.** Weights, thresholds, top-k, regex patterns for ID formats, task-type keywords, feature flags — everything in a single versioned config file (YAML or TOML) loaded into a typed Pydantic settings object. Changing a weight must never require touching Python code.

## Domain Knowledge (do not lose this)

### Task types
- **Implementation Task** — adds new test steps or creates new test cases.
- **Refactor Task** — updates existing test cases (TMS).
- **Investigation Task** — analysis/verification work ("check if step X is still needed", "look into ...").
- Titles often begin with an explicit header like `[Implementation]`, `[Refactor]`, `[Investigation]` — when present this is the strongest type signal (header aliases in config). Keyword classification is the fallback.
- Classify each ticket as `implementation | refactor | investigation | unknown` from the title header first, then title/description keywords (lists in config). Never guess with high confidence — `unknown` is an acceptable output. Record which route classified it.
- **Task types are soft context, not separators** (owner, 2026-07-15): an Investigation ticket can still duplicate or relate to a Refactor or Implementation ticket, and vice versa. Scoring treats type as weak evidence — same type adds a little confidence, cross-type gets mild partial credit, and type must never exclude a pair.
- Two tickets of the same type touching the same TMS/TRQ are the strongest duplicate signal. Cross-type pairs touching the same TMS are more likely **related** than duplicate (e.g. an implementation task and a refactor task on the same test case).

### Identifiers — the highest-value signal
- Tickets reference **TMS** items (test cases) and **TRQ** items (requirements). These appear:
  - as **links/URLs in the description** (extract the ID out of the URL — two different-looking links pointing at the same TMS are the same reference), or
  - as **bare numbers/IDs written in text** (`TMS 48213`, `TMS-48213`, `tms48213`, `TRQ1042`, …).
- Extraction must be tolerant: build the regex set from config, expect messy real-world variants, unit-test against a list of real formats. When a new format appears, it is added to config, not hardcoded.
- Normalize every extracted reference to a canonical form (e.g. `TMS-48213`) so link-sourced and text-sourced references compare equal.
- **Hard rule:** any pair sharing at least one canonical TMS or TRQ reference is always surfaced as a candidate, regardless of text score. Shared-ID overlap dominates the structured subscore.

### Test steps
- Descriptions often mention concrete test steps (added/changed steps). Extract mentioned step identifiers/numbers where present and include step overlap as a structured signal.

### The ticket template (new tickets)
New tickets loosely follow a template with these sections:

```
What needs to be done?
Affected Layer and Method
Affected TMS IDs
Affected TRQs
```

- **Do not anchor parsing on markdown syntax** (`##`, bold, etc.) — the formatting may change over time. Anchor on the **section label phrases themselves** ("Affected TMS IDs", "Affected TRQs", "Affected Layer and Method", "What needs to be done"), matched case-insensitively and tolerant of punctuation/formatting around them. Label phrases and their aliases live in config.
- Section routing, when labels are found:
  - `What needs to be done` → the free-text body used for lexical (and optional embedding) similarity and task-type classification.
  - `Affected Layer and Method` → see below.
  - `Affected TMS IDs` / `Affected TRQs` → high-confidence ID extraction (links or bare numbers), same canonicalization as everywhere else. IDs found near these labels are the most trustworthy references in the ticket.
- The template-aware extractor runs first; if labels are absent (old tickets), fall back to the **generic extractor** over the whole description. Both feed the same `TicketSignals` model — downstream code must not know or care which extractor produced the signals (record which one did, for debugging/eval).
- Even more robust than the labels: **the IDs themselves (TMS/TRQ patterns) are the primary indicators** and are extracted from the entire description regardless of sections. Section labels only add confidence/context; extraction must never depend on them.

### Layer and Method content
When testers fill this section (they often won't — treat it as a sparse, optional signal), it contains one of two shapes:

1. **BDD/Gherkin-style test steps**, e.g.
   ```
   Given the user clicks "LEFT_MENU" "button"
   AND the user sees dropdown menu
   ```
2. **Bare method names**, typically snake_case identifiers, e.g. `_expiratation_date_get`

Parsing rules:
- Detect Gherkin keywords (`Given`, `When`, `Then`, `And`/`AND`, `But`) to recognize BDD lines; extract the **quoted tokens** (`"LEFT_MENU"`, `"button"`) as the strongest comparable units, plus normalized step text.
- Detect snake_case / identifier-like tokens as method names.
- `layer_method_score` = overlap of these extracted tokens/identifiers between two tickets (exact identifier match > quoted-token overlap > normalized step-text similarity).
- **When the section is empty or absent for either ticket, the subscore is neutral (skipped, weights renormalized) — never zero.** Punishing tickets for missing an optional field would create false negatives; this field can only add evidence, not subtract it.

### Ticket quality varies
- Old tickets are freeform. The generic extractor is the baseline and must degrade gracefully: empty/short descriptions produce weak-but-valid results, never crashes.
- The template eases extraction but is **not the sole truth** — IDs, method names, and BDD steps can appear anywhere in the description; the generic extractor also runs over templated tickets as a supplement (deduplicated against section-sourced findings).
- Descriptions from ADO exports may contain **HTML** — strip tags before any text processing.
- Treat all metadata as supporting evidence, not guaranteed truth.

## Architecture

Pipeline of small, pure, independently testable modules. Data flows one way:

```
load CSV → clean/normalize → extract signals per ticket → pairwise candidate generation
        → hybrid scoring (with evidence) → rank & threshold → report output
                                   ↓
                    [extension point: LLM final checker — placeholder only]
```

### Modules

1. **`ingest`** — read the CSV export into typed `Ticket` models (Pydantic). Column mapping lives in config (exports change). Validate, report unreadable rows, never silently drop data.
2. **`clean`** — HTML stripping, whitespace/case normalization, encoding fixes. Pure functions `str -> str`.
3. **`signals`** — per-ticket extraction (template-aware first, generic fallback/supplement), output is a typed `TicketSignals` model:
   - task type (`implementation | refactor | unknown`)
   - canonical TMS references (with source: `link` / `text` / `template_section`)
   - canonical TRQ references (with source)
   - affected layers and methods (from the template section when present)
   - mentioned test steps
   - other URLs found (recorded, not fetched — see Extension Points)
   - cleaned title/description text for lexical (and optional embedding) scoring
   - which extractor(s) produced the signals
4. **`candidates`** — pair generation. Do not score all O(n²) pairs blindly at scale: bucket first (shared TMS/TRQ always paired; same task type; cheap lexical prefilter). For MVP dataset sizes full pairwise is acceptable, but keep it behind this module so blocking can be added without touching scoring.
5. **`scoring`** — the deterministic hybrid score. Each signal returns `(subscore: float in [0,1], evidence: list[str])`. Default shape (weights from config):

   ```
   final = w_id        * id_overlap_score        # shared TMS/TRQ (dominant)
         + w_layer     * layer_method_score      # BDD-step / method-name overlap; skipped (neutral) when absent
         + w_type      * task_type_score         # same type = 1.0, cross-type = partial, unknown = neutral
         + w_steps     * step_overlap_score
         + w_lexical   * lexical_score           # TF-IDF / token-set similarity on cleaned text
         + w_semantic  * semantic_score          # OPTIONAL local embedding cosine sim — see below
   ```

   Plus the shared-ID hard override from Domain Knowledge. Weights renormalize automatically over enabled signals. Classification bands (config): `possible_duplicate`, `possibly_related`, `not_shown`.

   **Structural gate (owner, 2026-07-19):** text similarity and task type are supporting evidence only — a pair may be surfaced only when at least one structural signal (shared items, shared identifiers/tokens, shared steps) agrees. Blank and copy-pasted template tickets otherwise produce meaningless perfect text matches ("text similarity 1.00" on unfilled templates). Bodies below a minimum token count are excluded from lexical scoring entirely; identical-after-cleaning texts carry an explicit copy-paste warning in their evidence. Config: `scoring.require_structural_evidence`, `scoring.lexical.min_tokens`.

   **Optional local-embedding signal** (`semantic.enabled: false` by default):
   - Purpose: catch paraphrase duplicates with no shared IDs — mainly among old freeform tickets. Secondary to ID/structured signals; do not expect it to carry the score.
   - **Strictly offline**: a local `sentence-transformers` model whose files are downloaded **once, manually, out-of-band** and pointed to via a config path. Load with local-files-only mode; the tool must fail loudly (not download) if files are missing. No hosted/online embedding API, ever — non-negotiable.
   - Only enable it after `duptool eval` shows it improves precision/recall on the labelled pairs versus the deterministic baseline. Keep the deterministic path fully functional without it (torch/sentence-transformers are optional extras, not core dependencies).
   - Embeddings are deterministic for a fixed model + input, so the eval loop stays reproducible.
6. **`report`** — output: a ranked CSV/JSON of pairs and a readable per-ticket summary ("Top candidates for TASK-123: …") with all subscores and evidence strings. No ticket bodies in the report beyond title + IDs unless a config flag enables it.

### Coding conventions
- Python 3.11+, Pydantic models at every module boundary, full type hints.
- Every module: pure functions where possible, unit tests required for: HTML stripping, every ID regex variant, link→ID extraction, canonicalization, task-type classification, each subscore, the hard-override rule, threshold banding.
- One config file; one `Settings` object; no magic numbers in code.
- CLI entry point (e.g. `python -m duptool run --input export.csv --config config.yaml --out results/`). No server, no UI in MVP.
- Dependencies stay minimal and offline-friendly: `pandas`, `pydantic`, `scikit-learn` (TF-IDF) or a small BM25 lib, stdlib `re`/`html`. **No API clients, ever.** `sentence-transformers`/`torch` are an **optional extra** (e.g. `pip install .[semantic]`) used only by the optional embedding signal; the core install and pipeline must work without them.

## Evaluation

- Maintain a labelled pair file: `ticket_a,ticket_b,relationship` with labels `duplicate | related | unrelated`.
- Primary metric: **precision** of `possible_duplicate` band. Also: recall, top-3 accuracy, false-positive rate.
- Provide `python -m duptool eval` that runs the pipeline against the labelled set and prints metrics — this is the referee for all weight/threshold tuning. Build it early; every scoring change is judged by it.
- Thresholds and weights are tuned against real labelled pairs, never assumptions.

## Extension Points (placeholders ONLY — do not implement)

Define narrow interfaces + no-op/disabled default implementations. The MVP ships with all of these OFF and empty.

1. **LLM final checker** — `FinalChecker` protocol: takes a scored candidate pair, returns `(verdict, reasoning)`. Default: `NoOpFinalChecker` (passes everything through). Config: `final_checker.enabled: false`.
2. **ADO live hook** — future: watch ADO for new tasks and warn the creator. Nothing in MVP; just keep `ingest` behind a `TicketSource` interface (CSV is the only implementation) so a live source can slot in.
3. **Screenshot extraction** — future OCR/vision over images in descriptions. Record image references in `TicketSignals`; do nothing with them.
4. **External link resolver** — future: hand URLs found in descriptions to an external tool (script/LLM/agent, implemented separately by the owner) that returns relevant info. Interface: `LinkResolver.resolve(url) -> LinkInfo | None`; default `NoOpLinkResolver`. The core pipeline must work identically with the no-op. **Never fetch URLs in this codebase.**

When implementing the MVP: if a change is only needed for one of these four, don't make it.

## Project Structure

Keep the repository small, flat, and obvious. Target structure (do not add layers, subpackages, or files beyond this without a reason stated to the owner):

```
duptool/
├── CLAUDE.md
├── README.md              # how to install, run, and read the output — kept up to date
├── pyproject.toml         # core deps only; [semantic] optional extra
├── config.yaml            # ALL tunables: weights, thresholds, regexes, labels, flags
├── duptool/
│   ├── __init__.py
│   ├── __main__.py        # CLI: run / eval
│   ├── models.py          # Ticket, TicketSignals, CandidatePair, ScoredPair, Settings
│   ├── ingest.py          # CSV → list[Ticket]
│   ├── clean.py           # HTML strip, normalization (pure str -> str functions)
│   ├── signals.py         # template-aware + generic extraction → TicketSignals
│   ├── candidates.py      # pair generation / blocking
│   ├── scoring.py         # subscores + evidence + final score + banding
│   ├── report.py          # ranked output files + per-ticket summaries
│   └── extensions.py      # ALL placeholder protocols + no-op defaults in ONE file
└── tests/
    ├── test_clean.py
    ├── test_signals.py    # the largest test file — real-world ID/BDD variants
    ├── test_scoring.py
    └── fixtures/          # small anonymized sample tickets + a labelled pair CSV
```

## Instructions to Claude Code — How to Work on This Project

The owner's goals are (a) a clear, lightweight codebase and (b) **learning the code while it is being built**. These shape how you must work, not just what you build:

### Build in checkpoints, not in one shot
- Follow the Development Order below **one step at a time**. After each step: stop, summarize what was built and why, and wait for the owner to review before continuing. Do not build ahead.
- **Mandatory checkpoint after step 2 (`signals`):** stop and have the owner validate ID/BDD extraction against a handful of real tickets before any scoring code exists. Extraction quality determines everything downstream; scoring built on broken extraction is wasted work.
- When the owner asks a question about the code, answer it before writing more code.

### Explain while building
- Before writing a module, state in 2–4 sentences: what it does, what goes in, what comes out, and the one or two design decisions being made (and the alternative that was rejected).
- Comment the *why* of domain rules inline (task types, ID canonicalization, neutral-when-missing scoring) — not the *what* of obvious Python.
- When a design choice is a genuine tradeoff (e.g. TF-IDF vs BM25, blocking strategy), present it briefly and let the owner pick rather than deciding silently.

### Keep it lightweight — default to "no"
- No new dependency without stating why stdlib/pandas/sklearn can't do it.
- No classes where functions suffice; no abstraction with a single implementation (the `extensions.py` placeholders are the deliberate exception).
- No async, no threading, no caching layers, no CLI frameworks beyond `argparse`, no logging frameworks beyond stdlib `logging`.
- Prefer a slightly repetitive, readable 20 lines over a clever 5. The owner must be able to read every file top to bottom and follow it.
- If a file grows past ~300 lines, that is a smell — flag it rather than silently splitting into a deeper hierarchy.

### Testing rhythm
- Write tests alongside each module (not deferred to the end). `test_signals.py` gets the most attention: every ID format variant, link→ID extraction, BDD quoted-token extraction, method-name detection, missing-section behavior.
- Every test should be readable as documentation of a rule — name tests after the rule they prove (`test_bare_number_and_link_to_same_tms_are_equal`).

## Development Order

1. `models.py` + `ingest.py` + `clean.py`, with tests. **Checkpoint: owner reviews the data models.**
2. `signals.py` — template-aware label parsing + generic extractor (IDs from links and text, canonicalization, task type, layer/method BDD parsing, steps). **Mandatory checkpoint: owner validates extraction on real tickets before proceeding.**
3. `scoring.py` (deterministic signals only) with evidence strings + config-driven weights. **Checkpoint.**
4. `candidates.py` + `report.py`. **Checkpoint: owner reviews a real output report.**
5. `eval` command + labelled pair workflow.
6. Tune weights/thresholds against labelled data (owner-driven, using `eval`).
7. Only then: try the optional local embedding signal and keep it only if `eval` shows a clear improvement.

Keep each step small and understandable — the owner intends to read and fully understand the foundation before anything is added on top. Prefer clarity over cleverness.

## Known Risks

- Same wording ≠ same task; same TMS across task types may be related, not duplicate → conservative wording, classification bands.
- Inconsistent ID formats → tolerant, config-driven regex; inventory real formats from ~100 sample tickets before trusting extraction.
- Empty/short descriptions → lean on IDs; degrade gracefully.
- User trust is fragile → few, strong, well-explained suggestions beat many weak ones.
