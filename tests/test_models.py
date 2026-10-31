import os

from jev_shadow.adapter import context_from_candidate, orderflow_contexts
from jev_shadow.analysis import analyze_records, render_markdown, render_terminal
from jev_shadow.cli import main
from jev_shadow.config import load_dotenv
from jev_shadow.models import context_hash, converge


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
                    "causal_features": {"causal_cutoff": "09:29"},
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
    assert "response" not in contexts[0]
    assert "direction" not in contexts[0]
    assert "oracle_exit_time" not in contexts[0]


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
