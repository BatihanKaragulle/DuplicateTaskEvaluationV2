# duptool

Offline, read-only duplicate-task suggestion tool for Azure DevOps CSV
exports. Reads a CSV of tasks, extracts structured signals (TMS/TRQ
references, task type, BDD steps, method names), scores ticket pairs with a
deterministic formula, and reports likely duplicates / related tickets with
human-readable evidence. It never writes back to ADO and makes **no network
calls of any kind** — see [CLAUDE.md](CLAUDE.md) for the full design rules.

For a detailed walk-through of every module, function, and design decision,
see [GUIDE.md](GUIDE.md).

## Status

Steps 1-5 of the development order are built: the full pipeline runs end to
end (ingest -> signals -> candidates -> scoring -> report) via the `run`
command, and `eval` scores the pipeline against a labelled pair file. Next:
owner-driven weight/threshold tuning against real labelled data (step 6).

## Install (development)

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

Alternatively, [requirements.txt](requirements.txt) pins the exact tested
versions: `pip install -r requirements.txt` followed by `pip install -e .`

## Run

```powershell
python -m duptool run --input export.csv --config config.yaml --out results/
```

Writes three files to `--out`:

- `pairs.csv` — one row per surfaced pair: final score, band, per-signal
  subscores, evidence
- `pairs.json` — the same pairs with full subscore/weight/evidence detail
- `summary.txt` — per-ticket "Top candidates for ..." sections, human-first

Reports contain ticket IDs and titles only, never descriptions. Wording is
always "possible duplicate" / "possibly related" — these are suggestions
for a human to judge, not verdicts.

## Evaluate against labelled pairs

Maintain a CSV of human-labelled pairs — this file referees ALL weight and
threshold tuning:

```csv
ticket_a,ticket_b,relationship
TASK-101,TASK-102,duplicate
TASK-101,TASK-103,unrelated
```

(`relationship` is one of `duplicate`, `related`, `unrelated`.)

```powershell
python -m duptool eval --input export.csv --labels labels.csv --config config.yaml
```

Prints a predicted-band vs. true-label matrix plus: precision of the
possible_duplicate band (the primary metric), duplicate recall, top-3
accuracy, and unrelated false-positive rates. Never tune config weights or
thresholds without checking this before and after.

## Run the tests

```powershell
python -m pytest tests -q
```

## Configuration

All tunables live in [config.yaml](config.yaml) — column mapping today;
weights, thresholds, and regex patterns as the pipeline grows. The file is
validated strictly: unknown/misspelled keys are rejected at load time.
