#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from road_condition_core.io import load_mapping_bundle
from road_condition_core.route import RouteConfig, run_tiled_analysis
from road_condition_core.roi import load_road_roi, resolve_roi_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run resumable 10 m core + 3 m halo analysis for one mapping chunk."
    )
    parser.add_argument("mapping_output", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--point-cloud-stage", choices=("raw", "clean"), default="raw")
    parser.add_argument("--road-roi-path", default=None)
    parser.add_argument("--core-tile-length-m", type=float, default=10.0)
    parser.add_argument("--halo-m", type=float, default=3.0)
    parser.add_argument("--report-segment-length-m", type=float, default=20.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    mapping_output = args.mapping_output.expanduser().resolve()
    bundle = load_mapping_bundle(mapping_output, stage=args.point_cloud_stage)
    roi_path = None
    if args.road_roi_path:
        roi_path = resolve_roi_path(mapping_output, args.road_roi_path)
    else:
        for candidate in (
            mapping_output / "data" / "road_roi.geojson",
            mapping_output / "road_roi.geojson",
        ):
            if candidate.is_file():
                roi_path = candidate
                break
    road_roi = load_road_roi(roi_path) if roi_path is not None else None
    pose_context = (
        {
            "T_enu_camera": bundle.camera_poses.T_enu_camera,
            "pose_quality_score": bundle.camera_poses.pose_quality_score,
        }
        if bundle.camera_poses is not None
        else None
    )
    manifest = run_tiled_analysis(
        bundle.points_enu_m,
        bundle.colors_rgb,
        bundle.trajectory_enu_m,
        args.output,
        route_config=RouteConfig(
            core_tile_length_m=args.core_tile_length_m,
            halo_m=args.halo_m,
            report_segment_length_m=args.report_segment_length_m,
        ),
        point_metadata=bundle.point_metadata,
        source={
            "type": "mapping_bundle",
            "mapping_output": str(mapping_output),
            "point_cloud_stage": args.point_cloud_stage,
        },
        source_origin=(bundle.summary.get("origin") if isinstance(bundle.summary.get("origin"), dict) else None),
        road_roi=road_roi,
        pose_context=pose_context,
        quality_context=bundle.analysis_quality,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
