#!/usr/bin/env bash
# Stop gate: if Python files changed in the working tree, the fast suite must be
# green before Claude ends its turn. Complements the global hooks (which format
# and guard but never run tests). Fails OPEN on any internal error — this is a
# quality gate, not a lock.
set -u
JQ=/usr/bin/jq

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" 2>/dev/null || exit 0
input=$(cat)

# Respect the loop-protection contract: if we already blocked once and Claude
# continued, let the stop through rather than ping-ponging forever.
active=$(printf '%s' "$input" | "$JQ" -r '.stop_hook_active // false' 2>/dev/null)
[ "$active" = "true" ] && exit 0

# Only gate when Python actually changed (tracked or new).
git status --porcelain 2>/dev/null | grep -q '\.py$' || exit 0

out=$(uv run pytest -q 2>&1 | tail -5)
case "$out" in
  *" passed"*)
    case "$out" in
      *failed*|*error*) ;;       # "1 failed, N passed" → fall through to block
      *) exit 0 ;;               # clean pass
    esac
    ;;
esac
case "$out" in
  *failed*|*error*|*ERROR*)
    "$JQ" -n --arg r "dj-agent fast suite is red — fix before stopping. Tail: $out" \
      '{"decision":"block","reason":$r}'
    exit 0
    ;;
esac
exit 0
