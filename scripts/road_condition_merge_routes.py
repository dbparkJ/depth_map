#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from road_condition_core.route import RouteChunkInput, aggregate_chunk_routes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge completed per-chunk route results using explicit chainage offsets."
    )
    parser.add_argument(
        "--chunk",
        action="append",
        required=True,
        metavar="ID,RESULT_DIR,OFFSET_M",
        help="Repeat for each chunk; point clouds are not loaded during merge",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    chunks = []
    for value in args.chunk:
        pieces = value.split(",", 2)
        if len(pieces) != 3:
            raise SystemExit("--chunk must be ID,RESULT_DIR,OFFSET_M")
        chunks.append(
            RouteChunkInput(
                chunk_id=pieces[0].strip(),
                route_result_dir=Path(pieces[1].strip()),
                chainage_offset_m=float(pieces[2]),
            )
        )
    manifest = aggregate_chunk_routes(chunks, args.output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
