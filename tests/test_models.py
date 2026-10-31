import os

from jev_shadow.adapter import context_from_candidate, orderflow_audit_records, orderflow_contexts
from jev_shadow.analysis import analyze_records, render_markdown, render_terminal
from jev_shadow.cli import main
from jev_shadow.config import ShadowConfig, load_dotenv
from jev_shadow.models import context_hash, converge
from jev_shadow.runner import run_jsonl


def test_context_hash_is_order_independent() -> None:
    assert context_hash({"b": 2, "a": 1}) == context_hash({"a": 1, "b": 2})


def test_converge_rejects_unfavorable_trade_assessment() -> None:
    response = {
        "trade_assessment": {"choice": "not_worth_taking", "confidence": 0.9},
        "signal_alignment": {"choice": "aligned", "confidence": 0.9},
        "market_regime": {"choice": "favorable", "confidence": 0.9},
    }
    assert converge(response) == "would_reject"


def test_converge_reviews_low_confidence() -> None:
    response = {
        "trade_assessment": {"choice": "worth_taking", "confidence": 0.9},
        "signal_alignment": {"choice": "aligned", "confidence": 0.9},
        "market_regime": {"choice": "neutral", "confidence": 0.5},
    }
    assert converge(response) == "would_review"


def test_converge_reviews_adverse_regime_even_with_favorable_assessment() -> None:
    response = {
        "trade_assessment": {"choice": "worth_taking", "confidence": 0.9},
        "signal_alignment": {"choice": "aligned", "confidence": 0.9},
        "market_regime": {"choice": "adverse", "confidence": 0.9},
    }
    assert converge(response) == "would_review"


def test_context_adapter_excludes_future_outcomes() -> None:
    context = context_from_candidate({
        "candidate_id": "c1",
        "date": "2026-10-31",
        "time": "10:00",
        "score": 2,
        "required_inputs": {"price": 25000},
        "exit_time": "10:30",
        "exit_reason": "stop",
        "net_pnl_rs": -100,
    })

    assert context == {
        "candidate_id": "c1",
        "date": "2026-10-31",
        "time": "10:00",
        "score": 2,
        "required_inputs": {"price": 25000},
    }


def test_orderflow_adapter_uses_causal_trace_at_oracle_times_only() -> None:
    payload = {
        "date": "2026-09-29",
        "contract_identity": ["NIFTY", "CONTINUOUS", "", "I"],
        "timeframes": {
            "1m": {
                "trace": [{
                    "time": "09:29",
                     "bar": {"Close": 22642.0},
                     "candle_features": {"clv": -0.4},
                     "volume_rank_vs_prior_20": 2,
                     "volume_percentile_vs_prior_20": 0.8,
                     "causal_features": {"causal_cutoff": "09:29"},
                     "causal_locations": {"levels": {"PD_LOW": 22600.0}, "touched": ["PD_LOW"]},
                    "response": {"next_15_bars": {"close_change": -15}},
                }],
                "oracle_trades": [{
                    "signal_time": "09:29",
                    "direction": "short",
                    "oracle_exit_time": "15:00",
                }],
            },
        },
    }

    contexts = orderflow_contexts(payload)

    assert len(contexts) == 1
    assert contexts[0]["time"] == "09:29"
    assert contexts[0]["market"]["bar"] == {"Close": 22642.0}
    assert contexts[0]["market"]["volume_rank_vs_prior_20"] == 2
    assert contexts[0]["causal_locations"]["touched"] == ["PD_LOW"]
    assert contexts[0]["source"]["feature_window"] == "09:15 <= bar time <= signal time"
    assert "volume_rank_in_session" not in contexts[0]["market"]
    assert "response" not in contexts[0]
    assert "direction" not in contexts[0]
    assert "oracle_exit_time" not in contexts[0]

    audit = orderflow_audit_records(payload)
    assert audit[0]["direction"] == "short"
    assert audit[0]["oracle_exit_time"] == "15:00"


def test_multitimeframe_context_excludes_future_and_marks_missing_views() -> None:
    payload = {
        "date": "2026-09-29", "timezone": "Asia/Kolkata",
        "contract_identity": ["NIFTY"],
        "timeframes": {
            "1m": {
                "trace": [{"time": "09:22", "bar": {"Time": "09:22", "Close": 100}}],
                "oracle_trades": [{"signal_time": "09:22", "cross_timeframe_features": {"trend_alignment": 1}}],
            },
            "5m": {
                "trace": [
                    {"time": "09:20", "bar": {"Time": "09:20", "Close": 99}},
                    {"time": "09:25", "bar": {"Time": "09:25", "Close": 101}},
                ],
            },
            "15m": {"trace": []},
        },
    }

    context = orderflow_contexts(payload)[0]

    assert context["context_schema"] == "jev-shadow.context.v2"
    assert context["decision_time"].endswith("+05:30")
    assert context["timeframes"]["5m"]["market"]["bar"]["Close"] == 99
    assert context["timeframes"]["15m"]["source"]["status"] == "missing"
    assert context["cross_timeframe_features"]["provenance"]["source_timeframes"] == ["1m", "5m", "15m"]


