#!/usr/bin/env bash
# Deploy a specific image to a Render image-backed service through its deploy hook.
#
# Required env:
#   RENDER_DEPLOY_HOOK_URL  the service's deploy hook (a secret: it triggers deploys)
#   IMAGE                   full image reference, e.g. ghcr.io/owner/repo-backend:sha-abc123
#
# Render rejects the request (400) if everything in IMAGE except the tag differs from the
# image URL configured on the service.
set -euo pipefail

: "${RENDER_DEPLOY_HOOK_URL:?}" "${IMAGE:?}"
encoded="$(jq -rn --arg v "$IMAGE" '$v|@uri')"
separator='?'
case "$RENDER_DEPLOY_HOOK_URL" in *\?*) separator='&' ;; esac

echo "Requesting Render deploy of ${IMAGE}"
status="$(curl -sS --max-time 60 -X POST -o /tmp/render-deploy.json -w '%{http_code}' "${RENDER_DEPLOY_HOOK_URL}${separator}imgURL=${encoded}")"
case "$status" in
  200) echo "Deploy started: $(jq -c . /tmp/render-deploy.json 2>/dev/null || cat /tmp/render-deploy.json)" ;;
  202) echo "Render queued the deploy behind another one in progress" ;;
  *)
    echo "Render deploy hook failed (HTTP ${status}):" >&2
    cat /tmp/render-deploy.json >&2 2>/dev/null || true
    exit 1
    ;;
esac
