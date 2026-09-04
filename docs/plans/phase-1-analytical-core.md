# Phase 1 — analytical core (T-006 · T-007 · T-008 · T-009)

**Status:** BUILT, reviewed through round 3, remediated, and **MERGED** — PR #3, 49 commits from
`feat/phase-1-analytical-core`, merged to `main` 2026-08-25 as `30871d8`. Ship criterion **MET**:
sealed 2026 fold accuracy `.6762` against a `.62` bar, log loss `.6020` against a constant-predictor
`.6870` (both halves of D-008). 12 findings carried below, none blocking — they are **not closed by
the merge**; each names the cycle-2 task that absorbs it. The next cycle is now active in
`docs/plans/modeling-second-cycle.md`.
**Reformatted:** 2026-08-24, when the workflow moved to one file per feature.
**Status corrected:** 2026-09-04 — this file read `not merged` for ten days after PR #3 landed.

<!--
Reconstructed from docs/IMPLEMENTATION.md + docs/findings.md at the cutover. Both are preserved
whole in docs/archive/ — go there for each finding's full evidence, the decisions log (D-001..D-047)
and the session chronology.

Round 3 was stopped deliberately: it was producing less each pass. That is the loop failing to
converge, not the code degrading.
-->

## Result

`point_diff_diff` carries essentially all of the signal (**D-031**). A grill-me on 2026-08-17 ran two
diagnostics against the pinned corpus rather than reasoning from the ablation, and reframed the
problem:

- **Rest signal is large but thin.** Home on a back-to-back against a rested opponent wins `.4393`;
  the reverse `.6438` — a 20.5-point swing. Linearity was never the issue. **Mass** is: 91% of games
  sit at `|rest_diff| ≤ 1` where the effect is nil. So is **cliff placement**: 0→1 day is +7.1
  points, 2→3+ is +1.0.
- **`point_diff_diff`, Elo and `form_diff` correlate at .87–.92** — three rulers for one latent
  variable, team strength. That is why the ablation found nothing and why 17.9% of games read as coin
  flips. **Moving AUC needs a second factor, not a better ruler.**
- The ablation deltas (+.0076, +.0023, +.0015) all sit inside a season's ±2.6-point noise band, so
  *"removing them helps"* was never supportable — *"they do nothing measurable"* is (**D-034**).

Two decisions changed standing project facts: **D-038** moves bulk history into the application
Postgres, superseding the separate-stores invariant; **D-043** amends `PHASE-1-RESULT.md` §6's
reproducibility claim, which stops being true once a running database is required.

## Findings (from review)

Carried from `docs/archive/findings.md`, which holds each one's full evidence. Compressed here to
the claim, the location and the fix.

**Test coverage — the strongest signal round 3 produced**

- [ ] **F-112** (tests, MED) — `run_evaluation.py` has **no test of any kind**. Transposing
      `fit(train_x, …)` and `predict_proba(test_x)` would produce a fully leaked headline number and
      not one of the 168 tests would fail. Found independently by both reviewers.
      Fix: fold into **T-030**, before the run that cannot be repeated — assert the fold's train
      frame never intersects its test frame, and that the fitted model is the one scored.
- [x] **F-111** (tests, MED) — **CLOSED 2026-09-04 in T-022.** `loader.py` had no test file;
      `backend/tests/test_loader.py` now carries **57 tests** covering every branch the finding
      names — content-hash mismatch, header/size/UTF-8 refusals, the redirect allowlist, path
      traversal containment, unpinned-season refusal, per-season `game_id` uniqueness — plus the
      cache-verification and normalization paths. Written **before** the parquet path, as the
      finding asked. Two details worth carrying forward:
      - The path-traversal test needs **three** `..` segments, not two. The season interpolates into
        the *filename*, so `../` yields the component `nba_schedule_..` — a literal directory name
        that absorbs the following `..`. The obvious `../../` lands back inside the data dir and
        would have proved nothing. A second test pins that contained case deliberately.
      - The allowlist tests have a non-vacuity control asserting each listed host **is** accepted.
        Without it, a check that refused everything would satisfy the refusal tests and reproduce
        F-037's original bug exactly.
- [ ] **F-125** (logic, MED) — `backend/model/splits.py:129` — the temporal-leak guard's
      exact-equality boundary is unpinned. Mutating `>=` to `>` leaves all 16 `test_splits.py` tests
      passing. Production code is correct; the module's own stated invariant is untested at its
      boundary. NBA schedules have simultaneous tip-offs, so the tie is real, not contrived.
      Fix: **test-only.** A fixture whose training game tips off exactly at the earliest test game,
      asserting the fold error still fires.

**Integrity**

