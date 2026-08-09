# Learning notes — Sports Dashboard

<!--
APPEND-ONLY canon-facing buffer (SYSTEM.md §5.4 step 5). Record OBSERVATIONS AND OPEN FORKS, not
solutions — grill-me resolves those at /reconcile-canon time. Distinguish from the tracker's
*Future hardening*: that is THIS project's next-cycle backlog; this file is what the NEXT project
should inherit from the factory.
-->

## 2026-08-09 — from the instantiation

- **Canon had no plain-Postgres overlay.** Both existing stacks assumed Supabase, so the first
  non-Supabase project had to author `nextjs-fastapi-postgres` at instantiation. Open fork: is the
  RLS-vs-API-authz split better expressed as a *property* of an overlay (`db-enforces-authz: yes/no`)
  than as two whole overlays that will drift apart?
- **Instantiating onto an existing, already-shipped repo is the untested path.** SYSTEM.md §4.4 covers
  greenfield-vs-existing for *agents* (scope down the invocation), but says nothing about stamping the
  system onto a repo that already has its own CI, its own plan format (`docs/superpowers/`), and
  shipped unreviewed code. Observations: (a) the pre-existing `ci.yml` now duplicates the `gate` job
  and nothing in canon says which wins; (b) already-shipped code has no Review-ledger history, so
  every historical task is invisible to `review-ledger-current` — the gate only ever sees work done
  after the stamp. Open fork: does canon want a "stamp onto brownfield" procedure, or an explicit
  `pre-canon` marker for code that predates the gate?
- **A task whose deliverable is a DECISION cannot be closed.** "Select the historical data source"
  was written as T-002, then had to be rewritten as an ingest task, because the status enum routes
  everything through the reviewer gate to reach `DONE` — and `review-ledger-current` fails any
  `REVIEWED`/`DONE` task lacking mandatory-reviewer ✅s, while also failing them if marked `n/a`. So
  a decision task is structurally unclosable: it has no reviewable artifact. The workaround used here
  was to keep Tasks about *work* and let the Decisions log carry the decision. Open fork: is that the
  intended discipline (and worth stating in canon), or does the enum need a terminal non-code state?
- **A declared-but-unrunnable gate is worse than an undeclared one**, and canon already knows this
  (`gate-completeness`) — but the *pressure* at instantiation is to declare the full canon manifest
  and leave it red. Observation: the honest move (declare 5 checks, leave a11y/perf/authz-deny out,
  record them in Future hardening) makes the project look less gated than a project that declares
  everything and never runs it. Open fork: should the tracker surface "canon gates this project is
  NOT running" as a standing, visible line rather than a hardening bullet that scrolls away?
