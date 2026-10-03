# CLAUDE.md

Self-hosted oil market dashboard for one person, running on a Raspberry Pi. `DESIGN.md` is the source of truth — read §1 (constraints) before proposing anything.

## Binding constraints

Every change must satisfy all six. If one fails, the change is out — don't argue around it.

- **C1** Runs on the Pi: Python, SQLite (WAL), systemd. No Docker stacks, no Postgres.
- **C2** $0: free data sources and self-hosting only.
- **C3** Solo operator over Tailscale: no auth, no multi-user, no mobile layout.
- **C4** Data is daily at best, mostly weekly: scheduled fetches, never on page load.
- **C5** Separate service: own venv, DB, systemd units, port. Shares nothing with other Pi services.
- **C6** One broken source only stales its own panel; it never blocks the others.

## Non-goals

Config UI, audit logs, alerting, ML/forecasting, user accounts, real-time data, tanker tracking. Don't add them or build "hooks" for them.

## Working rules

- Fetchers write raw data only (`observations`, `curve_snapshots`). Derived signals are SQL views. The dashboard opens the DB read-only.
- Fetches are idempotent upserts over a trailing window, one transaction per source run.
- Add a series only when a panel needs it. Register it in `series`; no new tables per source.
- `curve_snapshots` is the only data that can't be re-downloaded. Never write migrations or cleanups that could drop it.
- Fetcher tests parse saved response fixtures and never hit the network.
- API keys come from the environment and are never committed.

## Done means

Tests pass, the change fits C1–C6, and `DESIGN.md` is updated if the schema, sources, or schedule changed.

## When reviewing or critiquing

Check the change against C1–C6 and ask what breaks (power loss, a source being down, a format change). Don't suggest features.

## Tracking

Work is tracked in GitHub issues (#2–#6, one per build step). One issue per session; the PR says `Closes #N`. Tick the issue's checklist as tasks land. Don't create new issues without asking.

## Talking to the owner

Whenever you tell the owner to merge a PR, explain in plain words what merging it does: what lands on `main`, what changes on the Pi or in future sessions, which issues close, and whether it can be undone.

When comparing options, include a rough Claude token estimate for each: building it, and ongoing upkeep. Give ranges, and say what drives the number (session length, files read, retries).
