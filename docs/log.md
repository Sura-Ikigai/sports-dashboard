# Project log — Sports Dashboard

<!--
APPEND-ONLY CHRONOLOGY (SYSTEM.md §3, index-vs-log split). The tracker
(docs/IMPLEMENTATION.md) holds CURRENT STATE (overwritten); this holds HISTORY (never rewritten).
Newest entry on top. Keep it lean — a few lines per session; git history carries the detail.
-->

## 2026-08-14 — Canon reconciliation: grill-me on the Dev-System, and two integrity fixes

- Reviewed both Dev-Systems and this project's whole history against them. The finding that organises
  everything else: **the deterministic layer compounds and the prose layer does not.** Six canon
  defects found by using the system (F-018/019/025/035/036/043) were fixed in `checks/`, promoted, and
  now self-enforce. Six process failures (F-072, F-100..F-104) were fixed with rules to remember — and
  those are the ones still open. Tracker rule 5 is ordered **a, b, f, e, d, c**, appended mid-incident.
- **The proof is `26b360f` itself** — the one reconcile cycle that ran. It promoted the reviewer fix as
  prose into agent bodies, telling a `tools: Read, Grep, Glob` agent to run `git worktree add`,
  `git archive`, `pytest`, and set `PYTHONPYCACHEPREFIX`. None of it executable. F-072's lesson was
  promoted into a file that cannot act on it (F-106).
- **Promotion happened; adoption didn't.** We pushed the reviewer contract to canon on 08-12, stamped
  `last-reconciled: 26b360f`, and never pulled it back. For two days the agents the runtime loaded
  still said "write your Review ledger row" while canon and rule 5b said the opposite. `reconcile-canon`
  step 6 updates the pointer without syncing the files. **Fixed here (T-012).**
- **The lockfile carried a promotion that never happened.** It claimed F-103's `evaluateAgentIntegrity`
  went to canon "as part of `26b360f`". `git log -- checks/gate-completeness.mjs` in the factory returns
  one commit, `bd58a22`; canon's copy is 49 lines without it, ours 59 with it. The check that would have
  caught the paragraph above is the one that never got promoted. **Corrected here (T-011).**
- Measured the context cost properly: always-read set (`CLAUDE.md` + tracker) **~11,611 tokens**; every
  pointer followed, **~55,690**. The only successful scoped reviewer run on record was **53,837**. And
  the split is already regressing — 366 → 487 lines in one phase — because history didn't stop
  accumulating, it moved into the task `outcome:` blocks. Squeezed the balloon.
- Ran **grill-me over ten forks** and wrote **PLAN-v2** into the factory (DRAFT, on a branch, not
  merged). Every resolution converts a remembered practice into a mechanism: mechanism-only promotion;
  `learning-notes.md` deleted with `findings.md` as the single intake behind a `canon-debt` threshold;
  instantiation becomes a composer with a drift check; review currency moves from commit SHAs to
  declared file sets + content hashes; an 8k token budget on the always-read set; reviewers execute
  inside harness worktrees; namespaced per-reviewer report files; `reproduced: yes|no` ends a round.
  Between them those delete Tracker rules 5a–5d and retire F-072, F-100, F-101, F-102, F-104.
- Filed **F-105** (the Sura Media batch's 9-commit history is unpushed — two local clones, `origin` has
  only `main`), **F-106**, **F-107** (the factory runs none of its own hooks; PLAN-v2 sat untracked with
  nothing to notice — the one repo in the system not governed by the system).
- Marked `Sports/Dev-System/` as **NOT THE FACTORY**. Verified it holds nothing the factory clone lacks
  — both carry `canon/self-learning-batch-v1` at `ebb1dee`, tree-identical to the squashed `bd58a22` —
  so deletion is safe; marking is the reversible choice.
- Worth noting against PLAN-v2's own design: its `canon-debt` threshold of 3 would be **red at 6** today.
- Ended at: T-011/T-012 `BUILT`, Phase 1 branch pushed to origin for the first time. Next is grilling
  PLAN-v2 in the factory — Fork 5 hardest, since it rewrites the check that *is* the review gate.

## 2026-08-12 — T-006 round 3 closed out; T-007 built

- Round 3 came back ⛔⛔ but found **no live defect** — slices A (8/8 closures) and C (17/17 mutations)
  confirmed nothing regressed, and the real-corpus output is unchanged. What B and D found was
  future-regression insurance. That is the diminishing-returns signal, and I drove past it; the human
  called it.
