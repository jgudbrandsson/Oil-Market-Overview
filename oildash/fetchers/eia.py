"""EIA API v2 fetcher: spot prices and weekly stocks.

Each run fetches a trailing window for every configured EIA series and upserts
it in one transaction. A series that fails to fetch is reported, but the others
are still written, so one bad series id doesn't blank the whole source.
"""

import json
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from .. import db
from ..config import Series

SOURCE = "eia"
URL = "https://api.eia.gov/v2/seriesid/{series_id}"
PAGE_SIZE = 5000

GetJson = Callable[[str, dict[str, Any]], dict[str, Any]]


class EIAError(Exception):
    pass


def http_get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read(300).decode(errors="replace")
        # Never echo the request URL: it carries the API key.
        raise EIAError(f"HTTP {e.code} from {url}: {body}") from None
    except urllib.error.URLError as e:
        raise EIAError(f"cannot reach {url}: {e.reason}") from None


def normalize_period(period: str) -> str:
    """EIA periods are 'YYYY-MM-DD' (daily, weekly), 'YYYY-MM' or 'YYYY'."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        return period
    if re.fullmatch(r"\d{4}-\d{2}", period):
        return f"{period}-01"
    if re.fullmatch(r"\d{4}", period):
        return f"{period}-01-01"
    raise EIAError(f"unrecognised period {period!r}")


def parse(payload: dict[str, Any], series_key: str) -> tuple[list[tuple[str, str, float]], int]:
    """Turn one EIA response page into rows. Returns (rows, total rows on server)."""
    if "error" in payload:
        raise EIAError(f"EIA error: {payload['error']}")
    response = payload.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise EIAError("response has no data list; the API format may have changed")
    rows = []
    for item in response["data"]:
        value = item.get("value")
        if value is None or value == "":
            continue  # EIA publishes gaps (holidays, withheld values) as null
        rows.append((series_key, normalize_period(item["period"]), float(value)))
    return rows, int(response.get("total", len(rows)))


def fetch_series(
    series: Series, api_key: str, start: str, get_json: GetJson = http_get_json
) -> list[tuple[str, str, float]]:
    rows: list[tuple[str, str, float]] = []
    offset = 0
    while True:
        payload = get_json(
            URL.format(series_id=series.source_id),
            {"api_key": api_key, "start": start, "offset": offset, "length": PAGE_SIZE},
        )
        page, total = parse(payload, series.key)
        rows.extend(page)
        n = len(payload["response"]["data"])
        offset += n
        if n == 0 or offset >= total:
            return rows


def run(
    conn: sqlite3.Connection,
    series: list[Series],
    api_key: str,
    start: str,
    get_json: GetJson = http_get_json,
) -> tuple[int, dict[str, str]]:
    """Fetch and store every series. Returns (rows upserted, {series_key: error})."""
    run_id = db.start_run(conn, SOURCE)
    rows: list[tuple[str, str, float]] = []
    failures: dict[str, str] = {}
    for s in series:
        try:
            rows.extend(fetch_series(s, api_key, start, get_json))
        except Exception as e:  # noqa: BLE001 - one bad series must not stop the rest
            failures[s.key] = str(e)
    try:
        with db.transaction(conn):
            n = db.upsert_observations(conn, rows, db.utcnow())
    except Exception as e:
        db.finish_run(conn, run_id, 0, f"write failed: {e}")
        raise
    error = "; ".join(f"{k}: {v}" for k, v in failures.items()) or None
    db.finish_run(conn, run_id, n, error)
    return n, failures
