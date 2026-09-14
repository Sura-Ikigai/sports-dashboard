# Running the daily prediction job

> Ops note, 2026-09-14. Parked here to pick up when you set up scheduling.
> Context for *why* this is the shape it is: `docs/plans/next-up.md` item 1.

## The command

Run from the **repository root** — both paths are relative to it.

```bash
PYTHONPATH=backend backend/venv/bin/python -m model.job \
  "$DATABASE_URL" --artifact models/logistic-2026.json --season 2027
```

## What it needs in place

| | |
|---|---|
| `DATABASE_URL` | a Postgres holding the **ingested corpus** and the migrated schema — `postgresql://user:pass@host:port/dbname` |
| `models/logistic-2026.json` | the frozen artifact, version `c97a9b14ddd5`. **Gitignored** — it is not in the repo and has to be present on whatever host runs this |
| migrations | `alembic upgrade head` must have run (it creates `predictions`, `scheduled_games`, `schedule_snapshots`) |
| network | it fetches the live schedule each run |

The artifact being gitignored is the trap worth remembering: a fresh host clones the repo and the
job fails on a missing file, with a message that names the path.

## What one run does

1. Refreshes the live 2027 schedule and records the snapshot.
2. Scores every game tipping off in the next **7 days**.
3. **Appends** a prediction row per game, keyed by `(game_id, model_version, as_of)`.

`as_of` is truncated to the hour, so **two runs in the same hour write nothing the second time** —
a double-fire is a no-op, not a duplicate. Running it more than once a day is harmless.

## Why a missed day is survivable

Every game is re-predicted daily for the seven days before it. Miss a Tuesday and Tuesday's games
still carry Monday's prediction — the accuracy record simply scores a slightly older statement.

**A genuine hole needs ~7 consecutive missed days**, or a game that first appears on the schedule
inside the gap. So this does not need high availability; it needs "usually runs, and says so when
it hasn't."

## Checking it worked

```bash
psql "$DATABASE_URL" -c "
  SELECT max(as_of) AS newest,
         now() - max(as_of) AS age,
         count(*) FILTER (WHERE as_of > now() - interval '24 hours') AS written_today
  FROM predictions;"
```

## The watchdog — the piece that does not exist yet

The failure mode is **silent**: the site keeps working, the API returns valid responses, and the
upcoming page shows an empty window that reads exactly like "no games scheduled." Nothing notices.

Alert when this returns a row:

```sql
SELECT max(as_of) FROM predictions HAVING max(as_of) < now() - interval '26 hours';
```

26 hours rather than 24 so a daily job that drifts by an hour does not page anybody.

## Scheduling it

Not Claude Code's scheduled tasks — those are session-only and expire after 7 days, against a
180-day season.

- **macOS, this laptop** — `launchd` with `StartCalendarInterval`. Runs missed jobs on wake, which
  plain `cron` does not. Good enough given the seven-day redundancy above.
- **A real host** — the honest answer for a season-long trial, and it closes **F-006**
  (`docker-compose.prod.yaml` is still a 0-byte file). Compose stack plus a cron line.
- **GitHub Actions** — needs the database reachable from CI, so it implies hosted Postgres anyway.
  Its cron is best-effort and can drift tens of minutes, which is fine for a daily job.

## Deadline

**Opening night is 2026-10-20.** The job has to be running *and verified* by then, not merely
runnable.
