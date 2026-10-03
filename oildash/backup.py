"""Nightly copy of the database to storage that isn't the SD card.

The curve history can't be downloaded again (DESIGN.md §7), so this is the only
copy of it outside the Pi's SD card. Uses SQLite's online backup, which is safe
while a fetch is writing, checks the copy, then keeps the newest `keep` copies.
"""

import os
import re
import sqlite3
from datetime import date
from pathlib import Path

NAME = re.compile(r"oil-\d{4}-\d{2}-\d{2}\.db")


class BackupError(Exception):
    pass


def backup(
    db_path: str,
    dest_dir: str,
    keep: int = 14,
    today: date | None = None,
    require_other_device: bool = True,
) -> tuple[Path, int]:
    """Write dest_dir/oil-YYYY-MM-DD.db. Returns (path, curve rows in the copy)."""
    dest = Path(dest_dir)
    if not dest.is_dir():
        raise BackupError(f"{dest} does not exist; is the backup disk mounted?")
    if require_other_device and dest.stat().st_dev == Path(db_path).parent.stat().st_dev:
        # An unmounted USB disk or NAS leaves an empty folder on the SD card.
        raise BackupError(f"{dest} is on the same disk as the database; is the disk mounted?")
    final = dest / f"oil-{(today or date.today()).isoformat()}.db"
    tmp = final.with_name(final.name + ".tmp")
    tmp.unlink(missing_ok=True)

    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
        check = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise BackupError(f"backup copy failed integrity check: {check}")
        curve_rows = dst.execute("SELECT count(*) FROM curve_snapshots").fetchone()[0]
    except BaseException:
        dst.close()
        tmp.unlink(missing_ok=True)
        raise
    finally:
        src.close()
    dst.close()
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, final)

    # Prune only after a good copy exists, and only files this function names.
    copies = sorted(p for p in dest.iterdir() if NAME.fullmatch(p.name))
    for old in copies[:-keep] if keep > 0 else []:
        old.unlink()
    return final, curve_rows
