# Findings — Sports Dashboard

<!-- APPEND-ONLY. Split out of docs/IMPLEMENTATION.md on 2026-08-10 because the tracker had grown to
     1,367 lines, of which 74% was history that every agent was nonetheless told to read — 2,176
     lines of process documents to review 2,206 lines of code. The tracker keeps what an agent needs
     to ACT and stays bounded; this file keeps the record and is allowed to grow.

     Findings are superseded, never erased. The CURRENT STATUS of every finding lives in the
     `Findings — status index` table in docs/IMPLEMENTATION.md — that index is the thing you
     read to answer "is F-0NN open?"; this file is the evidence behind it.

     DO NOT read this file end to end. Grep for the id you need:
         grep -n 'F-0NN' docs/findings.md
-->

<!-- F-001..F-007 are from the instantiation audit (2026-08-09), not from a gate review — recorded
     here so they survive the session. The review gate has not yet run on this repo. -->

- **F-001** (security, HIGH) — There is no authorization boundary anywhere in the app.
  `POST /nba/favorite/{team_id}` is unauthenticated and mutates a single globally-shared favorites
  table; any caller can add or delete any favorite. With no auth and no RLS, nothing else does.
  Remediation: introduce identity + per-user scoping before favorites (or any user-scoped data) is
  treated as real, and declare the `authz-deny` gate in the stack overlay at the same time.
  Status: ACCEPTED. revisit-when: `first-user-scoped-data` (any endpoint that stores per-user state).
- **F-002** (data, HIGH) — The staged Kaggle corpus (`../archive/nba.sqlite`) ends **2023-06-12**;
  three seasons (2023-24, 2024-25, 2025-26) are missing relative to today. A model trained on it
  cannot be backtested against, or fed by, the live ESPN feed without a backfill.
  Status: **CLOSED 2026-08-09 — moot per D-005** (corpus dropped and deleted; the replacement source
  reaches the completed 2025-26 season).
- **F-003** (performance, MEDIUM) — That SQLite file has **zero indexes** across 16 tables, including
  a 13.6M-row `play_by_play`. Every modeling query is a full scan until fixed.
  Status: **CLOSED 2026-08-09 — moot per D-005.** The lesson survives the corpus: whatever store
  T-002 builds must ship its indexes with the ingest, not after the first slow query.
- **F-004** (data, MEDIUM) — `other_stats` (pace / paint / fast-break / lead-changes — the richest
  modeling features in the corpus) covers only 28,271 of 65,698 games (~43%). Any feature built on it
  is missing-not-at-random by era. Status: **CLOSED 2026-08-09 — moot per D-005.** The lesson
  survives: check per-season coverage of any derived feature before modeling on it.
