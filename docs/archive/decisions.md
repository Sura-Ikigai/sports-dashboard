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

- **D-030** 2026-08-12 — **Logistic regression is implemented in the standard library and the model
  artifact is JSON, not a pickle — because that makes T-009's security note inapplicable rather than
  merely mitigated.** The note is the sharpest in the plan: *"the serialized artifact is an
  arbitrary-code-execution vector. Loading a pickled object executes code inside it, and Phase 2
  loads this artifact inside the API service."* A four-feature logistic regression is a 5×5 Newton
  solve and the fitted model is five floats; serialized as JSON there is **no deserialization step
  that can execute anything** — `json.load` returns dicts and floats or raises. Same move as D-004 on
  F-005 (choose a source so no join exists to get wrong) and D-022 on leakage (make the target
  scoreless so its result is unreachable): design the threat out rather than guard it.
  Deviates from PLAN-v1's "thin wrapper over the logistic-regression implementation", deliberately.
  Three further consequences: the served image gains **nothing** for Phase 2 inference beyond `json`
  and the stdlib feature function; coefficients are directly readable, which T-010 requires; and the
  version is a **content hash**, so the same data and config always produce the same `model_version`
  (D-012 keys persisted predictions by it) and an edited artifact is detectable.
  The cost is that the fit is ours to get right, so it is checked three ways: coefficient recovery
  from synthetic data with known truth; the vanishing-gradient property that *defines* the optimum,
  which cannot agree with a wrong implementation by sharing its arithmetic; and a one-time
  cross-check against **scikit-learn 1.9.0** — max |Δcoefficient| **2.1e-08**, max |Δprobability|
  **1.9e-08** across 6 trials at n=200..3000. scikit-learn was installed ad hoc for that check and is
  **not** a dependency of anything committed; it deliberately does not appear in
  `requirements-train.txt`, because a test that needs it would skip in CI, which is how F-091
  happened. (Supersedes nothing; refines PLAN-v1's `estimator` description.)
- **D-031** 2026-08-12 — **The model ships, and it ships on one feature.** Ablation on the sealed
  2026 fold: removing `point_diff_diff` costs **3.7 accuracy points** (.6762 → .6392) and 5.0 AUC
  points; removing `home_advantage`, `form_diff` or `rest_diff` each *slightly improves* the model
  (+0.0023, +0.0015, +0.0076). Season-to-date point differential carries essentially all of the
  signal, which the coefficients already say (+0.685 against +0.037/+0.060/+0.157 on standardized
  features). This is not a defect and it does not change the ship verdict — but T-010 must report it
  plainly rather than describing four features as contributing. It also confirms **D-024/F-057**
  empirically: `home_advantage`'s coefficient is small and unstable across folds (+0.112, +0.003,
  +0.037), exactly as predicted for a column that is 1.0 in 2,642 of fold 1's 2,643 rows.
  Consequence for Phase 2: a simpler model is cheaper to serve and easier to explain, and the three
  weak features are candidates for removal — but not before T-010 has recorded the result as it
  stands. (Supersedes nothing.)
