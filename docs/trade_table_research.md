# Jev Shadow Trade-Table Research

## Objective

This experiment evaluates whether Jev's point-in-time advisory assessment agrees with a small set of completed Nifty trades. Jev is used only as a read-only shadow observer. It does not authorize entries, change quantity, bypass deterministic rejection, or control exits.

## Dataset

The input contains 11 position-intent lifecycle trades:

- 6 trades from 2026-09-25
- 1 trade from 2026-09-28
- 4 trades from 2026-09-29
- All contexts use the `1m` causal market trace and include the corresponding entry-time bar, candle features, volume metrics, delta metrics, VWAP/profile features, causal flow features, and causal locations.
- Each decision is timestamped at the close of the corresponding one-minute bar. The `target_*` fields are same-bar aliases retained by the adapter and are available at that causal cutoff; no later bar is included.

The tracked input is `data/orderflow_context_smoke_test.jsonl`. It contains entry-time information only. Exit time, exit price, reward, R:R, and exit reason are excluded from Jev input and used only for post-hoc comparison.

The Jev output was produced in `data/output/jev_shadow/trade_table_results_fresh.jsonl`. That generated provider-result artifact is intentionally excluded from version control; the summary below is a recorded result of that run and cannot be independently reproduced without rerunning the configured provider.

## Original Trades

The following table combines the original trade ledger with Jev's shadow assessment and the post-hoc realized outcome. Exit, reward, and outcome fields are not part of the Jev input.

The two Jev-related columns are intentionally separate:

- **Jev assessment** is the raw provider response from `trade_assessment.choice`. It describes Jev's qualitative judgment: `worth_taking` or `not_worth_taking`.
- **Convergence** is this repository's deterministic interpretation of the complete response. `would_pass` follows `worth_taking` when the required supporting assessments and confidence checks pass; `would_reject` follows `not_worth_taking`; `would_review` indicates uncertainty, conflict, insufficient evidence, or low confidence.

Convergence is therefore an auditable local classification, not a second independent Jev opinion. In this table, the raw assessments map directly to `would_pass` or `would_reject` because no row required the `would_review` path.

