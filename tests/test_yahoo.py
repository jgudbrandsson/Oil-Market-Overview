import json
from datetime import date
from pathlib import Path

import pytest

from oildash import db
from oildash.fetchers import yahoo

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 2)  # front month CLX26 (expires 2026-10-20)


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def as_symbol(symbol, payload=None):
    """The CLZ26 fixture relabelled as another contract."""
    p = json.loads(json.dumps(payload or fixture("yahoo_clz26.json")))
    p["chart"]["result"][0]["meta"]["symbol"] = symbol
    return p


def fake_api(overrides=None):
    """Every contract answers with the fixture unless overridden by symbol."""
    calls = []

    def get_json(url, params):
        symbol = url.rsplit("/", 1)[1]
        calls.append((symbol, dict(params)))
        r = (overrides or {}).get(symbol) or as_symbol(symbol)
        if isinstance(r, Exception):
            raise r
        return r

    get_json.calls = calls
    return get_json


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "oil.db"))
    db.init_db(c, [])
    yield c
    c.close()


def test_parse_dates_in_new_york_and_skips_null_closes():
    rows = yahoo.parse(fixture("yahoo_clz26.json"), "CLZ26.NYM")
    assert rows == [("2026-09-28", 71.84), ("2026-09-29", 72.10),
                    ("2026-10-01", 72.95), ("2026-10-02", 73.02)]


def test_parse_handles_bars_stamped_at_the_evening_session_open():
    p = fixture("yahoo_clz26.json")
    r = p["chart"]["result"][0]
    r["timestamp"] = [ts - 6 * 3600 for ts in r["timestamp"]]  # 18:00 ET the day before
    assert yahoo.parse(p, "CLZ26.NYM")[0] == ("2026-09-28", 71.84)


def test_parse_empty_window_is_not_an_error():
    p = fixture("yahoo_clz26.json")
    r = p["chart"]["result"][0]
    del r["timestamp"]
    r["indicators"]["quote"][0] = {}
    assert yahoo.parse(p, "CLZ26.NYM") == []


def test_parse_not_found_raises():
    with pytest.raises(yahoo.YahooError, match="symbol may be delisted"):
        yahoo.parse(fixture("yahoo_not_found.json"), "CLV26.NYM")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("chart"),
        lambda p: p["chart"].update(result=[]),
        lambda p: p["chart"]["result"][0].pop("indicators"),
        lambda p: p["chart"]["result"][0]["meta"].pop("exchangeTimezoneName"),
        lambda p: p["chart"]["result"][0]["timestamp"].pop(),
    ],
)
def test_parse_changed_format_raises(mutate):
    p = fixture("yahoo_clz26.json")
    mutate(p)
    with pytest.raises(yahoo.YahooError, match="format may have changed"):
        yahoo.parse(p, "CLZ26.NYM")


def test_parse_rejects_a_different_contract():
    with pytest.raises(yahoo.YahooError, match="got 'CL=F'"):
        yahoo.parse(as_symbol("CL=F"), "CLZ26.NYM")


def test_curve_contracts():
    curve = yahoo.curve_contracts(TODAY)
    assert curve[0] == ("CLX26.NYM", "2026-11")
    assert curve[-1] == ("CLV27.NYM", "2027-10")
    assert len(curve) == 12


def test_drops_bars_after_today_and_after_expiry():
    bars = [("2026-10-19", 1.0), ("2026-10-20", 2.0), ("2026-10-21", 3.0)]
    assert [r[0] for r in yahoo.to_curve_rows(bars, "2026-11", date(2026, 10, 30))] == [
        "2026-10-19", "2026-10-20"]
    assert [r[0] for r in yahoo.to_curve_rows(bars, "2026-12", date(2026, 10, 20))] == [
        "2026-10-19", "2026-10-20"]


def test_run_writes_twelve_months_and_logs_ok(conn):
    api = fake_api()
    n, failures = yahoo.run(conn, date(2026, 9, 25), TODAY, get_json=api, pause=0)
    assert (n, failures) == (48, {})
    months = conn.execute(
        "SELECT DISTINCT contract_month FROM curve_snapshots ORDER BY 1").fetchall()
    assert months[0] == ("2026-11",) and months[-1] == ("2027-10",) and len(months) == 12
    assert conn.execute("SELECT status, rows_upserted FROM fetch_log").fetchall() == [("ok", 48)]
    assert api.calls[0][1]["period1"] < api.calls[0][1]["period2"]


def test_one_bad_contract_does_not_block_the_others(conn):
    api = fake_api({"CLV27.NYM": fixture("yahoo_not_found.json"),
                    "CLF27.NYM": yahoo.YahooError("cannot reach host")})
    n, failures = yahoo.run(conn, date(2026, 9, 25), TODAY, get_json=api, pause=0)
    assert n == 40
    assert sorted(failures) == ["CLF27.NYM", "CLV27.NYM"]
    status, error = conn.execute("SELECT status, error FROM fetch_log").fetchone()
    assert status == "error" and "CLV27.NYM" in error


def test_rerun_is_idempotent_and_takes_corrections(conn):
    yahoo.run(conn, date(2026, 9, 25), TODAY, get_json=fake_api(), pause=0)
    fixed = as_symbol("CLZ26.NYM")
    fixed["chart"]["result"][0]["indicators"]["quote"][0]["close"][-1] = 73.50
    yahoo.run(conn, date(2026, 9, 25), TODAY, get_json=fake_api({"CLZ26.NYM": fixed}), pause=0)
    assert conn.execute("SELECT count(*) FROM curve_snapshots").fetchone()[0] == 48
    settle = conn.execute(
        """SELECT settle FROM curve_snapshots
           WHERE snapshot_date = '2026-10-02' AND contract_month = '2026-12'"""
    ).fetchone()[0]
    assert settle == 73.50


def test_failed_write_leaves_no_partial_data(conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "upsert_curve", boom)
    with pytest.raises(RuntimeError):
        yahoo.run(conn, date(2026, 9, 25), TODAY, get_json=fake_api(), pause=0)
    assert conn.execute("SELECT count(*) FROM curve_snapshots").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM fetch_log").fetchone()[0] == "error"
