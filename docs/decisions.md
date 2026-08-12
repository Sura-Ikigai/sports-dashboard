# Decisions log — Sports Dashboard

<!-- APPEND-ONLY. Split out of docs/IMPLEMENTATION.md on 2026-08-10 because the tracker had grown to
     1,367 lines, of which 74% was history that every agent was nonetheless told to read — 2,176
     lines of process documents to review 2,206 lines of code. The tracker keeps what an agent needs
     to ACT and stays bounded; this file keeps the record and is allowed to grow.

     Decisions are superseded, never erased. A superseding decision cites the one it replaces.

     DO NOT read this file end to end. Grep for the id you need:
         grep -n 'D-0NN' docs/decisions.md
-->

- **D-001** 2026-08-09 — Instantiated the Dev-System into `sports-dashboard/` before planning the modeling
  layer, rather than after. Reason: modeling adds a second data domain and a second store; without a
  tracker and gate in place first, those decisions live only in chat — the exact loss SYSTEM.md §0
  exists to prevent. (Supersedes nothing.)
- **D-002** 2026-08-09 — Authored a new stack overlay `nextjs-fastapi-postgres` instead of reusing
  `nextjs-fastapi-supabase`. Reason: this project runs plain Postgres with no Supabase and no RLS, so
  the Supabase overlay's RLS invariants and `rls-coverage` gate would be gates over a mechanism that
  does not exist — worse than no gate, because they read as protection. Its safety role transfers to
  `authz-deny`, which is undeclared until auth exists (see F-001). (Supersedes nothing.)
- **D-003** 2026-08-09 — The gate declares only the five checks that genuinely run today (fe-typecheck, fe-lint,
  fe-unit, be-lint, be-unit). `a11y`, `perf`, and `authz-deny` are deliberately NOT declared because
  their tooling is not installed; `gate-completeness` fails a declared-but-missing gate by design.
  (Supersedes nothing.)
- 2026-08-09 — **D-004: sportsdataverse-data is the historical source.** Seasons 2022–2026 (the last
  five) via GitHub release assets. Chosen over `nba_api` and `shufinskiy/nba_data`: `nba_api` pulls
  live from stats.nba.com, which rate-limits to ~1 req/s and silently drops datacenter IPs — a
  dependency that fails precisely when deployed, and one we decline to build on; `shufinskiy` was
  last refreshed 2025-02 and does not reach the current season. Decisive factor: sportsdataverse is
  ESPN-keyed, the same ID space the app already stores. (Supersedes nothing.)