| Date | Location | Side | Setup | Entry | Entry Px | Effective lots | Exit | Exit Px | Reward | R:R | Reason | Jev assessment | Convergence | Realized outcome |
| --- | --- | --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- | --- | --- | --- |
| 2026-09-25 | SESSION_HIGH | SHORT | position-intent-lifecycle-2026-09-25-NIFTY-1m_5m-short-candidate-2 | 2026-09-25T09:37 | 23128.0 | 1 | 2026-09-25T09:56 | 23100.0 | 26.7 | 0.6 | conditional_exit | worth_taking | would_pass | profitable |
| 2026-09-25 | OR_LOW | LONG | position-intent-lifecycle-2026-09-25-NIFTY-1m_5m-long-candidate-5 | 2026-09-25T09:58 | 23100.0 | 1 | 2026-09-25T10:26 | 23134.0 | 34.0 | 0.9 | conditional_exit | not_worth_taking | would_reject | profitable |
| 2026-09-25 | OR_HIGH | SHORT | position-intent-lifecycle-2026-09-25-NIFTY-1m_5m-short-candidate-6 | 2026-09-25T10:27 | 23131.1 | 1 | 2026-09-25T13:02 | 23094.0 | 37.1 | 1.3 | position_reversal | not_worth_taking | would_reject | profitable |
| 2026-09-25 | OR_LOW | LONG | position-intent-lifecycle-2026-09-25-NIFTY-1m_5m-long-candidate-10 | 2026-09-25T13:02 | 23094.0 | 1 | 2026-09-25T14:04 | 23182.8 | 88.8 | 2.9 | trail_stop | not_worth_taking | would_reject | profitable |
| 2026-09-25 | SESSION_HIGH | SHORT | position-intent-lifecycle-2026-09-25-NIFTY-1m_5m-short-candidate-15 | 2026-09-25T14:12 | 23188.0 | 0.7 | 2026-09-25T14:27 | 23187.4 | 0.4 | 0.0 | position_reversal | not_worth_taking | would_reject | profitable |
| 2026-09-25 | PD_LOW | LONG | position-intent-lifecycle-2026-09-25-NIFTY-1m_5m-long-candidate-16 | 2026-09-25T14:27 | 23191.4 | 1 | 2026-09-25T14:52 | 23170.0 | -21.4 | -0.9 | hard_stop | not_worth_taking | would_reject | loss-making |
| 2026-09-28 | OR_LOW | SHORT | position-intent-lifecycle-2026-09-28-NIFTY-1m_5m-short-candidate-1 | 2026-09-28T09:26 | 22959.4 | 1 | 2026-09-28T15:10 | 22817.2 | 130.5 | 5.9 | session_close | worth_taking | would_pass | profitable |
| 2026-09-29 | OR_LOW | SHORT | position-intent-lifecycle-2026-09-29-NIFTY-1m_5m-short-candidate-1 | 2026-09-29T09:22 | 22684.0 | 1 | 2026-09-29T11:11 | 22635.0 | 93.2 | 4.5 | vwap_stop | not_worth_taking | would_reject | profitable |
| 2026-09-29 | OR_LOW | LONG | position-intent-lifecycle-2026-09-29-NIFTY-1m_5m-long-candidate-7 | 2026-09-29T11:22 | 22704.9 | 1 | 2026-09-29T12:16 | 22750.0 | 45.1 | 1.8 | conditional_exit | not_worth_taking | would_reject | profitable |
| 2026-09-29 | SESSION_HIGH | SHORT | position-intent-lifecycle-2026-09-29-NIFTY-1m_5m-short-candidate-8 | 2026-09-29T12:17 | 22748.2 | 1 | 2026-09-29T12:37 | 22667.6 | 120.7 | 4.2 | trail_stop | not_worth_taking | would_reject | profitable |
| 2026-09-29 | OR_LOW | SHORT | position-intent-lifecycle-2026-09-29-NIFTY-1m_5m-short-candidate-11 | 2026-09-29T13:27 | 22666.6 | 1 | 2026-09-29T13:28 | 22674.5 | -7.9 | -0.2 | vwap_stop | not_worth_taking | would_reject | loss-making |

## Method

For each trade, the runner:

1. Loads one point-in-time JSON context.
2. Sends the context to the configured Jev provider.
3. Persists the complete response, confidence, rationale, latency, and convergence label.
4. Classifies `worth_taking` as `would_pass` and `not_worth_taking` as `would_reject`.
5. Compares that classification with the supplied realized reward only after the shadow run completes.

The experiment ran 11 requests successfully. Mean latency was 10.68 seconds, with a range of 7.07 to 16.50 seconds.

## Results

The complete set produced `+547.2` reward. The classification gap is summarized below; the detailed trade table above is the source for individual rows.

### Gap between Jev classification and realized outcomes

The main gap is selectivity versus realized opportunity. Treating `profitable` as the positive outcome, the 11-row confusion matrix is:

- True positives: 2 accepted profitable trades.
- False negatives: 7 rejected profitable trades.
- True negatives: 2 rejected loss-making trades.
- False positives: 0 accepted loss-making trades.

This gives Jev a 100% positive predictive value in this sample, but only 22.2% recall for profitable trades (`2 / 9`). A Jev-based filter would have rejected 77.8% of the profitable trades and excluded `+390.0` of realized reward, compared with `+157.2` from accepted trades. The rejected group therefore contained more realized reward than the accepted group, even though it also contained both observed losses.

## Jev's assessment pattern

Across all 11 trades:

- Signal alignment was `mixed` for 9 and `aligned` for 2.
- Market regime was `neutral` for 9 and `favorable` for 2.
- Directional view was neutral for 6, short for 3, and long for 2.
- Trade assessment was `not_worth_taking` for 9 and `worth_taking` for 2.

The rejection rationales commonly cited mixed orderflow, low or non-confirming volume, conflicting cumulative delta versus current-bar price action, nearby support or resistance, and late-session timing.