- Fixed only the four T-007 actually depends on. **F-091** mattered most: the exclude-by-default guard
  did not run in the gate at all — my F-061 fix landed in a file that `importorskip`s out of CI, so
  flipping the default gave 90 passed / 1 skipped under CI's own environment. Closed with a stdlib
  `ast` check in `test_corpus.py`; verified by flipping the default *with pandas absent*, which now
  gives 3 failed. **F-090**: the fixture patched with `return_value=`, so `load_games` ignoring
  `seasons` survived — under T-007 that would have every fold train on its own test season with no
  symptom but a better number. **F-077/F-078**: `assert_curated` checked one of two invariants, and
  reintroduced the generator hazard F-045 closed.
- Accepted the other six with `revisit-when` triggers; none is reachable in Phase 1 as built.

## 2026-08-12 — T-009 answered the question; T-010 drafted

- **The ship criterion is MET.** Sealed 2026 fold: accuracy .6762 (bar .62), log loss .6020 vs the
  constant .55534 predictor's .6870, AUC .7323. Both halves of D-008. All three folds clear .62
  (.6444/.6503/.6762) over 3,962 evaluation games — which is what D-013 bought by refusing to decide
  on one season.
- **D-030**: the estimator is stdlib logistic regression and the artifact is JSON, not a pickle. That
  makes T-009's security note — "the serialized artifact is an arbitrary-code-execution vector" —
  inapplicable rather than mitigated. The fit being ours is checked by coefficient recovery, the
  vanishing-gradient property, and a cross-check against scikit-learn (max |Δcoef| 2.1e-08).
- **Two controls, because this is the number the project exists to produce.** Shuffling the training
  labels collapses the model to .5552 accuracy — exactly the test-season base rate — and AUC .5494.
  Not leakage. Ablation (**D-031**) says the uncomfortable thing: `point_diff_diff` carries all of it,
  and removing any other feature slightly *improves* the model. It also confirms D-024/F-057
  empirically — `home_advantage`'s coefficient is unstable across folds exactly as predicted for a
  column that is 1.0 in 2,642 of fold 1's 2,643 rows. The review process called that before the model
  was ever fitted.
- **T-010 drafted** (`docs/analysis/PHASE-1-RESULT.md`) for the human to own. Its security note —
  publish nothing that cannot be reproduced — was enforced mechanically rather than asserted: every
  numeric claim re-derived from a fresh run and matched against the text, 30/31, the one miss being
  the checker's own string pattern. That pass caught a real defect: the early-season uplift was stated
  as 9.6 points, comparing that period against the *season's* base rate instead of its own. Corrected
  to 8.1, with the wrong baseline named rather than quietly fixed.
- The analysis leads with the unflattering result rather than burying it, and refuses three things the
  numbers do not license: calling this a four-feature model, quoting a home-court coefficient, and
  assuming a backtest holds live.

## 2026-08-12 — T-008: evaluation metrics

- `backend/model/evaluate.py`, stdlib-only. Every metric asserted against hand-computed values on one
  5-game set; the comparator checked at `constant=0.5`, where a constant predictor's log loss is `ln 2`
  for any labels, so the test does not trust the implementation twice.
- Three deliberate departures from a library implementation, each for the same reason — a sentinel
  would be indistinguishable from the conclusion T-009 is trying to draw. `roc_auc` **raises** on a
  single-class set instead of returning 0.5 ("no signal" vs "not measurable"); `log_loss` clips so one
  confidently-wrong call cannot swamp a fold; AUC uses Mann-Whitney ranks with half-credit ties,
  because a shrunk feature set emits repeated probabilities.
- The boundary that is the point of T-008: **a model that merely reproduces the constant does not beat
  it.** A tie is not a win — that is the null D-008 tests against. Also pinned that the best possible
  constant IS the observed base rate, so the comparison is not against a straw man.
- Uses D-026's 0.55534 rather than D-007's 0.55556, which predates F-042's exhibition removal.
- Added an independent cross-check rather than more fixtures: AUC agrees exactly with brute-force pair
  counting over 400 tie-heavy random sets. That is evidence; a single hand-computed case is not.
- 27 tests. Suite 126 → 153.

## 2026-08-12 — T-007: expanding-window folds

- `backend/model/splits.py`, stdlib-only. Three folds as literals rather than generated — the
  evaluation protocol should be auditable, not derived. `Fold` validates at construction, so a fold
  that trains on its own future cannot be built.