- 2026-08-09 — **D-005: the Kaggle `wyattowalsh/basketball` corpus is dropped and deleted.** Its
  2.35 GB bought pre-2002 depth the project does not need for game modeling, at the cost of a
  three-season staleness gap, zero indexes, and a foreign ID space. Deleted from disk this session.
  Consequence: the modeling horizon is 2002-present (sportsdataverse's floor), and F-002/F-003/F-004
  are closed as moot. (Supersedes nothing.)
- 2026-08-09 — **D-006: stay on Python 3.11 for now.** The container (`python:3.11-slim`), CI, and
  the local venv agree today; moving one without the others is drift. An upgrade is real work with a
  real failure mode (psycopg2-binary wheel availability against a `-slim` image), so it goes through
  the gate as T-004 rather than riding along with the instantiation. If it moves, 3.13 is preferred
  over 3.14 for scientific-stack wheel coverage. (Supersedes nothing.)

<!-- D-007..D-017 come from the 2026-08-09 grill-me session; the full reasoning is in PLAN-v1. -->

- **D-007** 2026-08-09 — **The NBA home-court baseline is 55.56%, not 58%.** Measured over 6,615
  completed games (2022–2026); only one of those five seasons reached 58%. Sampling back to 2002 shows
  why: pre-2020 seasons average 59.3% home wins, 2021-onward 55.2% — a ~4-point structural break at the
  COVID no-crowd seasons that never reverted. The 58% figure is an artifact of averaging across an era
  that no longer describes the sport. Consequence: every "beat the baseline" claim in this project is
  measured against 55.56%. (Supersedes nothing.)
- **D-008** 2026-08-09 — **Ship criterion is paired: ≥62% accuracy on the sealed fold AND log loss
  beating a constant 55.56% predictor.** Accuracy alone cannot validate the product's central promise —
  that a 55% call and an 80% call differ — and a model can hit 65% accuracy while being badly
  calibrated. Calibration is therefore a gate, not a nice-to-have. (Supersedes nothing.)
- **D-009** 2026-08-09 — **Train 2022–24, validate 2025, seal 2026.** 25 seasons are available back to
  2002; older ones are deliberately unused. Training on a 59.3% home-court era shifts every emitted
  probability, which fails D-008's calibration half even where accuracy survives. The window also
  excludes the two COVID-affected seasons as abnormal for a home-court model. (Supersedes nothing.)
- **D-010** 2026-08-09 — **Predictions are persisted append-only and written by a scheduled job inside
  FastAPI.** Forced by the live accuracy tracker: proving "we said 71% before tip-off" requires the
  prediction as it existed then, and a model that re-predicts a finished game will use information that
  did not exist. Keeping the writer inside FastAPI preserves the only-DB-writer invariant, currently the
  only structural guarantee about write access given there is no RLS and no auth. (Supersedes nothing.)
- **D-011** 2026-08-09 — **One `games` table, one feature function, for both training and inference.**
  Backfill history into Postgres and let the existing ESPN sync keep it current. Two sources feeding one
  feature function is train/serve skew — the failure that produces a great backtest and a quietly worse
  live model, with no visible symptom. Feasible only because D-004 chose an ESPN-keyed source, so the
  two merge rather than needing reconciliation. (Supersedes nothing.)
- **D-012** 2026-08-09 — **Seven-day horizon, appended daily, keyed (game_id, model_version, as_of).**
  Resolves the conflict between wanting a week-ahead board and immutable predictions: rows are never
  updated, the board shows the newest, and the tracker evaluates the last row before tip-off. A
  prediction made six days out has a rest-day feature that is wrong, not merely stale — refreshing is
  required, so immutability had to be defined per-row rather than per-game. (Supersedes nothing.)
- **D-013** 2026-08-09 — **Expanding-window walk-forward, three folds**, rather than one sealed season.
  A single 1,326-game season carries ~±2.6 points of accuracy noise at 95% confidence, so a 62% gate on
  one season is substantially decided by chance. Three folds give ~3,900 evaluation games plus a
  fold-to-fold spread, while the final fold remains untouched as the headline. Random k-fold is rejected
  outright: it trains on the future. (Supersedes nothing.)
- **D-014** 2026-08-09 — **Phase 1 is the analytical core evaluated offline, with no application
  change.** The existential risk is whether four pre-game features clear 62%, and that is answerable
  from the data files before any migration exists. Building the plumbing first means possibly writing it
  for a model that never ships. This is §5.2's "extract the analytical core" applied literally.
  (Supersedes nothing.)
- **D-015** 2026-08-09 — **Cold start handled by shrinkage toward the league mean**, weight ≈ `n/(n+k)`
  with k≈5, rather than dropping early-season games. Dropping until both teams have N prior games costs
  ~16% of every season including all of opening month — and would take the product dark for three weeks
  each autumn, exactly when interest peaks. (Supersedes nothing.)
- **D-016** 2026-08-09 — **The model package lives inside the backend, with serving and training
  dependencies split.** D-010 puts inference inside FastAPI, so the API image must eventually load the
  artifact and run the feature function regardless — placing the package anywhere else guarantees a
  later move or a duplicated feature function, the latter reintroducing exactly the skew D-011 removed.
  Training-only tooling stays out of the deployed image. (Supersedes nothing.)
- **D-017** 2026-08-09 — **Retrain on all five seasons before the 2026-27 opener (2026-09-30).** The
  walk-forward establishes the honest estimate using only past-trained folds; once banked, the holdout
  has done its job. The next NBA game is 2026-10-03, so there are ~7.5 weeks with no games — the window
  Phase 1 needs — and 2026-27 then becomes a genuine live out-of-sample test, the most credible number
  this project can produce. (Supersedes nothing.)

- **D-018** 2026-08-09 — **A reviewer ✅ goes stale only while a task is `REVIEWED`, never once it is
  `DONE`.** Gating DONE meant every completed task's review expired on the next commit anywhere in the
  repo, which made the check unusable past a project's first phase (F-018). REVIEWED is "passed review,
  awaiting merge" — its ✅ must reflect the code about to merge. DONE is "merged/shipped": frozen
  history. If DONE code is later modified, that is a new task with its own review, not a re-review of
  the old one. Promoted to canon (`be5f6dc`) rather than patched locally, because every project stamped
  from this canon has the bug. (Supersedes nothing.)

- **D-019** 2026-08-09 — **T-005 implementation choices the plan left open.** (1) Parses with pandas
  rather than the stdlib `csv` module — this source's CSVs carry a `highlights` column with embedded
  newlines *and* Python-repr'd numpy arrays inside quoted fields, and pandas' C parser is the more
  battle-tested engine for that; it's also what T-006/T-009 will need regardless, so `loader.py`'s
  "heavy import" is not wasted. (2) Data lands at repo-root `data/raw/nba_schedules/` (not
  `backend/data/`) — matches CLAUDE.md's framing of `data/` as the historical store's home. (3)
  Idempotency is cache-then-verify: a valid cached file short-circuits re-download entirely, rather
  than always re-fetching and overwriting; a corrupted/truncated cache fails loudly instead of
  silently re-downloading, consistent with "fail hard, don't self-heal quietly." (4) Filtering does
  **not** split by `season_type` — the pinned expected counts (docs/IMPLEMENTATION.md T-005) are
  measured across all three (regular/postseason/play-in), so filtering further would fail the count
  tripwire by construction; `season_type` is carried through unfiltered for T-006+ to use. (5) Hit an
  environment issue, not a plan gap: the python.org macOS build doesn't read the system keychain, so
  `urllib`'s default SSL context failed closed with `CERTIFICATE_VERIFY_FAILED`. Fixed by passing an
  explicit `ssl.create_default_context(cafile=certifi.where())` — `certifi` is already a transitive
  `requirements.txt` dependency (via `httpx`), so this added nothing to the served image; verification
  is unchanged, only the CA source is made explicit. (Supersedes nothing.)

- **D-020** 2026-08-10 — **T-006 feature definitions the plan left open.** (1) **Rolling form is
  season-scoped**, over a 10-game window. Last season describes a different roster, and scoping it
  this way is also what makes D-015's cold start recur every autumn rather than only in 2022 — which
  is what D-015's own cost estimate ("~16% of every season, including all of opening month") assumes.
  The window caps the shrinkage count too, not just the average, so 15 straight wins is n=10, not
  n=15. (2) **Season-to-date point differential uses every game of the season so far**, not a window —
  the plan's wording, and deliberately the slower-moving counterpart to form. (3) **Rest days are
  measured to tip-off, not to `as_of`, and capped at 5 days.** Measured on the real corpus: median gap
  2.0 days, p95 3.9, max 10.0 (the All-Star break), only 2.3% of gaps reach 5. Past that point extra
  days are schedule structure rather than rest. The cap also removes the empty case — a season opener's
  "no previous game" and an offseason gap both land on the cap, which reads as fully rested. Measuring
  to tip-off is what makes D-012's "a prediction six days out has a rest feature that is *wrong*, not
  merely stale" visibly true; measuring from `as_of` would hide it behind a self-consistent number.
  (4) **`home_advantage` is 0.0 at a neutral site** — 19 real games, all regular season, spread across
  all five seasons, so the feature is genuinely non-constant rather than an intercept in disguise.
  (5) **A tied final score is refused as corrupt input**: NBA games cannot tie, none of the 6,615 do
  (verified), and the bare `home_score > away_score` the loader uses would silently score a tie as a
  home loss and bias every form feature that team appears in. (Supersedes nothing.)
- **D-021** 2026-08-10 — **`features.py` is standard-library only; `dataset.py` is the pandas seam.**
  Two independent constraints force it, and either alone would be sufficient. CI (`gate.yml`) installs
  `requirements.txt` and never `requirements-train.txt`, so a pandas import in the feature module fails
  `be-unit` in CI while passing locally — the split-environment failure class that cost T-005 a review
  round (F-037). And D-016 has Phase 2 importing this module inside the FastAPI service, so whatever it
  imports the served image must carry; a heavy feature module creates pressure to write a lighter second
  copy for serving, which is exactly the train/serve skew D-011 removed. Rather than leave the seam
  implicit, `backend/model/dataset.py` exists to hold it: `games_from_frame` / `load_games` convert the
  loader's frame to `Game` records and are the only place the two worlds meet. (Supersedes nothing.)
- **D-022** 2026-08-10 — **The target is a scoreless `Matchup`, and an `as_of` after tip-off is
  refused.** The plan said "target game", which would naturally have been the completed `Game` record.
  Splitting the type is what turns the no-leakage guarantee from a filter into a structural property:
  the target's own result is not reachable from inside the feature computation at all, so no bug can
  leak it, and the same type is equally constructible for a game played in 2022 and one tipping off
  next Tuesday — which is what lets training and inference call one function (D-011). Refusing
  `as_of > target.date` makes D-010's "a model that re-predicts a finished game uses information that
  did not exist" mechanical rather than remembered. Cost: T-009 calls `compute_training_features`
  (`as_of` = the game's own tip-off, no parameter to get wrong) or `game.matchup` explicitly.
  (Supersedes nothing.)

- **D-023** 2026-08-10 — **Per-task review currency is derived from git, not declared in the tracker,
  and process commits do not claim ownership** (the two forks F-043's fix ran into). (1) A
  hand-maintained `files:` field per task was rejected: narrowing it narrows the gate, so it would be
  a new bypass of exactly the kind F-019 and F-025 already had to close. Deriving ownership from
  commit history can't be gamed by editing a document. Attribution reads the commit **subject** only —
  bodies routinely discuss other tasks, and the commit that opened F-043 names T-005, T-006, T-007 and
  T-009 in its body, so body matching would have handed T-005 ownership of files it never touched.
  (2) `review(...)` and `docs(...)` subjects are excluded, on evidence rather than taste: `0c3b8a9`
  (`review(T-005): record gate verdicts; fix F-035/F-036`) also touched `checks/lib/tracker.mjs` and
  `.claude/CANON-VERSION`, so attributing it would have made T-005 own the gate's own source — and
  the F-043 fix, which edits that file, would then have invalidated T-005's review. The remedy would
  have reintroduced the false positive it exists to remove. This also matches a convention the project
  had already written down in `log.md`: a review commit must be `docs/`-only. Note what the rule does
  **not** need: later commits need no attribution at all. A task's own commits establish its file set;
  git then answers whether anything touched those files since, however it was labelled. A task with no
  attributable commits falls back to the stricter repo-wide rule rather than being exempted.
  (Supersedes nothing; implements the *Future hardening* item F-019 filed and F-043 sharpened.)

- **D-024** 2026-08-10 — **`home_advantage` is near-collinear with the intercept in the early folds.
  Supersedes D-020(4).** D-020(4) justified the neutral-site feature with "19 real games, all regular
  season, spread across all five seasons, so the feature is genuinely non-constant rather than an
  intercept in disguise." The logic reviewer checked that claim against the corpus and it is wrong on
  both counts; re-verified independently here:
  - **3 of the 19 neutral games involve F-042's All-Star phantom ids**, so they are not all regular
    season, and they disappear the moment F-042's filter lands.
  - After that exclusion the per-season counts are `{2023: 1, 2024: 3, 2025: 6, 2026: 6}` — **2022 has
    zero**, so they are not spread across all five seasons either. PLAN-v1's **first training fold
    (2022–23) contains exactly 1 neutral game in 2,643 rows**: `home_advantage` is `1.0` in
    2,642 of 2,643.
  A column that constant is near-perfectly collinear with the intercept, so its fitted coefficient is
  unstable and essentially arbitrary — which directly undermines T-010's acceptance ("records which
  features carried signal, via coefficients and their direction"). **This is not a T-006 code change**
  — the feature is computed correctly and is genuinely informative by the later folds. It is a
  constraint on T-009/T-010: report the coefficient with its uncertainty and say plainly that the
  early folds cannot identify it, or fold `home_advantage` into the intercept for those folds. What
  must not happen is quoting a home-court coefficient from fold 1 as if it meant something.
  Lesson worth keeping: D-020(4) cited a real number (19) and drew a wrong conclusion from it,
  because the number was never broken down per season or checked against the known-contaminated ids
  the *same session* had just found (F-042). A count is not a distribution.

- **D-025** 2026-08-10 — **F-042's exhibition filter identifies by games-played, is verified rather
  than trusted, and excludes by default.** Three choices, each with a discarded alternative:
  (1) **Not a hardcoded list** of the ten `game_id`s or twelve team ids. That would be exact today
  and silently wrong at D-017's retrain, when a new season brings its own All-Star game with fresh
  ids and a list would pass it through with no signal. Games-played separates the populations by
  **3 versus 82**, so a threshold of 20 sits nowhere near either edge and keeps working for seasons
  nobody has downloaded. (2) **Verified, not trusted**: after filtering, every season must be left
  with exactly 30 team ids, and every pinned season must shed exactly its pinned count; an unpinned
  season is refused outright (F-028's lesson). If upstream's shape changes — expansion, a shortened
  season, a new exhibition format — the assertion fires instead of the model training on the wrong
  rows. (3) **Excluded by default** (`load_games(include_exhibitions=False)`). The two populations
  are indistinguishable downstream: an All-Star row has ordinary-looking features and a coin-flip
  label, so a caller who forgets a flag gets a contaminated evaluation and *no symptom*. The safe set
  is the one you get without asking. The filter lives in a new **standard-library-only**
  `backend/model/corpus.py` rather than in `dataset.py` specifically so its tests run in CI (D-021) —
  it decides what T-009 trains on, and `dataset.py`'s tests skip where pandas is absent.
  **The loader is deliberately NOT changed**: `EXPECTED_COMPLETED_COUNTS` still pins 6,615, because
  those counts are the tripwire proving the download is intact, and quietly changing what they count
  would defeat them. 6,615 is the *verified* corpus; 6,605 is the *modeling* corpus. (Supersedes
  nothing; implements F-042.)
- **D-026** 2026-08-10 — **The home-court baseline on the modeling corpus is 55.53%, not 55.56%.**
  D-007 measured 55.556% over 6,615 games; that set included F-042's ten All-Star exhibitions. Over
  the curated 6,605 it is **55.534%** (per season: 2022 .5480, 2023 .5811, 2024 .5474, 2025 .5450,
  2026 .5552). The difference is 0.02pp and changes no conclusion — D-007's structural finding (a
  ~4-point break at the COVID seasons, and 58% being an artifact of a bygone era) stands untouched.
  It is recorded because D-008 makes "log loss beating a **constant 55.56% predictor**" half of the
  ship criterion, so T-008/T-009 need to know which constant to use and to say which corpus produced
  it. **Use 55.534%**, the rate of the set the model is actually evaluated on, and state it. An
  unreproducible number in a portfolio artifact is exactly what T-010's acceptance forbids.
  (Refines D-007/D-008; supersedes neither.)

- **D-027** 2026-08-10 — **T-002 is closed as superseded by T-005, not built.** Its acceptance
  ("seasons 2022–2026 land in the modeling store; counts match the source; ingest is idempotent; the
  store is gitignored") is satisfied clause-for-clause by `backend/model/loader.py`, which was built
  under T-005 and reviewed ✅✅ there. Leaving it `BACKLOG` implied outstanding work that does not
  exist — the more expensive error, since the next planning session would have scheduled it.
  Its ledger row therefore carries **T-005's** review SHAs, with the provenance stated in the notes
  column: the code really was reviewed, just under a different task id. The one clause never built is
  the play-by-play parquet, and that is deliberate rather than an omission — Phase 1 is team-level
  pre-game only (PLAN-v1 *Out of Scope*), so PBP has no consumer. It becomes a new task if a feature
  set ever needs it. Consequence worth noting: T-002 predates the plan, and its overlap with T-005
  went unnoticed for a full phase because nothing cross-checks a `BACKLOG` task against work that
  later subsumes it. (Supersedes nothing; closes T-002.)

- **D-028** 2026-08-10 — **A re-review is scoped to the diff since the last verdict, plus whatever
  that diff added; the *reviewer* holds the trigger for a full re-review.** Round 1 reviews the task.
  Every round after reviews `<last-verdict-SHA>..HEAD` for that task's files, plus the additions the
  builder must declare under rule 5c. Evidence both ways, which is why the trigger placement matters:
  scoped slices A and C cost ~148k tokens *together* and both completed, against ~145k **each** for
  the unscoped runs, 6 of which died before reporting — while the rigour was identical (the same 17
  mutations, the same reproductions, run under a stricter cache discipline). But F-072 showed the
  scoping mechanism itself can introduce defects, so scoping is not a licence to relax scepticism.
  The trigger sits with the **reviewer**, not the builder: the builder declares what changed and what
  was added, and the reviewer decides whether that is patchable-in-place or a rewrite needing a whole
  -task round. Giving that judgement to the builder would let the party with the incentive to finish
  choose how much scrutiny it gets — and the reason T-006 took three rounds is precisely that
  builder-added code kept escaping review (F-102). (Supersedes nothing; implements Tracker rule 5e.)

- **D-029** 2026-08-10 — **LOW findings are batched, except the class that keeps turning out not to be
  LOW.** 45% of this project's findings (34 of 77) are LOW, and each has carried the same process
  weight as a HIGH: an index row, a remediation, a commit paragraph, and re-verification every
  subsequent round. That is a multiplier under everything else. Going forward a reviewer still
  **reports** every LOW observation — a reviewer who suppresses findings is worse than a verbose one
  — but genuinely cosmetic ones (dead defensive branches, stale docstring phrasing, unreachable
  clamps) collect into a single test-hygiene backlog entry per round: one index row, one remediation
  pass, no per-round re-verification.
  **The carve-out: a LOW finding describing "a test does not pin what it claims to pin" is promoted
  to MEDIUM and tracked individually.** That class has already twice proven to be a safety gap
  wearing a LOW label — F-059 (an unexercised `neutral_site` default) became **F-061**, the untested
  exclude-by-default that would have let contaminated rows reach T-009; and F-055's self-referential
  `to_vector` test was hiding a positional contract the estimator depends on. Severity at filing time
  is a guess, and this is the class where the guess has been reliably wrong.
  Not applied retroactively: of the 34 existing LOW findings only ~5 are still open, so rewriting
  append-only history would buy nothing. Accepted cost: a batched item is easier to defer forever,
  and this project's record there is poor — F-024 read OPEN for a full phase after being fixed, and
  F-019's promised *Future hardening* entry was never actually filed. The status index is the
  mitigation, and it only works if it is maintained. (Supersedes nothing; implements rule 5f.)
