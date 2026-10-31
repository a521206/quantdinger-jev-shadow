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
    args = parser.parse_args(argv)

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