- **F-005** (integration, HIGH) — The app keys teams on ESPN `team.id`; the Kaggle corpus keyed on
  NBA.com `team_id` (`1610612737`+). No shared key. Abbreviations look like a bridge but disagree
  (`GSW/NYK/SAS/UTA/NOP/WAS` vs ESPN's `GS/NY/SA/UTAH/NO/WSH`), so a naive join silently drops teams.
  Status: **CLOSED 2026-08-09 — designed out by D-004**, which selects an ESPN-keyed source so no
  join is needed. revisit-when: `first-nba-stats-keyed-source` — adding any stats.nba.com-keyed data
  reintroduces this in full. If that happens, do not hand-roll the mapping:
  `sportsdataverse-data/releases/download/nba_crosswalk/nba_team_crosswalk_2026.csv` is a verified
  30-row `espn_team_id` → `nba_team_id` table carrying `match_method`/`match_confidence`.
- **F-006** (ops, LOW) — `docker-compose.prod.yaml` is a 0-byte file. It reads as a production config
  that exists; it is empty. Remediation: write it or delete it. Status: OPEN.
- **F-007** (ops, LOW) — `.github/workflows/ci.yml` now duplicates every check the `gate` job runs,
  costing a second full CI pass per PR. Remediation: retire `ci.yml` once `gate` is the required
  status check on `main`. Status: OPEN.

### Review round 1 — T-001 @ d8e3515 (both reviewers ⛔; all remediated at the SHA in the ledger)

- **F-008** (security, HIGH) — The builder agents were installed unmodified from Supabase canon.
  `backend-engineer.md` instructed builders that *"RLS ships with the schema — any new table's
  row-level-security policies go in the same migration"*, in a project whose central invariant is
  that no RLS, no Supabase and no auth exist. A builder would have shipped RLS on the first
  user-scoped modeling table and reported it `BUILT` believing rows were protected — doubly wrong,
  since the app connects as table owner and an owner bypasses RLS absent `FORCE ROW LEVEL SECURITY`.
  This is D-002's own reasoning ("a gate over a mechanism that does not exist reads as protection")
  defeated one layer up, and `gate-completeness` cannot catch it: it existence-checks agent *files*,
  never their content. Remediation: rewrote the domain guidance of `backend-engineer.md` and
  `frontend-engineer.md` from the overlay's builder notes, leading with the no-RLS invariant.
  Status: FIXED.
- **F-009** (security, MEDIUM) — `gate.yml` declared no `permissions:` block and checked out with
  `persist-credentials` defaulting to true, so `GITHUB_TOKEN` was written into `.git/config` where
  every manifest `run:` command and every npm lifecycle script could read it — in a job whose own
  header claims to hold no secrets. Same-repo branch PRs (this repo's model) get a write-capable
  token. Remediation: added `permissions: contents: read` and `persist-credentials: false`.
  Status: FIXED. **This gap is inherited from canon** `templates/ci/gate.yml` — see learning-notes.
- **F-010** (security, MEDIUM) — `ui-ux-reviewer.md` instructs the reviewer to *"not re-audit"*
  a11y/perf and to *"assume a green gate means they passed"* — but D-003 deliberately declares
  neither check. Both layers were off: nothing mechanical ran, and the only reviewer who would look
  was told to assume it had. Remediation: added a project override to the agent making a11y and perf
  the reviewer's own responsibility until the checks are declared. Status: FIXED.
- **F-011** (security, MEDIUM) — `.gitignore` covered `.env`, `.env.local`, `.env*.local` but **not**
  `.env.production` / `.env.development`, the exact names Next.js loads by convention. Verified with
  `git check-ignore`. Remediation: `.env*` plus `!.env.example`. Status: FIXED.
- **F-012** (logic, MEDIUM) — T-001's acceptance claimed the gate "exits 0 locally", but from the
  project's own documented instructions it exits **1**: `ruff` and `pytest` are absent from
  `requirements.txt` and installed nowhere but `backend/venv`. The prerequisite existed only as a
  historical aside in `log.md`. Remediation: `CLAUDE.md` now carries the venv build + `PATH`
  invocation as explicit setup. Status: FIXED.
- **F-013** (ops, LOW) — `.claude/settings.json` interpolated `$CLAUDE_PROJECT_DIR` unquoted into
  both Stop-hook commands. This repo's path is space-free today, but the workspace above it is not
  (`Client Projects/`), and a hook that exits 127 fails *open* and silently — the enforcement just
  stops. Remediation: quoted. Status: FIXED.
- **F-014** (ops, LOW) — The gate's own fixture tests (`checks/*.test.mjs`) run nowhere. `run-gate`
  imports `checks/lib/*` directly and never shells to vitest, and the CI job deliberately skips
  installing `checks/`. The meta-checks are what make the gate self-guarding; their tests are the
  only thing guarding *them*. `checks/package.json` also floats `vitest: ^2.1.0` with no lockfile.
  Status: ACCEPTED. revisit-when: `first-edit-to-checks-lib` — the moment anyone changes the gate's
  own logic, this stops being theoretical. Deferred rather than half-wired: `npm ci` needs a
  lockfile that does not exist yet.
- **F-015** (ops, LOW) — Declaring `ui-ux-reviewer` in the manifest implies more enforcement than
  exists: `checks/lib/tracker.mjs` hardcodes `mandatory = ['security-auditor','logic-reviewer']` and
  never consults the manifest's reviewer list, so a deleted `ui-ux-reviewer` column or an `n/a` cell
  silently exempts it while `gate-completeness` still reports "3 reviewer(s) installed".
  Status: ACCEPTED — canon-level, not project-level. revisit-when: `reconcile-canon`.

### Review round 2 — T-001 @ d0e661d (both reviewers ✅; F-008..F-013 verified closed)

<!-- Round 2 confirmed each round-1 fix was real rather than cosmetic, and opened two LOW residuals.
     These are deliberately NOT fixed in this cycle: every non-docs/ edit advances the code tip and
     invalidates the ✅@d0e661d that was just earned. Fixing them is the next cycle's opening move. -->

- **F-016** (ops, LOW) — `.gitignore`'s `models/` is an unanchored directory pattern, matching at any
  depth. Harmless today, but `backend/models.py` → `backend/models/` is a routine FastAPI refactor,
  and the package would land untracked and silent. Remediation: anchor it (`/models/` or
  `data/models/`). Status: OPEN. revisit-when: `next-non-docs-commit`.
- **F-017** (docs, LOW) — Three lines in `.claude/agents/ui-ux-reviewer.md` still contradict the
  F-010 override: `:38-39` calls the reduced-motion fallback "a check's job", `:42-43` frames
  mechanical a11y as out of lane, `:51` refers to "the a11y check's evidence" that does not exist.
  The override wins on placement and explicitness, so the fix holds — but the stale lines weaken it.
  Remediation: strike them. Status: OPEN. revisit-when: `next-non-docs-commit`.

> Also noted in round 2, folded into existing items rather than opened as new findings: `ci.yml` has
> the exact `permissions:`/`persist-credentials` gap that F-009 closed in `gate.yml`, and injects
> `secrets.DB_*` into a `pull_request`-triggered job — **F-007** already schedules its retirement, so
> retire it rather than harden it. And `frontend/.gitignore` has `.env*` with no `!.env.example`
> negation, asymmetric with the root fix; pre-existing, cosmetic until someone adds that file.

### Review round 3 — T-001 @ fef3dc8 (logic ✅ · security ⛔ — the F-018 fix was over-scoped)

- **F-018** (canon, HIGH) — `review-ledger-current` gated every `REVIEWED` **and `DONE`** task against
  the repo's *current* code tip, so any commit anywhere invalidated every completed task's review at
  once. A project with N done tasks owed N re-reviews per commit; the check was unusable past its
  first phase. Surfaced the instant Phase 1 tried to start: T-005's first code commit would have failed
  the gate on T-001, a finished and correctly-reviewed task. Verified empirically with
  `--code-head abc1234` before changing anything. Remediation: gate `REVIEWED` only — it means "passed
  review, awaiting merge", which is the stale-review case the check exists to catch — while `DONE`
  means "merged/shipped", frozen history that later unrelated work must not retroactively invalidate.
  Status: FIXED, and **promoted to canon** as Dev-System `be5f6dc`; CANON-VERSION re-stamped.
- **F-014** — CLOSED. Its `revisit-when: first-edit-to-checks-lib` fired when F-018 required editing
  `checks/lib/tracker.mjs`. Wired `meta-unit` into the manifest and CI, committed `checks/package-lock.json`,
  pinned vitest to 2.1.9 exactly. **The deferral was vindicated immediately**: the F-018 edit broke two
  existing fixtures that used `DONE` as their gated status, and no CI anywhere would have caught it.
- **F-016** — CLOSED. `models/` → `/models/`, anchored to the repo root.
- **F-017** — CLOSED. The three stale `ui-ux-reviewer` lines now agree with the project override rather
  than contradicting it; mechanical a11y/perf is stated as in-lane, and the reference to non-existent
  "a11y check evidence" is gone.

- **F-019** (security, MEDIUM) — **The F-018 fix removed too much.** `evaluateLedgerCurrency` did two
  independent jobs for gated tasks: *verdict* (a ledger row exists, every mandatory reviewer is present,
  every applicable cell is a ✅ with a SHA) and *currency* (that SHA still equals the code tip). Only
  currency is meaningless for frozen history; verdict never depended on the code tip at all. Dropping
  `DONE` from the gated set entirely removed both — leaving canon's "never `DONE` until `REVIEWED`" with
  **no mechanical enforcement anywhere** (the auditor traced it: `gate-completeness` ignores statuses,
  neither hook reads them, and branch protection's only status-aware check is this one). It also opened a
  one-word bypass: a `REVIEWED` task failing on a stale review could be cleared by editing its status to
  `DONE` — a change that moves *forward* along the intended state machine and was the cheapest way to
  make the gate green. Demonstrated with fixtures: `DONE` + all-pending, `DONE` + `⛔`, and `DONE` with no
  ledger row all passed. Remediation: keep `DONE` gated for verdict; scope only the currency comparison
  to `REVIEWED`; four regression tests added. Status: FIXED, promoted to canon as `6f49f29`.
- **F-020** (docs, LOW) — Three places still asserted the pre-F-018 guarantee, including
  `review-ledger-current`'s **success message**, which printed a claim the check no longer made — the
  operator-facing statement of what the gate proved. Also its module header and `BRANCH-PROTECTION.md`,
  the document a human reads to decide the gate is sufficient. Status: FIXED (all three now state the
  verdict/currency split).
- **F-021** (deps, MEDIUM) — vitest was pinned to 2.1.9 while this project's own Future-hardening item
  specified the frontend's 4.1.10, leaving two vitest majors in one repo and 5 npm advisories (1 critical,
  1 high) on the committed lockfile. Not reachable as configured — every advisory needs a listening
  dev/UI server and `vitest run` starts none — but it was the wrong version to pin to. Status: FIXED,
  now 4.1.10 matching the frontend; `npm audit` reports **0 vulnerabilities**.
- **F-022** (docs, LOW) — `.claude/CANON-VERSION` was re-stamped but its trailing prose still named the
  previous SHA as "the SHA stamped above". Status: FIXED — it now lists all three canon contributions.
- **F-023** (docs, LOW) — F-017 enumerated three lines and fixed exactly those; the same contradiction
  survived in two it did not name: the agent's frontmatter `description` (what the agent picker surfaces)
  and its "What you do NOT do" section, where a prohibition reads as more binding than corrected prose.
  Status: FIXED. Lesson: a finding that enumerates line numbers gets fixed at those line numbers — state
  the *claim* to eliminate, not its coordinates.

### Review round 4 — T-001 @ 763101e (both reviewers ✅; F-019..F-023 verified closed)

<!-- Verified by independent fixtures run against the committed evaluator, not against the repo's own
     tests. Two LOW residuals opened; deliberately NOT fixed here — both live in non-docs files, so
     fixing them would advance the code tip and invalidate the ✅ this round records. -->

- **F-024** (docs, LOW) — The JSDoc header of `evaluateLedgerCurrency` was updated to say "REVIEWED or
  DONE" but its second bullet was left unscoped, so the docstring still asserts currency applies to
  DONE — the exact claim F-020 existed to remove, contradicted by the code twenty lines below. This is
  the **F-023 lesson repeating within the same cycle**, and because the file is byte-identical to canon
  the inaccuracy is now inherited by every future instantiation. Status: OPEN.
  revisit-when: `next-non-docs-commit`.
- **F-025** (logic, LOW) — `parseTasks` fails **OPEN** on an unparseable status: `statusFromBlock`
  returns `null` for anything outside `STATUS_ENUM`, and `null` is not in the gated set, so a
  stale-review failure clears if the status is lowercased, misspelled, or deleted. Demonstrated:
  `` `reviewed` ``, `` `Reviewed` ``, and a removed status word all turn a failing fixture green. Note
  `BLOCKED` is live vocabulary — `next-command.sh` greps for it — yet is absent from `STATUS_ENUM`, so
  a `BLOCKED` task is silently ungated. Pre-existing; predates the whole F-018 line. Remediation: fail
  closed on an unrecognized status, and add `BLOCKED` to the enum as explicitly ungated.
  Status: FIXED in the T-005 PR — an unrecognized status now fails closed, and `BLOCKED` is
  recognized as a known-but-ungated flag. Promoted to canon.
- **Residual bypass, judged and accepted.** Flipping a stale-review `REVIEWED` task to `DONE` still
  clears currency — but it no longer clears *review*: a complete ledger row with every mandatory
  reviewer ✅ and a SHA is still required, so a `⛔` or pending task cannot be laundered this way. The
  auditor's judgment, which I accept: exempting DONE from currency *necessarily* makes declaring DONE
  an escape from currency; the only real alternatives are re-gating DONE (reopens F-018) or recording a
  per-task **done-at SHA** and comparing against that instead of the moving tip. The latter is a design
  addition, not a bug fix — filed in *Future hardening*.
- **Canon gap noted, not closed:** F-021 was fixed project-locally only. Canon still ships
  `checks/package.json` with an unpinned `vitest: ^2.1.0` and no lockfile, so every future project
  inherits the advisory-bearing 2.x range. Belongs in a canon promotion, out of scope for T-001.

### Review gate — T-005 @ 32110ce (security ✅ · logic ⛔)

<!-- T-005 stays BUILT. Per SYSTEM.md §5.4 the builder remediates and the reviewers re-run. Both HIGH
     findings were demonstrated by corrupting fixtures, not reasoned about — that is why they landed. -->

- **F-026** (logic, HIGH) — **The count assertion is bypassable.** `verify_completed_counts` is called
  only from `load_completed_games`; `load_season` — the function that downloads, parses and normalizes
  a season — never calls it. Any caller doing `loader.load_season(2022)`, which T-006/T-009 or an
  ad-hoc script would do naturally, gets **zero verification**. Demonstrated: a file truncated by ~300
  lines returned 1,266 games with no exception and no warning. The module docstring and the T-005
  outcome note both claim the counts are re-asserted "on every run"; they are not.
  Remediation: verify inside `load_season` so no path can obtain data without it firing.
  Status: **FIXED**. `load_season` now calls a new `_verify_season` (count + `game_id` uniqueness,
  F-027/F-028) directly before returning, so every path — `load_completed_games`'s loop and any direct
  caller — is verified identically. Reproduced against the pre-fix code (`git show 32110ce`) on a
  truncated 2022 fixture: 1,266 games, no exception, same as the original finding. Against the fixed
  code the same fixture now raises before returning: caught first by the new `EXPECTED_SHA256` content
  check (F-030) inside `download_season_csv`, and independently by `_verify_season`'s own count check
  when exercised directly (bypassing the download/hash layer) — `LoaderIntegrityError: season 2022:
  got 1266 completed games, expected 1324`. Module docstring corrected to describe the real call graph.
- **F-027** (logic, HIGH) — **"Right count, wrong rows" passes.** Verification is count-only; nothing
  asserts `game_id` uniqueness. Demonstrated: a 2022 file with one real completed game dropped and
  another duplicated in its place — net count unchanged at 1,324 — passed with no exception, one real
  game silently missing. This is the exact failure the tripwire exists to catch, and it is the reason a
  count is a weak substitute for a test. Remediation: assert `game_id` uniqueness per season before
  counting.
  Status: **FIXED**. `_verify_season` asserts `game_id` uniqueness (via `value_counts()`) before the
  count comparison. Reproduced the exact fixture against pre-fix code: dropped game_id `401361042`,
  duplicated game_id `401360941` in its place, count held at 1,324 — `load_completed_games` (the old
  code's only verified path) passed it silently. Against the fixed code the same fixture now raises:
  caught first by the `EXPECTED_SHA256` content check (F-030), and independently by `_verify_season`'s
  uniqueness assertion when exercised directly — `LoaderIntegrityError: season 2022: 1 duplicate
  game_id value(s) ... {'401360941': 2}`.
- **F-028** (logic, MEDIUM) — A season absent from `EXPECTED_COMPLETED_COUNTS` is silently unverified:
  the raising loop iterates `expected.items()`, so an unpinned season contributes nothing to check.
  `seasons=(2021,)` loads 1,172 rows with no signal. Not live today, but D-017's 2026-27 retrain adds a
  season and nothing keeps the two structures in sync. Remediation: fail when `actual` carries a season
  `expected` does not.
  Status: **FIXED**, at two independent layers. `_verify_season` refuses any season not present in
  `EXPECTED_COMPLETED_COUNTS` before checking counts/uniqueness (`load_season(2021)` now raises
  `LoaderIntegrityError`, tested against the real leftover `data/raw/nba_schedules/nba_schedule_2021.csv`
  on disk). `verify_completed_counts` independently rejects any season present in `actual` but absent
  from `expected` (`set(actual) - set(expected)`), so a hand-built `actual` dict is covered too, not
  only the `load_season` call path.
- **F-029** (logic, LOW) — `_validate_header` decodes with `errors="strict"`, so a non-UTF-8 response
  raises `UnicodeDecodeError` rather than the module's own `LoaderVerificationError`. Fails loudly,
  wrong type.
  Status: **FIXED**. The decode is wrapped in `try/except UnicodeDecodeError`, re-raised as
  `LoaderVerificationError` with `from exc` preserving the original traceback.
- **F-030** (supply-chain, MEDIUM) — **The release tag is stable but its assets are mutable, so the
  source is not pinned in the sense the code claims.** The auditor queried the GitHub API: the release
  dates from 2023-03-04, but `nba_schedule_2022.csv` — a completed historical season — carries
  `updated_at 2026-07-29`. sportsdataverse uses one release per dataset as a rolling CDN. A re-upload
  that corrects a score or team ID while leaving row counts identical passes **silently**, and that is
  the likeliest form of upstream revision. This directly defeats PLAN-v1 user story 20 (re-running
  months later reproduces the same numbers) and matters most at T-009, where the headline numbers are
  produced. Remediation: record a SHA-256 per season file and verify before parsing — that pins content
  rather than a filename. If deferred, correct the docstring so "pinned" is not read as "immutable".
  Status: **FIXED**. New `EXPECTED_SHA256` dict (next to `EXPECTED_COMPLETED_COUNTS`) pins a SHA-256
  per season, computed from the five files on disk that produced the verified 6,615-game count
  (`8cd13a11…`, `71aad62f…`, `ae89a6e5…`, `a7a5b660…`, `5a4a7473…` for 2022–2026 respectively). Checked
  in `_validate_content_hash`, called from `download_season_csv` on **both** the fresh-download and
  cached-file paths, before parsing. A mismatch raises `LoaderVerificationError` with a message that
  states plainly this is not transient, must not be retried or silently re-baselined, and that
  `EXPECTED_SHA256`/`EXPECTED_COMPLETED_COUNTS` must be updated deliberately by a human after
  re-verifying the new content. Both HIGH repros (F-026/F-027) are in fact caught by this check first,
  ahead of the count/uniqueness assertions, since both corruptions change file bytes. Docstring rewritten
  to state what "pinned" now actually means (content, not just tag/filename).
- **F-031** (input validation, LOW) — The size cap is not enforced on the cached path: only 8,192 header
  bytes are read and length-checked, so an oversized file already on disk short-circuits straight into
  the parser. The download path is correct (bounded read before anything touches disk).
  Status: **FIXED**. `download_season_csv`'s cached-file branch now calls `dest.stat().st_size` and
  raises before reading anything into memory if it exceeds `_MAX_DOWNLOAD_BYTES`; only then is the full
  file read (needed anyway for the F-030 content-hash check, which requires the full bytes, not a
  header peek). The now-unused `_HEADER_PEEK_BYTES` constant was removed.
- **F-032** (network, LOW) — Redirects were followed without asserting the final scheme/host, so a
  redirect to `http://` would silently drop TLS. Fixed by checking the post-redirect response URL
  against https and a host allowlist. **The host I named in this finding was assumed, not observed —
  see F-037, which is how that shipped broken.** The allowlist now carries the observed
  `release-assets.githubusercontent.com` (recorded with its observation date) plus `github.com` and the
  prior `objects.githubusercontent.com`.
  Status: **FIXED** at `8eae86c`, but only after F-037 corrected it — the control shipped in
  `e2d2558` refused every real download. Recorded here because this entry had carried no `Status:`
  line at all until the 2026-08-10 ledger audit: a finding whose remediation is described in prose
  but never given a status reads as done to a writer and as unresolved to a reader.
- **F-033** (input validation, LOW) — `season` is unvalidated and the `url.startswith(...)` guard that
  looks like it prevents redirection does not — a `..` segment passes it. Not exploitable today (the
  fixed filename prefix makes every traversal hit a non-directory, and `season` only ever comes from
  the module-level tuple), but the comment advertises a control that does not work.
  Status: **FIXED**. New `_validate_season` rejects any `season` not in `SEASONS`, called once at the
  top of `download_season_csv` (the single choke point both `load_season` and any direct caller go
  through). The ineffective `url.startswith(...)` check was removed and replaced in `_dest_path` with
  an assertion on the *resolved* path (`resolved_dest.is_relative_to(resolved_data_dir)`) — the check
  that actually proves the write stays inside the data dir, rather than one that only looked like it
  did. Verified `load_season(1999)` (outside `SEASONS`) is rejected before any URL/path is built.
- **F-034** (deps, LOW) — `certifi` is imported directly but declared only transitively via
  `requirements.txt`; a direct import should be a declared dependency.
  Status: **FIXED**. Added `certifi==2026.6.17` to `backend/requirements-train.txt`, pinned to the
  exact version already resolved transitively in `backend/requirements.txt` (via `httpx`) — adds
  nothing to the served image, only makes the training-side dependency explicit.
- **F-035** (gate tooling, LOW) — **Regression introduced by my own F-025 fix.** `parseTasks` starts a
  task block on *any* `**T-NNN**` match, including a bold cross-reference in prose, producing a phantom
  task with a null status — which now hard-fails the gate. Before F-025 the phantom was silently
  ungated. The current tracker is clean, so this is latent, but a maintainer writing "depends on
  **T-001**" in an acceptance bullet would hit a confusing block. Direction of failure is right, the
  diagnostic is wrong. Remediation: anchor the task-header match to the start of a list item.
  Status: **FIXED**, canon `c513a52` — the same commit as F-036. Verified at the 2026-08-10 ledger
  audit against the running code: `parseTasks` matches `/^\s*-\s*\[[ xX]\]\s*\*\*T-(\d+[a-z]?)\*\*/`,
  and `checks/review-ledger-current.test.mjs` carries the regression test. This entry had carried no
  `Status:` line until that audit, even though the canon commit message names the fix.
- **F-036** (gate tooling, LOW) — `knownUngated` hand-duplicates `STATUS_ENUM` minus `gated`; adding a
  status to the enum without editing the literal hard-fails every task at that status. Fail-closed, but
  a trap. Remediation: derive it. Status: FIXED, canon c513a52.

### Re-review — T-005 @ e2d2558 (both reviewers ⛔ on the same regression)

- **F-026, F-027 — CLOSED and independently verified.** The logic reviewer did not accept that the
  count/uniqueness checks were fixed just because the new SHA-256 layer caught its fixtures: it
  *neutralised the hash layer* (repinning the hash to the corrupted file's own value) and re-ran, proving
  `_verify_season` catches both a truncated season (`got 1319, expected 1324`) and the drop-one/dup-one
  case (`1 duplicate game_id ... {'401360941': 2}`) on its own. That matters because a legitimate
  upstream refresh will require re-baselining the hashes, and the count logic must still stand.
- **F-028, F-029, F-030, F-031, F-033, F-034 — CLOSED**, each verified against the running code.
- **F-037** (availability / control integrity, HIGH) — **The F-032 redirect allowlist named a host
  GitHub no longer uses.** Release assets now redirect to `release-assets.githubusercontent.com`, not
  `objects.githubusercontent.com`, so `download_season_csv` refused every real download. Both reviewers
  reproduced it live and independently. It shipped because `data/` was already populated, so **the
  download path never executed** in any local run, in the builder's own testing, or in the gate — and
  the gate structurally cannot see it, since nothing outside `loader.py` calls the loader. The host I
  put in the F-032 write-up was assumed, not observed. Status: FIXED — allowlist now records the
  observed host with the date it was observed, and the prior host is retained. **Verified the only way
  that counts: a real download into an empty directory** (2023 → 1,776,788 bytes → hash-verified →
  1,321 completed games).
- **F-038** (error handling, LOW) — `EXPECTED_SHA256[season]` was indexed unguarded, so a season with
  counts but no pinned hash raised a bare `KeyError` instead of an actionable error — the same
  inconsistency F-028/F-029 fixed in the other two verification paths. Status: FIXED.

> **Acceptance note added for T-005, from the auditor's observation:** "gate green" is not evidence the
> download works. The loader is invoked by nothing outside itself, and every local run hits the cache.
> Any change to the download path must be verified by a run against an **empty** data dir.

### Final re-review — T-005 @ 8eae86c (both reviewers ✅)

- Download verified from a **fresh empty directory** for seasons not previously exercised (2024, 2025):
  bytes arrived, SHA-256 matched the pins, counts verified. Five-season load from empty: 6,615 games,
  6,615 unique `game_id`. The allowlist was probed as a control (8 cases) — it evaluates the
  post-redirect URL, rejects scheme downgrade, subdomain and userinfo tricks, and fires *before* the
  response body is read.
- **F-039** (docs, LOW) — `loader.py`'s module-level security note still names
  `objects.githubusercontent.com` as the redirect target: the exact stale claim F-037 was about, left
  uncorrected 60 lines above the constant that was fixed. Code correct, narrative stale.
  Status: OPEN. revisit-when: `next-non-docs-commit` (it lives in a `.py` file, so fixing it here would
  have moved the code tip and invalidated the ✅ this round records).
- **F-040** — FIXED in this commit (the F-032 entry had an unbalanced parenthesis and still enumerated
  a two-host allowlist the code no longer uses).
- **F-041** — FIXED in this commit. The empty-dir requirement had landed in the Findings section rather
  than in T-005's `acceptance:` bullet, where a builder would actually read it. Now in both.

### T-006 build session (2026-08-10) — findings opened by building, not by a review

- **F-039** — **CLOSED** in the T-006 commit, as its `next-non-docs-commit` trigger required. The
  loader's module-level security note no longer restates the allowlist's contents; it points at
  `_ALLOWED_DOWNLOAD_HOSTS` as the authoritative list and records *why* restating it is how the note
  came to name a host GitHub had stopped using. Fixing the claim rather than the coordinates, per the
  F-023 lesson.
- **F-024** — **CLOSED, and it was already fixed before this session.** The JSDoc of
  `evaluateLedgerCurrency` was correctly scoped in `32110ce` ("fix(canon): … scope JSDoc (F-024)"),
  but the finding was left reading `Status: OPEN` here. Verified against the committed file: the
  header now states the verdict/currency split and matches the code. The tracker was stale, not the
  code. Lesson: a finding closed in a commit message is not closed until the ledger says so.
- **F-042** (data, MEDIUM) — **The corpus contains 10 All-Star exhibition games.** Not a T-006 defect;
  found while verifying it against real data. The five seasons carry **42 distinct team ids, not 30**.
  Exactly 30 ids play ≥82 games per season; 12 phantom ids appear in 1–3 games each, all dated
  All-Star weekend, with tell-tale scores (2024's 211–186; the 2025/2026 mini-tournament formats at
  41–32, 42–35, 21–47). They are `season_type = 2` upstream, which is why T-005's pinned counts
  include them. **Feature computation is provably unaffected**: phantom and real ids do not overlap,
  and **zero games mix a phantom with a real id**, so no NBA team's form, rest or point differential
  reads an All-Star result. The exposure is downstream — these become 10 training/evaluation rows
  (0.15%) whose features are all-priors and whose labels are coin flips, in T-009's headline numbers.
  Remediation: exclude them in T-007/T-009, not here. The separation is unambiguous (≥82 games vs ≤3,
  no overlap in any season), so a per-season minimum-games threshold is safe. Do **not** filter inside
  `loader.py` or `dataset.py` without deliberately re-baselining `EXPECTED_COMPLETED_COUNTS` — the
  6,615 count is a pinned tripwire and silently changing what it counts defeats it. Status: OPEN.
  revisit-when: `T-007` (fold generation) — whichever of T-007/T-009 lands first owns the filter.
  Status: **FIXED 2026-08-10**, ahead of T-007 as intended. New standard-library-only
  `backend/model/corpus.py` identifies exhibition ids by games-played per season and verifies the
  result (every season must keep exactly 30 team ids; every pinned season must shed exactly its
  pinned count; an unpinned season is refused). `dataset.load_games` excludes by default. Design
  reasoning and the rejected alternatives are in **D-025**; the baseline shift it causes is
  **D-026**. Verified on the real corpus: 6,615 → **6,605**, exactly 10 removed, per season
  `{2022:1, 2023:1, 2024:1, 2025:3, 2026:4}`, **42 distinct team ids → 30**, and exactly 30 per
  season. 12 new tests in `backend/tests/test_corpus.py`, which run in CI.
- **F-043** (gate tooling / process, MEDIUM) — **`review-ledger-current`'s currency rule does not
  survive a multi-task branch: F-018's failure shape, one layer over.** Demonstrated before committing,
  with `node checks/review-ledger-current.mjs --code-head deadbee`: both of T-005's ✅@`8eae86c` fail
  as stale. The check compares every `REVIEWED` task's ✅ against a **repo-wide** code tip, so any
  non-docs commit anywhere invalidates it — including one that touches no file T-005 owns. Phase 1
  lands T-005…T-009 on one branch, so this compounds exactly as F-018 did: once T-006 is `REVIEWED`,
  T-007's first commit invalidates both, and so on. The escape valve D-018 leaves is flipping a task
  to `DONE`, which is exempt from currency — but `DONE` means "merged/shipped", and T-005 is not
  merged, so writing it would put a false claim in the source of truth. **This is the residual F-019
  already identified**: exempting DONE from currency necessarily makes declaring DONE the escape, and
  the real fix is a per-task **reviewed-at/done-at SHA** compared against that task's own last-touched
  commit rather than the moving tip — already filed in *Future hardening*, and now with a second,
  sharper motivation than the bypass it was filed for. Needs a human call; options in order of
  honesty: (a) implement the per-task SHA (canon change, fixes it for every project stamped from this
  canon); (b) merge the T-005 work to `main` and flip it `DONE`, matching T-001's precedent
  (`23a9a88` merge → `8ef637b` DONE) — correct but front-loads a merge mid-phase; (c) accept a red
  `review-ledger-current` for the rest of Phase 1, which trains the team to ignore the one check that
  makes the reviewer gate falsifiable, and is the worst option.
  **Status: FIXED — option (a), chosen by the human.** `evaluateLedgerCurrency` now accepts an
  injected `opts.staleAt(taskId, sha)` and asks, per task, whether any commit has touched *that task's
  own files* since its ✅. The repo-wide comparison survives as the fallback and under `--repo-wide`.
  The evaluator stays pure — git lives in the CLI, which derives ownership from commit **subjects**
  (`T-006: …`, `fix(T-005): …`), never bodies. 9 new fixture tests; 53 pass, including all 44
  pre-existing, so the change is backward-compatible. See **D-023** for the two forks this exposed.
  **Consequence to read before re-reviewing:** the gate is *still* red on T-005, and now correctly so.
  `406dd09` edited `backend/model/loader.py` (the F-039 docstring fix), a file T-005 owns, so its
  review is genuinely stale rather than spuriously stale — F-039's own entry predicted exactly this
  cost. The complaint changed from "the tip moved" to "`406dd09` touched this task's files", which is
  a true and specific statement. T-005 needs a cheap re-review (one docstring diff); T-006 needs its
  first. **Not yet promoted to canon** — see *Future hardening*.

### Review gate — T-006 @ 34759ed (security ⛔) · T-005 re-review @ 34759ed (security ✅)

<!-- T-006 stays BUILT; per SYSTEM.md §5.4 the builder remediates and the reviewer re-runs. Every
     finding below was reproduced by the auditor with a runnable script, not argued — F-044, F-045,
     F-046, F-048, F-049 and F-050 are all marked CONFIRMED with output. -->

- **T-005 re-review: ✅.** The auditor did not take "it's only a docstring" on faith. It stripped
  docstrings and compared ASTs, then compared every code object in the module recursively: the sole
  difference in `406dd09` is the module docstring string constant, with `download_season_csv`,
  `_validate_header`, `_validate_content_hash`, `_verify_season` and `load_season` byte-for-byte
  identical. It then honored F-041 anyway and ran the download path against a genuinely **empty**
  temp dir — 2023 → 1,776,788 bytes → hash matched the pin → 1,321 completed; 2024 → 1,320 — leaving
  the real corpus read-only and byte-identical. Also confirmed the new docstring is *accurate*: it
  stops restating the host set and points at `_ALLOWED_DOWNLOAD_HOSTS`.
- **F-044** (integrity/leakage, MEDIUM) — **CONFIRMED. The target's own result IS reachable through
  `history`.** T-006's security note claims the scoreless `Matchup` makes it "unreachable by any bug".
  That closes the *direct* door only: the scores live in `history`, and `compute_features` never
  compares `target.game_id` against the filtered window. The only thing keeping a target out of its
  own features is the *coincidence* that `Matchup.date` equals its `Game.date` — a caller-side data
  property, which is exactly the delegation the security note forbids. Demonstrated: with the target
  in history as a 200–80 blowout and `as_of` one hour after tip-off, `point_diff_diff` moves 6.0 →
  32.0. Not hypothetical via the new seam either — `to_pydatetime()` truncates nanoseconds, so a
  caller using the frame's own `Timestamp` as `as_of` is already past `Game.date` (the pinned corpus
  is minute-precision, so it does not fire today). Remediation: refuse or drop records whose
  `game_id == target.game_id`, and correct the docstring's claim.
- **F-045** (integrity, MEDIUM) — **CONFIRMED. A total lookup miss is indistinguishable from D-015's
  legitimate cold start.** Three triggers, none of which raises: a one-shot iterator (the declared
  `Iterable[Game]`) reused across calls returns priors on the second call; `Matchup.season` as `"2024"`
  vs `Game.season` as `2024`; team ids typed `str` in history and `int` in the target. Each silently
  yields `form_diff 0.0, point_diff_diff 0.0` — byte-identical to what opening night legitimately
  produces, because D-015 deliberately removed the dropped-games symptom that would have exposed it.
  Failure scenario: T-009's headline reads "four features carry no signal, no-ship" when the truth is
  "the pipeline is broken" — the exact silent failure PLAN-v1 §2 exists to design out. Remediation:
  type `history` as `Sequence[Game]` (or refuse a consumed iterator) and validate season/team-id types
  at the boundary. **Do not simply refuse unknown teams** — that would break the genuine cold start.
- **F-046** (integrity, MEDIUM) — **CONFIRMED. Nothing asserts `game_id` uniqueness across the
  *combined* collection.** T-005 checks per season; `verify_completed_counts` keys `actual` by season,
  so a repeated season collapses to one key and passes. Demonstrated on the real corpus:
  `load_games((2022, 2022))` → **2,648 games, 1,324 unique ids, no error**, and a target's
  `point_diff_diff` shifts 4.400 → 4.755. This is F-027's "right count, wrong rows" one layer
  downstream, and `GameHistory`'s docstring claims it is "a pure function of the **set** of input
  games" when it is a function of the multiset. Remediation: assert uniqueness in
  `GameHistory.__init__` — it already iterates every game, and it is the only place covering every
  path in. Scope note from the auditor: `verify_completed_counts` is byte-identical to what was
  reviewed at `8eae86c`, so this does **not** reopen T-005; it is filed against T-006 because
  `dataset.load_games` is the new entry point that surfaces it.
- **F-047** (integrity, LOW) — missing check CONFIRMED, exploit PLAUSIBLE. `date` is **tip-off**, not
  completion, so the contract "games completed strictly before `as_of`" is really "games that tipped
  off before". `Game` carries no status field. Harmless in Phase 1 — the auditor measured every
  consecutive-game gap in the corpus (min 0.50h, median 48h) and all seven sub-12h gaps belong to
  F-042's All-Star phantom ids, never a real team. But D-011 puts one `games` table behind training
  and inference, and the app's enum is `scheduled|live|final`; a Phase-2 caller that forgets to filter
  `status == 'final'` feeds a live partial score into a "pre-game" vector. Remediation: state it as a
  precondition, or give `Game` a final-only construction path so Phase 2 cannot forget.
- **F-048** (input validation, LOW) — **CONFIRMED.** `dataset.games_from_frame` coerces with bare
  `bool()`/`int()`/`str()` despite claiming "no reinterpretation": `neutral_site` as the *string*
  `'False'` becomes `True`, a float score `118.9` truncates to `118`, and a plain-`str` date column
  dies with a raw `AttributeError`. The inverted `neutral_site` matters most — it would make
  `home_advantage` a constant `0.0` and silently delete the model's only guaranteed feature, and
  `test_neutral_site_survives_as_a_real_bool` only exercises `True`. Nothing is wrong at `34759ed`
  (the loader emits a real bool); this is a gap in the seam that exists to *be* the trust boundary.
- **F-049** (input validation, LOW) — **CONFIRMED.** `Game.__post_init__` validates awareness,
  self-play and ties but not that scores are numbers. `float('nan')` scores pass the tie check
  because `nan != nan`; `to_vector` then propagates NaN and inf, and accepts the string `"3.0"`. Not
  reachable from the loader path (`.astype(int)` raises on NaN — verified). Remediation: require
  integral scores; assert `math.isfinite` in `to_vector`.
- **F-050** (integrity, LOW) — **CONFIRMED.** `GameHistory.of` uses `isinstance`, so a **subclass** is
  returned unwrapped and `compute_features` calls its `_records_before`. A 6-line subclass overriding
  that method leaks a future game — the literal counterexample to the docstring's "no code path in
  this module reads a game dated at or after `as_of`". Remediation: `type(history) is cls`.

> **What the auditor tried to break and could not** — recorded because it is as load-bearing as the
> findings. It neutered `_records_before` in memory two ways (`bisect_right`, and no filter at all)
> and re-ran the suite: **both mutations were caught by the leakage property test**, so that test is
> not vacuous and the builder's control is genuine. Timezone handling survived re-expressing `as_of`,
> the target and the whole history in `+05:00` — bit-identical output. `GameHistory` genuinely holds
> no as-of state (prebuilt index ≡ raw sequence across 14 real games). Leakage re-derived
> independently on 133 real corpus games: 0 mismatches. D-021 verified *faithfully* — the auditor
> noted pytest 9.1's `importorskip` defaults to `ModuleNotFoundError`, blocked pandas/numpy/pyarrow/
> dateutil/six, and got **52 passed, 1 skipped**; importing `model.features` loads **zero**
> site-packages modules. No pickle/yaml/eval/subprocess/network/filesystem access in either new
> module. T-005's verification chain is not weakened by the new entry point: `(1999,)`, `(2021,)` and
> `(2027,)` all raise before any URL or path is built. Cost is linear and flat (~0.06–0.10 µs/game;
> a full T-009-shaped pass over 6,615 games takes 0.12 s, 0 non-finite vectors).

> **Noted for later, not filed as findings:** `_form`/`_season_point_diff` re-scan the team's whole
> filtered history per call (`[r for r in records if r.season == season]`), which partly negates the
> bisect `GameHistory` justifies itself with — measured harmless, an efficiency/altitude call. The
> `max(gap, 0.0)` clamp in `_rest_days` is unreachable dead code, since
> `records[-1].date < as_of ≤ target.date` guarantees a positive gap. And `backend/models.py`
> (SQLAlchemy) vs `backend/model/` (this package) is a one-character import-path collision that will
> bite when Phase 2 imports both into the FastAPI service — pre-existing, out of T-006's scope.

### Review gate — T-006 @ 34759ed (logic ⛔) — mutation testing found the tests, not the module

<!-- The logic-reviewer's verdict is worth reading in full: the MODULE passed every acceptance
     criterion it could independently verify, including recomputing the golden fixture by hand. What
     failed was the TEST SUITE. It ran ~60 mutations; 39 were caught, 7 survived, and 3 of the
     survivors change 5,096 / 5,809 / 5,283 of the 6,615 real feature vectors while every test still
     passes. PLAN-v1's Testing Decisions is the standard: "the tests that matter are the ones that
     would fail if the module were subtly wrong in the ways this domain fails." -->

- **F-051** (logic/tests, **HIGH**) — **CONFIRMED. Nothing asserted that rolling form is *rolling*.**
  Reversing the window to the *oldest* `FORM_WINDOW` games (`[-FORM_WINDOW:]` → `[:FORM_WINDOW]`)
  passed all 46 tests, and changes **5,096 / 6,615** real vectors. Root cause is a fixture flaw, and
  it is mine: every form fixture was a *uniform* streak (15 straight wins, 10 identical +2s), so the
  first ten games and the last ten were numerically identical by construction. The feature would have
  been the opposite of the one T-006's acceptance names, and T-009 would have trained on it silently.
  Status: **FIXED** — new `_home_results("LLLLLWWWWWW")` helper builds non-uniform records, and two
  tests pin magnitude *and* direction (a reversed record must flip the sign). Mutation re-run: CAUGHT.
- **F-052** (logic/tests, MEDIUM) — **CONFIRMED.** The shrinkage count `n` for season-to-date point
  differential was unpinned: capping it at `FORM_WINDOW` (**5,809** vectors differ) and using the
  all-seasons record count (**5,225** differ) both passed. The old test asserted only a *direction*
  (`with_earlier > windowed_only`), which both mutations satisfy. Status: **FIXED** — literals pinned
  for a 12-game season (`12/17 × mean`), plus a prior-season fixture that separates the all-seasons
  count specifically. Both mutations re-run: CAUGHT.
- **F-053** (logic/tests, MEDIUM) — **CONFIRMED.** `compute_training_features`'s `as_of` — the single
  entry point T-009 uses for *every* training row — was pinned by nothing. Substituting
  `game.date - 1 day` or midnight-of-tip-off-day both passed, because the golden fixture has no game
  between day 9 and day 10 and every fixture `as_of` was midnight. Status: **FIXED** — a game three
  hours before tip-off now sits inside the window, so any coarsening is detectable. Both: CAUGHT.
- **F-054** (logic/tests, MEDIUM) — **CONFIRMED, and the sharpest of the set: the test written to
  catch this could not catch it.** `test_leakage_a_game_one_microsecond_before_as_of_is_included`
  claims to guard against "a coarser day-level comparison", but the whole fixture was anchored at
  `datetime(2024,1,1)` — midnight — and every `as_of` was a whole-day offset, so truncating `as_of`
  to midnight was a *no-op for the fixture*. Not theoretical: the corpus has **83 team-days with two
  games on the same UTC date** and **5,360 / 6,615** games tip off at a non-midnight instant; the
  mutation changes 77 real vectors. Status: **FIXED** — the fixture epoch is now `19:00Z`, which
  re-arms the existing microsecond test *and* F-053's mutations, plus an explicit same-UTC-date
  before/after pair. CAUGHT.
- **F-055** (logic/tests, MEDIUM) — **CONFIRMED.** `test_to_vector_emits_features_in_the_declared_order`
  built its expected tuple *from* `FEATURE_NAMES`, so both sides moved together and
  `tuple(features.values())` passed. Latent today (the dict literal happens to match) but `to_vector`
  is documented as accepting *any* mapping and T-009's coefficients line up positionally.
  Status: **FIXED** — asserts hardcoded literals in a fixed order, and feeds in a deliberately
  reversed mapping. CAUGHT.
- **F-056** (logic/tests, MEDIUM) — **CONFIRMED.** `dataset.py`'s `season` was not pinned by the test
  that claims to pin the field mapping: `_frame()` used `season=2022`, so hardcoding `season=2022` in
  the adapter passed — while changing **5,283 / 6,615** real vectors, because season scoping drives
  both accumulating features. Status: **FIXED** — fixture moved off 2022, plus a two-row/two-season
  assertion. CAUGHT.
- **F-057** (data/design, MEDIUM) — **CONFIRMED, and it corrects a decision.** See **D-024**, which
  supersedes D-020(4). Not a T-006 code change; a constraint on T-009/T-010. Status: OPEN,
  revisit-when: `T-009`.
- **F-058** (docs/tests, LOW) — **CONFIRMED.** `GameHistory`'s docstring said the `(date, game_id)`
  tiebreaker made the index "a pure function of the *set* of input games ... **asserted in the
  tests**". It was not asserted — dropping the tiebreaker passed. Status: **FIXED**, and the first
  fix attempt *also* failed the mutation: two same-instant games both inside the form window
  contribute identically however they are ordered. The tiebreaker is only observable when a
  same-instant pair **straddles** the window boundary, so the test now uses 11 season games with the
  pair at the oldest position. CAUGHT.
- **F-059** (tests, LOW) — **CONFIRMED.** The `neutral_site` defaults on `Matchup`/`Game` were never
  exercised (helpers always passed the flag), so flipping the default to `True` passed all 46 tests.
  Status: **FIXED**. CAUGHT.
- **F-060** (code hygiene, LOW) — **CONFIRMED.** Two unreachable defensive branches: `_rest_days`'s
  `max(gap_days, 0.0)` (records are strictly before `as_of ≤ tip_off`, so the gap is always positive)
  and `_shrink`'s `n <= 0` guard. Status: **FIXED** for the first — removed, with the invariant that
  actually holds written down instead. **Deliberately kept** for `_shrink`: it is the documented
  contract ("n=0 → the prior exactly, and `observed` is never read"), so it is a stated behavior
  rather than dead defense, and callers outside this module may rely on it.

> **What the logic reviewer verified and could not break** — 39 mutations caught, including every
> sign flip, both season filters, the rest cap, the rest-to-tip-off rule, `k`, the window cap, the
> home indicator, D-022's Game-as-target refusal, and both adapter id/score swaps. It recomputed the
> golden fixture by hand and confirmed all three literals. It confirmed the leakage property test
> catches a leak weighted at **1e-9** and could not construct one the test missed. It re-derived the
> leakage guarantee on 300 randomly sampled real games: 0 mismatches. Every corpus number in this
> tracker reproduced to the digit. **One caution it put on its own record:** mutation `P05` is listed
> as caught but failed on an `AttributeError` from `__slots__`, not on the leakage assertion — it
> explicitly said not to count that as evidence. That is the standard this project should hold.

### T-006 remediation @ (this commit) — all 10 surviving mutations now caught

- Every finding above and F-044..F-050 is addressed in code or tests. **Verified by re-running the
  reviewers' own mutations**, not by inspection: a harness applies each of the 10 survivors, runs the
  suite, and restores the file — **10/10 CAUGHT** (two needed a second attempt: M28, as F-058 records,
  and N13). Suite grew 58 → 79. Ruff clean. CI simulation with pandas/numpy blocked: 68 passed,
  1 skipped — D-021 still holds.
- Real-corpus re-verification after remediation is **unchanged to the digit**: 6,615 games, 19
  neutral, home win rate 0.55556, `form_diff` +0.0433/−0.0582, `point_diff_diff` +1.8922/−2.4415,
  leakage exact on 120 sampled real games. The fixes changed what is *refused*, not what is computed.
- F-046 landed at **two** layers, matching T-005's download/cached-path discipline: `GameHistory`
  protects the features, `games_from_frame` protects the *count* — without the second,
  `load_games((2022, 2022))` still returned 2,648 records to anything that merely counted them, and
  T-009's headline would have quoted a doubled number that never reached a `GameHistory`.

### Re-review — T-006 @ d8257b6 (security ⛔ · logic ⛔) — the remediation held; `corpus.py` did not

<!-- Both reviewers independently confirmed F-044..F-060 are genuinely closed, reproduced closed
     rather than accepted on the docstring. Both then ⛔'d on backend/model/corpus.py, which I added
     in d8257b6 AFTER their first review and which no reviewer had ever seen. The lesson is the
     one T-005 already taught once: code that arrives with a remediation is unreviewed code.
     NUMBERING: the two reviewers collided on F-061..F-066. Logic's numbering is kept as issued;
     security's new findings are renumbered F-067..F-071 here. Security's own F-061/F-062 are the
     same defect as logic's F-065 and are folded into it. -->

- **F-044..F-050 and F-051..F-060 — all CLOSED, verified independently by both reviewers.** Security
  re-ran its original reproductions against a hash-verified `git cat-file` snapshot of `d8257b6`:
  the F-044 leak is gone (`point_diff_diff` 32.0 → **6.0**, and history with vs without the target's
  own `Game` now returns bit-identical dicts), all three F-045 triggers raise, `load_games((2022,
  2022))` raises at both layers, and the genuine cold start still works so D-015 is not broken.
  Logic re-ran all 10 mutations: **10/10 caught**, and it checked the four highest-risk ones at the
  *assertion* level rather than the exit code — the standard it set for itself with P05 last round.
- **F-065** (logic; = security F-061 + F-062, integrity, **HIGH/MEDIUM**) — **CONFIRMED. A real bug
  in `corpus.py`, mine.** Two compounding causes. (1) `exhibition_team_ids` counted games per season
  but returned a bare **union of ids**, which `partition_exhibitions` then applied across *every*
  season — so a team under-played in one season was stripped from all of them. (2) The 30-team
  assertion was built from the *surviving* games, so a season that lost everything contributed no
  entry and the check passed **vacuously**. Together: `exclude_exhibitions(2022-2025 complete + the
  first 150 games of 2026, expected=None)` returned **0 games from a 5,439-game input and raised
  nothing** — using the module's own documented escape hatch exactly as documented. Reproduced here
  before fixing, at prefixes 100/150/250. D-025's "verified, not trusted" was false on that path.
  Status: **FIXED.** Identification is now keyed by `(season, team_id)` and membership tested per
  game season; the 30-team check iterates the seasons present in the **input**, so a wiped season
  reads as `0`, not as absent. The same input now raises `season(s) {2026: 0} do not have exactly 30
  team ids`. Two regression tests, both mutation-verified.
- **F-061** (logic, MEDIUM) — **CONFIRMED. `dataset.load_games` had no tests at all**, so D-025(3)'s
  exclude-by-default — the entire safety property — was unpinned: flipping the default to `True` or
  inverting the flag passed all 90 tests. Under either, `load_games()` returns the contaminated
  6,615, D-026's baseline silently reverts, and 10 coin-flip rows plus 12 phantom ids reach T-009.
  F-059's pattern (an unexercised default) landing on the one thing the module exists to guarantee.
  Status: **FIXED** — `load_games` is now tested against a synthetic frame via a patched loader (no
  corpus needed), asserting both the default and the raw path, plus that curation is verified.
- **F-062** (logic, LOW) — **CONFIRMED.** `test_identification_is_per_season_not_across_the_corpus`
  did not pin what its name claimed: its phantom's *global* count was also below threshold, so a
  global-counting implementation passed. Latent — id `111353` already recurs across two seasons and
  D-017 retrains annually. Status: **FIXED** — the fixture now uses a team **real in one season and
  under-played in another**, which only per-season counting classifies correctly.
- **F-063** (logic, MEDIUM) — **CONFIRMED. A test that passed for the wrong reason.** Reversing the
  set difference in the unpinned-season check still produced a non-empty result against a fixture
  whose seasons did not overlap `expected`, and the loose `match=` accepted an error naming the wrong
  season — so an unpinned season could be curated unverified while the test that exists to prevent
  exactly that stayed green. F-028's lesson defeated by its own guard. Status: **FIXED** — `expected`
  now overlaps the fixture's seasons and the offending season number is asserted present (and the
  healthy one absent).
- **F-064** (logic, LOW) — **CONFIRMED.** Three correct validation branches nothing would have
  noticed losing: the `isinstance(season, bool)` guard, the `game_id` str check, and `Game`'s
  team-id check (the suite covered `Matchup.home_id` and `Game.season`, never `Game.home_id`).
  Status: **FIXED**, four tests.
- **F-066** (logic, LOW) — **CONFIRMED.** `min_games` was forwarded but plumbed by no test, so
  dropping the forwarding survived; and the `<` vs `<=` boundary at exactly `min_games` was unpinned.
  Status: **FIXED**, both mutation-verified.
- **F-067** (security, MEDIUM) — **CONFIRMED. A default on the producer is not a guarantee at the
  consumer.** `load_games`' exclude-by-default holds on every path *through `load_games`*, but three
  public documented paths reach T-009 uncurated with no flag and no assertion:
  `games_from_frame(load_completed_games(...))`, `load_games(include_exhibitions=True)`, and any
  hand-assembled list. D-025's own argument — contamination has *no symptom* — applies verbatim to
  rows obtained any other way, and nothing let a consumer check. Status: **FIXED** — new
  `corpus.assert_curated(games)`, a cheap one-pass tripwire **T-007 and T-009 must call on their
  input**. It deliberately does not re-run `exclude_exhibitions`, which is not idempotent (security
  also noted this: re-curating a curated corpus trips the pinned-count assertion and reads as
  corruption). Added to T-007/T-009 acceptance below.
- **F-068** (security, MEDIUM) — **CONFIRMED. The F-044 fix introduced a fail-open control.**
  `_records_before` dropped *any* record whose `game_id` matched the target's, so a target id
  colliding with a real historical game silently deleted that game from both teams' windows
  (`form_diff` 0.125 → 0.0, `point_diff_diff` 6.0 → 1.143, no error). Not reachable from
  `compute_training_features` and ESPN ids are unique, so Phase 1 was safe — but a fail-open control
  on a module whose entire thesis is failing closed is the wrong shape. Status: **FIXED** — the
  exclusion now matches the **opponent** as well as the id, so it identifies the target precisely;
  a same-id game against a different opponent raises instead of being dropped. `_TeamGame` carries
  `opponent_id` for this. Both directions mutation-verified.
- **F-069** (security, LOW) — **CONFIRMED mechanism, PLAUSIBLE exploit.** The games-played rule
  classifies *teams*, never games, so an exhibition played **between two franchise ids** is
  structurally invisible to it — demonstrated by injecting a synthetic All-Star between two real
  2024 ids, which survived with both assertions passing. Not live: the corpus carries only
  `season_type` 2/3/5 (no preseason rows at all) and all ten removed games are phantom-id games.
  Status: **DOCUMENTED**, not coded — `corpus.py` now states the limitation, because D-025 read
  stronger than the rule is. The clean closure if a differently-shaped source ever lands is to carry
  `season_type` through; the loader already parses it and `dataset.REQUIRED_FRAME_COLUMNS` drops it
  before `corpus.py` could see it. revisit-when: `new-historical-source`.
- **F-070** (security, LOW) — **CONFIRMED, and half of it was a latent vacuous-test trap.**
  (a) `expected: ... = EXPECTED_EXHIBITION_COUNTS` bound the dict **object at definition time**, so
  rebinding the module constant — what a monkeypatching test does — was silently ignored while
  in-place mutation took effect. A future test patching that constant would have passed vacuously:
  precisely the class F-051..F-059 were all about. Status: **FIXED** — sentinel default resolved at
  call time, with a test that patches the constant and asserts it takes effect. (b) The
  `min_games`/`teams_per_season`/`expected` knobs can defeat both assertions
  (`min_games=1, teams_per_season=32, expected={2022:0}` keeps the All-Star game silently). Status:
  **ACCEPTED** — these are explicit caller opt-outs on a training-only module, not a bypass a
  default path can reach; the defaults are what T-007/T-009 use and `assert_curated` re-checks at the
  consumer regardless.
- **F-071** (process / review integrity, MEDIUM) — **CONFIRMED by both reviewers independently, and
  it threatened this task's central evidence.** `backend/model/__pycache__` held bytecode that did
  not match its source *while its `(mtime, size)` header validated*, so CPython reused it: a
  size-preserving mutation (`30`→`29`) restored within the same second leaves a valid-looking stale
  `.pyc`. Security proved it in one process — `compile(open(...).read())` gave 30 while
  `import model.corpus` gave 29, with `git hash-object` matching HEAD exactly — and watched the suite
  return 90, then 3 failed, 1 failed, 7 failed, then 94 passed across one session. **This directly
  threatens the "10/10 CAUGHT" evidence T-006's remediation rests on**, since a mutation can be
  scored against a previous mutant's bytecode. Status: **FIXED.** All caches purged (0 were tracked
  in git); the harness now gives every run its own `PYTHONPYCACHEPREFIX`, runs with
  `-p no:cacheprovider`, and **re-asserts a clean baseline between mutations**. Re-ran 19 mutations
  under it: **all caught** (one apparent survivor was an equivalent mutant of my own construction —
  `X if False else X` — and the real F-070 regression, reconstructed properly, is caught).
  The original 10/10 claim stands: logic independently reproduced it with a clean cache.

### Review round 3 — T-006 @ 4009937 — finding-number allocation (Tracker rule 5a)

<!-- Recorded BEFORE spawning, which is the whole point of rule 5a. The first attempt at round 3 was
     spawned with "new findings start at F-072" given to both reviewers -- the same non-allocation
     that produced two conflicting F-061..F-066 sets in round 2 (F-100). Those two agents then
     stalled without producing output and were relaunched; no findings were lost, and the range is
     re-allocated properly here. -->

| Reviewer | Block | Status |
|---|---|---|
| security-auditor | **F-072 – F-081** | round 3 in progress @ `4009937` |
| logic-reviewer | **F-082 – F-091** | round 3 in progress @ `4009937` |
| main thread | F-100 – F-109 | F-100..F-104 used |
| _(unallocated)_ | F-092 – F-099 | reserved for a round-4 reviewer block |

### Dev-System process gaps (filed 2026-08-10 by the main thread, not from a review)

<!-- NUMBERING: F-072..F-099 is deliberately left empty. The round-3 reviewers were spawned with
     "start at F-072" and are still running as this is written, so anything in that range may
     collide. Taking F-100+ is the immediate workaround for the very defect F-100 describes — and is
     itself the evidence that "start at F-0NN" is not an allocation scheme. -->

- **F-100** (process/canon, MEDIUM) — **Parallel reviewers collide on finding numbers; nothing
  allocates them.** Both round-2 reviewers were told "new findings start at F-061", and both did:
  two different, conflicting F-061..F-066 sets came back, which had to be reconciled by hand into
  F-061..F-071 before anything could be recorded. Canon offers no allocation scheme —
  `SYSTEM.md` shows finding ids only as narrative examples. Remediation: the round owner allocates a
  disjoint block per reviewer in the spawn prompt and records it in the tracker (now Tracker rule
  5a). Status: **MITIGATED** by rule 5a; the canon change is unfiled. revisit-when: `reconcile-canon`.
- **F-101** (process/canon, MEDIUM) — **Canon's reviewer contract assumes sequential reviewers and
  breaks under the parallelism everyone actually uses.** `.claude/agents/security-auditor.md`
  instructs the reviewer to "write your *Review ledger* row and append every issue" to
  `docs/IMPLEMENTATION.md`; `logic-reviewer.md` says the same. Two agents doing that concurrently
  write one file. This project has avoided it only because the main thread overrode canon and made
  both reviewers report-only — an undocumented divergence that also moves the fidelity risk onto the
  transcriber. Remediation: make report-only the canon contract (with the main thread transcribing),
  or have reviewers write to per-reviewer files the main thread merges. Status: OPEN.
  revisit-when: `reconcile-canon`.
- **F-102** (process/canon, MEDIUM) — **A remediation can add code, and canon has no notion of it.**
  SYSTEM.md §5.4 says only "⛔ → builder remediates, re-review". It has no concept that a remediation
  may introduce a whole new module, which then reaches the next round unreviewed. That is not
  hypothetical: it happened in **both** T-006 rounds. Round 1's remediation introduced
  `backend/model/corpus.py`, which round 2 ⛔'d on — including F-065, a real bug that silently
  returned 0 games from a 5,439-game input. Round 2's remediation then introduced `assert_curated`,
  the opponent-matched exclusion and a sentinel default, and round 3 was explicitly told to treat
  those as the highest-risk surface. Remediation: require the builder's hand-off to list additions
  separately from modifications. Status: **MITIGATED** by Tracker rule 5c; canon change unfiled.
  revisit-when: `reconcile-canon`.
- **F-103** (gate tooling, MEDIUM) — **`gate-completeness` existence-checks agent *filenames*, never
  their content.** It is `readdirSync(agentsDir).filter(f => f.endsWith('.md'))`, so any file with
  the right name satisfies it. This is precisely how F-008 shipped: `backend-engineer.md` was
  installed unmodified from Supabase canon and told builders "RLS ships with the schema" in a project
  whose central invariant is that no RLS exists — while the gate reported "3 reviewer(s) installed".
  The check cannot distinguish a correct agent from the wrong stack's, and F-008 was caught by a
  human reviewer, not by it. Remediation: assert each agent's frontmatter `name` matches its filename
  and that the file references the project's own stack overlay.
  Status: **FIXED.** `evaluateAgentIntegrity` (pure, in `checks/lib/manifest.mjs`) now asserts two
  mechanical properties of every installed agent: its frontmatter `name:` matches its filename (a
  file copied from another agent keeps the name it was written as), and any `stacks/<overlay>.md` it
  references is *this* project's overlay (an agent carrying a foreign stack's guidance is the F-008
  signature exactly). Agents that are legitimately stack-agnostic — `logic-reviewer`,
  `security-auditor` — cite no overlay and are unaffected: this asserts that a claim made is correct,
  not that every agent must make one. **Demonstrated end-to-end against the real gate**, not just in
  fixtures: repointing `backend-engineer.md` at `stacks/nextjs-fastapi-supabase.md` turns
  `gate-completeness` red naming the foreign overlay, and it goes green again on restore. 6 tests.
  Deliberately NOT attempted: judging whether an agent's guidance is *correct* for the stack — that
  needs a reader, and the review gate is where a reader belongs. This closes the mechanical half.
