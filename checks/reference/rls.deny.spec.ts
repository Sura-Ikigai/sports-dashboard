// REFERENCE two-JWT RLS deny-tests (SYSTEM.md §5.4, grill-me Q10-Q11). Copy to <project>/tests/rls/,
// then add one rlsDeny(...) block per RLS table — `rls-coverage` fails the build if any table is missing.
// Wired by the `rls-deny` check in the stack manifest: `vitest run tests/rls`.
//
// The seeds below are ILLUSTRATIVE (Sura Media schema) — replace columns with your own.

import { rlsDeny } from './rls-helpers';

// A post is owned by its author; another user must not see or mutate it.
rlsDeny('posts', {
  seed: async (asA) => {
    const { data, error } = await asA
      .from('posts')
      .insert({ media_item_id: crypto.randomUUID(), rating_value: 3, comment: 'A private take' })
      .select('id')
      .single();
    if (error) throw error;
    return data.id;
  },
});

// A reaction is owned by the reacting user.
rlsDeny('reactions', {
  seed: async (asA) => {
    const { data, error } = await asA
      .from('reactions')
      .insert({ post_id: crypto.randomUUID(), value: 2 })
      .select('id')
      .single();
    if (error) throw error;
    return data.id;
  },
});

// …one rlsDeny('<table>', …) per RLS-enabled table. Add: media_items, groups, group_members,
// post_groups, users — every table the migrations mark ENABLE ROW LEVEL SECURITY.
