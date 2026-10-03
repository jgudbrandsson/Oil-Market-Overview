import json
from pathlib import Path

import pytest

from oildash import db
from oildash.config import Series
from oildash.fetchers import eia

FIXTURES = Path(__file__).parent / "fixtures"

WTI = Series("wti_spot", "eia", "PET.RWTC.D", "$/bbl", "D")
CRUDE = Series("crude_stocks_ex_spr", "eia", "PET.WCESTUS1.W", "kbbl", "W")
BAD = Series("brent_spot", "eia", "PET.NOPE.D", "$/bbl", "D")


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def fake_api(responses):
    """get_json stand-in keyed by series id. Values are payloads or exceptions."""
    calls = []

    def get_json(url, params):
        calls.append((url, dict(params)))
        r = responses[url.rsplit("/", 1)[1]]
        if isinstance(r, Exception):
            raise r
        return r

    get_json.calls = calls
    return get_json


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "oil.db"))
    db.init_db(c, [WTI, CRUDE, BAD])
    yield c
    c.close()


def test_parse_daily_skips_nulls_and_accepts_string_values():
    rows, total = eia.parse(fixture("eia_rwtc_daily.json"), "wti_spot")
    assert total == 4
    assert rows == [
        ("wti_spot", "2026-10-01", 73.15),
        ("wti_spot", "2026-09-30", 72.40),
        ("wti_spot", "2026-09-28", 71.02),
    ]


def test_parse_weekly():
    rows, _ = eia.parse(fixture("eia_wcestus1_weekly.json"), "crude_stocks_ex_spr")
    assert rows[0] == ("crude_stocks_ex_spr", "2026-09-26", 418412.0)


def test_parse_error_payload_raises():
    with pytest.raises(eia.EIAError, match="Invalid series id"):
        eia.parse(fixture("eia_error.json"), "x")


def test_parse_changed_format_raises():
    with pytest.raises(eia.EIAError, match="format may have changed"):
        eia.parse({"response": {"rows": []}}, "x")


@pytest.mark.parametrize(
    "period, expected",
    [("2026-09-26", "2026-09-26"), ("2026-09", "2026-09-01"), ("2026", "2026-01-01")],
)
def test_normalize_period(period, expected):
    assert eia.normalize_period(period) == expected


def test_fetch_series_paginates():
    page = lambda period, total: {  # noqa: E731
        "response": {"total": total, "data": [{"period": period, "value": 1.0}]}
    }
    pages = iter([page("2026-01-02", 2), page("2026-01-01", 2)])
    rows = eia.fetch_series(WTI, "k", "2026-01-01", lambda url, params: next(pages))
    assert [r[1] for r in rows] == ["2026-01-02", "2026-01-01"]


def test_run_writes_rows_and_logs_ok(conn):
    api = fake_api({"PET.RWTC.D": fixture("eia_rwtc_daily.json"),
                    "PET.WCESTUS1.W": fixture("eia_wcestus1_weekly.json")})
    n, failures = eia.run(conn, [WTI, CRUDE], "secret", "2026-09-01", api)
    assert (n, failures) == (5, {})
    assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 5
    assert conn.execute("SELECT status, rows_upserted FROM fetch_log").fetchall() == [("ok", 5)]
    assert api.calls[0][1]["start"] == "2026-09-01"


def test_run_is_idempotent_and_takes_revisions(conn):
    payload = fixture("eia_rwtc_daily.json")
    eia.run(conn, [WTI], "k", "2026-09-01", fake_api({"PET.RWTC.D": payload}))
    payload["response"]["data"][0]["value"] = 74.00  # EIA revises a value
    eia.run(conn, [WTI], "k", "2026-09-01", fake_api({"PET.RWTC.D": payload}))
    assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 3
    value = conn.execute(
        "SELECT value FROM observations WHERE obs_date = '2026-10-01'"
    ).fetchone()[0]
    assert value == 74.00


def test_one_bad_series_does_not_block_the_others(conn):
    api = fake_api({"PET.RWTC.D": fixture("eia_rwtc_daily.json"),
                    "PET.NOPE.D": eia.EIAError("HTTP 400: Invalid series id")})
    n, failures = eia.run(conn, [WTI, BAD], "k", "2026-09-01", api)
    assert n == 3
    assert list(failures) == ["brent_spot"]
    status, error = conn.execute("SELECT status, error FROM fetch_log").fetchone()
    assert status == "error" and "brent_spot" in error


def test_failed_write_leaves_no_partial_data(conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "upsert_observations", boom)
    api = fake_api({"PET.RWTC.D": fixture("eia_rwtc_daily.json")})
    with pytest.raises(RuntimeError):
        eia.run(conn, [WTI], "k", "2026-09-01", api)
    assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM fetch_log").fetchone()[0] == "error"


def test_api_key_never_reaches_the_log(conn):
    api = fake_api({"PET.NOPE.D": eia.EIAError("HTTP 400 from https://api.eia.gov/...")})
    eia.run(conn, [BAD], "secret-key", "2026-09-01", api)
    assert "secret-key" not in conn.execute("SELECT error FROM fetch_log").fetchone()[0]
