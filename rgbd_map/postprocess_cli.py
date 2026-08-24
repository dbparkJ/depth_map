from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from .postprocess_args import add_postprocess_arguments, resolve_output_options
from .postprocess_backends import resolve_ground_backend, resolve_neighbor_backend
from .postprocess_config import resolve_postprocess_config
from .postprocess_io import atomic_json_dump, load_raw_cloud_bundle
from .postprocess_runner import (
    execute_postprocess,
    install_current_viewer,
    write_postprocess_execution,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Re-run point-cloud cleanup from preserved raw PLY/metadata without "
            "decoding RGB-D or recomputing trajectory/odometry."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Existing mapping output containing data/cloud_raw_enu.ply",
    )
    parser.add_argument(
        "--voxel-size-m",
        type=float,
        default=None,
        help="Override voxel size if the existing summary does not record it",
    )
    parser.add_argument(
        "--browser-max-points",
        type=int,
        default=None,
        help="Override clean browser point limit from the existing summary",
    )
    add_postprocess_arguments(parser)
    return parser


def _nested_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _recorded_voxel_size(summary: Mapping[str, Any]) -> float:
    parameters = _nested_mapping(summary.get("parameters"))
    resolved = _nested_mapping(parameters.get("resolved_cloud_config"))
    for source, name in (
        (resolved, "voxel_size_m"),
        (parameters, "voxel_size_m"),
        (_nested_mapping(summary.get("cloud")), "voxel_size_m"),
    ):
        value = source.get(name)
        if value is not None:
            return float(value)
    raise ValueError(
        "voxel size is absent from summary.json; provide --voxel-size-m"
    )


def _recorded_browser_limit(summary: Mapping[str, Any]) -> int:
    parameters = _nested_mapping(summary.get("parameters"))
    resolved = _nested_mapping(parameters.get("resolved_cloud_config"))
    for source, name in (
        (resolved, "browser_max_points"),
        (parameters, "browser_max_points"),
        (_nested_mapping(summary.get("cloud")), "clean_browser_point_count"),
        (_nested_mapping(summary.get("cloud")), "browser_point_count"),
    ):
        value = source.get(name)
        if value is not None and int(value) > 0:
            return int(value)
    return 700_000


def _explicit_overrides(args: argparse.Namespace) -> dict[str, object]:
    names = (
        "depth_edge_radius_px",
        "depth_edge_abs_m",
        "depth_edge_rel_ratio",
        "depth_edge_min_valid_neighbors",
        "min_distinct_frames",
        "max_voxel_position_std_m",
        "radius_outlier_radius_m",
        "radius_outlier_min_neighbors",
        "statistical_neighbors",
        "statistical_std_ratio",
        "postprocess_tile_size_m",
        "postprocess_tile_overlap_m",
        "road_corridor_half_width_m",
        "ground_grid_size_m",
        "below_ground_tolerance_m",
    )
    return {
        name: getattr(args, name)
        for name in names
        if getattr(args, name, None) is not None
    }


def run(args: argparse.Namespace) -> dict:
    output_dir = args.output.expanduser().resolve()
    bundle = load_raw_cloud_bundle(output_dir)
    voxel_size_m = (
        float(args.voxel_size_m)
        if args.voxel_size_m is not None
        else _recorded_voxel_size(bundle.summary)
    )
    browser_max_points = (
        int(args.browser_max_points)
        if args.browser_max_points is not None
        else _recorded_browser_limit(bundle.summary)
    )
    if browser_max_points <= 0:
        raise ValueError("--browser-max-points must be positive")
    config = resolve_postprocess_config(
        args.postprocess_preset,
        voxel_size_m,
        args,
    )
    resolve_neighbor_backend(args.neighbor_backend)
    resolve_ground_backend(args.ground_backend)
    output_options = resolve_output_options(args, config)
    existing_parameters = _nested_mapping(bundle.summary.get("parameters"))
    recorded_postprocess = _nested_mapping(
        existing_parameters.get("raw_build_postprocess_config")
    ) or _nested_mapping(existing_parameters.get("resolved_postprocess_config"))
    raw_build_depth_edge = (
        {
            "preset": recorded_postprocess.get("preset"),
            "enabled": recorded_postprocess.get("depth_edge_filter"),
            "radius_px": recorded_postprocess.get("depth_edge_radius_px"),
            "absolute_threshold_m": recorded_postprocess.get("depth_edge_abs_m"),
            "relative_ratio": recorded_postprocess.get("depth_edge_rel_ratio"),
            "minimum_valid_neighbors": recorded_postprocess.get(
                "depth_edge_min_valid_neighbors"
            ),
        }
        if recorded_postprocess
        else None
    )
    execution = execute_postprocess(
        bundle.points_enu_m,
        bundle.colors_rgb,
        bundle.metadata,
        bundle.trajectory_xyz,
        config,
        voxel_size_m=voxel_size_m,
        config_overrides=args,
        neighbor_backend=args.neighbor_backend,
        ground_backend=args.ground_backend,
        auto_fallback=output_options.auto_fallback,
        postprocess_only=True,
        raw_build_depth_edge=raw_build_depth_edge,
    )
    existing_cloud = _nested_mapping(bundle.summary.get("cloud"))
    cloud_summary, _paths = write_postprocess_execution(
        output_dir,
        execution,
        origin=bundle.origin,
        trajectory_enu_m=bundle.trajectory_xyz,
        browser_max_points=browser_max_points,
        output_options=output_options,
        base_cloud_summary=existing_cloud,
        parameter_context=_explicit_overrides(args),
        reuse_existing_raw_products=True,
    )
    cloud_summary["postprocess_status"] = "complete"
    updated_summary = dict(bundle.summary)
    updated_summary["cloud"] = cloud_summary
    parameters = dict(_nested_mapping(updated_summary.get("parameters")))
    parameters.update(
        {
            "postprocess_preset": config.preset,
            "last_postprocess_only_config": config.to_dict(),
            "neighbor_backend": args.neighbor_backend,
            "ground_backend": args.ground_backend,
            "postprocess_only": True,
        }
    )
    updated_summary["parameters"] = parameters
    atomic_json_dump(output_dir / "data" / "summary.json", updated_summary)
    viewer_source = Path(__file__).resolve().parent.parent / "viewer"
    install_current_viewer(output_dir, viewer_source)
    result = execution.selected
    print(
        json.dumps(
            {
                "output": str(output_dir),
                "requested_preset": execution.requested_preset,
                "selected_preset": result.config.preset,
                "raw_point_count": len(result.raw_points_enu_m),
                "clean_point_count": len(result.clean_points_enu_m),
                "removed_point_count": len(result.removed_points_enu_m),
                "removal_ratio": result.quality.removal_ratio,
                "fallback_attempted": execution.fallback_preset is not None,
                "fallback_selected": execution.fallback_selected,
                "quality_guards_passed": result.quality.passed,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return updated_summary


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        run(args)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
