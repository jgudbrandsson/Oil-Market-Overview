"""Yahoo Finance individual CL contracts, M1-M12, into `curve_snapshots`.

The back of the curve has no other free source, and Yahoo drops a contract once
it expires, so a day not captured here is lost for good. This is an unofficial
endpoint (the one the `yfinance` package wraps), called directly with the
standard library: same fragility, no pandas on the Pi. When it breaks, only this
source's runs fail; the EIA contracts 1-4 keep the short curve going (C6).

Each run re-fetches a trailing window for every contract, so a missed day is
filled in on the next run, and a value Yahoo corrects after the fact is updated.
"""

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import contracts, db

SOURCE = "yahoo"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
MONTHS = 12
# Yahoo rejects requests without a browser-like User-Agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) oil-dash"}

GetJson = Callable[[str, dict[str, Any]], dict[str, Any]]


class YahooError(Exception):
    pass


def http_get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        # Yahoo answers unknown or expired symbols with a 404 and a JSON error body.
        body = e.read(500).decode(errors="replace")
        try:
            return json.loads(body)
        except ValueError:
            raise YahooError(f"HTTP {e.code} from {url}: {body[:200]}") from None
    except urllib.error.URLError as e:
        raise YahooError(f"cannot reach {url}: {e.reason}") from None


def parse(payload: dict[str, Any], symbol: str) -> list[tuple[str, float]]:
    """Turn one chart response into (trade date, settle) pairs, oldest first."""
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        raise YahooError("response has no 'chart'; the API format may have changed")
    if chart.get("error"):
        err = chart["error"]
        detail = err.get("description", err) if isinstance(err, dict) else err
        raise YahooError(f"Yahoo error: {detail}")
    result = chart.get("result")
    if not isinstance(result, list) or not result:
        raise YahooError("response has no result; the API format may have changed")
    r = result[0]
    try:
        meta = r["meta"]
        quote = r["indicators"]["quote"][0]
        tz = ZoneInfo(meta["exchangeTimezoneName"])
    except (KeyError, IndexError, TypeError, ZoneInfoNotFoundError) as e:
        raise YahooError(f"missing {e} in response; the API format may have changed") from None
    if meta.get("symbol") != symbol:
        # Guards against Yahoo answering with a different (e.g. continuous) contract.
        raise YahooError(f"asked for {symbol}, got {meta.get('symbol')!r}")
    timestamps = r.get("timestamp", [])  # absent when the window has no trades
    closes = quote.get("close", [])
    if len(timestamps) != len(closes):
        raise YahooError("timestamp and close lists differ in length; format may have changed")
    rows = []
    for ts, close in zip(timestamps, closes, strict=True):
        if close is None:
            continue  # no trade that day
        # Daily bars are stamped at midnight or at the session open the evening
        # before, depending on the instrument; +12h lands on the trade date either way.
        day = (datetime.fromtimestamp(ts, UTC).astimezone(tz) + timedelta(hours=12)).date()
        rows.append((day.isoformat(), float(close)))
    return rows


def curve_contracts(today: date, months: int = MONTHS) -> list[tuple[str, str]]:
    """(Yahoo symbol, 'YYYY-MM') for contracts 1..months on a date."""
    front = contracts.front_month(today)
    out = []
    for i in range(months):
        y, m = contracts.add_months(*front, i)
        out.append((contracts.yahoo_symbol(y, m), f"{y:04d}-{m:02d}"))
    return out


def to_curve_rows(
    bars: list[tuple[str, float]], month: str, today: date
) -> list[tuple[str, str, str, float, str]]:
    """Keep bars that are real settles for this contract: not after its last
    trading day, and not after today (a bar for the next session can appear
    once evening trading opens)."""
    y, m = (int(p) for p in month.split("-"))
    last = min(contracts.last_trade_date(y, m), today).isoformat()
    return [(d, contracts.ROOT, month, v, SOURCE) for d, v in bars if d <= last]


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


def run(
    conn: sqlite3.Connection,
    start: date,
    today: date,
    months: int = MONTHS,
    get_json: GetJson = http_get_json,
    pause: float = 1.0,
) -> tuple[int, dict[str, str]]:
    """Fetch every contract on the curve and store them. Returns
    (rows upserted, {symbol: error})."""
    run_id = db.start_run(conn, SOURCE)
    rows: list[tuple[str, str, str, float, str]] = []
    failures: dict[str, str] = {}
    params = {"period1": _epoch(start), "period2": _epoch(today + timedelta(days=1)),
              "interval": "1d"}
    for i, (symbol, month) in enumerate(curve_contracts(today, months)):
        if i and pause:
            time.sleep(pause)  # be gentle with an unofficial endpoint
        try:
            bars = parse(get_json(URL.format(symbol=symbol), params), symbol)
            rows.extend(to_curve_rows(bars, month, today))
        except Exception as e:  # noqa: BLE001 - one bad contract must not stop the rest
            failures[symbol] = str(e)
    try:
        with db.transaction(conn):
            n = db.upsert_curve(conn, rows)
    except Exception as e:
        db.finish_run(conn, run_id, 0, f"write failed: {e}")
        raise
    error = "; ".join(f"{k}: {v}" for k, v in failures.items()) or None
    db.finish_run(conn, run_id, n, error)
    return n, failures
