#!/usr/bin/env bash
# Queue-watcher Stop hook (SYSTEM.md §6.3) — after work stops, surface the next EXPLICIT command so
# the delegate→build→review loop is automatic, not remembered. Reads docs/IMPLEMENTATION.md and
# prefers the next reviewable (BUILT) or buildable (PLANNED) task, emitting the subagent command
# (e.g. "Use the security-auditor subagent on T-003"); falls back to the Session anchor "Next
# action". Non-blocking — always allows the stop.
#
# Canonical copy. Install into a project at .claude/hooks/ and wire it in .claude/settings.json
# under hooks.Stop (alongside require-clean-commit.sh).

cat >/dev/null 2>&1   # drain the hook payload (unused)
cd "${CLAUDE_PROJECT_DIR:-$PWD}" 2>/dev/null || exit 0
tracker="docs/IMPLEMENTATION.md"
[ -f "$tracker" ] || exit 0

review_cmd=""
build_cmd=""
# Open tasks are "- [ ] **T-NNN** ... `STATUS` ... owner: `agent`"; DONE tasks are "- [x]".
while IFS= read -r line; do
  case "$line" in
    *'- [ ] **T-'*)
      printf '%s' "$line" | grep -q 'BLOCKED' && continue
      id="$(printf '%s' "$line" | grep -oE 'T-[0-9]+' | head -1)"
      if printf '%s' "$line" | grep -q 'BUILT'; then
        [ -z "$review_cmd" ] && review_cmd="Use the security-auditor subagent on $id (review the BUILT task)"
      elif printf '%s' "$line" | grep -q 'PLANNED'; then
        owner="$(printf '%s' "$line" | sed -n 's/.*owner:[^`]*`\([^`]*\)`.*/\1/p')"
        [ -z "$owner" ] && owner="the owner"
        [ -z "$build_cmd" ] && build_cmd="Use the $owner subagent on $id"
      fi
      ;;
  esac
done < "$tracker"

next="${review_cmd:-$build_cmd}"
if [ -z "$next" ]; then
  next="$(awk 'f && /^\*\*/{f=0} /^\*\*Next action:\*\*/{f=1} f' "$tracker" \
          | sed 's/^\*\*Next action:\*\*[[:space:]]*//' | tr '\n' ' ' | sed 's/  */ /g; s/ *$//')"
fi
[ -n "$next" ] || exit 0

esc="$(printf '%s' "$next" | sed 's/\\/\\\\/g; s/"/\\"/g')"
printf '{"systemMessage":"Next (from docs/IMPLEMENTATION.md): %s"}\n' "$esc"
exit 0
