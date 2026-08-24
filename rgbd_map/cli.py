from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .dataset import RgbdGpsDataset
from .exporters import export_mapping
from .geodesy import LocalENU
from .odometry import OdometryResult, SiftRgbdOdometry
from .pointcloud import build_point_cloud
from .trajectory import build_trajectory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an IMU-free RGB-D + GPS georeferenced point-cloud map."
    )
    parser.add_argument("dataset", type=Path, help="Dataset root containing timestamps.csv")
    parser.add_argument("--output", type=Path, required=True, help="Output directory")
    parser.add_argument("--pose-mode", choices=("hybrid", "gps"), default="hybrid")
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--trajectory-only", action="store_true")

    parser.add_argument("--odometry-image-scale", type=float, default=0.5)
    parser.add_argument("--sift-features", type=int, default=3500)
    parser.add_argument("--course-correction-gain", type=float, default=0.08)
    parser.add_argument("--level-correction-gain", type=float, default=0.01)
    parser.add_argument("--gps-weight-scale", type=float, default=1.0)
    parser.add_argument("--vertical-gps-scale", type=float, default=0.10)
    parser.add_argument("--max-visual-gps-angle-deg", type=float, default=45.0)
    parser.add_argument("--min-visual-gps-distance-ratio", type=float, default=0.25)
    parser.add_argument("--max-visual-gps-distance-ratio", type=float, default=2.5)
    parser.add_argument("--max-visual-vertical-error-m", type=float, default=1.5)
    parser.add_argument("--max-visual-edge-dt-s", type=float, default=0.25)

    parser.add_argument("--cloud-frame-stride", type=int, default=10)
    parser.add_argument("--pixel-stride", type=int, default=10)
    parser.add_argument("--voxel-size-m", type=float, default=0.25)
    parser.add_argument("--max-points", type=int, default=1_000_000)
    parser.add_argument("--browser-max-points", type=int, default=300_000)
    parser.add_argument("--min-depth-m", type=float, default=1.0)
    parser.add_argument("--max-depth-m", type=float, default=30.0)
    parser.add_argument("--roi-top-ratio", type=float, default=0.15)
    parser.add_argument("--roi-bottom-ratio", type=float, default=0.90)

    parser.add_argument("--mount-roll-deg", type=float, default=0.0)
    parser.add_argument("--mount-pitch-deg", type=float, default=0.0)
    parser.add_argument("--mount-yaw-deg", type=float, default=0.0)
    parser.add_argument("--camera-offset-right-m", type=float, default=0.0)
    parser.add_argument("--camera-offset-down-m", type=float, default=0.0)
    parser.add_argument("--camera-offset-forward-m", type=float, default=0.0)
    parser.add_argument("--progress-every", type=int, default=50)
    return parser


def _estimate_odometry(
    dataset: RgbdGpsDataset,
    frames,
    gps_positions: np.ndarray,
    args: argparse.Namespace,
) -> list[OdometryResult]:
    results = [OdometryResult.failed("origin")]
    if args.pose_mode == "gps":
        results.extend(OdometryResult.failed("pose_mode_gps") for _ in frames[1:])
        return results

    estimator = SiftRgbdOdometry(
        dataset.camera,
        image_scale=args.odometry_image_scale,
        max_features=args.sift_features,
        min_depth_m=args.min_depth_m,
        max_depth_m=args.max_depth_m,
    )
    previous_features = estimator.extract(frames[0].rgb_path)
    for index in range(1, len(frames)):
        current_features = estimator.extract(frames[index].rgb_path)
        gps_distance = float(np.linalg.norm(gps_positions[index, :2] - gps_positions[index - 1, :2]))
        result = estimator.estimate(
            previous_features,
            current_features,
            frames[index - 1].depth_path,
            gps_distance,
        )
        results.append(result)
        previous_features = current_features
        if args.progress_every and (
            index % args.progress_every == 0 or index == len(frames) - 1
        ):
            success = sum(item.success for item in results[1:])
            print(
                f"[odometry] {index}/{len(frames) - 1} edges, "
                f"accepted {success}/{index}, latest={result.method}:{result.reason}",
                flush=True,
            )
    return results


