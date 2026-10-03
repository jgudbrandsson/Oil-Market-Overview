---
name: add-fetcher
description: Add a new data source fetcher to oil-dash (e.g. CFTC COT, Baker Hughes, Yahoo curve, JODI). Use when an issue asks to fetch a new source into SQLite.
---

# Add a fetcher

Reference implementation: `oildash/fetchers/eia.py`. Copy its shape; don't invent a new one.

## Steps

1. **Series.** Add each series the panel needs to `config.toml` (`key`, `source`, `source_id`, `unit`, `frequency` D/W/M). Nothing the panels don't use.
   Curve data is the exception: it goes to `curve_snapshots`, not `observations`, and has no `series` rows.
2. **Fetcher module** `oildash/fetchers/<source>.py` with:
   - `SOURCE = "<source>"`
   - a pure `parse(payload_or_bytes, ...) -> rows` that raises a clear error when the format changes (missing column, sheet, key). Never return an empty list silently on a format change.
   - `run(conn, series, ..., get=<injectable http function>)` that:
     calls `db.start_run`, fetches each series in its own `try` (one bad series must not stop the others),
     writes all rows in **one** `db.transaction`, then `db.finish_run` with the joined per-series errors.
   - A trailing window on every run (so missed runs self-heal), plus a way to backfill (`--start`).
   - Error messages must never contain API keys or full request URLs with keys.
3. **CLI.** Add the source to the `fetch` choices in `oildash/__main__.py` and dispatch to its `run`. Keys come from the environment only.
4. **Fixtures.** Save a real response in `tests/fixtures/` (strip any echoed key). If the source is unreachable from the build container, hand-write it to the documented format and say so in `tests/fixtures/README.md` and the PR.
5. **Tests** `tests/test_<source>.py`, mirroring `tests/test_eia.py`: parse happy path, nulls/gaps, format change raises, one failing series doesn't block others, rerun is idempotent, failed write leaves no partial data, no secret in `fetch_log`.
6. **systemd.** Add `systemd/oil-fetch-<source>.service` + `.timer` copied from the EIA pair. Timer: schedule from `DESIGN.md` §8, `Persistent=true`, timezone `America/New_York`.
7. **Docs.** Update `DESIGN.md` §2 (series table) and §8 (schedule) if they changed; add the timer to the README install block.

## Curve fetchers (`eia_curve.py`, `yahoo.py`)

- Write with `db.upsert_curve` (rows `(snapshot_date, root, contract_month, settle, source)`). It never lets a `yahoo` row overwrite an `eia` row, and never deletes. Never add a cleanup or migration touching `curve_snapshots`: it can't be re-downloaded.
- Map trade date + position to delivery month with `oildash/contracts.py`. A new root (BZ, RB, HO) needs its own expiry rule there, with tests against a few known expiry dates.
- Trade dates are New York dates (`ZoneInfo("America/New_York")`), never the Pi's local date.
- Keep only bars dated on or before both today and the contract's last trading day.
- Yahoo: one request per contract, a pause between them, a browser-like User-Agent. Check `meta.symbol` matches the request.

## Check before pushing

```sh
python3 -m pytest -q && ruff check .
OIL_DASH_DB=/tmp/t.db python3 -m oildash fetch <source>   # expect a clean failure message if offline
```
