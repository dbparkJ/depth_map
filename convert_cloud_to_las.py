#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rgbd_map.las_export import export_las, make_export_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a mapping ENU PLY cloud to a georeferenced LAS 1.4 file."
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="mapping output directory containing data/summary.json",
    )
    parser.add_argument(
        "--stage", choices=("raw", "clean", "removed"), default="clean"
    )
    parser.add_argument("--las", type=Path, help="output LAS path")
    parser.add_argument(
        "--target-crs",
        help="projected target CRS (default: WGS 84 UTM zone inferred from origin)",
    )
    parser.add_argument("--scale-m", type=float, default=0.001)
    parser.add_argument("--pdal", default="pdal", help="PDAL executable")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    plan = make_export_plan(
        args.output,
        stage=args.stage,
        output_las=args.las,
        target_crs=args.target_crs,
        scale_m=args.scale_m,
    )
    print(
        f"Converting {plan.source_point_count:,} points: {plan.input_ply}\n"
        f"Target: {plan.output_las} ({plan.target_crs.to_string()})",
        flush=True,
    )
    report = export_las(plan, pdal_executable=args.pdal, overwrite=args.overwrite)
    result = {
        key: report[key]
        for key in (
            "output_las",
            "output_point_count",
            "output_size_bytes",
            "target_crs",
            "target_crs_name",
            "vertical_coordinate",
            "scale_m",
            "bounds",
            "crs_wkt_embedded",
            "pipeline_json",
        )
    }
    result["report_json"] = str(plan.report_json)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
