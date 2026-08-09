# Project log — Sports Dashboard

<!--
APPEND-ONLY CHRONOLOGY (SYSTEM.md §3, index-vs-log split). The tracker
(docs/IMPLEMENTATION.md) holds CURRENT STATE (overwritten); this holds HISTORY (never rewritten).
Newest entry on top. Keep it lean — a few lines per session; git history carries the detail.
-->

## 2026-08-09 — Phase 1 blocked by a canon bug (F-018); fixed and promoted

- Tried to start T-005 and found the gate would reject it. `review-ledger-current` gated every
  `REVIEWED` **and `DONE`** task against the repo's *current* code tip, so any commit invalidated every
  completed review at once — N done tasks meant N re-reviews per commit. Verified with
  `--code-head abc1234` before touching anything. The check was unusable past a project's first phase;
  this is the first instantiation, so it surfaced here first.
- Fixed by gating `REVIEWED` only (D-018) and **promoted to canon** as Dev-System `be5f6dc`;
  CANON-VERSION re-stamped. Not patched locally — every project stamped from this canon has the bug.
- Editing `checks/lib/tracker.mjs` fired F-014's `first-edit-to-checks-lib` trigger, so that deferral
  came due and was closed: `meta-unit` wired into the manifest and CI, lockfile committed, vitest
  pinned. It paid for itself immediately — the F-018 edit broke two fixtures that used `DONE` as their
  gated status, and no CI anywhere would have caught them.
- F-016 (`models/` → `/models/`, anchored) and F-017 (stale `ui-ux-reviewer` lines) also closed.
- T-001 → `BUILT`: its deliverable changed after review, which is exactly what that transition means.
- Round 3 review: logic ✅, **security ⛔ — the F-018 fix was over-scoped (F-019).**
  `evaluateLedgerCurrency` did two independent jobs; only *currency* was meaningless for frozen
  history, but removing `DONE` from the gate dropped *verdict* too. That left "never `DONE` until
  `REVIEWED`" with no mechanical enforcement anywhere, and opened a one-word bypass: a `REVIEWED` task
  failing on a stale review could be cleared by editing its status to `DONE`. The auditor demonstrated
  it with fixtures. Corrected: verdict gates `REVIEWED`+`DONE`, currency gates `REVIEWED` only;
  promoted as canon `6f49f29`.
- Also closed F-020 (the check's own success message asserted a guarantee it no longer made), F-021
  (vitest 2.1.9 → 4.1.10, matching the frontend; npm audit 5 advisories → 0), F-022, F-023.
- Ended at: T-001 owes review round 4. Phase 1 opens only after this phase merges to `main` — the
  system correctly refuses to let the next phase start on an unmerged, un-re-reviewed one.

## 2026-08-09 — PLAN phase: grill-me → PLAN-v1 (Phase 1 = analytical core)

- Ran grill-me over the prediction-model brief. Two of its premises did not survive: the 58% home-court
  baseline (really **55.56%** over 6,615 games — pre-2020 averages 59.3%, 2021+ 55.2%, a regime break at
  the COVID seasons that never reverted, so 58% was an artifact of the era the deleted Kaggle corpus
  averaged over) and the "Next.js/FastAPI/Supabase" stack, which this project is not.
- Ten forks resolved as D-007..D-017. The load-bearing ones: calibration made a **ship gate** alongside
  62% accuracy; one shared feature function for training and inference so train/serve skew is
  structurally impossible; expanding-window walk-forward because one sealed season carries ±2.6 points
  of noise on the ship decision; and Phase 1 scoped to the analytical core **offline**, so the plumbing
  is never built for a model that might not clear the bar.
- Noted a timing fact that shaped sequencing: the next NBA game is **2026-10-03** (season opens 09-30),
  so there are ~7.5 weeks with no games — almost exactly the window Phase 1 needs, and the live board
  has nothing to show before then regardless.
- to-PRD wrote `docs/plans/PLAN-v1.md` (ACTIVE, copied to `PLAN-current.md`), decomposed into T-005..
  T-010. T-003 → `BUILT`. Security notes captured at plan time, including the one that matters most:
  the serialized model artifact is an arbitrary-code-execution vector, and Phase 2 loads it inside the
  API service.
- Ended at: nothing built. Next is T-005 (`backend-engineer`), which is also this cycle's first
  non-docs commit and therefore fires the F-016/F-017 revisit trigger.

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
