# Jev Shadow Agent Guide

## Environment

- Windows with PowerShell; run commands directly through the configured shell.
- Use `.venv\Scripts\python.exe` for project commands when the local virtual environment is present.
- Use `src.`-prefixed absolute imports in new Python code.
- Run `.venv\Scripts\ruff.exe check .` from the repository root for lint.
- For Python changes, run `.venv\Scripts\pyright.exe <changed Python files>` from the repository root and resolve all reported errors.
- Run focused tests with `.venv\Scripts\python.exe -m pytest <test files> -q`.

## Critical Boundaries

- This repository is research-only and shadow-only. It must not authorize live trading or change NiftyZoning execution.
- Deterministic NiftyZoning risk, sizing, reservation, execution, and exit controls remain authoritative.
- Jev responses are advisory observations. They must never increase quantity, bypass a deterministic rejection, or control exits or emergency actions.
- Preserve the existing chronological research folds and locked holdout safeguards. Do not introduce or change validation folds without explicit confirmation.
- The NiftyZoning databases remain owned by the NiftyZoning checkout. Open them read-only through `NIFTYZONING_ROOT`, `NIFTYZONING_SLIM_DB`, or `NIFTYZONING_OPTIONS_DB`; never copy, migrate, or write to them from this repository.
- Request contexts must contain only point-in-time information available at the candidate decision time. Never send future bars, settlement results, realized P&L, or exit information to Jev.
- Context hashes identify exact payloads for reproducibility and deduplication. They must not be used to infer or retrieve similar historical instances.

## Code And Data Practices

- Keep the provider client, context builder, convergence rules, and artifact persistence behind small testable boundaries.
- Persist complete Jev choices, probabilities, confidence, latency, provider status, and errors for audit.
- Treat provider failure or malformed responses as recorded shadow outcomes; do not silently convert them into trading decisions.
- Do not commit API keys, `.env` files, databases, generated shadow output, or unredacted account data.
- Add focused tests for request/response validation, point-in-time field boundaries, read-only database access, deterministic convergence, and artifact persistence.
- Do not run the entire test suite by default; run tests relevant to changed files.
- Use `apply_patch` for manual edits and patch the smallest stable hunk.
- Preserve unrelated working-tree changes. Do not use destructive Git commands.

## Repository Boundary

- The adapter that creates point-in-time contexts from NiftyZoning belongs here only if it remains read-only and behavior-neutral.
- Do not duplicate NiftyZoning strategy, risk, sizing, portfolio, or execution implementations.
- Keep generated experiment artifacts under ignored output directories and keep canonical NiftyZoning artifacts unchanged.