def run(args: argparse.Namespace) -> dict:
    dataset = RgbdGpsDataset(args.dataset)
    frames = dataset.load_frames(args.start_frame, args.max_frames)
    gps = dataset.interpolate_gps(frames)
    origin = LocalENU(
        origin_longitude_deg=float(gps.longitude_deg[0]),
        origin_latitude_deg=float(gps.latitude_deg[0]),
        origin_ellipsoid_height_m=float(gps.ellipsoid_height_m[0]),
    )
    gps_positions = origin.geodetic_to_enu(
        gps.longitude_deg, gps.latitude_deg, gps.ellipsoid_height_m
    )
    print(
        f"[dataset] {len(frames)} synchronized frames, IMU files excluded, "
        f"pose_mode={args.pose_mode}",
        flush=True,
    )
    odometry = _estimate_odometry(dataset, frames, gps_positions, args)
    trajectory = build_trajectory(
        gps,
        gps_positions,
        odometry,
        pose_mode=args.pose_mode,
        mount_roll_deg=args.mount_roll_deg,
        mount_pitch_deg=args.mount_pitch_deg,
        mount_yaw_deg=args.mount_yaw_deg,
        course_correction_gain=args.course_correction_gain,
        level_correction_gain=args.level_correction_gain,
        gps_weight_scale=args.gps_weight_scale,
        vertical_gps_scale=args.vertical_gps_scale,
        max_visual_gps_angle_deg=args.max_visual_gps_angle_deg,
        min_visual_gps_distance_ratio=args.min_visual_gps_distance_ratio,
        max_visual_gps_distance_ratio=args.max_visual_gps_distance_ratio,
        max_visual_vertical_error_m=args.max_visual_vertical_error_m,
        max_visual_edge_dt_s=args.max_visual_edge_dt_s,
        camera_offset_right_m=args.camera_offset_right_m,
        camera_offset_down_m=args.camera_offset_down_m,
        camera_offset_forward_m=args.camera_offset_forward_m,
    )
    cloud = None
    if not args.trajectory_only:
        cloud = build_point_cloud(
            frames,
            dataset.camera,
            trajectory,
            frame_stride=args.cloud_frame_stride,
            pixel_stride=args.pixel_stride,
            voxel_size_m=args.voxel_size_m,
            max_points=args.max_points,
            min_depth_m=args.min_depth_m,
            max_depth_m=args.max_depth_m,
            roi_top_ratio=args.roi_top_ratio,
            roi_bottom_ratio=args.roi_bottom_ratio,
            camera_offset_right_m=args.camera_offset_right_m,
            camera_offset_down_m=args.camera_offset_down_m,
            camera_offset_forward_m=args.camera_offset_forward_m,
            progress_every=max(1, args.progress_every // 2),
        )

    parameters = {
        key: value
        for key, value in vars(args).items()
        if key not in {"dataset", "output"}
    }
    parameters["dataset"] = str(Path(args.dataset).expanduser().resolve())
    viewer_dir = Path(__file__).resolve().parent.parent / "viewer"
    summary = export_mapping(
        Path(args.output).expanduser().resolve(),
        viewer_dir,
        frames,
        gps,
        origin,
        trajectory,
        odometry,
        cloud,
        args.browser_max_points,
        parameters,
    )
    print(json.dumps(summary["trajectory"], ensure_ascii=False, indent=2), flush=True)
    print(f"[done] {Path(args.output).expanduser().resolve()}", flush=True)
    return summary


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.max_frames is not None and args.max_frames < 2:
        parser.error("--max-frames must be at least 2")
    run(args)


if __name__ == "__main__":
    main()
