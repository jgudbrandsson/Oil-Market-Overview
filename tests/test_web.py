import json
import threading
import urllib.error
import urllib.request
from datetime import date

import pytest

from oildash import config, db, web


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "oil.db")
    conn = db.connect(path)
    db.init_db(conn, config.load().series)
    conn.close()
    return path


def write(path, rows):
    conn = db.connect(path)
    with db.transaction(conn):
        db.upsert_observations(conn, rows, db.utcnow())
    conn.close()


def price_rows(day):
    return [
        ("wti_spot", day, 73.15),
        ("brent_spot", day, 76.38),
        ("gasoline_nyh", day, 2.30),
        ("ulsd_nyh", day, 2.60),
    ]


def board(path, today):
    conn = db.connect(path, readonly=True)
    try:
        return web.board(conn, today)
    finally:
        conn.close()


def test_fresh_panel(db_path):
    write(db_path, price_rows("2026-10-01"))
    b = board(db_path, date(2026, 10, 3))
    assert b["panels"]["price"] == {
        "status": "fresh", "through": "2026-10-01", "stale_series": [], "errors": []
    }
    assert b["price"]["spot"] == [["2026-10-01", 73.15, 76.38, pytest.approx(3.23)]]


def test_old_data_is_stale(db_path):
    write(db_path, price_rows("2026-09-20"))
    assert board(db_path, date(2026, 10, 3))["panels"]["price"]["status"] == "stale"


def test_missing_series_is_stale_and_named(db_path):
    write(db_path, price_rows("2026-10-01")[:2])
    info = board(db_path, date(2026, 10, 3))["panels"]["price"]
    assert info["status"] == "stale"
    assert info["stale_series"] == ["gasoline_nyh", "ulsd_nyh"]


def test_failed_fetch_is_reported_on_the_panel(db_path):
    write(db_path, price_rows("2026-10-01"))
    conn = db.connect(db_path)
    run = db.start_run(conn, "eia")
    db.finish_run(conn, run, 0, "brent_spot: HTTP 400")  # brent is on the price panel
    conn.close()
    info = board(db_path, date(2026, 10, 3))["panels"]["price"]
    assert info["status"] == "fresh"  # data is still recent
    assert info["errors"][0]["error"] == "brent_spot: HTTP 400"


def test_stocks_are_in_million_barrels(db_path):
    write(db_path, [("crude_stocks_ex_spr", "2026-09-26", 418412)])
    b = board(db_path, date(2026, 10, 3))
    assert b["inventories"]["crude_stocks_ex_spr"] == [["2026-09-26", pytest.approx(418.412)]]
    assert b["panels"]["inventories"]["stale_series"] == [
        "cushing_stocks", "gasoline_stocks", "distillate_stocks"
    ]


def serve(path):
    server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_http_routes(db_path):
    write(db_path, price_rows(date.today().isoformat()))
    server, base = serve(db_path)
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "<title>oil-dash</title>" in page
        body = json.load(urllib.request.urlopen(base + "/api/board"))
        assert body["panels"]["price"]["status"] == "fresh"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/nope")
        assert e.value.code == 404
    finally:
        server.shutdown()


def test_no_database_yet(tmp_path):
    server, base = serve(str(tmp_path / "missing.db"))
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/board")
        assert e.value.code == 503
        assert not (tmp_path / "missing.db").exists()  # read-only: never creates it
    finally:
        server.shutdown()


def test_series_error_only_shows_on_its_panel(db_path):
    write(db_path, price_rows("2026-10-01"))
    conn = db.connect(db_path)
    db.finish_run(conn, db.start_run(conn, "eia"), 0, "cushing_stocks: HTTP 400")
    conn.close()
    panels = board(db_path, date(2026, 10, 3))["panels"]
    assert panels["price"]["errors"] == []
    assert panels["inventories"]["errors"][0]["error"] == "cushing_stocks: HTTP 400"


def test_whole_run_error_shows_everywhere(db_path):
    conn = db.connect(db_path)
    db.finish_run(conn, db.start_run(conn, "eia"), 0, "write failed: disk I/O error")
    conn.close()
    panels = board(db_path, date(2026, 10, 3))["panels"]
    assert panels["price"]["errors"] and panels["inventories"]["errors"]
