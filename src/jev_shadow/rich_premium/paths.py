"""Where raw exchange downloads and derived parquet panels live."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_OUTPUT = Path("data/output/rich_premium")


@dataclass(frozen=True)
class DataPaths:
    raw: Path
    panel: Path

    @classmethod
    def from_data_dir(cls, data_dir: Path) -> DataPaths:
        """Use the research layout: downloads under <dir>/raw, panels directly in <dir>."""
        return cls(raw=data_dir / "raw", panel=data_dir)


DEFAULT_PATHS = DataPaths(raw=Path("data/raw/nse-daily"), panel=DEFAULT_OUTPUT / "panel")