- [x] **F-113** (data-integrity, MED) — **CLOSED 2026-09-04 — was already fixed, verified during
      T-024.** The finding said `revisit-when: before-T-024` and warned it would be "cheap now,
      expensive once T-024 and T-028 exist". Checking before building `store`, the remediation had
      already landed in the round-3 fixes: `features.Coverage` declares `teams` / `complete_from` /
      `complete_to`, `GameHistory` takes one (refusing a subclass, F-115), and
      `_require_covers` runs inside `compute_features` before any feature is computed.
      T-024 exercises it end-to-end: `store.load_history` declares its teams, and a target the
      declaration does not cover raises `FeatureInputError` rather than returning priors.
      One consequence, now load-bearing for the read layer: `_require_covers` refuses **any**
      non-`None` `complete_from`, because `rest_diff` is not season-scoped and `elo_diff` (D-032) is
      running state over every prior season. So a season-narrowed history is unusable for features
      however it is chosen — which is why `store.load_history` narrows by team only and has no
      parameter that could express a season.

- [ ] **F-110** (data-integrity, MED) — `backend/model/estimator.py:248-249` — `save_artifact`'s
      docstring asserts *"this refuses to write anywhere else so an artifact cannot be committed by
      accident."* The body does `mkdir(parents=True)` then `write_text` with **no path check**. The
      project's own tests writing to `tmp_path` prove it does not refuse.
      Fix: implement the containment check (same shape `loader.py` uses for its data dir), or delete
      the claim. Implementing is preferable — D-042 gives T-030 one artifact-producing run.
- [ ] **F-057** (data-integrity, MED) — confirmed; corrects a decision. See **D-024**, which
      supersedes D-020(4). Not a code change here — a constraint on T-009/T-010.
      `revisit-when: T-009`.

**Low / batched**

- [ ] **F-114** (tests, LOW — 8-item batch) — **loader half CLOSED 2026-09-04 in T-022; the
      estimator item remains open.**
      - ~~`loader._verify_season` and `verify_completed_counts` bind `EXPECTED_COMPLETED_COUNTS` as a
        **signature default** (the F-070 trap, lying directly across the tests F-111 asks for)~~ —
        both now default to `None` and resolve the module global inside the body. The trap was
        real and was verified as such: reverting the fix makes
        `test_patching_the_pinned_counts_actually_reaches_the_check` and its aggregate twin fail,
        with the function reading the original dict while `monkeypatch.setattr` rebound the module
        attribute. Those two tests exist to keep it closed.
      - **Still open:** `load_artifact` leaks `AttributeError`/`KeyError` rather than
        `EstimatorError`. Belongs with `estimator.py`, untouched by T-022.
- [ ] **F-126** (tests, LOW — 3-item batch) — `estimator.py`'s `l2 < 0` and singular-matrix error
      paths are unexercised; stale 3-arg fixture signature in `test_features.py` (harmless, never
      invoked).
- [ ] **F-006** (ops, LOW) — `docker-compose.prod.yaml` is a **0-byte file**. It reads as a
      production config that exists. Write it or delete it.
- [ ] **F-016** (ops, LOW) — `.gitignore`'s `models/` is unanchored, matching at any depth. Harmless
      today, but `backend/models.py` → `backend/models/` is a routine FastAPI refactor and the
      package would land untracked and silent. Anchor it (`/models/` or `data/models/`).
- [x] **F-039** (docs, LOW) — **CLOSED 2026-09-04 — was already fixed, verified during T-022.**
      The module-level note names `release-assets.githubusercontent.com` (the current host) and
      defers to `_ALLOWED_DOWNLOAD_HOSTS` as authoritative, with an explicit instruction not to
      restate the host set in prose — which is precisely how it came to name a stale host. The
      remediation landed before the cutover; the finding was carried forward unclosed.

## Closed at the cutover — 2026-08-24

Seven open findings had the deleted machinery as their subject. Recorded rather than silently
dropped:

| Finding | Was | Disposition |
|---|---|---|
| **F-007** | retire `ci.yml` once `gate` is the required check | **Inverted.** `gate` was never required — `ci.yml` owns both required contexts. `gate.yml` retired instead. |
| **F-017** | stale lines in `.claude/agents/ui-ux-reviewer.md` | Agent deleted; UI/UX review is a paired Playwright session. |
| **F-024** | JSDoc drift in `evaluateLedgerCurrency` | `checks/` deleted. (Also double-stamped in `findings.md` — one entry OPEN, a later one CLOSED.) |
| **F-101** | canon's reviewer contract assumes sequential reviewers | **Fixed.** Report-only is now the contract; the main thread transcribes. |
| **F-104** | the docs-only review-commit convention is enforced by nothing | Moot — the currency design it propped up is deleted. |
| **F-106** | canon instructs read-only reviewers to execute | **Fixed.** Both agents are `Read, Grep, Glob` with no execute instructions. |
| **F-107** | the factory runs none of its own hooks | **Fixed.** Dev-System now wires `require-clean-commit.sh` in its own `.claude/settings.json`. |

**F-105** (the unpushed `canon/self-learning-batch-v1` branch) is left as-is: it is history of the
machinery, and the pre-collapse state is deliberately retained unmerged.

**F-042** reads `Status: OPEN` and then `Status: FIXED 2026-08-10` in the same entry — it is fixed
(exhibition games excluded via `backend/model/corpus.py`, 6,615 → 6,605, 12 tests). Not carried.

## Notes

Reviewer parallelism, finding-number blocks and the review ledger are gone. Findings are per-feature
from F1 and advisory; the historical `F-NNN` numbers above are kept because the archived evidence is
indexed by them.
