---
name: add-panel
description: Add or change a panel on the oil-dash board (curve, positioning, supply, signal strip, 5-year bands). Use when an issue asks for something new on the dashboard page.
---

# Add a panel

The board is one page (`dashboard/index.html`) fed by one JSON route (`/api/board` in `oildash/web.py`). Visual reference with sample data: `docs/mockup.html` — reuse its markup and CSS classes for the panel you're building.

## Steps

1. **Data exists?** The series must already be fetched (see the `add-fetcher` skill). Don't add a fetcher inside a panel PR.
2. **Derived numbers go in a view** in `db/views.sql` (`DROP VIEW IF EXISTS` + `CREATE VIEW`). No maths in Python or JS beyond unit conversion and week-over-week changes.
3. **Freshness.** Add the panel and its series keys to `PANELS` in `oildash/web.py`. That gives it a badge and scoped error notes for free.
4. **JSON.** Extend `board()` with the panel's data: lists of `[date, value, ...]` rows, oldest first, limited to the window the chart shows. Convert units here (e.g. kbbl → mb) and say so in a comment.
5. **Page.** In `dashboard/index.html`:
   - copy the panel's section from `docs/mockup.html` (header with `id="b-<panel>"` badge and `id="n-<panel>"` note);
   - render it in `render(b)` with `tiles(...)` and `chart(...)`; handle "no data yet" with `empty(...)`;
   - colors only from tokens (`--s1..--s3` for series, never status colors for series); one y-axis per chart; legend when 2+ series.
6. **Tests** in `tests/test_web.py`: the JSON shape, unit conversion, stale when a series is missing.
7. **Look at it.** Seed a scratch DB with a year of made-up rows, run `OIL_DASH_DB=<scratch> python3 -m oildash serve`, screenshot desktop and 400px wide with Playwright (Chromium at `/opt/pw-browsers`). Check: no horizontal overflow at 400px, labels don't collide, no console errors. Don't commit the scratch DB.
8. **Docs.** If the panel adds a view or changes the layout, update `DESIGN.md` §5–6.

## Check before pushing

```sh
python3 -m pytest -q && ruff check .
```
