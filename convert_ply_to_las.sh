#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./convert_ply_to_las.sh --file /path/to/cloud.ply [--ground-only] [--overwrite]

The PLY directory must also contain summary.json with the mapping ENU origin.
The LAS, PDAL pipeline, and conversion report are written beside the input PLY.
EOF
}

PLY_FILE=""
EXTRA_ARGS=()
while (($# > 0)); do
  case "$1" in
    --file)
      if (($# < 2)); then
        echo "[error] --file requires a PLY path" >&2
        usage >&2
        exit 2
      fi
      PLY_FILE="$2"
      shift 2
      ;;
    --ground-only|--overwrite)
      EXTRA_ARGS+=("$1")
      shift
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

if [[ -z "${PLY_FILE}" ]]; then
  echo "[error] --file is required" >&2
  usage >&2
  exit 2
fi
if [[ ! -f "${PLY_FILE}" ]]; then
  echo "[error] PLY file not found: ${PLY_FILE}" >&2
  exit 2
fi
if [[ "${PLY_FILE,,}" != *.ply ]]; then
  echo "[error] input file must have a .ply extension: ${PLY_FILE}" >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PLY_DIR="$(cd -- "$(dirname -- "${PLY_FILE}")" && pwd -P)"
PLY_FILE="${PLY_DIR}/$(basename -- "${PLY_FILE}")"
SUMMARY_FILE="${PLY_DIR}/summary.json"

if [[ ! -f "${SUMMARY_FILE}" ]]; then
  echo "[error] summary.json was not found beside the PLY: ${SUMMARY_FILE}" >&2
  exit 2
fi
if ! command -v conda >/dev/null 2>&1; then
  echo "[error] conda was not found in PATH" >&2
  exit 2
fi

echo "[input] ${PLY_FILE}"
echo "[summary] ${SUMMARY_FILE}"
conda run --no-capture-output -n depth-map-postprocess \
  python "${SCRIPT_DIR}/convert_cloud_to_las.py" \
  --file "${PLY_FILE}" \
  "${EXTRA_ARGS[@]}"