## Rerun On 2026-11-05

The documented trade-table command was rerun against the same 11 entry-time contexts. The output was written to the ignored artifact `data/output/jev_shadow/trade_table_results_rerun.jsonl` and analyzed immediately afterward.

- 11 contexts were submitted.
- 10 responses were valid and 1 request timed out (`short-candidate-11`).
- The valid responses produced 3 `would_pass` and 7 `would_reject` classifications.
- Mean latency was 11.07 seconds, ranging from 7.52 to 16.88 seconds.
- The accepted rows were candidates 2, 1 on 2026-09-28, and 7 on 2026-09-29. All three were profitable in the supplied post-hoc ledger, for `+202.3` reward.
- Of the seven valid rejected rows, six were profitable and one was loss-making, for `+352.8` reward. The timed-out row was also loss-making (`-7.9`), but has no Jev classification.

Among the 10 classified rows this rerun therefore has 3 true positives, 6 false negatives, 1 true negative, and 0 false positives. The apparent improvement over the earlier run is not evidence of model improvement: the same provider request returned a different assessment distribution, and one request failed operationally.

## What The Experiment Is Actually Testing

The oracle trade list is not a representative set of opportunities. It is a post-hoc, strategy-selected set of entries that were allowed to complete under deterministic entry, position-management, and exit rules. Nine of the 11 rows were profitable, but two were loss-making. This means the experiment cannot ask Jev to identify “all profitable trades” without giving it the candidate universe, the counterfactual rejected candidates, and a predeclared outcome horizon.

The labels also measure different things:

- Jev answers whether the point-in-time evidence is sufficiently favorable to take the opportunity.
- The ledger labels the result after future price path, stop/target behavior, reversals, trailing exits, session close, and execution rules have occurred.

A trade can therefore be a reasonable entry despite weak immediate confirmation and still make money through later mean reversion or trend continuation. Conversely, a favorable-looking entry can lose. Jev is not shown the future path, the exit policy, the stop distance, the target, or the opportunity's counterfactual alternatives, so realized reward is not an observable target at decision time.

The individual rationales illustrate this causal mismatch. Jev rejected the profitable 2026-09-29 short at 09:22 because the entry bar showed positive CLV and positive bar-delta proxy, rejected the profitable 12:17 short because volume was only 0.24x its 20-bar average, and rejected the profitable 13:02 long because the entry bar was bearish and lacked stabilization. Those are defensible entry-time objections; later profitability does not make them errors unless the research question is explicitly “would the eventual trade have made money?”

The main challenge is consequently label and selection bias, not simply Jev's inability to read orderflow:

- Survivorship and oracle selection remove the ordinary losing and rejected opportunities from the input set.
- Conditioning on completed trades makes recall against realized profitability look like the primary objective, although a real filter also needs abstention quality, costs, risk, and opportunity coverage.
- The causal features are noisy proxies (`delta_proxy`, CLV, relative volume) and often conflict at the exact entry bar; the oracle may rely on location and subsequent management behavior that are not encoded as a predictive label.
- The sample has only 11 rows across three sessions, so one provider response changes the percentages materially.
- `temperature=0` does not make this an independently reproducible benchmark when the configured provider can vary responses or availability; the rerun changed 2 accepts to 3 and introduced a timeout.

The appropriate next experiment is a chronological candidate-level dataset containing every eligible entry, including deterministic rejects and non-trades, with labels defined prospectively (for example, fixed-horizon return, maximum adverse excursion, maximum favorable excursion, and costs). Jev should then be evaluated for calibration, selective risk/coverage, and incremental value over the deterministic baseline, while keeping all execution and risk controls outside Jev.

## Latest Multi-Timeframe Experiment

The follow-up experiment used the generated `data/output/orderflow_contexts_20260929.jsonl` input. It contains 10 point-in-time contexts. Each context embeds `1m`, `3m`, and `5m` causal views, with `1m` as the primary timeframe. Jev received the complete multi-timeframe context for each request; the analyzer's timeframe count reports only the primary `1m` value.

