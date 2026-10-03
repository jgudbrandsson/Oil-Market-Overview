# Fixtures

These files follow the documented EIA API v2 `seriesid` response shape. They
were written by hand because the build environment can't reach api.eia.gov.

Replace them with real captured responses on the Pi:

```sh
curl -s "https://api.eia.gov/v2/seriesid/PET.RWTC.D?api_key=$EIA_API_KEY&start=2026-09-28" > eia_rwtc_daily.json
```

Before committing a captured file, remove `api_key` from its `request.params`
block if EIA echoes it back.
