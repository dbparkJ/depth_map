#!/usr/bin/env bash
set -Eeuo pipefail

API_URL="${ROAD_CONDITION_API_URL:-http://127.0.0.1:8081}"
WEB_URL="${ROAD_CONDITION_WEB_URL:-http://127.0.0.1:8080}"

command -v curl >/dev/null 2>&1 || {
  echo "[error] curl is required" >&2
  exit 2
}

echo "[check] API health"
curl --fail --silent --show-error "${API_URL}/api/v1/health" | python3 -m json.tool

echo "[check] web index"
curl --fail --silent --show-error "${WEB_URL}/" >/dev/null

echo "[submit] synthetic mixed job"
JOB_ID="$(
  curl --fail --silent --show-error \
    -H 'Content-Type: application/json' \
    -d '{"source_type":"synthetic","synthetic_profile":"mixed"}' \
    "${API_URL}/api/v1/jobs" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])'
)"
echo "[job] ${JOB_ID}"

for _ in $(seq 1 120); do
  STATUS_JSON="$(curl --fail --silent --show-error "${API_URL}/api/v1/jobs/${JOB_ID}")"
  STATE="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])' <<<"${STATUS_JSON}")"
  echo "[status] ${STATE}"
  if [[ "${STATE}" == "completed" ]]; then
    curl --fail --silent --show-error "${API_URL}/api/v1/jobs/${JOB_ID}/summary" | python3 -m json.tool
    echo "[ok] road-condition stack smoke test passed"
    exit 0
  fi
  if [[ "${STATE}" == "failed" ]]; then
    python3 -m json.tool <<<"${STATUS_JSON}" >&2
    exit 1
  fi
  sleep 1
done

echo "[error] job did not finish within the smoke-test polling window" >&2
exit 1
