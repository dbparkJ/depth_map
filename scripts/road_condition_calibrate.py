#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from road_condition_core.calibration import (
    analyze_flat_surface_noise,
    write_calibration_bundle,
)
from road_condition_core.io import load_mapping_bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Stage 03 flat-surface calibration bundle from one approved "
            "short depth_map mapping output."
        )
    )
    parser.add_argument("mapping_output", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--max-points", type=int, default=500_000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.max_points < 10_000:
        raise SystemExit("--max-points must be at least 10,000")
    mapping_output = args.mapping_output.expanduser().resolve()
    bundle = load_mapping_bundle(mapping_output, stage="raw")
    manifest = bundle.analysis_source_manifest or {
        "format_version": 1,
        "dataset_id": mapping_output.name,
        "mapping_commit_sha": "unknown",
        "camera_model": "unknown",
        "camera_height_m": None,
        "mount_yaw_deg": None,
        "mount_pitch_deg": None,
        "mount_roll_deg": None,
        "camera_offset_right_m": None,
        "camera_offset_down_m": None,
        "camera_offset_forward_m": None,
        "rgb_depth_alignment": "unknown",
        "timestamp_basis": "monotonic_ns",
        "calibration_status": "unknown",
        "manual_review_required": True,
    }
    distances = bundle.point_metadata.get("mean_depth_m")
    if distances is None:
        raise SystemExit(
            "cloud_raw_metadata.npz does not contain mean_depth_m; "
            "distance-band calibration cannot be inferred"
        )
    count = len(bundle.points_enu_m)
    keep = (
        np.linspace(0, count - 1, num=args.max_points, dtype=np.int64)
        if count > args.max_points
        else np.arange(count, dtype=np.int64)
    )
    report = analyze_flat_surface_noise(
        bundle.points_enu_m[keep],
        np.asarray(distances)[keep],
    )
    output = args.output or (mapping_output / "calibration")
    artifacts = write_calibration_bundle(output, manifest, noise_report=report)
    for name, path in artifacts.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