- **Made the security note temporal rather than nominal.** "Every training season < test season" is a
  claim about labels, and labels can lie. `split_games` asserts the *dates*: latest training game
  strictly before earliest test game. Those coincide as a measured fact — seasons are disjoint with
  120–133 day offseasons — not as an assumption. A game backfilled into the wrong season passes the
  label check and fails the date check, and there is a test that does exactly that.
- Also verifies the input is curated (F-067), and that every season a fold names is present — a
  missing one would otherwise produce a quietly smaller fold reporting a healthy-looking number.
- Real corpus: 6,605 → fold 1 train 2,643 / test 1,319; fold 2 3,962 / 1,321; fold 3 (sealed)
  5,283 / 1,322. **3,962 evaluation games**, against D-013's predicted ~3,900; fold 1's 2,643 matches
  D-024 to the game. 15 tests, CI-safe. Suite 108 → 124.

## 2026-08-10 — Split the tracker: mandatory reading down 76%

- Measured first, because the bloat was assumed rather than known. `IMPLEMENTATION.md` had grown
  **455 → 1,367 lines** across T-005 and T-006, of which **74% was append-only history** — findings
  (785 lines) and decisions (236). Every agent was told to read all of it: **2,176 lines of process
  documents to review 2,206 lines of code.** That term grows forever; rounds and mutations do not.
- Split findings → `docs/findings.md` and decisions → `docs/decisions.md`, both still append-only and
  committed. Tracker is now **366 lines** and bounded. Tasks and the Review ledger deliberately stay
  put: `checks/lib/tracker.mjs` parses both out of `IMPLEMENTATION.md`, so moving them would break the
  gate. Verified nothing was lost — 76 findings, 24 decisions, 10 tasks before and after.
- The *status index* stays in the tracker and is now the only thing anyone needs to answer "is F-0NN
  open?". The archives are grepped for the two or three ids a task actually cites.
- **Slice C proved the thesis before the split even landed.** The same logic reviewer, the same 17
  mutations, the same rigour — but scoped to one job and told not to read the tracker end to end:
  **53,837 tokens in 5.8 minutes**, against ~145,000 and ~20 minutes for the earlier unscoped runs.
  17/17 caught, and it *finished* where 6 of 11 earlier reviewer runs died. Rigour was never the
  problem; unbounded scope and unbounded mandatory reading were.
- Reviewers now also report **incrementally to disk**, one appended block per item, so a death costs
  one item rather than the whole run.

## 2026-08-10 — F-103 closed: the gate now reads agent CONTENT, not just filenames

- Round 3 could not run: **all four reviewer agents died on the account session limit** (resets
  00:50). Two returned only mid-stream narration, which is not a verdict — T-006 still has no round-3
  result and none was invented. Corrects my earlier guess that the first pair had hit a harness bug.
- Used the time on F-103, the gap that let F-008 ship. `gate-completeness` was
  `readdirSync(agentsDir).filter(f => f.endsWith('.md'))` — any file with the right basename passed,
  whatever it said inside. New `evaluateAgentIntegrity` asserts the frontmatter `name:` matches the
  filename, and that any `stacks/<overlay>.md` an agent references is *this* project's overlay.
- Chose to assert only that a claim *made* is correct, rather than requiring every agent to declare a
  stack: `logic-reviewer` and `security-auditor` are legitimately stack-agnostic and cite none.
- Demonstrated against the real gate rather than only in fixtures: repointing `backend-engineer.md`
  at the Supabase overlay turns the check red naming the foreign stack — the literal F-008 signature
  — and green again on restore. 6 new tests; meta-unit 53 → 59.
- Explicitly out of scope: judging whether an agent's *guidance* is right for the stack. That needs a
  reader, and the review gate is where a reader belongs. This closes the mechanical half only.

## 2026-08-10 — T-002 closed; the parallel-review damage filed and mitigated

- **T-002 closed as superseded by T-005 (D-027).** Its acceptance was satisfied clause-for-clause by
  `loader.py`, built and reviewed under T-005; its ledger row carries T-005's ✅s with the provenance
  stated, because the code really was reviewed, just under another id. Leaving it `BACKLOG` implied
  work that did not exist — the more expensive error, since the next planning session would have
  scheduled it. The play-by-play parquet is the one clause never built, deliberately: Phase 1 is
  team-level pre-game only, so PBP has no consumer.
