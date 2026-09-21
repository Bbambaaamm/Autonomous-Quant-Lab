#!/usr/bin/env bash
# Source only from trusted policy. Callbacks must not execute candidate code.
# Reuse one already sealed commit; never invoke generation or change local HEAD.
publication_retry() {
  local source="$1" expected="$2" parent="$3" read_ref="$4" authorize="$5" push_commit="$6"
  local attempt remote parents
  [[ "$expected" =~ ^[0-9a-f]{40}$ && "$parent" =~ ^[0-9a-f]{40}$ ]] || return 2
  [[ -z "$source" || "$source" =~ ^[0-9a-f]{40}$ ]] || return 2
  [[ -z "$source" || "$source" == "$parent" ]] || return 2
  for attempt in 1 2 3; do
    [[ "$(git rev-parse HEAD)" == "$expected" ]] || return 2
    parents="$(git show -s --format=%P "$expected")" || return 2
    [[ "$parents" == "$parent" ]] || return 2
    # Authorization failures are not transport failures and must never be retried.
    "$authorize" || return 2
    if remote="$("$read_ref")"; then
      if [[ "$remote" == "$expected" ]]; then
        printf '%s\n' 'Publication verified: reusing the same sealed commit; no model call.'
        return 0
      fi
      [[ "$remote" == "$source" ]] || return 2
      # A failed response can mean the server already accepted this exact commit.
      # Always read back before deciding whether another push is needed.
      "$push_commit" || true
      if remote="$("$read_ref")"; then
        if [[ "$remote" == "$expected" ]]; then
          printf '%s\n' 'Publication verified: reusing the same sealed commit; no model call.'
          return 0
        fi
        [[ "$remote" == "$source" ]] || return 2
      fi
    fi
    if [[ "$attempt" -lt 3 ]]; then sleep 2; fi
  done
  printf '%s\n' 'Publication could not be verified after three attempts; no regeneration.' >&2
  return 1
}
