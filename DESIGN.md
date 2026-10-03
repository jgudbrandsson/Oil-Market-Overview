# oil-dash — design

Hand-designed, constraint-first. Anything not justified by a constraint below is out.

## 1. Binding constraints

Every proposal gets checked against this list. Fails one → rejected, no debate.

| # | Constraint | Consequence |
|---|---|---|
| C1 | Runs on the Pi, alongside the health pipeline | SQLite, Python, systemd. No Docker-compose zoo, no Postgres. |
| C2 | $0 running cost | Free APIs only. No paid data, no cloud hosting. |
| C3 | Solo operator, accessed over Tailscale from a laptop | No auth, no multi-user, no mobile layout. |
| C4 | Data is daily at best, mostly weekly | Fetch on a schedule, not on page load. No streaming, no websockets. |
| C5 | Separate repo, separate service | Own venv, own DB file, own systemd units, own port/path (`/oil`). Shares nothing with the health pipeline. |
| C6 | Broken source ≠ broken board | A failed fetch makes one panel stale and says so; it never blocks other panels. |

### Explicit non-goals

Config UI, audit logs, alerting, ML/forecasting, user accounts, mobile app, real-time ticks, tanker tracking. Revisit only if a concrete question can't be answered without one.

## 2. Data sources

| Source | What | Cadence / release | Access | Fragility |
|---|---|---|---|---|
| EIA API v2 | Spot crude + products, weekly stocks, refinery util, production, NYMEX futures M1–M4 | Daily prices; weekly stocks Wed 10:30 ET (Thu on holiday weeks) | Free key | Low — official |
| FRED | Macro joins: broad dollar index, CPI, industrial production | Daily–monthly | Free key | Low |
| CFTC COT (Socrata API) | Disaggregated futures-only, managed money vs producer/merchant | Fri 15:30 ET for Tuesday data | No key | Low |
| Baker Hughes | US oil rig count | Fri 13:00 ET | XLSX download, no API | Medium — URL/format changes |
| Yahoo Finance (chart endpoint, the one `yfinance` wraps) | Individual contract months (e.g. `CLZ26.NYM`) for the full 12–24 month curve | Daily after settle | Unofficial, no key; called with the standard library | **High** — breaks without notice, expired contracts vanish |
| JODI | Monthly global stocks/demand by country | Monthly, ~2 month lag | CSV bulk download | Medium |

**Challenge on yfinance:** it's the only free source for the back of the curve, but it's an unofficial scraper. Use EIA's M1–M4 contracts (`RCLC1`..`RCLC4`) as the authoritative short curve and treat Yahoo as a best-effort extension to M12. If Yahoo breaks, the curve panel degrades to M1–M4 instead of disappearing.

