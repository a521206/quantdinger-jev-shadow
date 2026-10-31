"""Read-only access to the databases owned by NiftyZoning."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb


@contextmanager
def read_only_connection(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open an existing DuckDB file without permitting writes."""
    connection = duckdb.connect(str(path), read_only=True)
    try:
        yield connection
    finally:
        connection.close()


def table_names(path: Path) -> tuple[str, ...]:
    """Return tables visible in a NiftyZoning database."""
    with read_only_connection(path) as connection:
        rows = connection.execute("SHOW TABLES").fetchall()
    return tuple(str(row[0]) for row in rows)
