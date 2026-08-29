#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from road_condition_core.evidence import EvidenceConfig, build_route_evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build lightweight RGB point evidence tiles for a route analysis result."
    )
    parser.add_argument("mapping_output", type=Path)
    parser.add_argument("route_output", type=Path)
    parser.add_argument("--max-points-per-tile", type=int, default=60_000)
    parser.add_argument("--surface-band-tolerance-m", type=float, default=0.15)
    parser.add_argument("--tile-context-m", type=float, default=1.0)
    parser.add_argument("--context-point-stage", choices=("raw", "clean"), default="clean")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    manifest = build_route_evidence(
        args.mapping_output,
        args.route_output,
        config=EvidenceConfig(
            max_points_per_tile=args.max_points_per_tile,
            surface_band_tolerance_m=args.surface_band_tolerance_m,
            tile_context_m=args.tile_context_m,
            context_point_stage=args.context_point_stage,
        ),
    )
    print(
        json.dumps(
            {
                "tile_count": manifest["tile_count"],
                "completed_tile_count": manifest["completed_tile_count"],
                "point_count": sum(
                    int(item.get("point_count", 0)) for item in manifest["tiles"]
                ),
                "byte_size": sum(
                    int(item.get("byte_size", 0)) for item in manifest["tiles"]
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
