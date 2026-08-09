#!/usr/bin/env bash
# Stop hook — enforce "commit on completion" (SYSTEM.md section 3.3 / 6.3).
#
# Blocks a clean stop while the project's git tree has uncommitted changes, so a
# tracker/wiki update can't be left to die in the working tree at the session
# boundary (the exact failure the dev-system exists to prevent).
#
# Loop-safe: if this stop is already a continuation prompted by this hook
# (stop_hook_active=true), it yields — one nudge, never a trap.
#
# Canonical copy. Install into a project at .claude/hooks/ and wire it in
# .claude/settings.json under hooks.Stop (see SYSTEM.md section 6.3:
# keep enforcement hooks at the project settings.json level).

input="$(cat)"

# Don't trap: if we already blocked once this turn, allow the stop.
if printf '%s' "$input" | grep -Eq '"stop_hook_active"[[:space:]]*:[[:space:]]*true'; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || exit 0

# Only meaningful inside a git work tree.
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  reason="Uncommitted changes in the project. Per the tracker contract (SYSTEM.md section 3.3, Write on completion): before ending, update docs/IMPLEMENTATION.md (task status + Session anchor), then run 'git add -A && git commit'. Use 'git status' to see what is pending. This nudge fires once."
  printf '{"decision":"block","reason":"%s"}\n' "$reason"
  exit 0
fi

exit 0
