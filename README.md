# Oil-Market-Overview
iterative approach to create a dashboard that shows the oil market from drilling to prices at the pump

Self-hosted (Raspberry Pi) dashboard built only on free data: EIA, FRED, CFTC COT, Baker Hughes, Yahoo futures, JODI.

See [DESIGN.md](DESIGN.md) for constraints, sources, schema, signals, and failure modes.

## Run it on the Pi

Needs Python 3.11+ (Raspberry Pi OS bookworm has it). The fetchers use only the standard library.

```sh
sudo useradd --system --home /var/lib/oil-dash oildash
sudo git clone https://github.com/jgudbrandsson/Oil-Market-Overview /opt/oil-dash
cd /opt/oil-dash && sudo python3 -m venv .venv

# API key: free from https://www.eia.gov/opendata/register.php
sudo mkdir -p /etc/oil-dash
echo 'EIA_API_KEY=your-key' | sudo tee /etc/oil-dash/env >/dev/null
sudo chmod 600 /etc/oil-dash/env

sudo cp systemd/oil-fetch-*.service systemd/oil-fetch-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oil-fetch-eia.timer oil-fetch-eia-curve.timer oil-fetch-yahoo.timer

# First run: a normal fetch (creates /var/lib/oil-dash), then backfill history
sudo systemctl start oil-fetch-eia.service
sudo -u oildash env $(sudo cat /etc/oil-dash/env) OIL_DASH_DB=/var/lib/oil-dash/oil.db \
  .venv/bin/python -m oildash fetch eia --start 2000-01-01
journalctl -u oil-fetch-eia -n 20   # look for failed series ids

# Curve: start capturing today (Yahoo's back months can't be fetched later)
sudo systemctl start oil-fetch-eia-curve.service oil-fetch-yahoo.service
journalctl -u oil-fetch-yahoo -n 20  # look for failed contracts
# EIA contracts 1-4 have history back to 1983
sudo -u oildash env $(sudo cat /etc/oil-dash/env) OIL_DASH_DB=/var/lib/oil-dash/oil.db \
  .venv/bin/python -m oildash fetch eia-curve --start 1983-04-01
```

Backup (the curve history exists nowhere else). Point it at a folder on a USB
disk or NAS mount, owned by `oildash`; it refuses a folder on the SD card:

```sh
sudo mkdir -p /mnt/backup/oil-dash && sudo chown oildash /mnt/backup/oil-dash
echo 'OIL_DASH_BACKUP_DIR=/mnt/backup/oil-dash' | sudo tee -a /etc/oil-dash/env >/dev/null
sudo cp systemd/oil-backup.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oil-backup.timer
sudo systemctl start oil-backup.service && journalctl -u oil-backup -n 5
```

Dashboard:

```sh
sudo cp systemd/oil-dash-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oil-dash-web
# Reachable only from your tailnet, over HTTPS, at https://<pi-name>.<tailnet>.ts.net/
sudo tailscale serve --bg 8710
```

The server binds to localhost and only reads the database. If the health
pipeline already uses `tailscale serve` on `/`, add `--set-path=/oil` instead.

## Develop

```sh
pip install -e '.[dev]'
pytest && ruff check .
OIL_DASH_DB=./dev.db python -m oildash serve   # http://127.0.0.1:8710
```
