# Project log — Sports Dashboard

<!--
APPEND-ONLY CHRONOLOGY (SYSTEM.md §3, index-vs-log split). The tracker
(docs/IMPLEMENTATION.md) holds CURRENT STATE (overwritten); this holds HISTORY (never rewritten).
Newest entry on top. Keep it lean — a few lines per session; git history carries the detail.
-->

## 2026-08-09 — Dev-System instantiated onto the Stage 3 dashboard

- Cloned `sports-dashboard` into the LabRoom `Sports/` workspace alongside a staged 2.35 GB Kaggle
  NBA corpus (`archive/`). Audited all three: the Dev-System factory, the repo, and the dataset.
- Instantiated the Dev-System (T-001, `BUILT`): 5 agents + 2 Stop hooks + `settings.json` +
  `CANON-VERSION` under `.claude/`; `checks/` and a new `stacks/nextjs-fastapi-postgres.md` overlay;
  tracker, log and learning-notes under `docs/`; root `CLAUDE.md`; `.github/workflows/gate.yml`.
- Authored the `nextjs-fastapi-postgres` stack overlay in the factory — no existing overlay fit a
  plain-Postgres, no-RLS project (D-002).
- Opened F-001..F-007 from the instantiation audit. The load-bearing ones: no authorization boundary
  exists at all (F-001), the staged corpus is three seasons stale (F-002), and the app's ESPN team IDs
  do not share a key space with the corpus's NBA.com team IDs (F-005).
- Gate verified green locally, all 7 checks: `gate-completeness`, `review-ledger-current`,
  fe-typecheck, fe-lint, fe-unit (11 tests), be-lint, be-unit (12 tests). Backend needed a Python
  3.11 venv — system Python is 3.10 and `nba_service` uses `datetime.UTC` (3.11+).
- Researched T-002 and verified by download: sportsdataverse-data carries ESPN-keyed NBA schedules
  and play-by-play through the completed 2025-26 season, plus an `espn_team_id` → `nba_team_id`
  crosswalk that answers F-005 directly.
- Walked the findings with the user and closed the data question: D-004 picks sportsdataverse
  (ESPN-keyed, seasons 2022–2026); `nba_api` was rejected specifically because it pulls live from
  stats.nba.com, which drops datacenter IPs. D-005 dropped and **deleted** the Kaggle corpus, closing
  F-002/F-003/F-004 as moot and F-005 as designed-out. D-006 holds Python at 3.11.
- Promoted the overlay to the factory as Dev-System/main `8398baa` and re-stamped `CANON-VERSION`
  against it, so the overlay reads as canon rather than project drift at reconcile time.
- **Ran the review gate for real, and it caught things.** Round 1 @ `d8e3515`: both reviewers ⛔.
  The blocker (F-008) was mine — I installed the builder agents unmodified from Supabase canon, so
  `backend-engineer` was telling builders "RLS ships with the schema" in a project whose whole
  premise is that no RLS exists. Also F-009 (gate.yml leaked `GITHUB_TOKEN` into `.git/config` for
  every manifest `run:` to read), F-010, F-011, F-012, F-013. Remediated in `d0e661d`; round 2
  returned ✅✅ and verified each fix was real rather than cosmetic. T-001 → `REVIEWED`.
- Learned a mechanic worth keeping: the review commit must be **`docs/`-only**, because
  `review-ledger-current` computes the code tip excluding `docs/` — bundling any code edit with the
  ledger write invalidates the ✅ it is recording. F-016/F-017 were therefore deferred, not fixed.
- Ended at: T-001 `REVIEWED` at `d0e661d` on `chore/dev-system-instantiation`. Next is the PLAN phase
  for the modeling layer.
