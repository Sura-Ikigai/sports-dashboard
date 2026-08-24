# Branch protection — instantiation step (SYSTEM.md §6.2, §6.3, §8)

Making the gate a *structural* contract, not a human rule. This is the mechanical replacement for
"don't push to `main` until I say" (grill-me Q7): with branch protection on, an unreviewed or red change
**cannot** merge, on a busy day or an absent one.

> A gate that runs "when I remember" is not a gate. Branch protection is what turns the review contract
> from disciplined into automatic.

## Do this once per project, right after the first push

1. **Add the workflow.** Copy `templates/ci/gate.yml` → `.github/workflows/gate.yml`, set `<STACK>`, and
   fill the stack-specific setup. Push it so the `gate` job has run at least once (GitHub only lists a
   check as "required-able" after it has appeared).

2. **Protect `main`** (Settings → Branches → Add rule, or `gh api`):
   - **Require a pull request before merging** — no direct pushes to `main`.
   - **Require status checks to pass before merging** → select the **`gate`** check.
     - Also check **"Require branches to be up to date before merging"** so `review-ledger-current`
       evaluates against the real merge state.
   - **Require approvals: 1** — this is where the human "I approve" click lives. Branch protection
     handles *mechanical* green; the human still curates the merge.
   - **Do not allow bypassing the above** (include administrators) — otherwise the gate is optional
     for exactly the person most likely to be in a hurry.

3. **Verify.** Open a throwaway PR that breaks a check (e.g. a failing type) and confirm merge is
   blocked; then confirm a clean PR with an up-to-date `Review ledger` (every required reviewer ✅ at
   the head SHA) is mergeable.

## What the `gate` check actually enforces

Running `checks/run-gate.mjs` = the deterministic lane of the gate:
- `gate-completeness` — every manifest reviewer is installed + every check script is present.
- `review-ledger-current` — every REVIEWED and DONE task has each required reviewer present and ✅ with
  a SHA; and for `REVIEWED` specifically, that ✅ must be **at the current code tip** (a stale SHA fails
  → re-review). `DONE` is frozen history: exempt from the currency comparison, but not from the verdict
  (F-018/F-019 — gating DONE on currency expired every completed review on the next commit anywhere).
  This is how the *reviewer* gate becomes machine-checked without running an LLM in CI.
- every deterministic `checks:` entry in the stack manifest (typecheck, lint, unit, rls-deny,
  rls-coverage, a11y, perf, …).

The LLM **reviewers** themselves run in the local read-only lane (they never run in CI); their verdicts
reach CI only as the ledger ✅@SHA that `review-ledger-current` checks.

> **Security invariant:** the manifest's `run:` commands are arbitrary trusted config (they execute by
> design). So the `gate` job must never be granted deploy/service secrets, and its trigger must stay
> `on: pull_request` (never `pull_request_target`). Otherwise a fork PR that edits a `run:` command
> becomes a secret-exfiltration vector. Run deploys in a separate, protected workflow — not the gate.

## Phase discipline (SYSTEM.md §5.5)

A **phase** is independently shippable *and* independently reviewable → one PR through this gate. Ship
in gated slices; each phase's blast radius is bounded by its own green gate.
