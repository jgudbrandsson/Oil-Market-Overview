import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .config import Series

SQL_DIR = Path(__file__).resolve().parent.parent / "db"


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str, readonly: bool = False) -> sqlite3.Connection:
    """Open the database. Transactions are explicit (see `transaction`)."""
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All-or-nothing write. A crash or power loss mid-way leaves no partial run."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def init_db(conn: sqlite3.Connection, series: Iterable[Series]) -> None:
    """Create tables if missing, recreate views, and sync `series` from config.

    Safe to run before every fetch: it never drops a table or a row of data.
    """
    conn.executescript((SQL_DIR / "schema.sql").read_text())
    conn.executescript((SQL_DIR / "views.sql").read_text())
    with transaction(conn):
        conn.executemany(
            """INSERT INTO series (series_key, source, source_id, unit, frequency)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (series_key) DO UPDATE SET
                 source = excluded.source, source_id = excluded.source_id,
                 unit = excluded.unit, frequency = excluded.frequency""",
            [(s.key, s.source, s.source_id, s.unit, s.frequency) for s in series],
        )


def start_run(conn: sqlite3.Connection, source: str) -> int:
    """Log a run as 'running'. A previous run still marked 'running' was cut off
    (power loss, kill) before it could finish, so mark it as an error first."""
    with transaction(conn):
        conn.execute(
            """UPDATE fetch_log SET status = 'error', finished_at = ?,
                 error = 'interrupted before finishing'
               WHERE source = ? AND status = 'running'""",
            (utcnow(), source),
        )
        cur = conn.execute(
            "INSERT INTO fetch_log (source, started_at, status) VALUES (?, ?, 'running')",
            (source, utcnow()),
        )
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection, run_id: int, rows: int, error: str | None = None
) -> None:
    with transaction(conn):
        conn.execute(
            """UPDATE fetch_log SET status = ?, finished_at = ?, rows_upserted = ?, error = ?
               WHERE run_id = ?""",
            ("error" if error else "ok", utcnow(), rows, error, run_id),
        )


def upsert_observations(
    conn: sqlite3.Connection, rows: Iterable[tuple[str, str, float]], fetched_at: str
) -> int:
    """Insert or overwrite (series_key, obs_date, value) rows. Last write wins."""
    data = [(k, d, v, fetched_at) for k, d, v in rows]
    conn.executemany(
        """INSERT INTO observations (series_key, obs_date, value, fetched_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (series_key, obs_date) DO UPDATE SET
             value = excluded.value, fetched_at = excluded.fetched_at""",
        data,
    )
    return len(data)
