# Jev Shadow Design

## Purpose

Jev Shadow is a research-only integration that evaluates Jev as a read-only
observer of NiftyZoning pre-trade context. It produces reproducible,
append-only observations without changing strategy, risk, sizing, reservation,
execution, or exit behavior.

The system is intentionally split into two stages:

1. An adapter projects an existing NiftyZoning artifact or causal orderflow
   trace into point-in-time JSON contexts.
2. An isolated provider runner sends those contexts to Jev and persists the
   complete shadow result.

Post-hoc oracle and trade outcomes remain separate from the context sent to
Jev.

## Architecture

```text
NiftyZoning artifacts or trace
              |
              v
       jev_shadow.adapter
              |
              | point-in-time contexts.jsonl
              v
       jev_shadow.runner -----> Jev HTTP provider
              |
              | append-only ShadowRecord JSONL
              v
       data/output/jev_shadow
              |
              v
       jev_shadow.analysis
```

The shared NiftyZoning databases are owned by the NiftyZoning checkout. When
used, they are opened read-only through the configured database paths; this
repository does not copy, migrate, or write to them.

## Context construction

`adapter.context_from_candidate` projects only the approved point-in-time
fields from a candidate record. These include candidate identity, timestamp,
direction, eligibility, score inputs, entry and stop basis, sizing diagnostics,
causal features, and feature provenance.

`adapter.orderflow_contexts` uses an oracle signal timestamp only to select the
corresponding observed trace bar. The generated context contains data available
through that bar, including market features, causal locations, and the feature
window. It does not contain oracle direction, entry or exit prices, future bars,
settlement, or realized outcome.

The adapter also creates a separate audit stream. Audit records may contain
oracle direction and outcome metadata for comparison after the shadow run, but
they are never passed to Jev.

## Provider evaluation

`runner.run_jsonl` processes one JSON object per input line. Each context must
contain `candidate_id`. For every request, the runner:

1. Computes a canonical SHA-256 context hash.
2. Sends the unchanged context to the configured Jev client.
3. Validates and deterministically classifies the response with
   `models.converge`.
4. Records success or provider failure, latency, response, error text, and
   request metadata.
5. Appends one JSON object to the output file.

Provider failures and malformed responses are recorded as shadow outcomes.
They are not converted into trading decisions and do not stop already-written
records from remaining auditable.

## Response convergence

Convergence is a local analytical label, not an execution command. A valid
response must contain `signal_alignment`, `market_regime`, and
`trade_assessment`, each with an allowed choice and confidence in the range
`0..1`.

The deterministic rules are:

- `not_worth_taking` produces `would_reject`.
- `worth_taking` with adequate confidence and compatible signal/regime produces
  `would_pass`.
- Low confidence, insufficient evidence, conflicting signal, adverse regime,
  or an otherwise incomplete positive response produces `would_review`.
- Missing or malformed response structure produces `invalid_response`.
- Transport or provider exceptions produce `unavailable` in the runner.

No convergence label can increase quantity, bypass a deterministic rejection,
control an exit, or trigger an emergency action.

## Persistence and reproducibility

Each `ShadowRecord` contains:

- `request_id`, `candidate_id`, and the exact `context_hash`
- the context sent to Jev
- the complete provider response, when available
- status, convergence label, provider, and model
- latency and error details
- optional metadata for the experiment

Records are appended as JSONL so a run can be inspected incrementally and
previous observations are not overwritten. Canonical JSON uses sorted keys and
stable separators before hashing. The hash identifies the exact payload for
reproducibility; it is not a similarity key and must not be used to retrieve
historical contexts.

API keys are read from environment variables and are never written to output
artifacts. Generated contexts, audit files, and results belong under ignored
output directories. The tracked smoke-test fixture contains entry-time fields
only.

## Operational boundaries

- The project is shadow-only and research-only.
- Deterministic NiftyZoning controls remain authoritative.
- Jev cannot alter quantity, reservations, execution, exits, or emergency
  actions.
- Contexts must contain only information available at the candidate decision
  time.
- NiftyZoning databases remain external, read-only inputs.
- Realized outcomes are evaluation data and are not request inputs.

## Extension points

Future adapters may consume NiftyZoning's typed decision stream at the
post-sizing boundary, provided they preserve the same causal field boundary and
write append-only context and audit artifacts. Provider implementations should
remain behind the client boundary, while convergence and persistence should
remain deterministic and independently testable.

## Verification

Focused tests should cover:

- exclusion of post-decision fields from projected contexts
- causal orderflow field and timestamp boundaries
- read-only database configuration
- response validation and convergence rules
- context hashing and append-only artifact persistence

For local verification, use the repository's virtual environment and run the
focused test files, Ruff, and Pyright checks described in `AGENTS.md`.
