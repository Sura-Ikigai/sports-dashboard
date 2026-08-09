# checks/reference — project-level test scaffolds

These are **reference implementations** the factory ships but does not run against itself (the factory
has no app). At instantiation, copy them into the project and wire them to the paths the stack manifest
names, then adapt routes/tables to the app.

| Reference file | Copy to (manifest `run`) | What it proves |
|----------------|--------------------------|----------------|
| `a11y.spec.ts` | `tests/a11y/` (`playwright test tests/a11y`) | axe (no serious/critical), keyboard operability, reduced-motion — the deterministic a11y gate (grill-me Q8) |
| `perf-budgets.json` | `checks/perf-budgets.json` (`node checks/perf-budgets.mjs`) | performance budgets (LCP/CLS/TBT/…) — the deterministic perf gate |
| `rls.deny.spec.ts` | `tests/rls/` (`vitest run tests/rls`) | two-JWT cross-user denial through the real session client — the RLS gate (grill-me Q10) |

Why they live here and not at `tests/…` in the factory: `gate-completeness` existence-checks the
manifest's paths, so in a project those paths must resolve. The factory dogfoods with
`run-gate.mjs --only-meta`, which runs only the two universal meta-checks and does not require
project paths.
