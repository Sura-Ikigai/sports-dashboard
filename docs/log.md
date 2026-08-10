# Project log — Sports Dashboard

<!--
APPEND-ONLY CHRONOLOGY (SYSTEM.md §3, index-vs-log split). The tracker
(docs/IMPLEMENTATION.md) holds CURRENT STATE (overwritten); this holds HISTORY (never rewritten).
Newest entry on top. Keep it lean — a few lines per session; git history carries the detail.
-->

## 2026-08-10 — T-006 reviewed (⛔⛔), remediated; T-005 re-reviewed (✅✅)

- Ledger audit first: F-024/F-032/F-035 all had a status true in the code and wrong or absent here.
  Added a *Findings — status index* as an explicitly derived view, because with 43 append-only entries
  "is F-0NN open?" could only be answered by reading the whole document.
- **T-005 re-review ✅✅ at `34759ed`.** Both reviewers proved `406dd09`'s loader edit is
  narrative-only — AST identical after stripping docstrings, every code object byte-for-byte the same
  — and security honored F-041 anyway by re-running the download into an empty temp dir. The F-043
  fix paid for itself immediately: the remediation commit touches no file T-005 owns, so its ✅ stays
  current instead of expiring again.
- **T-006 round 1: ⛔⛔, both earned.** Security found F-044 — the target's own result *was* reachable
  through `history`; the scoreless `Matchup` closed the direct route only, and the target stayed out
  of its own features purely because `as_of == target.date`, a caller-side property. With `as_of` an
  hour later, a 200-80 result moved `point_diff_diff` 6.0 → 32.0. Also F-045 (a consumed generator or
  a mistyped season silently returns priors — indistinguishable from D-015's cold start), F-046
  (`load_games((2022,2022))` → 2,648 games, no error), F-047..F-050.
- **The logic review landed harder, and on the tests rather than the module.** ~60 mutations: 39
  caught, **7 survived**, three of which change 5,096 / 5,809 / 5,283 of the 6,615 real vectors. The
  worst was mine twice over: every form fixture was a *uniform* streak, so first-ten and last-ten were
  identical by construction and **nothing asserted that rolling form is rolling** (F-051, HIGH). And
  the fixture epoch was midnight, so `test_leakage_a_game_one_microsecond_before_as_of_is_included` —
  written precisely to catch day-granularity — could not fail (F-054).
- Remediated all 17. Verified by re-running the reviewers' own mutations rather than by inspection:
  **10/10 now caught**, two only after a second attempt (the first same-instant fixture still let the
  sort-tiebreaker mutation through, because two same-instant games inside the window contribute
  identically however ordered — it only bites when the pair *straddles* the window boundary). Suite
  58 → 79; real-corpus numbers unchanged to the digit, since the fixes changed what is *refused*.
- **D-024 supersedes D-020(4)**, which I had gotten factually wrong: 3 of the 19 neutral-site games
  are All-Star phantoms, and after F-042's exclusion 2022 has *zero*, so fold 1 has 1 neutral game in
  2,643 rows. `home_advantage` is not identifiable in the early folds. A count is not a distribution.

## 2026-08-10 — F-043 fixed: review currency is now per-task, not repo-wide

- Human picked the hard option: fix the rule rather than work around it. `evaluateLedgerCurrency`
  now takes an injected `staleAt(taskId, sha)` and asks whether anything touched *that task's own
  files* since its ✅. Repo-wide comparison kept as the fallback and behind `--repo-wide`. The
  evaluator stays pure; git lives in the CLI, per the pure-core/thin-shell split the gate is built on.
- Ownership is derived from git, not declared in the tracker (D-023) — a hand-maintained `files:`
  field would narrow the gate by editing a document, which is the bypass shape F-019 and F-025 both
  had to close. Attribution reads commit **subjects** only; the F-043 commit's own body names four
  tasks, which is exactly why bodies can't be trusted.
- Two things caught by testing the fix against real history rather than reasoning about it: `review(…)`
  and `docs(…)` commits had to be excluded from attribution, because `0c3b8a9` (`review(T-005)`)
  bundled gate-tooling fixes — so T-005 would have "owned" `checks/lib/tracker.mjs`, and this very fix
  would have invalidated T-005's review. The remedy would have reintroduced the bug it removes.
- 9 new fixture tests, 53 pass including all 44 pre-existing — the change is backward-compatible.
- **The gate is still red on T-005, and now correctly.** `406dd09` edited `loader.py` for the F-039
  docstring fix, a file T-005 owns, so its review is genuinely stale. F-039's own entry predicted that
  cost. The complaint went from "the tip moved" to "`406dd09` touched this task's files" — a true and
  specific statement instead of a spurious one. T-005 needs a cheap re-review; T-006 needs its first.
- Not promoted to canon: the factory is a separate repo under `Client Projects/`, and a promotion is a
  decision to make deliberately rather than as a side effect. Filed in *Future hardening* with the
  warning that `Sports/Dev-System/` is a stale copy at `bd58a22`, not the factory.

## 2026-08-10 — T-006 built: the `features` deep module

- Built `backend/model/features.py` — one interface, `compute_features(history, target, as_of)`,
  emitting `home_advantage`, `form_diff`, `rest_diff`, `point_diff_diff`. Pure: no I/O, no DB, and no
  clock (`as_of` is always a parameter, so every number stays reproducible). T-006 → `BUILT`.
- Honored the security note three independent ways rather than one: the as-of filter runs inside the
  module at query time (strict `<`); the target is a **scoreless `Matchup`**, so a game's own result is
  structurally unreachable from its own features rather than merely filtered out; and `as_of` after
  tip-off raises. D-022 records why splitting the type was worth the small cost at the call site.
- **Made the module standard-library only (D-021), which CI forced.** `gate.yml` installs
  `requirements.txt` and never `requirements-train.txt`, so a pandas import in `features.py` would
  have passed locally and failed in CI — the same split-environment shape as F-037. `dataset.py` is
  the new seam where pandas meets the pipeline. Verified by running the suite with pandas and numpy
  blocked at import: 52 passed, 1 skipped, nothing errored.
- 40 tests, including the leakage property test (200 seeded trials, exact equality) **and a control
  proving it is not vacuous** — a module that ignored history entirely would pass the leakage test
  perfectly, so the control asserts a game dated *before* `as_of` does change the output.
- **Ran it against the real 6,615-game corpus, not just fixtures** — the T-005/F-037 lesson applied
  without being told to. That is what turned up F-042: the corpus carries **42 team ids, not 30**,
  because 10 All-Star exhibition games are in it. Feature computation is provably unaffected (zero
  games mix a phantom id with a real one), but they are 10 junk rows for T-009 to exclude.
- Closed **F-039** (the trigger this commit fired) and **F-024** — the latter turned out to have been
  fixed in `32110ce` already, with only the tracker left saying OPEN.
- **Opened F-043, which blocks a green gate and needs a human call.** Demonstrated with
  `--code-head deadbee` before committing: this commit moves the repo-wide code tip, so T-005's
  `REVIEWED` ✅@`8eae86c` is now stale even though T-006 changed nothing about the loader's behavior.
  This is F-018's failure shape one layer over, and it compounds — once T-006 is `REVIEWED`, T-007's
  first commit invalidates both. Options are in the finding; the honest fix is the per-task
  reviewed-at SHA, which F-019 claimed to have filed in *Future hardening* and had not.
- Ended at: T-006 `BUILT` on `feat/phase-1-analytical-core`. Next is the review gate on T-006.

## 2026-08-09 — T-005 remediated: F-026..F-034 fixed, awaiting re-review

- Remediated the nine open `backend/model/loader.py` findings from the T-005 review gate
  (F-026..F-034; F-035/F-036 were `checks/` and already fixed by the main thread). Both HIGH findings
  verified closed with before/after reproductions in a scratch dir (real `data/` files never touched,
  only copies): F-026 (`load_season` returned unverified data — truncated-2022 fixture went from
  1,266 games/no exception to a hard raise) and F-027 (count-only check passed a drop-one/duplicate-one
  fixture that held the count at 1,324 — now raises). Each also isolated at the specific layer its
  finding named (`_verify_season`'s count/uniqueness assertion), independent of the new content-hash
  check that catches both first in the normal call path.
- `load_season` now calls a new `_verify_season` directly (count + `game_id` uniqueness + refusing any
  season absent from `EXPECTED_COMPLETED_COUNTS`), closing F-026/F-027/F-028 on the one function every
  caller goes through. New `EXPECTED_SHA256` dict pins a per-season SHA-256, computed from the files on
  disk that produced the verified 6,615-game count, checked before parsing on both the download and
  cached-file paths (F-030 — the release tag was stable but assets were mutable). Size cap now enforced
  via `stat()` before the cached file is read (F-031); redirect final URL checked against an
  https+host allowlist (F-032); `season` validated against `SEASONS` with the resolved destination
  path asserted inside the data dir, replacing an ineffective `startswith` guard (F-033); non-UTF-8
  header decode now raises `LoaderVerificationError` instead of a raw `UnicodeDecodeError` (F-029);
  `certifi` added directly to `requirements-train.txt`, pinned to the version already resolved in the
  served image (F-034).
- Module docstring and T-005's tracker outcome corrected — they previously claimed counts were
  re-asserted "on every run," which F-026 showed was false for `load_season`.
- Ran the loader for real against the five pinned seasons: 2022→1324, 2023→1321, 2024→1320, 2025→1324,
  2026→1326, 6,615 total, all matching (and now all content-hash verified). Gate green, 8/8. `git
  status` clean.
- Ended at: T-005 `BUILT`, remediation complete, awaiting re-review. Review ledger not touched by this
  session — re-review is the next action, not a self-grant.

## 2026-08-09 — T-005: historical data loader built

- `backend/model/loader.py` downloads the five pinned season CSVs (`espn_nba_schedules` release tag)
  into gitignored `data/raw/nba_schedules/`, verifies header+size before parsing, parses with pandas
  (never a code-executing deserializer), and normalizes to a completed-game collection. Ran for real:
  2022→1324, 2023→1321, 2024→1320, 2025→1324, 2026→1326 — 6,615 total, all matching the pinned
  expected counts (D-007's figure). `verify_completed_counts` raises hard on any mismatch — the
  tripwire standing in for the test suite this module deliberately skips (Testing Decisions).
- New `backend/requirements-train.txt` (D-016): pandas/numpy/python-dateutil/six pinned exactly;
  `backend/requirements.txt` untouched. `backend/model/__init__.py` left empty on purpose so a future
  serving-time import of the package never pulls in these training-only deps.
- Hit a real macOS/python.org SSL gap (default context doesn't read the system keychain) — fixed with
  an explicit certifi CA bundle, not a verification bypass (D-019).
- Verified `data/raw/nba_schedules/` is actually gitignored (`git check-ignore`) and `git status` stays
  clean after running the loader. Gate green, 8/8.
- **Scope note:** this session was scoped to `backend/model/` only and did not touch `checks/`, so the
  `next-non-docs-commit` trigger on F-024/F-025 fired but neither was closed — both remain `OPEN`.
- Ended at: T-005 `BUILT`, not reviewed. Next is review (security-auditor + logic-reviewer), then
  T-006, and F-024/F-025 still need an owner.

## 2026-08-09 — Phase 1 opens: T-005 loader built; F-024/F-025 closed

- Merged the instantiation phase to `main` (`23a9a88`), T-001 `DONE`, and opened Phase 1 on
  `feat/phase-1-analytical-core`.
- **T-005 built** by `backend-engineer` (`68e3efa`): `backend/model/loader.py` pulls the five pinned
  season CSVs from release tag `espn_nba_schedules` into gitignored `data/raw/`, verifies each before
  parsing, and normalizes to a completed-game collection. Verified independently: **6,615 completed
  games**, per-season counts exact — the same 6,615 the 55.56% baseline was computed from during
  planning, which is a useful cross-check that the loader and the planning analysis agree.
  `backend/requirements.txt` untouched; training deps isolated in `requirements-train.txt` (D-016).
- Probed the count tripwire (it substitutes for tests here): it fires on a mismatch and on a missing
  season. **But a season with no pinned expectation loads entirely unverified** — `seasons=(2021,)`
  returned 1,172 rows with nothing checking them. Left for the review gate rather than pre-fixed.
- F-024 and F-025 closed — this PR's first non-docs commit fired their trigger. F-025 mattered more
  than its LOW severity suggested: `gated` was an exact uppercase match and an unparsed status
  returned `null`, so lowercasing or deleting a status word made a stale-review failure vanish. Now
  fails closed, with `BLOCKED` recognized as a known-but-ungated flag. Promoted to canon `d8b7a8a`.
- **Review gate on T-005: security ✅, logic ⛔.** The logic reviewer earned it — it did not reason about
  the count assertion, it corrupted files and watched what happened. Truncating a season by ~300 lines
  and calling `load_season` directly returned 1,266 games with no error (F-026: verification lives only
  in `load_completed_games`). Dropping one real game while duplicating another kept the count at exactly
  1,324 and passed silently (F-027). This is the cost of the plan's decision not to unit-test the
  loader, arriving on schedule.
- Security ✅ but flagged F-030: the upstream release **tag** is stable while its **assets are mutable** —
  a completed 2021-22 season's file was re-uploaded 2026-07-29. "Pinned by tag" is not reproducible, and
  PLAN-v1 user story 20 assumed it was. A content hash is the fix, and it matters most at T-009.
- F-035 is a regression from my own F-025 fix: `parseTasks` treats any bold `**T-NNN**` in prose as a
  task header, and a phantom task with a null status now hard-fails the gate rather than being ignored.
- Remediation round 1 (`e2d2558`) closed F-026..F-034 — and both reviewers ⛔'d it again, live, on the
  same regression: the F-032 redirect allowlist named `objects.githubusercontent.com`, but GitHub now
  redirects release assets to `release-assets.githubusercontent.com`, so **no real download worked**.
  It passed everything because `data/` was already populated — the download path never executed in the
  builder's testing, in local runs, or in the gate, and nothing outside `loader.py` calls the loader.
  I had assumed that hostname in the F-032 write-up rather than observing it. My own hash check missed
  it too, because `curl` bypassed the module.
- The logic reviewer's best move: it refused to accept F-026/F-027 as fixed just because the new
  SHA-256 layer caught its fixtures, and **neutralised the hash layer** to prove the count and
  uniqueness assertions stand on their own — which matters, because a legitimate upstream refresh will
  force a hash re-baseline.
- Fixed in `8eae86c`, verified the only way that counts: a real download into an **empty** directory.
  Added that as a standing acceptance constraint on T-005 — "gate green" is not evidence the download
  works. Final round: **both ✅**. T-005 → `REVIEWED`.
- Ended at: T-005 `REVIEWED` at `8eae86c`. F-039 deferred to T-006's first commit. Next is T-006.

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
- Round 4 @ `763101e`: **both reviewers ✅.** F-019 verified closed against independent fixtures rather
  than the repo's own tests; F-020..F-023 closed. Two LOW residuals (F-024 a docstring bullet that
  still asserts the removed guarantee — the F-023 lesson repeating inside the same cycle; F-025
  `parseTasks` fails OPEN on an unparseable status, pre-existing) deferred, since both live in non-docs
  files and fixing them would have invalidated the ✅ being recorded.
- T-001 → `REVIEWED`. Four rounds, three of them blocking, two of the defects in canon itself.
- Merged the phase to `main` (`23a9a88`) and set T-001 `DONE`. The merge was itself the first real test
  of the F-018/F-019 fix: the merge commit moved the code tip, and a `DONE` task correctly stayed
  ungated where it would previously have failed the build.
- Ended at: Phase 1 open. T-005 next, on `feat/phase-1-analytical-core`.

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
