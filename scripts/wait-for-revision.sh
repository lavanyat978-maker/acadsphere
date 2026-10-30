#!/usr/bin/env bash
# Wait until /api/health on a host reports the given revision.
#   scripts/wait-for-revision.sh https://praxis-api.up.railway.app <git-sha> [timeout-seconds]
set -euo pipefail

base="${1:?base url}"; sha="${2:?git sha}"; timeout="${3:-420}"
deadline=$((SECONDS + timeout))
while [ "$SECONDS" -lt "$deadline" ]; do
  body="$(curl -sS --max-time 10 "${base%/}/api/health" 2>/dev/null || true)"
  if [ "$(jq -r '.revision // empty' <<<"$body" 2>/dev/null)" = "$sha" ] && [ "$(jq -r '.status // empty' <<<"$body" 2>/dev/null)" = "ok" ]; then
    echo "Revision ${sha} is live and healthy"
    exit 0
  fi
  sleep 10
done
echo "Timed out after ${timeout}s waiting for revision ${sha}. Last response: ${body:-none}" >&2
exit 1