- **F-104** (process, LOW) — **The docs-only review-commit convention is enforced by nothing**, and
  the whole F-043 currency design leans on it. It exists only in source comments and one line of
  `log.md`. It has already been violated once — `0c3b8a9` (`review(T-005)`) bundled gate-tooling
  fixes into a review commit — which is the sole reason `taskIdFromSubject` has to exclude `review(`
  from ownership attribution. A workaround for an unenforced convention is now load-bearing canon.
  Remediation: a pre-commit or gate check that a commit whose subject starts `review(` touches only
  `docs/`. Status: OPEN.

### Round 3 (sliced) — T-006 @ fe27280

- **Slice A (security, closure verification): 8 CLOSED, 0 NOT-CLOSED, 0 PARTIAL.** F-044, F-045,
  F-046, F-049, F-050, F-065, F-068, F-070 all reproduced closed against a pristine `fe27280` export.
  Each guard was shown to be *load-bearing*, not merely present: restoring the pre-fix date-only
  filter moves `point_diff_diff` 6.0 → 39.43 on the same input; the pre-fix blunt `game_id` match
  shifts `form_diff` 0.2308 → 0.2917 silently. D-015 confirmed intact — empty history and unseen
  teams still return priors, so nothing was fixed by breaking the cold start.
- **Slice C (logic, mutation regression): 17/17 CAUGHT, 0 survived, 0 equivalent mutants.**
- **Round 3 is INCOMPLETE.** Slices B and D — a fresh review of what the round-2 remediation *added*
  (`assert_curated`, the opponent-matched exclusion, the sentinel default, per-`(season, team)`
  identification) — have not run. Per F-102 that is exactly the surface that broke rounds 2 and 3, so
  T-006 stays `BUILT`.

