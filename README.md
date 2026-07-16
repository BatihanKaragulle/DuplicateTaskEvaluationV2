# duptool

Offline, read-only duplicate-task suggestion tool for Azure DevOps CSV
exports. Reads a CSV of tasks, extracts structured signals (TMS/TRQ
references, task type, BDD steps, method names), scores ticket pairs with a
deterministic formula, and reports likely duplicates / related tickets with
human-readable evidence. It never writes back to ADO and makes **no network
calls of any kind** — see [CLAUDE.md](CLAUDE.md) for the full design rules.

## Status

Under construction, step 2 of the development order: signal extraction
(TMS/TRQ references, template sections, task type, BDD/method parsing) is
built and awaiting validation against real tickets. No CLI yet — the
`run` / `eval` commands arrive in later steps.

## Install (development)

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## Run the tests

```powershell
python -m pytest tests -q
```

## Configuration

All tunables live in [config.yaml](config.yaml) — column mapping today;
weights, thresholds, and regex patterns as the pipeline grows. The file is
validated strictly: unknown/misspelled keys are rejected at load time.
