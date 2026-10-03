# Fixtures

These files were written by hand because the build environment can't reach
api.eia.gov or Yahoo. The `eia_*` files follow the documented EIA API v2
`seriesid` shape; the `yahoo_*` files follow the shape of Yahoo's v8 chart
endpoint (undocumented, so check them first).

Replace them with real captured responses on the Pi:

```sh
curl -s "https://api.eia.gov/v2/seriesid/PET.RWTC.D?api_key=$EIA_API_KEY&start=2026-09-28" > eia_rwtc_daily.json
```

Before committing a captured file, remove `api_key` from its `request.params`
block if EIA echoes it back.

Yahoo (no key):

```sh
curl -s -A 'Mozilla/5.0' "https://query1.finance.yahoo.com/v8/finance/chart/CLZ26.NYM?range=5d&interval=1d" > yahoo_clz26.json
curl -s -A 'Mozilla/5.0' "https://query1.finance.yahoo.com/v8/finance/chart/CLV26.NYM?range=5d&interval=1d" > yahoo_not_found.json
```

The tests pin the fixture's dates and prices, so update their expected values
when you swap in a captured file.
