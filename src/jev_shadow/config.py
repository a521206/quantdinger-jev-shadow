"""Explicit configuration for the separate Jev shadow repository."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    """Load simple KEY=VALUE settings without overriding the process environment."""
    dotenv_path = path or Path.cwd() / ".env"
    if not dotenv_path.is_file():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True, slots=True)
class ShadowConfig:
    """Runtime paths and provider settings; database access is always read-only."""

    niftyzoning_root: Path
    slim_db: Path
    options_db: Path
    output_root: Path
    jev_base_url: str = "https://openrouter.ai/api/v1"
    jev_model: str = "typesafe/jev-router"
    jev_api_key: str = ""
    timeout_seconds: float = 8.0

    @classmethod
    def from_environment(cls) -> ShadowConfig:
        load_dotenv()
        root_value = os.environ.get("NIFTYZONING_ROOT")
        if not root_value:
            raise RuntimeError("NIFTYZONING_ROOT must point to the current NiftyZoning checkout")
        root = Path(root_value).expanduser().resolve()
        return cls(
            niftyzoning_root=root,
            slim_db=Path(os.environ.get("NIFTYZONING_SLIM_DB", root / "data/db/nifty_slim.duckdb")),
            options_db=Path(os.environ.get("NIFTYZONING_OPTIONS_DB", root / "data/db/nifty.db")),
            output_root=Path(os.environ.get("JEV_SHADOW_OUTPUT", "data/output/jev_shadow")),
            jev_base_url=os.environ.get("JEV_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            jev_model=os.environ.get("JEV_MODEL", "typesafe/jev-router"),
            jev_api_key=os.environ.get("OPENROUTER_API_KEY", os.environ.get("JEV_API_KEY", "")),
            timeout_seconds=float(os.environ.get("JEV_TIMEOUT_SECONDS", "8")),
        )

    def validate(self) -> None:
        if not self.niftyzoning_root.is_dir():
            raise FileNotFoundError(f"NiftyZoning root not found: {self.niftyzoning_root}")
        for path in (self.slim_db, self.options_db):
            if not path.is_file():
                raise FileNotFoundError(f"NiftyZoning database not found: {path}")
        if self.timeout_seconds <= 0:
            raise ValueError("JEV_TIMEOUT_SECONDS must be positive")