- **Filed F-100..F-104 for the Dev-System process gaps**, and mitigated the two that have already
  caused damage with a new *Tracker rule 5* — a review-round protocol that binds the next round
  rather than waiting on a canon change:
  - **F-100**: both round-2 reviewers were told "start at F-061" and both used it, producing two
    conflicting F-061..F-066 sets that had to be reconciled by hand. "Start at F-0NN" is not an
    allocation. Rule 5a now requires a disjoint block per reviewer, recorded before spawning.
  - **F-101**: canon tells reviewers to write the tracker themselves, which is safe only when they
    run sequentially. Parallel reviewers means concurrent writers to one file. This project has been
    overriding canon (report-only + main-thread transcription) without documenting it; rule 5b makes
    that explicit, including the fidelity cost it moves onto the transcriber.
  - **F-102**: canon has no notion that a remediation can *add* code, and that has now broken two
    consecutive rounds — `corpus.py` in round 1's fix, `assert_curated` and the opponent-matched
    exclusion in round 2's. Rule 5c requires a hand-off to list additions separately.
  - **F-103** (`gate-completeness` checks agent filenames, never content — how F-008 shipped) and
    **F-104** (nothing enforces the docs-only review commit the F-043 design leans on) filed, open.
- Numbering note: F-072..F-099 is left empty on purpose. The round-3 reviewers were spawned with
  "start at F-072" and are still running, so taking F-100+ was the only collision-free block —
  which is itself the evidence for F-100.

## 2026-08-10 — T-006 round 2: ⛔⛔ again, and the right call

