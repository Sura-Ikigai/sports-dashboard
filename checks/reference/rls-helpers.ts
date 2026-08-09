// REFERENCE two-JWT RLS deny-test harness (SYSTEM.md §5.4, grill-me Q10). Copy to <project>/tests/rls/
// and adapt seeds. Proves RLS at RUNTIME through the REAL session path — not by reading policies.
//
// INVARIANT (never violate): no service-role key anywhere. Two real users are created with the anon
// key via signUp/signIn, exactly as the app authenticates. A test that reaches for service-role is
// testing a path the app never uses.
//
// Deps (project): vitest, @supabase/supabase-js. Env: SUPABASE_URL, SUPABASE_ANON_KEY (local Supabase).

import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import { describe, it, expect, beforeAll } from 'vitest';

const URL = process.env.SUPABASE_URL!;
const ANON = process.env.SUPABASE_ANON_KEY!;

/** A Supabase client bound to a freshly-created user's session (anon key + that user's JWT). */
export async function newUserClient(tag: string): Promise<SupabaseClient> {
  const client = createClient(URL, ANON);
  const email = `rls_${tag}_${Date.now()}_${Math.random().toString(36).slice(2)}@example.test`;
  const password = 'test-password-123!';
  const { error: signUpErr } = await client.auth.signUp({ email, password });
  if (signUpErr) throw signUpErr;
  // Local Supabase runs with autoconfirm; sign in to obtain the session JWT.
  const { error: signInErr } = await client.auth.signInWithPassword({ email, password });
  if (signInErr) throw signInErr;
  return client;
}

type DenyOpts = {
  // Insert a row OWNED BY user A and return its primary-key id. Runs as A's session client.
  seed: (asA: SupabaseClient) => Promise<string>;
  pk?: string; // primary-key column (default 'id')
};

/**
 * rlsDeny('<table>', { seed }) — asserts user B cannot read, update, or delete a row user A owns.
 * The literal table name is what `rls-coverage` greps for, so every RLS table must appear here.
 */
export function rlsDeny(table: string, opts: DenyOpts) {
  const pk = opts.pk ?? 'id';
  describe(`RLS deny: ${table}`, () => {
    let asA: SupabaseClient, asB: SupabaseClient, rowId: string;
    beforeAll(async () => {
      asA = await newUserClient(`${table}_a`);
      asB = await newUserClient(`${table}_b`);
      rowId = await opts.seed(asA);
    });

    it(`B cannot READ A's ${table} row`, async () => {
      const { data } = await asB.from(table).select('*').eq(pk, rowId);
      expect(data ?? []).toHaveLength(0); // RLS hides the row → empty, not an error
    });

    it(`B cannot UPDATE A's ${table} row`, async () => {
      const { data } = await asB.from(table).update({ [pk]: rowId }).eq(pk, rowId).select();
      expect(data ?? []).toHaveLength(0); // no rows visible to update
    });

    it(`B cannot DELETE A's ${table} row`, async () => {
      const { data } = await asB.from(table).delete().eq(pk, rowId).select();
      expect(data ?? []).toHaveLength(0);
      // and confirm the row still exists for A
      const { data: still } = await asA.from(table).select(pk).eq(pk, rowId);
      expect(still ?? []).toHaveLength(1);
    });
  });
}
