"""EIA NYMEX WTI futures, contracts 1-4, into `curve_snapshots`.

The authoritative short end of the curve (DESIGN.md §2). EIA publishes rolling
"contract n" series, so each value is mapped to its delivery month with the CL
calendar. Same API and key as the spot fetcher, but its own run and transaction,
so the curve and the spot prices never block each other.
"""

import sqlite3
from datetime import date

from .. import contracts, db
from ..config import Series
from . import eia

SOURCE = "eia_curve"
CONTRACTS = {
    n: Series(f"wti_fut_m{n}", "eia", f"PET.RCLC{n}.D", "$/bbl", "D") for n in (1, 2, 3, 4)
}


def to_curve_rows(
    rows: list[tuple[str, str, float]], position: int
) -> list[tuple[str, str, str, float, str]]:
    """(series_key, trade date, settle) -> curve_snapshots rows."""
    return [
        (d, contracts.ROOT, contracts.contract_month(date.fromisoformat(d), position), v, "eia")
        for _, d, v in rows
    ]


def run(
    conn: sqlite3.Connection,
    api_key: str,
    start: str,
    get_json: eia.GetJson = eia.http_get_json,
) -> tuple[int, dict[str, str]]:
    """Fetch contracts 1-4 and store them. Returns (rows upserted, {series_key: error})."""
    run_id = db.start_run(conn, SOURCE)
    rows: list[tuple[str, str, str, float, str]] = []
    failures: dict[str, str] = {}
    for position, s in CONTRACTS.items():
        try:
            rows.extend(to_curve_rows(eia.fetch_series(s, api_key, start, get_json), position))
        except Exception as e:  # noqa: BLE001 - one bad series must not stop the rest
            failures[s.key] = str(e)
    try:
        with db.transaction(conn):
            n = db.upsert_curve(conn, rows)
    except Exception as e:
        db.finish_run(conn, run_id, 0, f"write failed: {e}")
        raise
    error = "; ".join(f"{k}: {v}" for k, v in failures.items()) or None
    db.finish_run(conn, run_id, n, error)
    return n, failures
