import pytest

from oildash import config, db
from oildash.config import Series

SERIES = [
    Series("wti_spot", "eia", "PET.RWTC.D", "$/bbl", "D"),
    Series("brent_spot", "eia", "PET.RBRTE.D", "$/bbl", "D"),
    Series("gasoline_nyh", "eia", "x", "$/gal", "D"),
    Series("ulsd_nyh", "eia", "y", "$/gal", "D"),
]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "oil.db"))
    db.init_db(c, SERIES)
    yield c
    c.close()


def test_wal_mode(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_init_db_twice_keeps_data(conn):
    conn.execute(
        "INSERT INTO curve_snapshots VALUES ('2026-10-02', 'CL', '2026-11', 73.45, 'eia')"
    )
    db.upsert_observations(conn, [("wti_spot", "2026-10-01", 73.15)], db.utcnow())
    db.init_db(conn, SERIES)
    assert conn.execute("SELECT count(*) FROM curve_snapshots").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 1


def test_interrupted_run_is_marked_error(conn):
    first = db.start_run(conn, "eia")  # never finished: power loss
    second = db.start_run(conn, "eia")
    rows = dict(conn.execute("SELECT run_id, status FROM fetch_log").fetchall())
    assert rows == {first: "error", second: "running"}


def test_unknown_series_is_rejected(conn):
    with pytest.raises(Exception, match="FOREIGN KEY"):
        db.upsert_observations(conn, [("not_configured", "2026-10-01", 1.0)], db.utcnow())


def test_views(conn):
    rows = [
        ("wti_spot", "2026-10-01", 73.15),
        ("brent_spot", "2026-10-01", 76.38),
        ("gasoline_nyh", "2026-10-01", 2.30),
        ("ulsd_nyh", "2026-10-01", 2.60),
        ("wti_spot", "2026-09-30", 72.40),  # no Brent that day: not in the spread view
    ]
    db.upsert_observations(conn, rows, db.utcnow())
    assert conn.execute("SELECT obs_date, spread FROM v_brent_wti").fetchall() == [
        ("2026-10-01", pytest.approx(3.23))
    ]
    crack = conn.execute("SELECT crack FROM v_crack_321").fetchone()[0]
    assert crack == pytest.approx((2 * 2.30 * 42 + 2.60 * 42 - 3 * 73.15) / 3)


def test_readonly_connection_cannot_write(conn, tmp_path):
    ro = db.connect(str(tmp_path / "oil.db"), readonly=True)
    with pytest.raises(Exception, match="readonly"):
        ro.execute("DELETE FROM observations")


def test_shipped_config_loads(monkeypatch):
    monkeypatch.setenv("OIL_DASH_DB", "/tmp/x.db")
    cfg = config.load()
    assert cfg.db_path == "/tmp/x.db"
    assert {s.key for s in cfg.for_source("eia")} >= {
        "wti_spot", "brent_spot", "gasoline_nyh", "ulsd_nyh"
    }
