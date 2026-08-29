#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BASE_COMPOSE="${REPO_DIR}/compose.road-condition.yml"
PUBLIC_COMPOSE="${REPO_DIR}/compose.road-condition-public.yml"

# Keep this workstation's road-condition ports separate from the existing
# services that already use the Compose defaults 8080/8081.
export ROAD_CONDITION_WEB_PORT="${ROAD_CONDITION_WEB_PORT:-18080}"
export ROAD_CONDITION_API_PORT="${ROAD_CONDITION_API_PORT:-18081}"
export ROAD_CONDITION_PUBLIC_PORT="${ROAD_CONDITION_PUBLIC_PORT:-18082}"

docker compose -f "${BASE_COMPOSE}" up -d
docker compose -f "${PUBLIC_COMPOSE}" up -d

public_url=""
for _attempt in $(seq 1 30); do
    public_url="$({
        docker compose -f "${PUBLIC_COMPOSE}" logs --no-color cloudflare-tunnel 2>&1 || true
    } | grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1 || true)"
    if [[ -n "${public_url}" ]]; then
        break
    fi
    sleep 1
done

if [[ -z "${public_url}" ]]; then
    echo "Cloudflare Quick Tunnel URL을 확인하지 못했습니다." >&2
    docker compose -f "${PUBLIC_COMPOSE}" logs --tail 80 cloudflare-tunnel >&2
    exit 1
fi

echo "Public read-only URL: ${public_url}"
echo "Stop: docker compose -f ${PUBLIC_COMPOSE} down"