- **F-072** (process / review integrity, MEDIUM) — **CONFIRMED. Parallel reviewers sharing one
  working tree corrupt each other's results.** Slice C mutation-tested by patching
  `backend/model/features.py` **in the shared tree** and reverting; slice A was running the suite
  against that same tree concurrently. Slice A's first two runs landed inside patch windows and
  reported 2 then 1 failures — different tests each time, all passing in isolation and against a
  pristine export, with `git status` showing ` M backend/model/features.py` at that instant. **Taken
  at face value that was three phantom HIGH findings against T-006.** They were avoided only because
  the auditor re-verified against a `git archive` export rather than trusting the first red run.
  This is F-071's hazard by a second route, and it was introduced *by the parallel-slice design added
  to reduce cost* — the remedy created it. Status: **FIXED** by Tracker rule 5d: any agent that
  mutates source runs in an isolated copy of the tree, never the shared one, and snapshots
  `git status --porcelain` around every test run. Note this also means a red suite in a shared tree
  is not evidence of anything until the tree is confirmed clean.

#### Slice B (security) — the round-2 additions. ⛔, 5 findings

<!-- Transcribed before remediation, per rule 5b. Slice B reviewed ONLY what round 2 added; the
     sentinel (#3) came back CLEAN. Reviewer applied rule 5f itself and ruled that F-079/F-080/F-081
     must NOT batch: they are LOW by reachability, not cosmetics, and none is test hygiene. -->

- **F-077** (integrity, MEDIUM) — **`assert_curated` certifies shapes `exclude_exhibitions` refuses.**
  It re-runs only the *identification* step and never the 30-team invariant, so on identical input it
  passes a season missing a whole franchise (29 ids, 83 real games gone) and a 21-game non-NBA block
  that clears the threshold (32 ids). **My docstring's own rationale was wrong:** it justified not
  re-running `exclude_exhibitions` on non-idempotence, which is true of the *pinned-count* assertion
  only — the 30-team one is idempotent by construction and got dropped with it.
- **F-078** (integrity, MEDIUM) — **F-045's hazard, reintroduced in the new function.**
  `assert_curated` declares `Sequence[Game]` and enforces nothing, so a generator passes *and is
  consumed by the check itself*, leaving the caller 0 games; an empty collection also passes, so a
  second call cannot detect the emptiness the first caused. It only bites on **clean** input — an
  uncurated generator raises — which is exactly what makes it silent. `exclude_exhibitions([])`
  likewise returns OK(0), F-065's signature for the empty case.
- **F-079** (usability/integrity, LOW — does NOT batch) — false "has not been curated" on genuinely
  curated **partial-season** data, including the D-017 retrain shape this tracker plans for, and the
  error tells you to get the data from `load_games()`, which is where it came from. Matters because
  the obvious remedy is the `min_games` knob, which has **no floor**: `min_games=0` turns the check
  into an unconditional pass on the real uncurated 6,615-game corpus.
- **F-080** (integrity/leakage, LOW — does NOT batch) — **F-068's exclusion identifies the target up
  to the *pair*, not precisely as its comment claims.** A same-pair id collision still silently
  deletes a real game from both windows (`point_diff_diff` 1.3418 → 1.4864, no error); the
  different-opponent control raises correctly. Confined to inference — in training `GameHistory`'s
  duplicate-id check pre-empts it. Closure is one term wider: `compute_features` already holds
  `target.date`.
- **F-081** (integrity, LOW — does NOT batch) — the team check counts *thirty ids*, not *which*
  thirty: a 2024 season with a franchise deleted and a 21-game impostor inserted passes both
  assertions. Free strengthening measured by the reviewer: the 30 ids are identical across all five
  pinned seasons, so requiring the surviving id sets to agree across the input's seasons catches it
  and still fires on a real expansion.
- **F-070(b) — acceptance rationale INVALIDATED.** It was accepted on the grounds that
  "`assert_curated` re-checks at the consumer regardless". As shipped it re-checks one of the two
  assertions (F-077) and carries the same defeatable knob (F-079). That rationale must be re-stated
  or the finding reopened when F-077/F-079 are fixed.

#### Slice D (logic) — fresh mutations against the round-2 additions. ⛔

15 mutations: **8 CAUGHT, 6 SURVIVED, 1 caught locally but GREEN IN CI.** Two declared equivalent and
not counted (`is`→`==` on an `object()` sentinel; a season-set expression differing only for a
fully-wiped season already refused upstream). Isolated `git archive` tree, fresh
`PYTHONPYCACHEPREFIX` per run, baseline 103 re-asserted after all 15 restores.

- **F-090** (logic/tests, **HIGH**) — **`load_games` ignoring BOTH its arguments survives 103/103.**
  The new fixture patches `load_completed_games` with `return_value=`, which answers any argument
  list, and no test inspects `call_args`. Failure scenario is the one this whole project exists to
  prevent: T-007 builds expanding-window folds *by season*, so under this defect **every fold trains
  on its own test season**, T-007's own fold tests still pass, and the only symptom is a
  good-looking accuracy number. Remediation: assert `call_args` — that `seasons` and `data_dir` reach
  the loader.
- **F-091** (logic/tests, **HIGH**) — **F-061's exact regression is GREEN IN THE GATE.** Ran with
  pandas/numpy absent exactly as `gate.yml` does: **90 passed, 1 skipped with the mutation applied.**
  The F-061 fix landed only in `test_dataset.py`, which `importorskip`s out of CI, and
  `test_corpus.py` never imports `model.dataset` — so nothing in the gate touches `load_games` at
  all. My F-061 remediation protects local runs and not the gate. Explicitly **not** covered by the
  accepted "dataset.py tests skip in CI" exposure: that acceptance is conditioned on the adapter
  staying "a field-by-field conversion", and `load_games` now carries D-025(3), the exclude-by-default
  safety property.
- **F-087** (logic/tests, MEDIUM) — `assert_curated`'s `min_games` is unexercised end to end.
  Dropping the forward, or rebinding the default to anything in `[2, 58]`, passes 103/103. At `3`, a
  6-game All-Star round-robin — *the docstring's own documented 2026 shape* — sails through the guard
  T-007/T-009 are **required** to call. The F-066 shape, on the function round 2 added to close F-067.
- **F-088** (logic/tests, MEDIUM) — `exclude_exhibitions`'s `expected` parameter is unpinned: three
  mutations making it read the module constant instead of the argument, or skip the block on
  `expected={}`, all survive. `{2024: 1}` is the only non-`None` value any test passes and it agrees
  with the constant, so no test can tell which dict was read. Fails **open** (`expected={}` → 870
  games curated unverified) *and* **closed** (`expected={2027: 2}` → D-017's hand-pinned season
  refused). `test_an_unpinned_season_is_refused_rather_than_curated_unverified` — written to close
  F-063's version of exactly this — is itself vacuous on this axis.
- **F-089** (logic/tests, MEDIUM) — the 30-team assertion is only ever driven from **below** (29, 28,
  0), so `!=` → `<` survives. The unpinned direction is the dangerous one: **>30 ids means an
  exhibition id survived curation**, which is silent, and this assertion is the only net for the
  F-069 shape.

**Overloaded test, flagged:** `test_a_game_id_collision_against_a_different_opponent_is_refused`
carries two properties asymmetrically — it is the **sole** guard for F-068's collision-raise (that
assertion appears once in the entire suite), while its second half duplicates a F-044 case that
already has two other guards. Deleting it would open F-068 completely and cost F-044 nothing.
`_synthetic_frame` itself is **not** vacuous — M-08 and M-15 both fail through it, and it carries a
proper control — but it stops being honest at the patch boundary (F-090).

