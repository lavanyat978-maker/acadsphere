#!/usr/bin/env bash
# Point frontend/vercel.json at the real backend host.
#   BACKEND_URL=https://praxis-api.up.railway.app scripts/configure-vercel.sh
set -euo pipefail

: "${BACKEND_URL:?Set BACKEND_URL to the Railway public URL, e.g. https://praxis-api.up.railway.app}"
host="${BACKEND_URL#https://}"
host="${host%%/*}"
case "$BACKEND_URL" in
  https://*) ;;
  *) echo "BACKEND_URL must start with https://" >&2; exit 1 ;;
esac

file="$(dirname "$0")/../frontend/vercel.json"
sed -i.bak "s#BACKEND_HOST_PLACEHOLDER#${host}#" "$file"
rm -f "$file.bak"
if grep -q BACKEND_HOST_PLACEHOLDER "$file"; then
  echo "vercel.json still contains the placeholder" >&2
  exit 1
fi
echo "vercel.json now proxies /api/* to https://${host}"
