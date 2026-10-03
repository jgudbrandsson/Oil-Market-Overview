-- Raw data only. Derived signals live in views.sql.
-- Every statement is idempotent: running this file again never drops data.

CREATE TABLE IF NOT EXISTS series (
    series_key  TEXT PRIMARY KEY,          -- 'wti_spot'
    source      TEXT NOT NULL,             -- 'eia' | 'fred' | 'cftc' | 'bakerhughes' | 'yahoo' | 'jodi'
    source_id   TEXT NOT NULL,             -- 'PET.RWTC.D'
    unit        TEXT NOT NULL,
    frequency   TEXT NOT NULL              -- 'D' | 'W' | 'M'
);

CREATE TABLE IF NOT EXISTS observations (
    series_key  TEXT NOT NULL REFERENCES series(series_key),
    obs_date    TEXT NOT NULL,             -- ISO date of the period the value describes
    value       REAL NOT NULL,
    fetched_at  TEXT NOT NULL,             -- UTC; last write wins on revision
    PRIMARY KEY (series_key, obs_date)
) WITHOUT ROWID;

-- Curve snapshots: the one dataset that cannot be re-downloaded later. Never drop.
CREATE TABLE IF NOT EXISTS curve_snapshots (
    snapshot_date  TEXT NOT NULL,          -- trade date
    root           TEXT NOT NULL,          -- 'CL' | 'BZ' | 'RB' | 'HO'
    contract_month TEXT NOT NULL,          -- 'YYYY-MM' delivery month
    settle         REAL NOT NULL,
    source         TEXT NOT NULL,          -- 'eia' | 'yahoo'
    PRIMARY KEY (snapshot_date, root, contract_month)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS fetch_log (
    run_id        INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,           -- 'running' | 'ok' | 'error'
    rows_upserted INTEGER,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS fetch_log_source ON fetch_log (source, run_id);
