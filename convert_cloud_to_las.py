#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rgbd_map.las_export import (
    GroundFilterConfig,
    export_las,
    make_export_plan,
    make_file_export_plan,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a mapping ENU PLY cloud to a georeferenced LAS 1.4 file."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--output",
        type=Path,
        help="mapping output directory containing data/summary.json",
    )
    source.add_argument(
        "--file",
        type=Path,
        help="input PLY; summary.json must be in the same directory",
    )
    parser.add_argument(
        "--stage",
        choices=("raw", "clean", "removed"),
        default="clean",
        help="cloud stage used with --output (default: clean)",
    )
    parser.add_argument("--las", type=Path, help="output LAS path")
    parser.add_argument(
        "--target-crs",
        help="projected target CRS (default: WGS 84 UTM zone inferred from origin)",
    )
    parser.add_argument("--scale-m", type=float, default=0.001)
    parser.add_argument(
        "--ground-only",
        action="store_true",
        help="discard non-ground points using ELM and SMRF before writing LAS",
    )
    parser.add_argument("--ground-cell-m", type=float, default=0.50)
    parser.add_argument("--ground-scalar", type=float, default=1.20)
    parser.add_argument("--ground-slope", type=float, default=0.15)
    parser.add_argument("--ground-threshold-m", type=float, default=0.20)
    parser.add_argument("--ground-window-m", type=float, default=8.0)
    parser.add_argument("--pdal", default="pdal", help="PDAL executable")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    ground_filter = (
        GroundFilterConfig(
            cell_m=args.ground_cell_m,
            scalar=args.ground_scalar,
            slope=args.ground_slope,
            threshold_m=args.ground_threshold_m,
            window_m=args.ground_window_m,
        )
        if args.ground_only
        else None
    )
    common = {
        "output_las": args.las,
        "target_crs": args.target_crs,
        "scale_m": args.scale_m,
        "ground_filter": ground_filter,
    }
    if args.file is not None:
        plan = make_file_export_plan(args.file, **common)
    else:
        plan = make_export_plan(args.output, stage=args.stage, **common)
    print(
        f"Converting {plan.source_point_count:,} points: {plan.input_ply}\n"
        f"Target: {plan.output_las} ({plan.target_crs.to_string()})\n"
        f"Ground only: {'yes' if plan.ground_filter is not None else 'no'}",
        flush=True,
    )
    report = export_las(plan, pdal_executable=args.pdal, overwrite=args.overwrite)
    result = {
        key: report[key]
        for key in (
            "output_las",
            "output_point_count",
            "removed_point_count",
            "retention_ratio",
            "output_size_bytes",
            "target_crs",
            "target_crs_name",
            "vertical_coordinate",
            "scale_m",
            "bounds",
            "crs_wkt_embedded",
            "ground_only",
            "ground_filter",
            "pipeline_json",
        )
    }
    result["report_json"] = str(plan.report_json)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
