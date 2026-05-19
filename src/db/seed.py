"""CLI entry point to seed the prototype DB with fixture data.

Idempotent: if the target DB already contains any sites, the script
exits without writing rather than duplicating.

Usage (from the repo root):

    python -m src.db.seed [--db-path ./prototype.db]
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from src.db.fixtures import seed_fixtures
from src.db.store import MeterHistoryStore


def _already_seeded(db_path: Path) -> bool:
    """Return True if ``db_path`` exists and already has at least one site.

    Checked before instantiating the Store so the side effect of running
    schema.sql against a fresh file is reserved for the actual seeding
    case.
    """

    if not db_path.exists():
        return False
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            (count,) = conn.execute("SELECT COUNT(*) FROM sites").fetchone()
        except sqlite3.OperationalError:
            return False
        return count > 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the prototype DB with fixture data.")
    parser.add_argument(
        "--db-path",
        default="./prototype.db",
        type=Path,
        help="Path to the SQLite DB file (default: ./prototype.db).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Delete the DB file before seeding so the result is deterministic. "
            "Used by the demo harness to guarantee a known starting state."
        ),
    )
    args = parser.parse_args()
    db_path: Path = args.db_path

    if args.reset and db_path.exists():
        db_path.unlink()
        print(f"Reset: removed {db_path}")

    if _already_seeded(db_path):
        print(f"Skipping: {db_path} already contains fixture data.")
        return 0

    store = MeterHistoryStore(db_path)
    try:
        counts = seed_fixtures(store)
    finally:
        store.close()

    print(
        f"Seeded: {counts['sites']} sites, {counts['accounts']} accounts, "
        f"{counts['meters']} meters, {counts['readings']} readings"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