def test_multitimeframe_partial_bar_is_not_presented_as_closed() -> None:
    payload = {
        "date": "2026-09-29", "timezone": "UTC", "contract_identity": ["NIFTY"],
        "timeframes": {
            "1m": {"trace": [{"time": "09:22", "bar": {"Time": "09:22"}}],
                    "oracle_trades": [{"signal_time": "09:22"}]},
            "5m": {"trace": [{"time": "09:25", "partial_bar": True}]},
        },
    }

    context = orderflow_contexts(payload)[0]

    assert context["timeframes"]["5m"]["bar_status"] == "missing"
    assert context["timeframes"]["5m"]["source"]["status"] == "missing"


def test_multitimeframe_hash_changes_when_secondary_view_changes() -> None:
    payload = {
        "date": "2026-09-29", "contract_identity": ["NIFTY"],
        "timeframes": {
            "1m": {"trace": [{"time": "09:22"}], "oracle_trades": [{"signal_time": "09:22"}]},
            "5m": {"trace": [{"time": "09:20", "bar": {"Close": 99}}]},
        },
    }
    first = orderflow_contexts(payload)[0]
    payload["timeframes"]["5m"]["trace"][0]["bar"]["Close"] = 100
    second = orderflow_contexts(payload)[0]
    assert context_hash(first) != context_hash(second)


def test_load_dotenv_preserves_environment_and_parses_values(tmp_path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# comment\n"
        "NIFTYZONING_ROOT='C:/NiftyZoning'\n"
        "export JEV_MODEL=typesafe/jev-router\n"
        "EXISTING=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXISTING", "from-environment")

    load_dotenv(dotenv)

    assert os.environ["NIFTYZONING_ROOT"] == "C:/NiftyZoning"
    assert os.environ["JEV_MODEL"] == "typesafe/jev-router"
    assert os.environ["EXISTING"] == "from-environment"


def test_build_contexts_does_not_require_shared_database_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NIFTYZONING_ROOT", raising=False)
    input_path = tmp_path / "candidate.json"
    output_path = tmp_path / "contexts.jsonl"
    input_path.write_text('{"records":[{"candidate_id":"c1","score":1}]}', encoding="utf-8")

    assert main([
        "build-contexts", "--input", str(input_path), "--output", str(output_path),
    ]) == 0
    assert '"candidate_id":"c1"' in output_path.read_text(encoding="utf-8")


def test_analysis_summarizes_results_and_failures() -> None:
    summary = analyze_records([
        {
            "candidate_id": "c1",
            "context": {"timeframe": "1m"},
            "status": "ok",
            "shadow_convergence": "would_pass",
            "latency_ms": 1000,
            "response": {"trade_assessment": {"choice": "worth_taking"}},
        },
        {
            "candidate_id": "c2",
            "context": {"timeframe": "5m"},
            "status": "error",
            "shadow_convergence": "unavailable",
            "latency_ms": 3000,
            "response": None,
            "error": "provider failed",
        },
    ])

    report = render_markdown(summary, "results.jsonl")

    assert summary["successful"] == 1
    assert summary["failed"] == 1
    assert summary["dimensions"] == {"trade_assessment": {"worth_taking": 1}}
    assert "Contexts processed: 2" in report
    assert "provider failed" in report


def test_analysis_compares_direction_against_separate_audit() -> None:
    summary = analyze_records([
        {
            "candidate_id": "c1",
            "status": "ok",
            "shadow_convergence": "would_pass",
            "response": {"directional_view": {"choice": "short"}},
        },
    ], audit_records=[{"candidate_id": "c1", "direction": "short"}])
    assert summary["oracle_comparison"]["direction_agreement_rate"] == 1.0


def test_terminal_analysis_uses_tables() -> None:
    summary = analyze_records([
        {
            "candidate_id": "c1",
            "status": "ok",
            "shadow_convergence": "would_pass",
            "response": {"trade_assessment": {"choice": "worth_taking"}},
        },
    ])

    report = render_terminal(summary, "results.jsonl")

    assert "Jev Shadow Analysis" in report
    assert "Contexts processed" in report
    assert "trade_assessment" in report
    assert "worth_taking" in report


def test_run_jsonl_rewrites_existing_output(tmp_path, monkeypatch) -> None:
    input_path = tmp_path / "contexts.jsonl"
    output_path = tmp_path / "results.jsonl"
    input_path.write_text('{"candidate_id":"c1"}\n', encoding="utf-8")
    output_path.write_text("stale\n", encoding="utf-8")

    class FakeClient:
        def __init__(self, *args):
            pass

        def evaluate(self, context):
            return {
                "trade_assessment": {"choice": "not_worth_taking", "confidence": 0.9},
                "signal_alignment": {"choice": "aligned", "confidence": 0.9},
                "market_regime": {"choice": "neutral", "confidence": 0.9},
            }

        def close(self):
            pass

    monkeypatch.setattr("jev_shadow.runner.JevClient", FakeClient)
    config = ShadowConfig(tmp_path, tmp_path / "slim.db", tmp_path / "options.db", tmp_path, jev_api_key="key")

    assert run_jsonl(input_path, output_path, config) == 1
    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "stale" not in lines[0]
