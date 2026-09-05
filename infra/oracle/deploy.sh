#!/usr/bin/env bash
# Sentinel-G — Oracle Cloud one-shot redeploy.
# Run from infra/oracle/ on the VM. Pulls the latest image from GHCR, rolls
# the FastAPI container, leaves Caddy untouched. Safe to re-run.

set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "ERROR: .env missing in $(pwd). Copy .env.example to .env and fill it in." >&2
  exit 1
fi

echo "[deploy] git pull..."
git -C ../.. pull --ff-only

echo "[deploy] docker compose pull..."
docker compose pull

echo "[deploy] docker compose up -d..."
docker compose up -d --remove-orphans

echo "[deploy] waiting for sentinel-g-api to report healthy..."
for i in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' sentinel-g-api 2>/dev/null || echo "starting")
  if [[ "$status" == "healthy" ]]; then
    echo "[deploy] sentinel-g-api healthy (after ${i}0s)"
    break
  fi
  sleep 10
done

echo "[deploy] current state:"
docker compose ps

echo
echo "[deploy] smoke test against https://${DOMAIN:-<set DOMAIN in .env>}..."
DOMAIN_VAL=$(grep -E '^DOMAIN=' .env | head -1 | cut -d= -f2-)
if [[ -n "${DOMAIN_VAL:-}" ]]; then
  curl -fsS -o /dev/null -w "  /health -> HTTP %{http_code} in %{time_total}s\n" \
    "https://${DOMAIN_VAL}/health" || echo "  /health probe failed — check 'docker compose logs caddy'"
fi