#### Round-3 remediation — 4 fixed, 6 accepted (2026-08-12)

Scoped deliberately. Round 3 found **no live defect** — the module's real-corpus output is unchanged
and slices A and C confirmed nothing regressed. The four fixed are the ones **T-007 depends on**; the
six accepted are hardening, and each carries a `revisit-when:` so acceptance expires on a trigger
rather than by being forgotten (§5.4).

- **F-077 — FIXED.** `assert_curated` now checks *both* invariants. Only the pinned-count assertion
  is genuinely un-re-runnable (it counts games removed, so reads 0 on clean input); the team count is
  idempotent by construction and is now re-run. Tests cover the 29-id and 32-id shapes it used to
  certify.
- **F-078 — FIXED.** `assert_curated` refuses a non-`Sequence` (the F-045 guard, which I had failed
  to carry over) and refuses an empty collection — certifying nothing as curated would have hidden
  the emptiness a consumed generator causes.
- **F-087 — FIXED** (bundled: same function, one test). `min_games` is now exercised end to end.
- **F-090 — FIXED.** The dataset fixture now asserts `call_args`: `seasons` and `data_dir` must reach
  the loader. Was HIGH because T-007 builds folds *by season*.
- **F-091 — FIXED, and verified in CI's own environment.** A stdlib `ast` check in `test_corpus.py`
  reads `load_games`' `include_exhibitions` default out of the source without importing the module,
  so it runs where `test_dataset.py` cannot. Proof: with the default flipped **and pandas/numpy
  absent as `gate.yml` runs it**, the suite now reports **3 failed** where it previously reported
  90 passed, 1 skipped. **[CORRECTED 2026-08-12, F-096(a): "3 failed" is the number with pandas
  INSTALLED. In the gate's own environment the mutated suite reports 1 failed, 93 passed, 1 skipped.
  The conclusion held; the cited evidence was the local number labelled as the gate one.]** Deliberately narrow — installing training deps in the gate would have removed
  the accidental guard on `features.py`'s stdlib purity that D-021 partly rests on.
