"""Read-only dashboard server.

    GET /            the board (dashboard/index.html)
    GET /api/board   everything the board shows, as one JSON document

Opens the database read-only on every request, so it can never write and a
fetch running at the same time is never blocked (WAL).
"""

import json
import sqlite3
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import db

PAGE = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"

# Which series each panel depends on. A panel is stale if any of them is.
PANELS = {
    "price": ["wti_spot", "brent_spot", "gasoline_nyh", "ulsd_nyh"],
    "inventories": [
        "crude_stocks_ex_spr", "cushing_stocks", "gasoline_stocks", "distillate_stocks"
    ],
}

# Oldest acceptable data point, in days, by publication frequency. Daily allows
# for weekends plus a holiday and EIA's one-day lag; weekly allows a late WPSR.
MAX_AGE_DAYS = {"D": 5, "W": 10, "M": 45}

PRICE_DAYS = 365
STOCK_DAYS = 371  # 53 weeks, so the 1-year-ago week is included


def _rows(conn: sqlite3.Connection, sql: str, *params) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


def freshness(conn: sqlite3.Connection, keys: list[str], today: date) -> dict:
    """Panel status: 'fresh' or 'stale', the date the data is complete through,
    and the error from the source's latest run if that run failed."""
    stale_keys, last_dates, sources = [], [], set()
    for key in keys:
        row = conn.execute(
            """SELECT s.frequency, s.source, max(o.obs_date)
               FROM series s LEFT JOIN observations o ON o.series_key = s.series_key
               WHERE s.series_key = ? GROUP BY s.series_key""",
            (key,),
        ).fetchone()
        if row is None or row[2] is None:
            stale_keys.append(key)
            if row:
                sources.add(row[1])
            continue
        frequency, source, last = row
        sources.add(source)
        last_dates.append(last)
        if (today - date.fromisoformat(last)).days > MAX_AGE_DAYS[frequency]:
            stale_keys.append(key)
    all_keys = [k for (k,) in conn.execute("SELECT series_key FROM series")]
    errors = []
    for source in sorted(sources):
        run = conn.execute(
            """SELECT status, finished_at, error FROM fetch_log
               WHERE source = ? ORDER BY run_id DESC LIMIT 1""",
            (source,),
        ).fetchone()
        if run and run[0] == "error" and _concerns(run[2] or "", keys, all_keys):
            errors.append({"source": source, "at": run[1], "error": run[2]})
    return {
        "status": "stale" if stale_keys else "fresh",
        "through": min(last_dates) if last_dates else None,
        "stale_series": stale_keys,
        "errors": errors,
    }


def _concerns(error: str, panel_keys: list[str], all_keys: list[str]) -> bool:
    """A failed run's error belongs on a panel if it names one of the panel's
    series, or names no series at all (the whole run failed)."""
    named = [k for k in all_keys if f"{k}:" in error]
    return not named or any(k in panel_keys for k in named)


def board(conn: sqlite3.Connection, today: date) -> dict:
    price_from = (today - timedelta(days=PRICE_DAYS)).isoformat()
    stock_from = (today - timedelta(days=STOCK_DAYS)).isoformat()
    spot = _rows(
        conn,
        """SELECT obs_date, wti, brent, spread FROM v_brent_wti
           WHERE obs_date >= ? ORDER BY obs_date""",
        price_from,
    )
    crack = _rows(
        conn,
        "SELECT obs_date, crack FROM v_crack_321 WHERE obs_date >= ? ORDER BY obs_date",
        price_from,
    )
    stocks = {}
    for key in PANELS["inventories"]:
        stocks[key] = [
            [d, v / 1000]  # kbbl -> million barrels
            for d, v in _rows(
                conn,
                """SELECT obs_date, value FROM observations
                   WHERE series_key = ? AND obs_date >= ? ORDER BY obs_date""",
                key,
                stock_from,
            )
        ]
    return {
        "today": today.isoformat(),
        "panels": {name: freshness(conn, keys, today) for name, keys in PANELS.items()},
        "price": {
            "spot": [list(r) for r in spot],
            "crack": [list(r) for r in crack],
        },
        "inventories": stocks,
    }


def make_handler(db_path: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server naming
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", PAGE.read_bytes())
            elif path == "/api/board":
                try:
                    conn = db.connect(db_path, readonly=True)
                except sqlite3.OperationalError:
                    self._json(503, {"error": "No database yet. Run the first fetch."})
                    return
                try:
                    self._json(200, board(conn, date.today()))
                except sqlite3.OperationalError as e:
                    self._json(503, {"error": f"Database not ready: {e}"})
                finally:
                    conn.close()
            else:
                self._send(404, "text/plain", b"not found")

        def _json(self, code: int, body: dict) -> None:
            self._send(code, "application/json", json.dumps(body).encode())

        def _send(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_request(self, code="-", size="-"):  # quieter journal: errors only
            if str(code)[:1] in ("4", "5"):
                super().log_request(code, size)

    return Handler


def serve(db_path: str, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"oil-dash serving on http://{host}:{port}")
    server.serve_forever()
