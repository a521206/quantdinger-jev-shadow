"""Command-line checks for the Jev shadow repository."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .adapter import (
    run_orderflow_trace,
    write_contexts,
    write_orderflow_audit,
    write_orderflow_contexts,
)
from .analysis import analyze_records, load_records, render_terminal
from .config import ShadowConfig
from .database import table_names
from .runner import run_jsonl


def _add_rich_premium_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "rich-premium", help="research-only rich-premium straddle study on NSE/BSE daily files"
    )
    actions = parser.add_subparsers(dest="rich_premium_command", required=True)
    commands = {
        "fetch": "download index history, India VIX and F&O bhavcopies",
        "build": "build daily option, futures and index panels from the raw files",
        "backtest": "score the frozen rule per split and write the cycle tables",
        "track": "update the forward paper ledger, or replay it over history",
    }
    for name, help_text in commands.items():
        sub = actions.add_parser(name, help=help_text)
        sub.add_argument(
            "--data-dir",
            type=Path,
            help="research layout: raw files under <dir>/raw, panels directly in <dir>",
        )
        sub.add_argument("--raw-dir", type=Path, help="override the raw download directory")
        sub.add_argument("--panel-dir", type=Path, help="override the parquet panel directory")
        if name == "fetch":
            sub.add_argument("--start", default="2012-01-01", help="first trading day, YYYY-MM-DD")
            sub.add_argument("--retry-poi", action="store_true")
        if name == "backtest":
            sub.add_argument("--output-dir", type=Path)
            sub.add_argument("--index", action="append", choices=["nifty", "banknifty", "sensex"])
        if name == "track":
            sub.add_argument("--forward-dir", type=Path)
            sub.add_argument("--replay", action="store_true", help="replay 2025-01-01..2026-11-23")
            sub.add_argument(
                "--lane-dir", type=Path, help="compare the replay with these cycle CSVs"
            )


def _run_rich_premium(args: argparse.Namespace) -> int:
    from datetime import date

    from .rich_premium.paths import DEFAULT_OUTPUT, DEFAULT_PATHS, DataPaths

    base = DataPaths.from_data_dir(args.data_dir) if args.data_dir else DEFAULT_PATHS
    paths = DataPaths(raw=args.raw_dir or base.raw, panel=args.panel_dir or base.panel)
    command = args.rich_premium_command
    if command == "fetch":
        from .rich_premium.fetch import fetch_all

        fetch_all(paths.raw, date.fromisoformat(args.start), args.retry_poi)
    elif command == "build":
        from .rich_premium.panel import build_panels

        build_panels(paths)
    elif command == "backtest":
        from .rich_premium.backtest import run_backtest

        indices = args.index or ["nifty", "banknifty", "sensex"]
        run_backtest(paths.panel, args.output_dir or DEFAULT_OUTPUT / "backtest", indices)
    elif command == "track":
        from .rich_premium import forward

        forward_dir = args.forward_dir or DEFAULT_OUTPUT / "forward"
        if args.replay:
            lanes = forward.load_lanes(args.lane_dir) if args.lane_dir else None
            print(forward.run_replay(paths, forward_dir / "selftest", lanes), end="")
        else:
            print(forward.run_forward(paths, forward_dir), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jev-shadow")
    subparsers = parser.add_subparsers(dest="command", required=True)
    db_parser = subparsers.add_parser("db-info", help="verify shared NiftyZoning databases read-only")
    db_parser.add_argument("--json", action="store_true")
    run_parser = subparsers.add_parser("run-jsonl", help="evaluate prepared point-in-time contexts")
    run_parser.add_argument("--input", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    context_parser = subparsers.add_parser(
        "build-contexts", help="project a NiftyZoning candidate artifact into Jev contexts"
    )
    context_parser.add_argument("--input", type=Path, required=True)
    context_parser.add_argument("--output", type=Path, required=True)
    orderflow_parser = subparsers.add_parser(
        "build-orderflow-contexts", help="run NiftyZoning's orderflow trace and build Jev contexts"
    )
    orderflow_parser.add_argument("--date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    orderflow_parser.add_argument("--output", type=Path, required=True)
    orderflow_parser.add_argument("--audit-output", type=Path, help="separate post-hoc oracle metadata JSONL")
    orderflow_parser.add_argument("--trace", type=Path, help="use an existing full-day trace JSON")
    orderflow_parser.add_argument("--niftyzoning-root", type=Path)
    analysis_parser = subparsers.add_parser(
        "analyze-results", help="print a tabulated summary of Jev shadow JSONL results"
    )
    analysis_parser.add_argument("--input", type=Path, required=True)
    analysis_parser.add_argument("--oracle-audit", type=Path, help="post-hoc oracle metadata JSONL")
    _add_rich_premium_parser(subparsers)
    args = parser.parse_args(argv)

    if args.command == "rich-premium":
        return _run_rich_premium(args)

    if args.command == "build-contexts":
        if not args.input.is_file():
            raise FileNotFoundError(f"Candidate artifact not found: {args.input}")
        count = write_contexts(args.input, args.output)
        print(f"Wrote {count} point-in-time contexts -> {args.output}")
        return 0
    if args.command == "build-orderflow-contexts":
        if args.trace:
            count = write_orderflow_contexts(args.trace, args.output)
            if args.audit_output:
                write_orderflow_audit(args.trace, args.audit_output)
        else:
            root = args.niftyzoning_root or Path(
                os.environ.get("NIFTYZONING_ROOT", "")
            )
            if not root.is_dir():
                raise RuntimeError("--niftyzoning-root or NIFTYZONING_ROOT is required")
            count = run_orderflow_trace(root, args.date.replace("-", ""), args.output, args.audit_output)
        print(f"Wrote {count} point-in-time orderflow contexts -> {args.output}")
        return 0
    if args.command == "analyze-results":
        if not args.input.is_file():
            raise FileNotFoundError(f"Jev results not found: {args.input}")
        audit = load_records(args.oracle_audit) if args.oracle_audit else None
        summary = analyze_records(load_records(args.input), audit_records=audit)
        print(render_terminal(summary, args.input.name), end="")
        return 0
    config = ShadowConfig.from_environment()
    config.validate()
    if args.command == "db-info":
        result = {
            "niftyzoning_root": str(config.niftyzoning_root),
            "slim_db": str(config.slim_db),
            "options_db": str(config.options_db),
            "slim_tables": table_names(config.slim_db),
            "options_tables": table_names(config.options_db),
            "read_only": True,
        }
        if args.json:
            print(json.dumps(result, indent=2, default=list))
        else:
            print(f"NiftyZoning root: {result['niftyzoning_root']}")
            print(f"Slim database: {result['slim_db']}")
            print(f"Options database: {result['options_db']}")
            print(f"Slim tables: {', '.join(result['slim_tables'])}")
            print(f"Options tables: {', '.join(result['options_tables'])}")
            print("Database access: read-only")
    elif args.command == "run-jsonl":
        if not args.input.is_file():
            raise FileNotFoundError(f"Input context file not found: {args.input}")
        count = run_jsonl(args.input, args.output, config)
        print(f"Wrote {count} Jev shadow records -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