Both go to `curve_snapshots`, not `observations`. EIA only names "contract 1..4", so `oildash/contracts.py` maps each trade date to delivery months with the CL expiry rule (3 business days before the 25th of the prior month; 4 if the 25th isn't a business day). Where both sources cover the same contract month, EIA's row wins and Yahoo never overwrites it. EIA M1–M4 history can be re-fetched (`fetch eia-curve --start 1983-04-01`); only Yahoo's back months are truly lost if not captured.

Not available free: true replenishment lead times, tanker flows (Kpler/Vortexa), freight rates (Baltic). Proxy = curve shape (§4).

### Initial series (verify IDs on first fetch)

| Key | Source id | Unit |
|---|---|---|
| wti_spot | EIA `PET.RWTC.D` | $/bbl |
| brent_spot | EIA `PET.RBRTE.D` | $/bbl |
| gasoline_nyh | EIA `PET.EER_EPMRU_PF4_Y35NY_DPG.D` (NY Harbor conventional regular, the free daily proxy for RBOB) | $/gal |
| ulsd_nyh | EIA `PET.EER_EPD2DXL0_PF4_Y35NY_DPG.D` | $/gal |
| crude_stocks_ex_spr | EIA `PET.WCESTUS1.W` | kbbl |
| cushing_stocks | EIA `PET.W_EPC0_SAX_YCUOK_MBBL.W` | kbbl |
| gasoline_stocks | EIA `PET.WGTSTUS1.W` | kbbl |
| distillate_stocks | EIA `PET.WDISTUS1.W` | kbbl |
| refinery_util | EIA `PET.WPULEUS3.W` | % |
| us_crude_prod | EIA `PET.WCRFPUS2.W` | kbbl/d |
| wti_fut_m1..m4 | EIA `PET.RCLC1.D`..`RCLC4.D` → `curve_snapshots` (source `eia`) | $/bbl |
| CL M1–M12 | Yahoo `CL<month code><yy>.NYM` → `curve_snapshots` (source `yahoo`) | $/bbl |
| usd_broad | FRED `DTWEXBGS` | index |
| cot_wti | CFTC disaggregated, contract `067651` | contracts |
| rigs_oil_us | Baker Hughes | count |

Start with these. Add series only when a panel needs them.

## 3. Architecture

```
systemd timer ──► oildash/fetchers/<source>.py ──► observations / curve_snapshots (SQLite, WAL)
                                                │
                                   transforms (SQL views, computed on read)
                                                │
                                   dashboard (read-only connection)
```

- **Fetchers** write raw data only. One process per source, one transaction per run, idempotent upserts.
- **Transforms** are SQL views, not stored tables. The data is small enough that recomputing on read is free, and there's no derived state to go out of sync.
- **Dashboard** opens the DB read-only. It never writes.

### Dashboard layer — decision

**Decided: hand-rolled** (see #7). A standard-library Python server (`python -m oildash serve`) serves one HTML page and one JSON route, `/api/board`, read from the views over a read-only connection. No framework and no extra packages. Reasons: the layout (signal strip, stale notes, 5-year band) doesn't fit Grafana well; the layout lives in git where agents can work on it; about 40 MB of memory instead of about 200 MB. Visual reference: `docs/mockup.html`.

## 4. Schema

```sql
PRAGMA journal_mode = WAL;

CREATE TABLE series (
    series_key  TEXT PRIMARY KEY,          -- 'wti_spot'
    source      TEXT NOT NULL,             -- 'eia' | 'fred' | 'cftc' | 'bakerhughes' | 'yahoo' | 'jodi'
    source_id   TEXT NOT NULL,             -- 'PET.RWTC.D'
    unit        TEXT NOT NULL,
    frequency   TEXT NOT NULL              -- 'D' | 'W' | 'M'
);

CREATE TABLE observations (
    series_key  TEXT NOT NULL REFERENCES series(series_key),
    obs_date    TEXT NOT NULL,             -- ISO date of the period the value describes
    value       REAL NOT NULL,
    fetched_at  TEXT NOT NULL,             -- UTC; last write wins on revision
    PRIMARY KEY (series_key, obs_date)
) WITHOUT ROWID;

-- Curve snapshots: the one dataset that cannot be re-downloaded later.
CREATE TABLE curve_snapshots (
    snapshot_date  TEXT NOT NULL,          -- trade date
    root           TEXT NOT NULL,          -- 'CL' | 'BZ' | 'RB' | 'HO'
    contract_month TEXT NOT NULL,          -- 'YYYY-MM' delivery month
    settle         REAL NOT NULL,
    source         TEXT NOT NULL,          -- 'eia' | 'yahoo'
    PRIMARY KEY (snapshot_date, root, contract_month)
) WITHOUT ROWID;

CREATE TABLE fetch_log (
    run_id      INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,             -- 'running' | 'ok' | 'error'
    rows_upserted INTEGER,
    error       TEXT
);
```

Deliberate choices:
- **Long/narrow `observations`**, not one table per source. Adding a series is a row in `series`, not a migration.
- **No revision history.** EIA revises weekly numbers; last-write-wins is fine for a solo board. If revisions ever matter, add `vintage` to the key — not before.
- **COT stored as several series** (`cot_wti_mm_long`, `cot_wti_mm_short`, `cot_wti_oi`), not a bespoke table.
- **Staleness** = `max(obs_date)` per series compared to its expected frequency, plus the last `fetch_log` status. Shown on each panel.

### Size check

Daily series: ~15 × 260 trading days × 25 years ≈ 100k rows. Curve: 4 roots × 18 months × 260 days ≈ 19k rows/year. At ~60 bytes/row that's **single-digit MB after a decade**. DB size is a non-issue; don't design for it.

## 5. Derived signals (SQL views)

| View | Formula | Reads as |
|---|---|---|
| `v_brent_wti` | Brent − WTI | Transatlantic tightness / US export pull |
| `v_crack_321` | (2 × gasoline × 42 + 1 × ULSD × 42 − 3 × WTI) / 3 | Refinery margin, $/bbl. Note the ×42 gal→bbl conversion |
| `v_curve_slope` | M1 − M12 (fallback M1 − M4), and as % of M1 | > 0 backwardation = barrels wanted now; < 0 contango = surplus/storage play |
| `v_stocks_vs_5y` | Weekly stock vs min/avg/max of same ISO week over prior 5 years | Deviation from band matters, level doesn't |
| `v_cot_mm_net` | (MM long − MM short) / open interest | Speculative positioning, normalised |
| `v_rigs` | Oil rig count, 13-week change | Supply pipeline 6–12 months out |

Two known traps to handle in the views, not later:
- **5-year band and 2020.** As of 2026 data, 2020 has left the 5-year window, but historical views (2021–2025) still include COVID-distorted weeks. Flag it on the chart when 2020 is in the window; don't silently drop it.
- **Spot vs futures dates.** EIA spot lags by a day or two; align on `obs_date`, never on fetch time.

## 6. How the panels fit together

Price is the output. The other panels explain it:

1. **Price** — WTI, Brent, Brent−WTI, 3-2-1 crack.
2. **Urgency (curve)** — slope and full curve today vs 1 month / 1 year ago.
3. **Buffer (inventories)** — crude, Cushing, gasoline, distillate vs 5-year band.
4. **Future supply** — rigs, US production.
5. **Positioning** — COT managed-money net.

Rule of thumb: a price move backed by backwardation **and** below-band stocks is fundamental. A move with a flat curve, normal stocks and extreme MM positioning is froth. Crack spreads tell you whether the pull is coming from refiners (real demand) or just crude.

## 7. Failure modes — "what breaks if the Pi loses power mid-fetch?"

| Failure | Outcome | Mitigation |
|---|---|---|
| Power loss mid-transaction | SQLite WAL rolls back the uncommitted run; DB stays consistent | One transaction per source run. `fetch_log` row stays `running` → next run marks it `error` and retries. |
| Missed scheduled run (Pi off) | Data gap until next run | systemd timer with `Persistent=true` runs on boot. Fetchers always request a trailing window (e.g. last 30 days), so gaps self-heal via upsert. |
| SD card corruption | DB lost | Everything except `curve_snapshots` is re-downloadable from source history → `python -m oildash fetch <source> --start 2000-01-01` rebuilds it. Back up **only** what can't be re-fetched: nightly `python -m oildash backup` (SQLite online backup, integrity-checked, 14 dated copies kept) to a non-SD location. It refuses a folder on the same disk as the DB, so an unmounted USB disk fails loudly instead of backing up to the SD card. |
| Source down / format change | That panel goes stale | Per-source process + staleness badge (C6). Fetcher fails loudly in `journalctl`. |
| Yahoo breaks permanently | Curve shrinks to M1–M4 | EIA futures as the authoritative fallback (§2). |
| Clock skew after power loss | Wrong `fetched_at` | Cosmetic only — `obs_date` comes from the source, not the clock. |

The key insight: the curve history is the only irreplaceable data. Yahoo drops expired contracts and EIA only has M1–M4. That's what the backup exists for.

## 8. Schedule (systemd timers, times in ET)

One service + timer pair per source (C6). Every fetch first syncs tables, views and the `series` table from `config.toml`.

| Unit | When | Why |
|---|---|---|
| `oil-fetch-eia` | Mon–Fri 19:00; Wed, Thu 12:00 | Daily spot after NYMEX settle; WPSR stocks (Thursday run covers holiday weeks, upserts make it harmless) |
| `oil-fetch-eia-curve` | Mon–Fri 19:00 | EIA futures contracts 1–4 |
| `oil-fetch-yahoo` | Mon–Fri 17:15 | CL contracts M1–M12. In the 17:00–18:00 halt, so the newest bar is the finished day and not the evening session |
| `oil-fetch-cftc`, `oil-fetch-bakerhughes` | Fri 17:00 | COT, rig count |
| `oil-fetch-jodi` | 20th of month | JODI |
| `oil-backup` | Daily 03:00 | Copy to off-SD storage (`OIL_DASH_BACKUP_DIR`) |

## 9. Repo layout

```
oil-dash/
├── oildash/         # python -m oildash {init-db, fetch <source>, serve, backup}; web.py serves the board
│   ├── contracts.py # CL expiry calendar: trade date + position → delivery month
│   └── fetchers/    # eia.py, eia_curve.py, yahoo.py, then cftc.py, bakerhughes.py, …
├── db/              # schema.sql, views.sql
├── dashboard/       # index.html: the board, fed by /api/board
├── docs/            # mockup.html: visual reference with sample data
├── systemd/         # *.service, *.timer — versioned
├── config.toml      # series list, paths; API keys via env/EnvironmentFile
└── tests/           # fetcher parsing against saved fixtures
```

## 10. Build order

Each step is usable on its own; stop at any point.

1. Schema + EIA fetcher + price and inventory panels. Already covers most of the questions.
2. Curve snapshots (EIA M1–M4, then Yahoo extension). Start early — curve history can't be backfilled, every day not captured is lost.
3. COT + Baker Hughes.
4. 5-year bands and staleness badges.
5. JODI, FRED macro — only if a question needs them.

## Open decisions (yours)

- Backup target: USB disk or NAS mount (the code takes any mounted folder). A private GitHub repo/release would need a token and its own code.
- Which product curves to snapshot beyond CL: BZ, RB, HO cost nothing extra in storage, but each Yahoo root is another thing to break.
