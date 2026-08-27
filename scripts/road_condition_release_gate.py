#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from road_condition_core.release_gate import (
    evaluate_release_readiness,
    load_release_readiness,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed road-condition v1 release readiness gate."
    )
    parser.add_argument("manifest", help="Release readiness YAML manifest")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = load_release_readiness(args.manifest)
        result = evaluate_release_readiness(payload, artifact_root=Path.cwd())
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["release_allowed"] else 2


if __name__ == "__main__":
    sys.exit(main())
