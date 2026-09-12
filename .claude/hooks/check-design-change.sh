#!/usr/bin/env bash
# Before `git push`: did a design decision go out without being written down?
#
# Reads the PreToolUse payload on stdin. Allows the push (exit 0, silent) unless
# commits touching the watched modules are about to be pushed without DESIGN.md
# being touched too, in which case it denies with an explanation.
#
# This cannot detect that a design decision was made. It detects that code which
# tends to embody design decisions changed while the record did not — a proxy,
# which will fire on pure refactors. When it does, that is a signal the watched
# set is too wide, not a reason to reach for the escape hatch.
#
# Escape hatch: put "no-design-change" anywhere in the command (a trailing
# `# no-design-change` comment) for a change that genuinely decides nothing.
set -uo pipefail

allow() { exit 0; }

deny() {
  jq -nc --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

payload=$(cat)
command=$(printf '%s' "$payload" | jq -r '.tool_input.command // ""' 2>/dev/null) || allow
[[ "$command" == *"git push"* ]] || allow
[[ "$command" == *"no-design-change"* ]] && allow

root=$(git rev-parse --show-toplevel 2>/dev/null) || allow
[[ -f "$root/DESIGN.md" ]] || allow

# Where this branch is going. No upstream means a first push: nothing to compare.
upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) || allow
git rev-parse --verify --quiet "$upstream" >/dev/null || allow

# The modules where a change is likely to *be* a decision rather than an
# implementation detail. Deliberately narrow; widen only with evidence.
watched=(
  voidsight/vision
  voidsight/capture
  voidsight/trigger
  voidsight/live.py
  voidsight/session.py
  voidsight/config.py
)

changed=$(git diff --name-only "$upstream"..HEAD -- "${watched[@]}" 2>/dev/null)
[[ -n "$changed" ]] || allow

# Recorded in the same push? Then the decision went out with its reasoning.
git diff --name-only "$upstream"..HEAD -- DESIGN.md 2>/dev/null | grep -q . && allow

count=$(git rev-list --count "$upstream"..HEAD 2>/dev/null || echo "?")

deny "About to push $count commit(s) touching design-bearing modules with no change to DESIGN.md.

Changed: $(tr '\n' ' ' <<<"$changed")

If this push decides something — how a frame is located, when a scan fires, what
is captured and how often, what the config means, what the ledger records — add an
entry to DESIGN.md saying what was decided, why, and what would overturn it. Then
push again.

Recording it is the point: the reasoning behind '\''why does OCR exist at all'\'' and
'\''which Warframe setting does the mask follow'\'' both had to be rediscovered from a
10 MB game log because neither was written down.

If this genuinely decides nothing — a rename, a test, a refactor with identical
behaviour — re-run the command with a trailing '\''# no-design-change'\''. If that is
happening often, the watched list in this script is too wide; shrink it."
