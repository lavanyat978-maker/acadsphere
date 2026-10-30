#!/usr/bin/env bash
# Point a Railway service at a container image and trigger a deployment.
#
# Required env:
#   RAILWAY_TOKEN           account/workspace token (Authorization: Bearer)
#                           or a project token with RAILWAY_TOKEN_TYPE=project
#   RAILWAY_SERVICE_ID      the backend service
#   RAILWAY_ENVIRONMENT_ID  e.g. production
#   IMAGE                   full image reference, e.g. ghcr.io/owner/repo-backend:sha-abc123
# Optional: RAILWAY_API (defaults to the public GraphQL endpoint).
set -euo pipefail

: "${RAILWAY_TOKEN:?}" "${RAILWAY_SERVICE_ID:?}" "${RAILWAY_ENVIRONMENT_ID:?}" "${IMAGE:?}"
api="${RAILWAY_API:-https://backboard.railway.com/graphql/v2}"
if [ "${RAILWAY_TOKEN_TYPE:-account}" = "project" ]; then
  auth_header="Project-Access-Token: ${RAILWAY_TOKEN}"
else
  auth_header="Authorization: Bearer ${RAILWAY_TOKEN}"
fi

call() {
  local body response
  body="$(jq -n --arg q "$1" --argjson v "$2" '{query: $q, variables: $v}')"
  response="$(curl -sS --max-time 60 -X POST "$api" -H "$auth_header" -H "Content-Type: application/json" -d "$body" -w '\n%{http_code}')"
  local status="${response##*$'\n'}" payload="${response%$'\n'*}"
  # GraphQL reports most failures as HTTP 200 with an `errors` array.
  if [ "$status" != "200" ] || [ "$(jq 'has("errors")' <<<"$payload" 2>/dev/null || echo true)" = "true" ]; then
    echo "Railway API call failed (HTTP ${status}):" >&2
    jq -r '.errors[]?.message // .' <<<"$payload" >&2 2>/dev/null || echo "$payload" >&2
    exit 1
  fi
  echo "$payload"
}

echo "Setting image to ${IMAGE}"
call 'mutation($serviceId: String!, $environmentId: String!, $image: String!) {
  serviceInstanceUpdate(serviceId: $serviceId, environmentId: $environmentId, input: {source: {image: $image}})
}' "$(jq -n --arg s "$RAILWAY_SERVICE_ID" --arg e "$RAILWAY_ENVIRONMENT_ID" --arg i "$IMAGE" '{serviceId:$s, environmentId:$e, image:$i}')" >/dev/null

echo "Triggering deployment"
call 'mutation($serviceId: String!, $environmentId: String!) {
  serviceInstanceDeployV2(serviceId: $serviceId, environmentId: $environmentId)
}' "$(jq -n --arg s "$RAILWAY_SERVICE_ID" --arg e "$RAILWAY_ENVIRONMENT_ID" '{serviceId:$s, environmentId:$e}')" >/dev/null
echo "Deployment requested"
