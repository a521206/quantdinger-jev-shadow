# NiftyZoning Jev Shadow

Read-only research integration for evaluating QuantDinger/TypeSafe Jev against NiftyZoning pre-trade decisions.

This repository does not modify NiftyZoning strategy, risk, sizing, reservations, execution, or exits. Jev results are persisted as shadow observations only.

## Shared databases

The database remains owned by the NiftyZoning checkout. Set `NIFTYZONING_ROOT` to that checkout. By default this repository opens:

- `data/db/nifty_slim.duckdb` for market/features data
- `data/db/nifty.db` for options data

Both connections use DuckDB read-only mode. No database files are copied into this repository.

```powershell
$env:NIFTYZONING_ROOT = 'C:\Users\agraw\Python\NiftyZoning'
$env:JEV_SHADOW_OUTPUT = 'data/output/jev_shadow'
.venv\Scripts\python.exe -m pip install -e '.[dev]'
jev-shadow db-info
```

Optional overrides are `NIFTYZONING_SLIM_DB`, `NIFTYZONING_OPTIONS_DB`, `JEV_BASE_URL`, `JEV_MODEL`, and `JEV_TIMEOUT_SECONDS`. The default provider is OpenRouter at `https://openrouter.ai/api/v1`, using TypeSafe's `typesafe/jev-router` model.

`db-info` and `run-jsonl` validate the shared database configuration before running. The context-building commands do not require `NIFTYZONING_ROOT` when given local input artifacts, so they can be used independently of the NiftyZoning checkout.

To run the isolated client, prepare one point-in-time context JSON object per line. Each object must contain `candidate_id`; the runner sends the object as Jev state and rewrites the requested result file for each run:

```powershell
$env:OPENROUTER_API_KEY = '<local secret>'
$env:PYTHONPATH = 'src'
python -m jev_shadow.cli run-jsonl `
  --input .\data\output\contexts.jsonl `
  --output .\data\output\jev_shadow\jev_shadow_v1.jsonl
```

The NiftyZoning adapter that creates `contexts.jsonl` is intentionally separate from this provider runner so the canonical replay remains unchanged.

To project a NiftyZoning `candidate_trace.json` artifact into point-in-time contexts:

```powershell
python -m jev_shadow.cli build-contexts `
  --input 'C:\path\to\run\candidate_trace.json' `
  --output .\data\output\contexts.jsonl
```

The adapter excludes exit time, exit reason, settlement, and realized P&L fields even when they are present in the source artifact.

For the orderflow oracle timestamps, run NiftyZoning's research trace and build one causal context per identified opportunity:

```powershell
python -m jev_shadow.cli build-orderflow-contexts `
  --niftyzoning-root 'C:\Users\agraw\Python\NiftyZoning' `
  --date 20260929 `
  --output .\data\output\orderflow_contexts_20260929.jsonl `
  --audit-output .\data\output\orderflow_oracle_audit_20260929.jsonl
```

This uses oracle timestamps only to select which observed bars to inspect. Oracle direction, entry/exit prices, future bars, and outcomes are not sent to Jev.

An existing full-day trace can be projected without rerunning NiftyZoning:

```powershell
python -m jev_shadow.cli build-orderflow-contexts `
  --trace 'C:\path\to\orderflow-full-day-trace.json' `
  --date 20260929 `
  --output .\data\output\orderflow_contexts_20260929.jsonl `
  --audit-output .\data\output\orderflow_oracle_audit_20260929.jsonl
```

The audit file contains oracle direction and outcome metadata for post-hoc comparison only; it is never sent to Jev. Compare it with Jev results using:

```powershell
python -m jev_shadow.cli analyze-results `
  --input .\data\output\jev_shadow\orderflow_results_20260929.jsonl `
  --oracle-audit .\data\output\orderflow_oracle_audit_20260929.jsonl
```

Generated result, audit, and full-day context JSONL files should be kept under ignored output directories or otherwise excluded from commits. `data/orderflow_context_smoke_test.jsonl` is a tracked 11-trade smoke-test fixture containing entry-time fields only.

## Boundaries

- Jev receives only point-in-time candidate context supplied by a future adapter.
- Jev cannot change deterministic decisions or quantities.
- Exits and emergency actions are outside this project.
- Request context hashes identify exact payloads for reproducibility; they are not similarity keys.
- API keys are read from the environment and never written to artifacts.

## Status

The initial slice provides shared-database validation, typed shadow records, deterministic convergence, and the isolated Jev HTTP adapter. The next adapter should consume NiftyZoning's typed decision stream at the post-sizing boundary and write append-only JSONL records.
