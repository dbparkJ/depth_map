#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./run_temporal_chunks.sh --path /path/to/rgbd_dataset

The dataset directory must contain timestamps.csv, gps.csv, metadata.json,
and the RGB/depth files referenced by timestamps.csv.
EOF
}

DATASET_PATH=""
while (($# > 0)); do
  case "$1" in
    --path)
      if (($# < 2)); then
        echo "[error] --path requires a dataset directory" >&2
        usage >&2
        exit 2
      fi
      DATASET_PATH="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "[error] unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${DATASET_PATH}" ]]; then
  echo "[error] --path is required" >&2
  usage >&2
  exit 2
fi
if [[ ! -d "${DATASET_PATH}" ]]; then
  echo "[error] dataset directory not found: ${DATASET_PATH}" >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DATASET_PATH="$(cd -- "${DATASET_PATH}" && pwd -P)"
DATASET_NAME="$(basename -- "${DATASET_PATH}")"
CHUNK_SECONDS=60

for REQUIRED_FILE in timestamps.csv gps.csv metadata.json; do
  if [[ ! -f "${DATASET_PATH}/${REQUIRED_FILE}" ]]; then
    echo "[error] missing dataset file: ${DATASET_PATH}/${REQUIRED_FILE}" >&2
    exit 2
  fi
done
if ! command -v python3 >/dev/null 2>&1; then
  echo "[error] python3 was not found in PATH" >&2
  exit 2
fi
if ! command -v conda >/dev/null 2>&1; then
  echo "[error] conda was not found in PATH" >&2
  exit 2
fi

CHUNK_INDEX_TEXT="$(
  python3 - "${DATASET_PATH}/timestamps.csv" "${CHUNK_SECONDS}" <<'PY'
import csv
import sys
from collections import Counter
from pathlib import Path

timestamps_path = Path(sys.argv[1])
duration_ns = int(float(sys.argv[2]) * 1_000_000_000)
timestamps = []
with timestamps_path.open("r", encoding="utf-8", newline="") as stream:
    for row in csv.DictReader(stream):
        if not (row.get("rgb_file") and row.get("depth_file")):
            continue
        timestamps.append(int(row["frame_host_monotonic_ns"]))

if len(timestamps) < 2:
    raise SystemExit("timestamps.csv has fewer than two synchronized RGB-depth frames")
if any(current < previous for previous, current in zip(timestamps, timestamps[1:])):
    raise SystemExit("synchronized frame timestamps are not chronological")

first = timestamps[0]
counts = Counter((timestamp - first) // duration_ns for timestamp in timestamps)
valid = []
for index in range(max(counts) + 1):
    count = counts.get(index, 0)
    if count >= 2:
        valid.append(index)
    elif count:
        print(
            f"[warning] chunk {index} has only {count} synchronized frame and will be skipped",
            file=sys.stderr,
        )
    else:
        print(f"[warning] chunk {index} is empty and will be skipped", file=sys.stderr)

if not valid:
    raise SystemExit("no 60-second chunk contains at least two synchronized frames")
print("\n".join(str(index) for index in valid))
PY
)"
mapfile -t CHUNK_INDICES <<<"${CHUNK_INDEX_TEXT}"

OUTPUT_ROOT="${SCRIPT_DIR}/artifacts/${DATASET_NAME}/temporal_60sec"
mkdir -p -- "${OUTPUT_ROOT}"

echo "[dataset] ${DATASET_PATH}"
echo "[chunks] ${#CHUNK_INDICES[@]} runnable 60-second chunks"
echo "[output] ${OUTPUT_ROOT}"

for CHUNK_INDEX in "${CHUNK_INDICES[@]}"; do
  CHUNK_TAG="$(printf '%04d' "${CHUNK_INDEX}")"
  CHUNK_OUTPUT="${OUTPUT_ROOT}/chunk_${CHUNK_TAG}"
  SUMMARY_PATH="${CHUNK_OUTPUT}/data/summary.json"

  if [[ -f "${SUMMARY_PATH}" ]]; then
    echo "[skip] chunk ${CHUNK_TAG}: completed summary already exists"
    continue
  fi

  echo "[start] chunk ${CHUNK_TAG} -> ${CHUNK_OUTPUT}"
  conda run --no-capture-output -n depth-map-postprocess \
    python "${SCRIPT_DIR}/map_rgbd_gps.py" \
    "${DATASET_PATH}" \
    --output "${CHUNK_OUTPUT}" \
    --pose-mode hybrid \
    --cloud-preset dense \
    --cloud-frame-stride 1 \
    --pixel-stride 1 \
    --voxel-size-m 0.03 \
    --per-frame-max-points 0 \
    --max-points 40000000 \
    --browser-max-points 1000000 \
    --min-depth-m 0.7 \
    --stationary-speed-threshold-m-s 0.30 \
    --stationary-min-duration-s 2.0 \
    --stationary-max-cloud-frames 5 \
    --chunk-duration-seconds "${CHUNK_SECONDS}" \
    --chunk-index "${CHUNK_INDEX}" \
    --postprocess-preset road-map-temporal \
    --neighbor-backend scipy \
    --ground-backend local \
    --write-debug-stages \
    --debug-stage-max-points 500000 \
    --no-auto-postprocess-fallback

  if [[ ! -f "${SUMMARY_PATH}" ]]; then
    echo "[error] chunk ${CHUNK_TAG} finished without summary.json" >&2
    exit 1
  fi
  echo "[done] chunk ${CHUNK_TAG}"
done

echo "[complete] all runnable chunks are available under ${OUTPUT_ROOT}"
