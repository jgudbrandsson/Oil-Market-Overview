"""Command line entry point, run by the systemd units.

    python -m oildash init-db
    python -m oildash fetch eia [--days 30 | --start 2000-01-01]
"""

import argparse
import os
import sys
from datetime import date, timedelta

from . import config, db
from .fetchers import eia


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oildash")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create tables and views, sync series from config")
    fetch = sub.add_parser("fetch", help="fetch one source into the database")
    fetch.add_argument("source", choices=["eia"])
    window = fetch.add_mutually_exclusive_group()
    window.add_argument("--days", type=int, default=30, help="trailing window (default 30)")
    window.add_argument("--start", help="fetch from this date, e.g. 2000-01-01 to backfill")
    args = parser.parse_args(argv)

    cfg = config.load()
    conn = db.connect(cfg.db_path)
    # Cheap and idempotent: keeps tables, views and `series` in step with config.toml.
    db.init_db(conn, cfg.series)

    if args.command == "init-db":
        print(f"initialised {cfg.db_path} with {len(cfg.series)} series")
        return 0

    api_key = os.environ.get("EIA_API_KEY")
    if not api_key:
        print("EIA_API_KEY is not set", file=sys.stderr)
        return 2
    start = args.start or (date.today() - timedelta(days=args.days)).isoformat()
    n, failures = eia.run(conn, cfg.for_source("eia"), api_key, start)
    print(f"eia: upserted {n} rows since {start}")
    for key, err in failures.items():
        print(f"eia: {key} failed: {err}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
