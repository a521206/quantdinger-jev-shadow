"""JSONL persistence for the separate shadow experiment."""

from __future__ import annotations

import json
from pathlib import Path

from .models import ShadowRecord


def reset_output(path: Path) -> None:
    """Start a new run without retaining records from an earlier run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8", newline="\n")


def append_record(path: Path, record: ShadowRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record.to_dict(), sort_keys=True, default=str) + "\n")
