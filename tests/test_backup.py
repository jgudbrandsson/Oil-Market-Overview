import sqlite3
from datetime import date

import pytest

from oildash import backup, db


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "data" / "oil.db"
    c = db.connect(str(path))
    db.init_db(c, [])
    with db.transaction(c):
        db.upsert_curve(c, [("2026-10-02", "CL", "2026-11", 73.21, "eia")])
    c.close()
    return str(path)


@pytest.fixture
def dest(tmp_path):
    d = tmp_path / "usb"
    d.mkdir()
    return d


def test_writes_a_checked_copy(db_path, dest):
    path, curve_rows = backup.backup(db_path, str(dest), today=date(2026, 10, 3),
                                     require_other_device=False)
    assert path == dest / "oil-2026-10-03.db" and curve_rows == 1
    assert sqlite3.connect(path).execute("SELECT settle FROM curve_snapshots").fetchone() == (
        73.21,)
    assert not list(dest.glob("*.tmp"))


def test_keeps_only_the_newest_copies_and_nothing_else(db_path, dest):
    (dest / "notes.txt").write_text("mine")
    for day in range(1, 6):
        backup.backup(db_path, str(dest), keep=3, today=date(2026, 10, day),
                      require_other_device=False)
    assert sorted(p.name for p in dest.iterdir()) == [
        "notes.txt", "oil-2026-10-03.db", "oil-2026-10-04.db", "oil-2026-10-05.db"]


def test_missing_destination_fails_without_creating_it(db_path, tmp_path):
    with pytest.raises(backup.BackupError, match="mounted"):
        backup.backup(db_path, str(tmp_path / "nope"))
    assert not (tmp_path / "nope").exists()


def test_refuses_a_folder_on_the_same_disk(db_path, dest):
    # An unmounted USB disk leaves its mount folder on the SD card.
    with pytest.raises(backup.BackupError, match="same disk"):
        backup.backup(db_path, str(dest))
    assert not list(dest.iterdir())
