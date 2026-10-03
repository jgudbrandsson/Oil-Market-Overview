"""Command line entry point, run by the systemd units.

    python -m oildash init-db
    python -m oildash fetch {eia,eia-curve,yahoo} [--days 30 | --start 2000-01-01]
    python -m oildash backup [DEST] [--keep 14]
    python -m oildash serve [--host 127.0.0.1] [--port 8710]
"""

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import backup, config, db, web
from .fetchers import eia, eia_curve, yahoo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oildash")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create tables and views, sync series from config")
    fetch = sub.add_parser("fetch", help="fetch one source into the database")
    fetch.add_argument("source", choices=["eia", "eia-curve", "yahoo"])
    window = fetch.add_mutually_exclusive_group()
    window.add_argument("--days", type=int, default=30, help="trailing window (default 30)")
    window.add_argument("--start", help="fetch from this date, e.g. 2000-01-01 to backfill")
    serve = sub.add_parser("serve", help="serve the dashboard (read-only)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8710)
    bak = sub.add_parser("backup", help="copy the database to off-SD storage")
    bak.add_argument("dest", nargs="?", default=os.environ.get("OIL_DASH_BACKUP_DIR"),
                     help="mounted backup folder (default: $OIL_DASH_BACKUP_DIR)")
    bak.add_argument("--keep", type=int, default=14, help="copies to keep (default 14)")
    args = parser.parse_args(argv)

    cfg = config.load()
    if args.command == "serve":
        web.serve(cfg.db_path, args.host, args.port)
        return 0
    if args.command == "backup":
        return run_backup(cfg.db_path, args.dest, args.keep)

    conn = db.connect(cfg.db_path)
    # Cheap and idempotent: keeps tables, views and `series` in step with config.toml.
    db.init_db(conn, cfg.series)

    if args.command == "init-db":
        print(f"initialised {cfg.db_path} with {len(cfg.series)} series")
        return 0

    # Trade dates are New York dates, whatever the Pi's own timezone is.
    today = datetime.now(ZoneInfo("America/New_York")).date()
    start = args.start or (today - timedelta(days=args.days)).isoformat()
    if args.source == "yahoo":
        n, failures = yahoo.run(conn, date.fromisoformat(start), today)
    else:
        api_key = os.environ.get("EIA_API_KEY")
        if not api_key:
            print("EIA_API_KEY is not set", file=sys.stderr)
            return 2
        if args.source == "eia":
            n, failures = eia.run(conn, cfg.for_source("eia"), api_key, start)
        else:
            n, failures = eia_curve.run(conn, api_key, start)
    print(f"{args.source}: upserted {n} rows since {start}")
    for key, err in failures.items():
        print(f"{args.source}: {key} failed: {err}", file=sys.stderr)
    return 1 if failures else 0


def run_backup(db_path: str, dest: str | None, keep: int) -> int:
    if not dest:
        print("no backup folder: pass DEST or set OIL_DASH_BACKUP_DIR", file=sys.stderr)
        return 2
    try:
        path, curve_rows = backup.backup(db_path, dest, keep)
    except (backup.BackupError, sqlite3.Error) as e:
        print(f"backup failed: {e}", file=sys.stderr)
        return 1
    print(f"backup: wrote {path} ({curve_rows} curve snapshot rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
