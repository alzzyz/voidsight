#!/usr/bin/env bash
# Before `git push`: has the version been bumped for what is about to go out?
#
# Reads the PreToolUse payload on stdin. Allows the push (exit 0, silent) unless
# commits touching voidsight/ are about to be pushed with the version unchanged
# from what the remote already has, in which case it denies with an explanation.
#
# Escape hatch: put "no-version-bump" anywhere in the command (a trailing
# `# no-version-bump` comment) for a deliberate push without one.
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
[[ "$command" == *"no-version-bump"* ]] && allow

root=$(git rev-parse --show-toplevel 2>/dev/null) || allow
[[ -f "$root/pyproject.toml" ]] || allow

version_in() { sed -n 's/^version = "\(.*\)"$/\1/p' <<<"$1" | head -1; }

local_version=$(version_in "$(cat "$root/pyproject.toml" 2>/dev/null)")
[[ -n "$local_version" ]] || allow

# Where this branch is going. No upstream means a first push: nothing to compare.
upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) || allow
git rev-parse --verify --quiet "$upstream" >/dev/null || allow

# The two files must agree, whatever the number is.
dunder=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$root/voidsight/__init__.py" 2>/dev/null | head -1)
if [[ -n "$dunder" && "$dunder" != "$local_version" ]]; then
  deny "Version mismatch before push: pyproject.toml says $local_version but voidsight/__init__.py says $dunder. Make them agree, then push again."
fi

remote_version=$(version_in "$(git show "$upstream:pyproject.toml" 2>/dev/null)")
[[ -n "$remote_version" ]] || allow
[[ "$local_version" != "$remote_version" ]] && allow

# Same version as the remote. Does this push actually change the package?
changed=$(git diff --name-only "$upstream"..HEAD -- voidsight 2>/dev/null | head -5)
[[ -n "$changed" ]] || allow

count=$(git rev-list --count "$upstream"..HEAD 2>/dev/null || echo "?")

# Spell the three options out in this project's own numbering.
if [[ "$local_version" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
  major="${BASH_REMATCH[1]}"; minor="${BASH_REMATCH[2]}"; patch="${BASH_REMATCH[3]}"
  next_patch="$major.$minor.$((patch + 1))"
  next_minor="$major.$((minor + 1)).0"
  next_major="$((major + 1)).0.0"
else
  next_patch="the next patch"; next_minor="the next minor"; next_major="the next major"
fi

deny "About to push $count commit(s) touching voidsight/ with the version still at $local_version, which is what $upstream already has.

Changed: $(tr '\n' ' ' <<<"$changed")

Decide the bump and apply it to BOTH pyproject.toml and voidsight/__init__.py, then push again:
  - patch ($local_version -> $next_patch): bug fixes, internal changes, no new capability
  - minor ($local_version -> $next_minor): new capability, or behaviour someone would notice
  - major ($local_version -> $next_major): breaking change to config, CLI or data on disk

If it is not obvious whether this is minor or major, ask the user rather than guessing. If the push genuinely needs no bump, re-run the command with a trailing '# no-version-bump'."
