# Plans

Versioned specs (SYSTEM.md §5.5). The plan is the durable spec — it must outlive the chat.

- `PLAN-v<N>.md` — a version. Bump `<N>` on a material re-scope; the new version **supersedes** the
  old (say so in its header). Never rewrite a shipped plan — plan history is an audit trail.
- `PLAN-current.md` — a copy of the active version. `docs/IMPLEMENTATION.md` names it under
  *Current state → Active plan*.

Written by the PLAN phase: grill-me → to-PRD → `PLAN-v<N>.md`. A GitHub issue is a secondary output;
the file here is the durable one. The §5.4 final review records the outcome in the active plan's
*Final review* section; a large divergence becomes `PLAN-v<N+1>`.

**Nothing here yet** — the first planning session (T-003) writes `PLAN-v1.md`.

Stage 3 predates the Dev-System; its plan and design doc are preserved under `docs/superpowers/`
as a historical record. Don't add to that folder.