- **F-070(b) — rationale restored.** It was accepted on "assert_curated re-checks at the consumer";
  F-077's fix makes that true for both invariants. The defeatable-knob half remains, tracked as F-079.

**ACCEPTED, with triggers** — none is reachable in Phase 1 as built:
- **F-079** (false negative on curated partial seasons; `min_games` has no floor). revisit-when:
  `first-partial-season-run` — D-017's mid-season retrain is the first caller that passes one.
- **F-080** (same-pair `game_id` collision drops a real game). Unreachable in training —
  `GameHistory`'s duplicate-id check pre-empts it. revisit-when: `phase-2-inference`.
- **F-081** (team check counts thirty ids, not which thirty). Needs a deleted franchise *and* an
  impostor simultaneously. revisit-when: `first-expansion-or-source-change`.
- **F-088** (`exclude_exhibitions`' `expected` argument unpinned). Every caller today passes the
  pinned constant or `None`. revisit-when: `first-hand-pinned-season` (D-017's retrain).
- **F-089** (30-team assertion never driven from above). revisit-when:
  `first-expansion-or-source-change`, with F-081 — same fixture closes both.
- **Overloaded test noted, not fixed:** `test_a_game_id_collision_against_a_different_opponent_is_refused`
  is the sole guard for F-068's collision-raise while duplicating F-044 coverage that has two other
  guards. Deleting it would silently open F-068. revisit-when: `next-edit-to-test_features`.

#### Narrow verification of the round-3 fixes — ⛔, and two of the four did not hold

<!-- F-077 and F-078 verified clean. F-090 and F-091 did NOT hold as claimed, and the verifier's
     summary is the fair one: "in the gate's environment, three separate mutations that hand T-007
     silently wrong training data still report green." -->

- **F-092** (logic/tests, **HIGH**) — **The F-091 fix pinned the literal default, not the behaviour.**
  The `ast` check read the default out of `load_games`' signature; the *body* is what decides. Both
  `return exclude_exhibitions(games) if include_exhibitions else games` and `return games` left the
  default reading `False` and reported **94 passed, 1 skipped** under CI's environment — exactly the
  outcome F-091 exists to prevent. F-061's own docstring had named both hazards ("flipping the
  default to True, **or inverting the flag**"); I closed one, named the test `..._ENFORCED_IN_CI`,
  and recorded it as verified.
  Status: **FIXED.** The policy moved into `corpus.apply_default_curation` — a stdlib function, so
  the gate runs a real *behavioural* test rather than a signature check. Verified: flag inverted →
  **1 failed**; exclusion removed → **1 failed**; unmutated → **112 passed, 1 skipped**, all with
  pandas/numpy absent.
- **F-093** (logic/tests, **HIGH**) — **The F-090 fix landed inside the module F-091 was filed
  about.** `test_dataset.py` `importorskip`s out of CI, so `load_games` ignoring *both* arguments
  still gave 94 passed, 1 skipped in the gate. I diagnosed that structural gap for F-091 in the same
  commit and reproduced it one file over.
  Status: **FIXED.** `test_corpus.py` now asserts structurally (via `ast`, no import) that
  `load_games` forwards `seasons`/`data_dir` **by name and in order** and delegates curation.
  Verified: args ignored → **1 failed** in the gate's environment.
- **F-094** (logic/tests, MEDIUM) — the F-090 assertion checked value *presence*, not binding:
  `{*args, *kwargs.values()}` plus membership. Swapped arguments passed; `seasons[:1]` passed —
  and that one is the silent case F-090's own docstring describes, surviving because the fixture used
  a **single-element** tuple, making truncation undetectable by construction.
  Status: **FIXED** — `assert_called_once_with((2023, 2024), data_dir)` with a two-season tuple.
- **F-095** (integrity, LOW — does NOT batch) — **the F-077 fix added a new defeatable knob of the
  exact shape F-087 was filed for, in the same commit that fixed F-087.** `teams_per_season` had no
  caller and no test that the argument is honoured, so rewriting the check to read the module
  constant passed 108/108; and `assert_curated(thirty_two_id_season, teams_per_season=32)` silences
  the very shape F-077 exists to refuse. Status: **FIXED**, with a test that the argument is honoured
  *and* that the default still refuses.
- **F-096** (docs/evidence, LOW) — two checkable defects in the remediation's own record.
  (a) The commit message and this file recorded F-091's proof as "3 failed … as `gate.yml` runs it";
  measured, the gate environment gives **1 failed, 93 passed, 1 skipped** — 3 failed is the number
  *with* pandas. Corrected in place above with a dated note rather than erased. In a ledger where
  recorded proof is load-bearing, a number labelled with the wrong environment is a real defect.
  (b) `assert_curated`'s new error ended with a plain string implicitly concatenated to an f-string,
  emitting literal braces. Ruff does not catch it — `RUF` is not in `select`. Status: **FIXED** (b);
  (a) corrected.