- **D-032** 2026-08-17 — **Margin-of-victory Elo replaces `point_diff_diff`.** K=20, home adjustment
  100, season carryover 0.75. Measured on the 2024+2025 dev seasons (2,640 games, 2026 deliberately
  untouched): MOV Elo scores **AUC .7149 alone** against `point_diff_diff`'s **.7073**, at
  **correlation .9187**. Plain win/loss Elo scores .7093 and K=40 scores .7086 — margin is what earns
  the gain, and a high K over-reacts. The two are not complements: MOV Elo *is* an opponent-adjusted
  point differential, the same margin information with strength of schedule folded in. Keeping both
  buys roughly +.002 and costs coefficient interpretability, which is the same trap D-024 documented
  for `home_advantage` — and §3 of the analysis is a coefficient table while user story 30 promises an
  explanation of why a team was favored. Elo's residual on `point_diff_diff` still ranks at AUC .566,
  which is the orthogonal signal replacement captures and addition would double-count. On the 13.6%
  of games where the two pick different winners, Elo is right .522 — near a coin flip, so this is a
  modest upgrade honestly labeled, not a breakthrough. (Supersedes PLAN-v1's feature set.)
- **D-033** 2026-08-17 — **`form_diff` and `home_advantage` are removed.** Form is a shrunk win rate
  over ten games — a worse ruler for the strength factor Elo now measures, correlated with it and
  with point differential in the .87–.92 band, and it ablated at +.0015 (inside noise). Home
  advantage was never identifiable (D-024/F-057): the corpus holds ~16 neutral-site games, so the
  column is 1.0 almost everywhere and near-perfectly collinear with the intercept. Home-court
  advantage is real and stays in the intercept. F-057's `revisit-when: T-009` fired and is resolved
  by this entry. (Supersedes nothing; completes D-031's open question.)
- **D-034** 2026-08-17 — **Rest is re-encoded as `home_b2b`, `away_b2b` and a bucketed `rest_edge`.**
  The capped linear day-difference measured nothing, and a diagnostic against the pinned corpus
  established why — the failure is not linearity. The marginal curve is already monotonic (.4857 /
  .5116 / .5577 / .5714 / .6667 across rest_diff −2→+2), so a linear term *can* fit that shape. Two
  other things kill it. **Mass**: 3,694 of 6,476 games sit at rest_diff 0 and another 2,228 at ±1, so
  91% of the corpus is where rest does nothing and the fit is dragged to the null; the tail carrying
  the effect is 5.9% of games. **Cliff placement**: home rest 0→1 is **+7.1 points**, 1→2 is +2.6,
  2→3+ is +1.0 — one slope cannot fit a step and a plateau. The raw signal is large: home on a
  back-to-back against a 2+-day-rested opponent wins **.4393**; the reverse wins **.6438**, a
  **20.5-point swing** around a .5553 baseline, with mean home margin swinging −0.10 to +3.59.
  Separate home and away indicators because the effect need not be symmetric. **Also corrects how
  D-031 was being read**: one season carries ±2.6 accuracy points at 95%, and the ablation deltas
  (+.0076, +.0023, +.0015) are all inside that band. "Removing them improves the model" is not
  supported; "they do nothing measurable" is — which points at the encoding, not at deletion.
  (Refines D-031's interpretation; supersedes PLAN-v1's `rest_diff`.)
- **D-035** 2026-08-17 — **Availability is lagged rotation participation, never same-game.** Player
  box scores exist upstream for every season (`espn_nba_player_boxscores`, ~750 KB/season, ~175k
  player-game rows across 2022–2026), but they record *who actually played*, which is post-game
  information. Using them for the game being predicted is textbook leakage, and "played 0 minutes"
  additionally correlates with blowouts and garbage time, so it would partly encode the outcome.
  Availability is therefore derived strictly from games completed **before** the as-of moment: each
  team's rotation defined by trailing minutes, then the share of that rotation's minutes that
  actually played in recent games. It catches multi-game absences — which is most star injuries — and
  **cannot** catch a game-day scratch. That limitation is a consequence of choosing a construction
  whose leakage-freedom is structural rather than procedural, and it is reported rather than hidden.
  Rejected: a live injury report (no historical archive exists, so it is untrainable until a season
  of snapshots accumulates) and a train-lagged/serve-live hybrid (the feature would mean different
  things at train and serve time, which is exactly the skew D-011 exists to prevent).
  (Supersedes nothing — this is the first feature outside team-level aggregates.)
- **D-036** 2026-08-17 — **Travel and altitude derive from the venue city already in the corpus.** The
  schedule rows carry `venue_full_name`, `venue_address_city`, `venue_address_state`, `venue_indoor`
  and a tz-aware `game_date_time`, so travel distance since a team's previous game, timezone shift
  and an altitude flag need no new data source — only a static city table with coordinates and
  elevation. A venue absent from that table raises; a silent zero would read as "no travel".
  Expected value is small (~+.002) and it is included because it is nearly free given D-034's work,
  not because it is expected to matter on its own. (Supersedes nothing.)
- **D-037** 2026-08-17 — **Seasons 2016–2019 are ingested as Elo warm-up state only.** Elo converges
  from its initial rating over roughly a season, so starting at 2022 means fold 1 trains on burn-in
  noise. The same pinned release carries schedules back to at least 2013 (verified by request). But
  extending the *training* window crosses a regime change: home-win rate measured here runs ~55.2%
  from 2021 onward against 59.3% before (D-007/D-026), and 2020–2021 were bubble and limited-crowd
  seasons. So warm-up games feed the rating recursion and **never** become training or test rows —
  T-029 asserts it. This buys converged ratings without mixing eras. (Supersedes nothing.)
- **D-038** 2026-08-17 — **Bulk history moves into the application Postgres, superseding the
  separate-stores invariant.** The tracker's *Architecture snapshot* has said "The app DB (Postgres)
  and the historical/modeling store are separate concerns. Bulk history never enters Postgres
  wholesale"; this entry overturns it deliberately, at the owner's decision, and that snapshot line
  must be rewritten rather than quietly dropped. The driver is that this cycle joins schedules,
  player participation and venues across ten seasons, and hand-rolled frame manipulation is where
  correctness bugs hide. The volume does not justify infrastructure on its own (~12 MB, ~175k rows) —
  the argument is queryability and auditability, not scale. Recorded cost: the alternative considered
  and rejected was an embedded DuckDB over parquet, which would have preserved single-command
  reproducibility; the owner chose Postgres on the grounds that this is a real project. That cost is
  paid explicitly in D-043 and D-044 rather than absorbed. (**Supersedes** the separate-stores
  invariant in `docs/IMPLEMENTATION.md` → Architecture snapshot.)
- **D-039** 2026-08-17 — **SQL narrows; `features` filters. No query carries an as-of predicate.**
  Reading history from SQL makes `WHERE date < :as_of` the natural thing to write, and that would
  move the integrity control out of the tested module and into every call site — exactly what T-006's
  security note forbids, and the class of defect F-044 already was (an as-of an hour past tip-off
  moved `point_diff_diff` from 6.0 to 32.0). Queries may reduce rows by season or by team for
  performance; the strict `<` filter, the target-game exclusion and `FeatureLeakageError` all stay
  inside the feature module, where they are enforced and tested. **The test that makes this real**: a
  store query deliberately returning future rows must still produce identical feature vectors. T-024
  additionally adds a mechanical check that fails if a date predicate appears in the store module —
  the same enforcement style as T-009's `ast` argument-forwarding check. (Supersedes nothing;
  preserves T-006's security note under D-038.)
- **D-040** 2026-08-17 — **The game detail surface is a decomposition waterfall plus a fenced what-if
  panel.** The linear model chosen in D-032/D-033 is the best possible model for an explanation UI:
  per-feature contributions are additive in log-odds, so the waterfall is exact arithmetic rather
  than an approximation — a gradient-boosted model would need SHAP to estimate what this yields
  directly. That is now a reason to keep the model class linear, recorded here so a future
  "just use LightGBM" proposal has to argue against it. The what-if panel answers the owner's ask for
  interactivity, but it manufactures probabilities the model was never evaluated on, so hypothetical
  state is visually distinct, never persisted, and never counted in the accuracy record.
  (Supersedes nothing; realizes PLAN-v1 user stories 25 and 30.)
- **D-041** 2026-08-17 — **A TypeScript scorer is permitted, and a gate check is what permits it.**
  The what-if panel needs instant recompute, which means a second implementation of the scoring path
  in the browser — precisely what D-011's single-feature-function design exists to prevent. Rather
  than forbid it or accept the skew, both implementations run over a grid of feature vectors in the
  gate and must agree to floating-point tolerance on **both** the probability and the per-feature
  contribution decomposition. This is the project's standing pattern: make the guarantee mechanical
  rather than asserted (T-006's as-of filter, T-009's `ast` check, T-010's string-matched figures).
  The check runs in CI, not locally — F-091 is the precedent for why. (Refines D-011.)
- **D-042** 2026-08-17 — **Full scope, model frozen before 2026-09-30.** Predictions are keyed by
  `model_version` (D-012), so a mid-season model change would be recorded rather than hidden — but it
  would split the live trial into two partial seasons, neither with a full sample. D-017 designates
  2026-27 as the genuine out-of-sample trial, so the model freezes before the opener and the season
  runs on one version. The owner considered and rejected a thin end-to-end slice and a
  frontend-first-on-v1 sequencing, on the grounds that nothing should be half-built when the trial
  begins. Recorded constraint, not an objection: this repo's measured review throughput is 4 rounds
  for T-001, 4 for T-005, 3 so far for T-006, against ~15 new tasks plus 4 awaiting review.
  (Supersedes nothing; scopes D-017's trial.)
- **D-043** 2026-08-17 — **The reproducibility claim is amended, not quietly broken.** `PHASE-1-RESULT.md`
  §6 says every number is reproducible from committed code and the pinned data release with one
  command. Under D-038 the pipeline requires a running Postgres, so that sentence stops being true
  the moment the corpus moves. It is amended to state the dependency explicitly (T-035). The
  underlying property — same data and configuration produce the same `model_version` — is unchanged;
  what changes is what a reader must have running to reproduce it. (**Supersedes** the unqualified
  §6 claim.)
- **D-044** 2026-08-17 — **CI provisions a Postgres service container for corpus-touching tests.**
  Consequence of D-038. The standing hazard is F-037/F-091's: a test that skips in CI is not a test,
  and this project has lost review rounds to exactly that. `features.py` and its siblings stay
  standard-library only (D-021/D-016) so the *feature* tests still run without a database; the
  service container is for `ingest`, `store` and anything reading the corpus. Any test that would
  skip must be justified in its task's outcome. (Supersedes nothing.)
- **D-045** 2026-08-17 — **Migrations own schema; `ingest` owns data.** A data load inside an Alembic
  migration has no coherent `downgrade()` — delete every row, or only the ones that revision added? —
  and this project's two existing migrations are cleanly reversible DDL, a property one data
  migration would forfeit for the whole chain. It would also make CI pay to download and parse ten
  seasons on every `alembic upgrade head`, and it would put regenerable derived data into an audit
  trail meant for irreversible structural change. So `alembic upgrade head` creates empty corpus
  tables and `model.ingest` populates them, idempotent on game id. The verification machinery already
  written in `loader.py` keeps running at ingest, which is where it belongs. (Supersedes nothing.)
- **D-046** 2026-08-17 — **Integrity is verified at the ingest boundary; the database is trusted
  after.** T-005 pins per-season SHA-256 hashes over source bytes, which stops meaning anything once
  rows live in a mutable table. Rather than invent a table-level integrity scheme, the guarantee moves
  to the boundary: source bytes are still hashed and verified before parsing (existing code, zero
  additional cost), pinned per-season counts still assert, and `corpus.assert_curated` — per-season
  counts plus the 30-franchise invariant, standard library — re-runs on read, which would catch a
  mutated table. The owner accepted trusting the store; this narrows what is actually being trusted
  to "the database, after a verified ingest, with a cheap invariant checked on the way out."
  (Refines T-005's content pinning under D-038.)
- **D-047** 2026-08-17 — **No authorization boundary; model results are public.** All readers are
  equivalent and every visitor may see every model output, so no RLS and no per-caller checks are
  built (this is the standing state F-001 documents, now made deliberate for this surface rather than
  merely unaddressed). One concrete consequence is mitigated: `POST /nba/sync/teams` and
  `POST /nba/sync/games` are unauthenticated write endpoints that under D-038 share a database with
  the training corpus. The mitigation is grants, not row-level security — the API's database role
  holds `SELECT` only on corpus tables and the ingest job holds a separate writing role, so no API
  bug can reach training data. T-021 asserts it by connecting as the API role and proving a write is
  refused. F-001 stays ACCEPTED and fires on `first-user-scoped-data`. (Scopes F-001.)
- **D-048** 2026-09-06 — **Live operational data is verified by structure and continuity, not by
  content hash.** D-046 pins SHA-256 over source bytes and per-season row counts. That works because
  a finished season's schedule file never changes again. T-031 needs the *current* season's file, and
  it changes nightly: scores fill in, `status_type_completed` flips, playoff rows are appended
  (2026-27 is 1,206 rows in September and will be ~1,320 by June), and existing rows change identity
  — the five NBA Cup placeholders carry `home_id = -1` / `away_id = -2` and resolve into real
  matchups **under the same game ids** in December. A pinned hash is not merely unavailable here; it
  answers a different question. For the corpus the question is *"is this exactly the data the model
  was fit on"* — reproducibility. For a live schedule it is *"is this a plausible, self-consistent
  NBA schedule"* — plausibility. Conflating them would leave the weaker guarantee wearing the
  stronger one's name.

  So this is a **separate decision with a separate table and a separate code path**, and D-046 is
  untouched. `corpus_games` keeps its NOT NULL scores and its verified-corpus contract; scheduled
  games live in `scheduled_games`, and a row crosses over only through the verified ingest. The
  boundary is visible in the schema rather than remembered.

  What replaces the hash:
  - **Structure** — required columns present, every row parses, dates timezone-aware, team ids are
    known franchises, the season label matches the season requested.
  - **Continuity, against the previous accepted snapshot** — the game count may grow or hold but
    never shrink, and a game id we have already predicted must not vanish. This is *stronger* than a
    pinned count for the threat it replaces: a total count masks a partial regression where rows are
    both lost and gained, and an id-level check does not.
  - **Provenance** — every snapshot is hashed, dated and retained, and each prediction records which
    snapshot produced it. Reproducibility is not lost, it moves from *before* to *after*: "what did
    we know when we predicted this?" is answerable exactly, which is the question a prediction
    actually raises and one a pre-pinned hash could not have answered better.

  Postponements are an explicit state, not an inference: the source carries `status_type_name =
  STATUS_POSTPONED`, the postponed row stays in the file permanently and never completes, and the
  replay is a **new game with a new id** on a new date (verified across all four postponements of the
  completed 2026 season — e.g. `401810507` on Jan 26 → `401858694` on Apr 1). So a prediction for a
  postponed game is retained and marked void rather than scored: there is no "eventual result" to
  score it against, because that game never happened and a different one did. The replay is an
  ordinary new game and gets its own predictions with features correct for its actual date.

  Change thresholds start permissive and logging. There is exactly one calibration anchor today — the
  completed 2026 season ended with 4 postponed games out of 1,330, about 0.3% churn across a whole
  season — and nobody has watched what a normal Tuesday looks like. A tight threshold built on zero
  observations would be a number someone made up. (**Complements D-046**, which continues to govern
  immutable training data; supersedes nothing.)
- **D-049** 2026-09-06 — **Confidence bands are defined on the favoured side's probability, in four
  fixed bands, and an untested band reports no hit rate rather than a hit rate of zero.** User
  stories 20, 21 and 31 turn a probability into a label and then ground that label in a track record,
  which makes the banding a published definition rather than a rendering choice: once hit rates are
  quoted per band, changing the boundaries silently invalidates every number previously shown.

  The band is taken on `max(p, 1-p)` — the probability the model assigned to the side it favoured —
  not on the home-win probability. A .75 home probability and a .25 home probability are one call at
  one confidence pointing opposite ways; filing them apart would make "how often is the model right
  at this confidence" unanswerable, which is the whole of story 21.

  Four bands, not ten: **Toss-up** [.50, .55), **Lean** [.55, .65), **Clear** [.65, .75), **Strong**
  [.75, 1.0]. A season is ~1,230 games, so ten bands would hold ~120 games each and sampling noise
  would swamp the differences between them. The boundaries separate the questions a visitor asks —
  "is this a coin flip?" from "is this a real call?" — rather than making the arithmetic tidy.
  `band_for` **raises** on a probability that falls in no band rather than defaulting, so a hole in
  the tiling is loud; a silent default would file games into the wrong bucket forever.

  A band with nothing scored reports `hit_rate: null`, never `0.0` — the two render identically as
  `0%` and one of them is a lie. Every band that does have games carries a 95% **Wilson** interval
  beside the point estimate: three-from-three is 100% and means nothing, and a surface shown only the
  point estimate has no way to say so. Wilson rather than the normal approximation because the normal
  form is wrong exactly where it matters here — small samples, rates near 0 or 1 — and produces
  intervals that run off the end of [0, 1].

  The record is scoped to **one model version**, defaulting to the version that made the most recent
  prediction. Mixing versions would average a frozen model's trial with whatever superseded it and
  call the result a track record, which D-017 (2026-27 is the genuine trial of one version) makes
  meaningless. (Implements user stories 20/21/31; supersedes nothing.)
- **D-050** 2026-09-06 — **One sanctioned second implementation of the scoring arithmetic, bounded to
  the arithmetic, valid only while the contract gate holds it.** D-011 forbids a second
  implementation of the scoring path, and the reason is sound: two implementations drift, and the
  symptom is a user seeing one number on a page and a different one in the accuracy record, with
  neither wrong in a way anybody can point at.

  T-034 needs one anyway. Story 25 wants a visitor to change a value and watch the probability
  respond; story 27 requires that exploration never reach anything that records it. A server round
  trip per interaction would be slow *and* would put a scoring request into a service whose entire
  security property is that it has no write path. So the browser scores.

  The exception is drawn as narrowly as it can be, and the boundary is the load-bearing part:

  - **What is duplicated is `prediction.decompose` and nothing else** — the logistic arithmetic,
    about five lines. `compute_features` stays a single implementation behind T-006's property test.
    A what-if changes a feature's **value**; it never recomputes a feature from history. A browser
    that derived `elo_diff` from game results would be the drift D-011 exists to prevent, and would
    not be covered by any contract.
  - **The exception is conditional on the gate.** `backend/model/contract.py` generates a committed
    grid from the Python implementation; `test_contract.py` fails if the grid is stale, and
    `contract.test.ts` fails if the TypeScript disagrees. Both live in required status checks on
    `main`. If either check is ever removed, this decision lapses with it and the second
    implementation must go — a duplicate scorer with no gate is simply a D-011 violation.
  - **Agreement is exact where it can be.** Contributions and the logit are compared with `==`, not
    a tolerance: each contribution is three IEEE-754 operations in a fixed order, which both
    languages perform identically, and the summation order is pinned on both sides. Only the
    probability, which passes through `exp`, is given slack — measured at **exactly 0 divergence**
    across 148 cases, with the tolerance retained because `exp` is not specified to agree across
    platforms.
  - **The grid is synthetic.** Scoring the frozen artifact would mean committing its parameters, and
    `/models/` is gitignored precisely so a model cannot enter git as if it were source (F-016,
    F-110). The grid instead **brackets** the shipped model — coefficients to ±8 against its
    |0.13|–|0.71|, means to ±1000, stds from 1e-3 to 1e3 against its .089–90 — and additionally
    covers regions the real model never visits. (**Scopes D-011**; supersedes nothing.)
