import json
from pathlib import Path

import pytest

from oildash import db
from oildash.fetchers import eia, eia_curve

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def fake_api(responses):
    def get_json(url, params):
        r = responses[url.rsplit("/", 1)[1]]
        if isinstance(r, Exception):
            raise r
        return r

    return get_json


def shifted(payload, delta):
    """The same response, as if for a contract `delta` dollars away."""
    p = json.loads(json.dumps(payload))
    for item in p["response"]["data"]:
        item["value"] = float(item["value"]) + delta
    return p


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "oil.db"))
    db.init_db(c, [])
    yield c
    c.close()


def all_contracts():
    m1 = fixture("eia_rclc1_daily.json")
    return {f"PET.RCLC{n}.D": shifted(m1, -0.5 * (n - 1)) for n in (1, 2, 3, 4)}


def test_maps_positions_to_delivery_months(conn):
    n, failures = eia_curve.run(conn, "k", "2026-09-01", fake_api(all_contracts()))
    assert (n, failures) == (12, {})
    rows = conn.execute(
        """SELECT contract_month, settle, source FROM curve_snapshots
           WHERE snapshot_date = '2026-10-02' ORDER BY contract_month"""
    ).fetchall()
    assert rows == [("2026-11", 73.21, "eia"), ("2026-12", 72.71, "eia"),
                    ("2027-01", 72.21, "eia"), ("2027-02", 71.71, "eia")]
    assert conn.execute("SELECT status FROM fetch_log").fetchone()[0] == "ok"


def test_one_bad_contract_does_not_block_the_others(conn):
    api = all_contracts()
    api["PET.RCLC4.D"] = eia.EIAError("HTTP 400: Invalid series id")
    n, failures = eia_curve.run(conn, "k", "2026-09-01", fake_api(api))
    assert n == 9 and list(failures) == ["wti_fut_m4"]
    assert conn.execute("SELECT status FROM fetch_log").fetchone()[0] == "error"


def test_rerun_is_idempotent(conn):
    eia_curve.run(conn, "k", "2026-09-01", fake_api(all_contracts()))
    eia_curve.run(conn, "k", "2026-09-01", fake_api(all_contracts()))
    assert conn.execute("SELECT count(*) FROM curve_snapshots").fetchone()[0] == 12


def test_eia_overwrites_yahoo_but_not_the_reverse(conn):
    with db.transaction(conn):
        db.upsert_curve(conn, [("2026-10-02", "CL", "2026-11", 70.0, "yahoo")])
    eia_curve.run(conn, "k", "2026-09-01", fake_api(all_contracts()))
    with db.transaction(conn):
        db.upsert_curve(conn, [("2026-10-02", "CL", "2026-11", 70.0, "yahoo")])
    row = conn.execute(
        """SELECT settle, source FROM curve_snapshots
           WHERE snapshot_date = '2026-10-02' AND contract_month = '2026-11'"""
    ).fetchone()
    assert row == (73.21, "eia")


def test_failed_write_leaves_no_partial_data(conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "upsert_curve", boom)
    with pytest.raises(RuntimeError):
        eia_curve.run(conn, "k", "2026-09-01", fake_api(all_contracts()))
    assert conn.execute("SELECT count(*) FROM curve_snapshots").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM fetch_log").fetchone()[0] == "error"