For each context, the adapter selected the latest trace available at the decision time for every timeframe. No later bars, exits, realized P&L, or oracle outcomes were included in the Jev request. The runner rewrote the result file before processing, preventing records from earlier runs from being counted in this experiment.

The output was rewritten before the run at `data/output/jev_shadow/orderflow_results_20260929.jsonl`, so it contains exactly the 10 records from this run:

- 10 records processed
- 9 successful responses and 1 failed response
- 3 `would_pass`, 6 `would_reject`, and 1 `unavailable`
- Mean latency: 12.93 seconds, ranging from 5.37 to 23.71 seconds
- Failed candidate: `orderflow-2026-09-29-1m-11:26-7`
- Failure: the provider response was missing the required `signal_alignment` assessment

The failed response is persisted as an unavailable shadow outcome and does not authorize or alter any deterministic trading decision.

### Multi-Timeframe Limitations

- This rerun has only 10 contexts from one trading day and is not an independent validation sample.
- The 10 contexts were selected from an oracle-driven research trace; selection bias and post-hoc opportunity definition remain possible.
- One provider response was malformed, so only 9 contexts produced complete Jev assessments.
- The rerun has no realized-outcome join and therefore does not support precision, recall, reward, or profitability claims.
- It does not compare multi-timeframe Jev responses against a single-timeframe baseline, measure incremental value from `3m` or `5m`, or assess calibration.
- Provider latency ranged up to 23.71 seconds, and operational impact was not evaluated.
- The result reflects one configured provider/model run and may vary with provider response stochasticity or availability.

## Interpretation

In the original recorded run, Jev accepted two profitable trades and rejected seven trades that subsequently made money. In the 2026-11-05 rerun, it accepted three profitable trades and rejected six profitable trades. Across both runs, `would_reject` is not a prediction of loss; it means the point-in-time evidence did not justify taking the trade.

Neither run establishes profitability, predictive accuracy, or a deployable trading rule. The sample is small and externally supplied, and no baseline comparison, calibration analysis, randomized sample, counterfactual execution analysis, or Jev-controlled execution, sizing, or exit behavior is included. Latency, timeout handling, and provider-response repeatability are recorded but not evaluated for operational impact. Jev should therefore be treated as a conservative evidence filter, not a demonstrated outcome predictor.

## Reproducibility

Run the shadow evaluation with:

```powershell
.venv\Scripts\python.exe -m jev_shadow.cli run-jsonl `
  --input data\orderflow_context_smoke_test.jsonl `
  --output data\output\jev_shadow\trade_table_results_fresh.jsonl
```

Then inspect the provider responses with:

```powershell
.venv\Scripts\python.exe -m jev_shadow.cli analyze-results `
  --input data\output\jev_shadow\trade_table_results_fresh.jsonl
```

To reproduce the dated rerun recorded above, use a separate ignored output file:

```powershell
.venv\Scripts\python.exe -m jev_shadow.cli run-jsonl `
  --input data\orderflow_context_smoke_test.jsonl `
  --output data\output\jev_shadow\trade_table_results_rerun.jsonl

.venv\Scripts\python.exe -m jev_shadow.cli analyze-results `
  --input data\output\jev_shadow\trade_table_results_rerun.jsonl
```

To reproduce the latest multi-timeframe experiment, first build the causal contexts and then run Jev:

```powershell
.venv\Scripts\python.exe -m jev_shadow.cli build-orderflow-contexts `
  --niftyzoning-root 'C:\Users\agraw\Python\NiftyZoning' `
  --date 20260929 `
  --output data\output\orderflow_contexts_20260929.jsonl `
  --audit-output data\output\orderflow_oracle_audit_20260929.jsonl

.venv\Scripts\python.exe -m jev_shadow.cli run-jsonl `
  --input data\output\orderflow_contexts_20260929.jsonl `
  --output data\output\jev_shadow\orderflow_results_20260929.jsonl

.venv\Scripts\python.exe -m jev_shadow.cli analyze-results `
  --input data\output\jev_shadow\orderflow_results_20260929.jsonl
```