- Both reviewers independently verified **every** round-1 finding closed — security re-ran its own
  reproductions against a hash-verified `git cat-file` snapshot (F-044's leak: 32.0 → 6.0), logic
  re-ran all 10 mutations and checked the four highest-risk ones at the *assertion* level, not the
  exit code. Then both ⛔'d on `corpus.py`. **It arrived with the remediation and no reviewer had
  ever seen it** — the same lesson T-005 taught: code that ships with a fix is unreviewed code.
- **F-065 was a real bug and it was mine.** Identification counted per season but returned a bare
  union of team ids, applied globally — so one partial season condemned every team in every season —
  and the 30-team assertion was built from the *survivors*, so a wiped season contributed no entry
  and passed vacuously. Result: 2022–2025 complete plus 150 games of 2026 returned **0 games from
  5,439 and raised nothing**, via the module's own documented `expected=None` path. Reproduced before
  fixing. Now keyed by `(season, team_id)`, and the team check iterates the *input's* seasons.
- **F-061**: `load_games` had no tests, so exclude-by-default — the whole safety property — was
  unpinned; flipping the default passed all 90 tests. F-059's pattern landing on the one guarantee
  the module exists for.
- **F-068**: my own F-044 fix was fail-open — a blunt `game_id` match silently deleted a colliding
  historical game. Now matches the opponent too, and raises on a genuine collision.
- **F-071 is the one to remember.** Both reviewers independently hit stale bytecode that CPython
  reused because its `(mtime, size)` header still validated — a size-preserving mutation restored
  within the same second. Security proved it in one process: `compile(source)` said 30,
  `import` said 29, `git hash-object` matched HEAD. **This threatens the "10/10 mutations caught"
  evidence the whole remediation rests on.** Caches purged, harness now isolates
  `PYTHONPYCACHEPREFIX` per run and re-asserts a clean baseline between mutations. The original claim
  survived — logic had independently reproduced it with a clean cache.
- Also fixed F-062/F-063/F-064/F-066/F-067/F-070; F-069 documented as a structural limit of
  identifying exhibitions by team rather than by game. `assert_curated` added and written into
  T-007/T-009's acceptance, because a default on the producer is not a guarantee at the consumer.
- The two reviewers collided on finding numbers F-061..F-066; reconciled in the ledger with logic's
  numbering kept as issued and security's renumbered F-067..F-071.

## 2026-08-10 — F-043 promoted to canon (Dev-System `1c52645`, pushed)

- Copied the three F-043 files to the factory (`Client Projects/Dev-System`), verified there, committed
  and pushed to `origin/main`: `bd58a22..1c52645`. `.claude/CANON-VERSION` re-stamped with
  `last-reconciled: 1c52645`.
- Verified *in canon*, not by assuming byte-identical files behave identically: canon's suite passes on
  its own **`vitest: ^2.1.0`** range (resolved 2.1.9, 53 tests) rather than only on this project's
  4.1.10 pin, and both CLI modes were smoke-tested against the canon repo's own tracker — the new
  per-task rule and `--repo-wide` both return 0 there.
- `checks/package.json` deliberately excluded: its only diffs from canon are F-021's project-local
  vitest pin and a `§`-encoding regression in *our* copy. Promoting F-021 is now the single remaining
  known canon gap and is filed in *Future hardening* and in `CANON-VERSION` under "STILL PROJECT-LOCAL".
- Factory left clean (the `node_modules/` installed to run the tests was removed; it is gitignored there
  anyway). Note again that `Sports/Dev-System/` is a stale checkout, not the factory.

## 2026-08-10 — F-042 closed: the corpus is curated before T-007 starts

- New standard-library-only `backend/model/corpus.py` removes the 10 All-Star exhibition games the
  source mixes in as `season_type = 2`. **6,615 verified → 6,605 modeling games**, 42 distinct team
  ids → 30, exactly 30 per season. 12 tests, and they run in CI — the module is stdlib-only on purpose
  (D-021), because it decides what T-009 trains on and `dataset.py`'s tests skip where pandas is absent.
- Identification is by games-played per season, not a hardcoded list of ids (D-025). A list would be
  exact today and silently wrong at D-017's retrain, when a new season brings its own All-Star game
  with fresh ids. The populations differ by **3 games versus 82**, so the threshold is nowhere near
  either edge — and the result is *verified* (30 teams per season, pinned per-season counts, unpinned
  seasons refused) rather than trusted.
- Excluded **by default**, which was the real decision. An All-Star row has ordinary-looking features
  and a coin-flip label, so a caller who forgets a flag gets a contaminated evaluation and no symptom.
- The loader is deliberately untouched: `EXPECTED_COMPLETED_COUNTS` still pins 6,615, because those
  counts are the tripwire proving the download is intact. 6,615 is the verified corpus; 6,605 is the
  modeling corpus, and the two now have names.
- **Caught a number that would have gone unreproducible:** D-007's 55.556% baseline was measured over
  the uncurated 6,615. On the 6,605 the model is actually evaluated on it is **55.534%** (D-026).
  0.02pp, changes no conclusion — but D-008 makes "beat a constant 55.56% predictor" half the ship
  criterion, so T-008/T-009 need to know which constant and say which corpus produced it.

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

## 2026-08-17 — PLAN phase for the second cycle (grill-me → PLAN-v3-modeling)

- **Grilled the owner's four proposed features and two of them turned out to already exist.**
  `form_diff` *is* "shrunk win rate over the last ten games" and `rest_diff` *is* days rest capped at
  five — both shipped in T-006, both ablated at zero in D-031. The session opened by putting that in
  front of the owner rather than building what was asked for.
- **Ran two diagnostics against the pinned corpus instead of arguing.** Rest: the raw signal is
  large — home on a back-to-back against a rested opponent wins .4393, the reverse .6438, a
  20.5-point swing around a .5553 baseline. But linearity was *not* the problem: the marginal curve
  is already monotonic. The problem is mass (91% of games sit at |rest_diff| ≤ 1, where the effect is
  nil) and cliff placement (0→1 day is +7.1 points; 2→3+ is +1.0). Elo: MOV Elo scores AUC .7149
  alone against `point_diff_diff`'s .7073 — but at correlation .92, with Elo right only .522 of the
  time on the games where they disagree.
- **The finding that reframed the whole plan:** `point_diff_diff`, Elo and `form_diff` correlate at
  .87–.92. They are three measurements of one latent variable — team strength. That is why the
  ablation found nothing, and why 17.9% of games read as coin flips. Moving AUC needs a *second
  factor*, not a better ruler for the first. Of the owner's four asks, exactly one (availability) is
  a second factor.
- **Corrected an over-reading of D-031 that would have driven the wrong plan.** One season carries
  ±2.6 accuracy points at 95%; the ablation deltas (+.0076, +.0023, +.0015) are all inside it. The
  supportable statement is "these features do nothing measurable," not "removing them helps" — the
  difference between deleting them and fixing their encoding. D-034 records it.
- **Resolved sixteen decisions, D-032…D-047.** Feature set (Elo replaces point differential; form and
  home advantage cut; rest re-encoded as dummies; lagged availability; travel/altitude; warm-up
  seasons as Elo state only), store (corpus into the app Postgres, superseding the separate-stores
  invariant), integrity (SQL narrows but never filters as-of; verification moves to the ingest
  boundary), surface (decomposition waterfall + fenced what-if; a gate-pinned TS scorer), and
  schedule (full scope frozen before the 2026-09-30 opener).
- **The owner overturned a standing invariant knowingly.** D-038 puts bulk history into the
  application Postgres. DuckDB-over-parquet was recommended and declined; the cost is paid explicitly
  in D-043 (§6's reproducibility claim amended) and D-044 (CI needs a service container) rather than
  absorbed silently.
- **Wrote `docs/plans/PLAN-v3-modeling.md`** — 40 user stories, 15 tasks (T-021…T-035, numbered clear
  of the factory's reserved T-013…T-020). Deliberately **not** copied to `PLAN-current.md`: PLAN-v1
  is still ACTIVE with T-006…T-009 `BUILT` and unreviewed, and pointing the tracker at a DRAFT would
  misrepresent state to the next session. No GitHub issue — the owner asked for the plan file only.
- **Numbered v3, skipping v2**, because this tracker already uses "PLAN-v2" for the *factory's* plan
  whose T-011/T-012 execute here. A second PLAN-v2 would collide in the tracker and in conversation.
- Ended at: PLAN-v3-modeling `DRAFT` on `feat/phase-1-analytical-core`. Next is the batched review of
  T-006…T-009, then the merge, then T-021.

## 2026-08-21 — batched review round, T-006/T-007/T-008/T-009 @ `eacc066`

- **Ran both mandatory reviewers concurrently against all four tasks in one round** (D-029's
  batching rationale: T-006's four earlier rounds produced only test-hygiene findings after round 2).
  Blocks allocated in writing *before* spawning, per rule 5a — security F-110–F-124, logic
  F-125–F-139, main thread F-140+ — and each reviewer appended to its own file, so the two concurrent
  writers could not interleave the way F-100 did.
- **Result: 7 of 8 verdicts ✅ at `eacc066`. One ⛔ — T-007/logic, F-125 — and its fix is test-only.**
  No HIGH, no CRITICAL. Nothing found that blocks the merge on its own.
- **The round's stated top priority came back clean.** The logic-reviewer reproduced every headline
  figure from committed code and the pinned corpus: .6444/.6503/.6762 accuracy, the log losses, the
  AUCs, 6,605 curated games, 3,962 evaluation games, every calibration decile, and D-026's .55534 by
  independent recount (3668/6605 = .555337). 168/168 tests pass. No published claim is wrong.
- **Verified rather than trusted.** Every load-bearing claim in both reports was independently
  reproduced by the main thread before transcription: the absent `test_loader.py`, the absent
  `run_evaluation` test, the `save_artifact` docstring against its own body, the `loader.py`
  signature-default trap — and F-125 by re-running the mutation in a fresh `git archive` copy with
  its own `PYTHONPYCACHEPREFIX` (16/16 green against the mutant). Shared tree confirmed clean before,
  during and after; both reviewers left it untouched.
- **F-113 is the finding that matters, and it is forward-looking rather than a defect in shipped
  code.** The as-of filter is structural; history *completeness* is not — it lives in a docstring, and
  D-038 removes the single caller that satisfied it trivially. It is the exact complement of D-039:
  that decision closes the leaky direction, but permits "narrow by season or team" with no definition
  of *sufficient*. The failure is silent because an under-narrowed history produces shrinkage priors,
  byte-identical to opening night. Season-narrowing is safe today only because `MAX_REST_DAYS = 5.0`
  happens to sit below the 120–133 day offseason — arithmetic, not design. And **T-028's Elo breaks it
  outright**, being running state across all prior seasons including the D-037 warm-up.
- **Two small process corrections made during transcription, both recorded rather than silently
  fixed.** The logic-reviewer returned its LOW batch labelled "D-029" — a *decision* number, not a
  finding — renumbered to F-126 from its own block. And the earlier `eacc066` commit was described in
  its message as "docs-only" when it touched `CLAUDE.md`, which sits outside `docs/` and therefore
  *did* move the code tip; harmless here because per-task ownership held and the gate stayed green,
  but the claim was wrong as written.
- **F-106 was live and cost nothing this round only because it was anticipated.** The canon agent
  files instruct `Read, Grep, Glob` reviewers to run worktrees and pytest; the reviewers were spawned
  with tooling that could actually do it, and the isolation rules from rule 5d were restated in the
  spawn prompts rather than relied on from canon.
- Ended at: round transcribed, four ledger rows written, F-110…F-114 / F-125 / F-126 in
  `docs/findings.md` and the status index. Next is remediation of F-125 + F-110, the F-113 decision,
  then a scoped re-review and the merge.
