// Factory test scope: run ONLY the check cores' fixture suites (*.test.mjs). The reference/*.spec.ts
// files are project-copy scaffolds (they need @playwright/test, @supabase/supabase-js as peer deps and
// only run inside an instantiated project), so exclude them here — otherwise the factory dogfood suite
// reports failed collections for deps the factory doesn't have. (Plain object; no import needed.)
export default {
  test: {
    include: ['*.test.mjs'],
    exclude: ['reference/**', '**/node_modules/**'],
  },
};
