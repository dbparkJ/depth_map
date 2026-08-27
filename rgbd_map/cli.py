from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, replace
from math import isfinite
from pathlib import Path

import numpy as np

from .dataset import RgbdGpsDataset
from .depth_quality import DepthQualityPolicy
from .exporters import cloud_build_stats_summary, export_mapping
from .geodesy import LocalENU
from .odometry import OdometryResult, SiftRgbdOdometry
from .frame_quality import (
    audit_cloud_frames,
    write_frame_quality_diagnostics,
    write_pose_frame_quality_csv,
)
from .pointcloud import build_point_cloud, spatially_sample_indices
from .postprocess_args import add_postprocess_arguments, resolve_output_options
from .postprocess_backends import resolve_ground_backend, resolve_neighbor_backend
from .postprocess_config import resolve_postprocess_config
from .postprocess_io import (
    atomic_json_dump,
    atomic_savez_compressed,
    write_raw_cloud_bundle,
)
from .postprocess_runner import execute_postprocess, write_postprocess_execution
from .registration_quality import compute_adjacent_frame_registration_quality
from .trajectory import build_trajectory


@dataclass(frozen=True)
class CloudBuildConfig:
    frame_stride: int
    pixel_stride: int
    voxel_size_m: float
    max_points: int
    browser_max_points: int
    per_frame_max_points: int
    roi_top_ratio: float
    roi_bottom_ratio: float
    keyframe_distance_m: float | None = None
    keyframe_angle_deg: float | None = None
    keyframe_max_dt_s: float | None = None
    stationary_speed_threshold_m_s: float | None = None
    stationary_min_duration_s: float = 2.0
    stationary_max_cloud_frames: int = 5


CLOUD_PRESETS: dict[str, CloudBuildConfig] = {
    "preview": CloudBuildConfig(
        frame_stride=10,
        pixel_stride=10,
        voxel_size_m=0.25,
        max_points=1_000_000,
        browser_max_points=300_000,
        per_frame_max_points=5_000,
        roi_top_ratio=0.15,
        roi_bottom_ratio=0.90,
    ),
    "balanced": CloudBuildConfig(
        frame_stride=2,
        pixel_stride=4,
        voxel_size_m=0.10,
        max_points=5_000_000,
        browser_max_points=700_000,
        per_frame_max_points=20_000,
        roi_top_ratio=0.10,
        roi_bottom_ratio=0.98,
    ),
    "dense": CloudBuildConfig(
        frame_stride=1,
        pixel_stride=2,
        voxel_size_m=0.05,
        max_points=20_000_000,
        browser_max_points=800_000,
        per_frame_max_points=50_000,
        roi_top_ratio=0.05,
        roi_bottom_ratio=0.98,
    ),
}


def resolve_cloud_build_config(args: argparse.Namespace) -> CloudBuildConfig:
    preset_name = getattr(args, "cloud_preset", None) or "balanced"
    try:
        preset = CLOUD_PRESETS[preset_name]
    except KeyError as exc:
        choices = ", ".join(CLOUD_PRESETS)
        raise ValueError(f"cloud_preset must be one of: {choices}") from exc

    def override(argument_name: str, preset_value):
        value = getattr(args, argument_name, None)
        return preset_value if value is None else value

    config = CloudBuildConfig(
        frame_stride=int(override("cloud_frame_stride", preset.frame_stride)),
        pixel_stride=int(override("pixel_stride", preset.pixel_stride)),
        voxel_size_m=float(override("voxel_size_m", preset.voxel_size_m)),
        max_points=int(override("max_points", preset.max_points)),
        browser_max_points=int(
            override("browser_max_points", preset.browser_max_points)
        ),
        per_frame_max_points=int(
            override("per_frame_max_points", preset.per_frame_max_points)
        ),
        roi_top_ratio=float(override("roi_top_ratio", preset.roi_top_ratio)),
        roi_bottom_ratio=float(
            override("roi_bottom_ratio", preset.roi_bottom_ratio)
        ),
        keyframe_distance_m=(
            None
            if getattr(args, "cloud_keyframe_distance_m", None) is None
            else float(args.cloud_keyframe_distance_m)
        ),
        keyframe_angle_deg=(
            None
            if getattr(args, "cloud_keyframe_angle_deg", None) is None
            else float(args.cloud_keyframe_angle_deg)
        ),
        keyframe_max_dt_s=(
            None
            if getattr(args, "cloud_keyframe_max_dt_s", None) is None
            else float(args.cloud_keyframe_max_dt_s)
        ),
        stationary_speed_threshold_m_s=(
            None
            if getattr(args, "stationary_speed_threshold_m_s", None) is None
            else float(args.stationary_speed_threshold_m_s)
        ),
        stationary_min_duration_s=float(
            getattr(args, "stationary_min_duration_s", 2.0)
        ),
        stationary_max_cloud_frames=int(
            getattr(args, "stationary_max_cloud_frames", 5)
        ),
    )
    if config.frame_stride <= 0:
        raise ValueError("--cloud-frame-stride must be positive")
    if config.pixel_stride <= 0:
        raise ValueError("--pixel-stride must be positive")
    if not isfinite(config.voxel_size_m) or config.voxel_size_m <= 0.0:
        raise ValueError("--voxel-size-m must be positive")
    if config.max_points <= 0:
        raise ValueError("--max-points must be positive")
    if config.browser_max_points <= 0:
        raise ValueError("--browser-max-points must be positive")
    if config.per_frame_max_points < 0:
        raise ValueError("--per-frame-max-points must be non-negative")
    if not (
        isfinite(config.roi_top_ratio)
        and isfinite(config.roi_bottom_ratio)
        and 0.0 <= config.roi_top_ratio < config.roi_bottom_ratio <= 1.0
    ):
        raise ValueError(
            "cloud ROI must satisfy 0 <= --roi-top-ratio < "
            "--roi-bottom-ratio <= 1"
        )
    for option, value in (
        ("--cloud-keyframe-distance-m", config.keyframe_distance_m),
        ("--cloud-keyframe-angle-deg", config.keyframe_angle_deg),
        ("--cloud-keyframe-max-dt-s", config.keyframe_max_dt_s),
    ):
        if value is not None and (not isfinite(value) or value <= 0.0):
            raise ValueError(f"{option} must be positive when specified")
    if config.keyframe_angle_deg is not None and config.keyframe_angle_deg > 180.0:
        raise ValueError("--cloud-keyframe-angle-deg must not exceed 180")
    if config.stationary_speed_threshold_m_s is not None and (
        not isfinite(config.stationary_speed_threshold_m_s)
        or config.stationary_speed_threshold_m_s < 0.0
    ):
        raise ValueError(
            "--stationary-speed-threshold-m-s must be finite and non-negative"
        )
    if (
        not isfinite(config.stationary_min_duration_s)
        or config.stationary_min_duration_s <= 0.0
    ):
        raise ValueError("--stationary-min-duration-s must be positive")
    if config.stationary_max_cloud_frames <= 0:
        raise ValueError("--stationary-max-cloud-frames must be positive")
    min_depth_m = float(getattr(args, "min_depth_m", 1.0))
    max_depth_m = float(getattr(args, "max_depth_m", 30.0))
    if not (
        isfinite(min_depth_m)
        and isfinite(max_depth_m)
        and 0.0 < min_depth_m < max_depth_m
    ):
        raise ValueError("depth range must satisfy 0 < --min-depth-m < --max-depth-m")
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an IMU-free RGB-D + GPS georeferenced point-cloud map."
    )
    parser.add_argument("dataset", type=Path, help="Dataset root containing timestamps.csv")
    parser.add_argument("--output", type=Path, required=True, help="Output directory")
    parser.add_argument("--pose-mode", choices=("hybrid", "gps"), default="hybrid")
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument(
        "--chunk-duration-seconds",
        type=float,
        default=None,
        help=(
            "Select a half-open timestamp chunk of this duration instead of "
            "--start-frame/--max-frames"
        ),
    )
    parser.add_argument(
        "--chunk-index",
        type=int,
        default=None,
        help=(
            "Zero-based timestamp chunk index (default: 0 when "
            "--chunk-duration-seconds is set)"
        ),
    )
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

    parser.add_argument(
        "--cloud-preset",
        choices=tuple(CLOUD_PRESETS),
        default="balanced",
        help="Point-cloud density preset (default: balanced)",
    )
    parser.add_argument("--cloud-frame-stride", type=int, default=None)
    parser.add_argument("--pixel-stride", type=int, default=None)
    parser.add_argument("--voxel-size-m", type=float, default=None)
    parser.add_argument("--max-points", type=int, default=None)
    parser.add_argument("--browser-max-points", type=int, default=None)
    parser.add_argument(
        "--per-frame-max-points",
        type=int,
        default=None,
        help="Maximum valid points retained per sampled frame; 0 disables the cap",
    )
    parser.add_argument("--min-depth-m", type=float, default=1.0)
    parser.add_argument("--max-depth-m", type=float, default=30.0)
    parser.add_argument("--roi-top-ratio", type=float, default=None)
    parser.add_argument("--roi-bottom-ratio", type=float, default=None)
    parser.add_argument(
        "--cloud-keyframe-distance-m",
        type=float,
        default=None,
        help="Select a cloud keyframe after this much translation",
    )
    parser.add_argument(
        "--cloud-keyframe-angle-deg",
        type=float,
        default=None,
        help="Select a cloud keyframe after this much rotation",
    )
    parser.add_argument(
        "--cloud-keyframe-max-dt-s",
        type=float,
        default=None,
        help="Maximum time between cloud keyframes",
    )
    parser.add_argument(
        "--stationary-speed-threshold-m-s",
        type=float,
        default=None,
        help=(
            "Enable GPS stationary cloud-frame limiting at or below this speed; "
            "disabled by default"
        ),
    )
    parser.add_argument(
        "--stationary-min-duration-s",
        type=float,
        default=2.0,
        help="Minimum continuous low-speed duration to limit (default: 2.0)",
    )
    parser.add_argument(
        "--stationary-max-cloud-frames",
        type=int,
        default=5,
        help="Maximum uniformly retained cloud frames per stationary run (default: 5)",
    )

    parser.add_argument("--mount-roll-deg", type=float, default=0.0)
    parser.add_argument("--mount-pitch-deg", type=float, default=0.0)
    parser.add_argument("--mount-yaw-deg", type=float, default=0.0)
    parser.add_argument("--camera-offset-right-m", type=float, default=0.0)
    parser.add_argument("--camera-offset-down-m", type=float, default=0.0)
    parser.add_argument("--camera-offset-forward-m", type=float, default=0.0)
    parser.add_argument("--progress-every", type=int, default=50)
    add_postprocess_arguments(parser)
    return parser


def validate_frame_selection_args(args: argparse.Namespace) -> None:
    """Reject ambiguous frame-count and timestamp-chunk selections."""

    duration = getattr(args, "chunk_duration_seconds", None)
    chunk_index = getattr(args, "chunk_index", None)
    if chunk_index is not None:
        if isinstance(chunk_index, bool) or not isinstance(chunk_index, int):
            raise ValueError("--chunk-index must be a non-negative integer")
        if chunk_index < 0:
            raise ValueError("--chunk-index must be a non-negative integer")
    if duration is None:
        if chunk_index is not None:
            raise ValueError(
                "--chunk-index requires --chunk-duration-seconds"
            )
        return

    duration_seconds = float(duration)
    if not isfinite(duration_seconds) or duration_seconds <= 0.0:
        raise ValueError("--chunk-duration-seconds must be finite and positive")
    if getattr(args, "start_frame", 0) != 0:
        raise ValueError(
            "--chunk-duration-seconds cannot be combined with --start-frame"
        )
    if getattr(args, "max_frames", None) is not None:
        raise ValueError(
            "--chunk-duration-seconds cannot be combined with --max-frames"
        )


def _estimate_odometry(
    dataset: RgbdGpsDataset,
    frames,
    gps_positions: np.ndarray,
    args: argparse.Namespace,
    depth_quality_policy: DepthQualityPolicy,
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
        depth_quality_policy=depth_quality_policy,
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
            frames[index - 1].confidence_path,
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
    validate_frame_selection_args(args)
    cloud_config = resolve_cloud_build_config(args)
    postprocess_preset = getattr(args, "postprocess_preset", "road-map")
    postprocess_config = resolve_postprocess_config(
        postprocess_preset,
        cloud_config.voxel_size_m,
        args,
    )
    output_options = resolve_output_options(args, postprocess_config)
    depth_quality_policy = DepthQualityPolicy(
        min_depth_m=float(args.min_depth_m),
        max_depth_m=float(args.max_depth_m),
        far_depth_policy=postprocess_config.far_depth_policy,
        far_depth_soft_start_m=postprocess_config.far_depth_soft_start_m,
        far_depth_hard_m=postprocess_config.far_depth_hard_m,
        confidence_threshold=postprocess_config.depth_confidence_threshold,
        confidence_order=postprocess_config.depth_confidence_order,
        edge_enabled=postprocess_config.depth_edge_filter,
        edge_domain=postprocess_config.depth_edge_domain,
        edge_radius_px=postprocess_config.depth_edge_radius_px,
        edge_abs_m=postprocess_config.depth_edge_abs_m,
        edge_rel_ratio=postprocess_config.depth_edge_rel_ratio,
        edge_min_valid_neighbors=postprocess_config.depth_edge_min_valid_neighbors,
        invalid_boundary_erosion_px=postprocess_config.invalid_boundary_erosion_px,
        far_speckle_max_pixels=postprocess_config.far_speckle_max_pixels,
    )
    # Explicit unavailable backends fail before RGB-D/VO work begins.
    resolve_neighbor_backend(getattr(args, "neighbor_backend", "auto"))
    resolve_ground_backend(getattr(args, "ground_backend", "auto"))
    if cloud_config.browser_max_points > 1_000_000:
        print(
            "[warning] Browser point counts above 1,000,000 may cause long load "
            "times or browser memory pressure.",
            flush=True,
        )
    dataset = RgbdGpsDataset(args.dataset)
    frame_chunk = None
    if getattr(args, "chunk_duration_seconds", None) is not None:
        chunk_index = getattr(args, "chunk_index", None)
        frames, frame_chunk = dataset.load_frame_chunk(
            float(args.chunk_duration_seconds),
            0 if chunk_index is None else int(chunk_index),
        )
    else:
        frames = dataset.load_frames(args.start_frame, args.max_frames)
    gps = dataset.interpolate_gps(frames)
    origin_gps = gps
    if frame_chunk is not None:
        origin_frames = dataset.load_frames(0, 2)
        origin_gps = dataset.interpolate_gps(origin_frames)
    origin = LocalENU(
        origin_longitude_deg=float(origin_gps.longitude_deg[0]),
        origin_latitude_deg=float(origin_gps.latitude_deg[0]),
        origin_ellipsoid_height_m=float(origin_gps.ellipsoid_height_m[0]),
    )
    gps_positions = origin.geodetic_to_enu(
        gps.longitude_deg, gps.latitude_deg, gps.ellipsoid_height_m
    )
    print(
        f"[dataset] {len(frames)} synchronized frames, IMU files excluded, "
        f"pose_mode={args.pose_mode}",
        flush=True,
    )
    odometry = _estimate_odometry(
        dataset, frames, gps_positions, args, depth_quality_policy
    )
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
    frame_audit = audit_cloud_frames(
        frames,
        trajectory,
        odometry,
        policy=postprocess_config.pose_cloud_policy,
        max_edge_dt_s=postprocess_config.pose_cloud_max_edge_dt_s,
        min_inliers=postprocess_config.pose_cloud_min_inliers,
        min_inlier_ratio=postprocess_config.pose_cloud_min_inlier_ratio,
        max_reprojection_error_px=(
            postprocess_config.pose_cloud_max_reprojection_error_px
        ),
    )
    cloud = None
    if not args.trajectory_only:
        cloud = build_point_cloud(
            frames,
            dataset.camera,
            trajectory,
            frame_stride=cloud_config.frame_stride,
            pixel_stride=cloud_config.pixel_stride,
            voxel_size_m=cloud_config.voxel_size_m,
            max_points=cloud_config.max_points,
            per_frame_max_points=cloud_config.per_frame_max_points,
            min_depth_m=args.min_depth_m,
            max_depth_m=args.max_depth_m,
            roi_top_ratio=cloud_config.roi_top_ratio,
            roi_bottom_ratio=cloud_config.roi_bottom_ratio,
            keyframe_distance_m=cloud_config.keyframe_distance_m,
            keyframe_angle_deg=cloud_config.keyframe_angle_deg,
            keyframe_max_dt_s=cloud_config.keyframe_max_dt_s,
            gps_speed_m_s=gps.speed_m_s,
            stationary_speed_threshold_m_s=(
                cloud_config.stationary_speed_threshold_m_s
            ),
            stationary_min_duration_s=cloud_config.stationary_min_duration_s,
            stationary_max_cloud_frames=(
                cloud_config.stationary_max_cloud_frames
            ),
            depth_edge_filter=postprocess_config.depth_edge_filter,
            depth_edge_radius_px=postprocess_config.depth_edge_radius_px,
            depth_edge_abs_m=postprocess_config.depth_edge_abs_m,
            depth_edge_rel_ratio=postprocess_config.depth_edge_rel_ratio,
            depth_edge_min_valid_neighbors=(
                postprocess_config.depth_edge_min_valid_neighbors
            ),
            depth_quality_policy=depth_quality_policy,
            frame_audit=frame_audit,
            support_enabled=postprocess_config.support_enabled,
            support_voxel_size_m=postprocess_config.support_voxel_size_m,
            support_far_voxel_size_m=postprocess_config.support_far_voxel_size_m,
            support_far_start_m=postprocess_config.support_far_start_m,
            support_min_independent_frames=(
                postprocess_config.support_min_independent_frames
            ),
            support_min_baseline_m=postprocess_config.support_min_baseline_m,
            support_min_time_separation_s=(
                postprocess_config.support_min_time_separation_s
            ),
            max_support_position_std_m=(
                postprocess_config.max_support_position_std_m
            ),
            temporal_enabled=postprocess_config.temporal_enabled,
            temporal_window_seconds=postprocess_config.temporal_window_seconds,
            temporal_depth_abs_m=postprocess_config.temporal_depth_abs_m,
            temporal_depth_rel_ratio=postprocess_config.temporal_depth_rel_ratio,
            temporal_max_free_space_contradictions=(
                postprocess_config.temporal_max_free_space_contradictions
            ),
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
    parameters["frame_chunk"] = (
        asdict(frame_chunk) if frame_chunk is not None else None
    )
    if frame_chunk is not None:
        parameters["chunk_index"] = frame_chunk.chunk_index
    parameters["cloud_preset"] = getattr(args, "cloud_preset", None) or "balanced"
    parameters.update(
        {
            "cloud_frame_stride": cloud_config.frame_stride,
            "pixel_stride": cloud_config.pixel_stride,
            "voxel_size_m": cloud_config.voxel_size_m,
            "max_points": cloud_config.max_points,
            "browser_max_points": cloud_config.browser_max_points,
            "per_frame_max_points": cloud_config.per_frame_max_points,
            "roi_top_ratio": cloud_config.roi_top_ratio,
            "roi_bottom_ratio": cloud_config.roi_bottom_ratio,
            "stationary_speed_threshold_m_s": (
                cloud_config.stationary_speed_threshold_m_s
            ),
            "stationary_min_duration_s": cloud_config.stationary_min_duration_s,
            "stationary_max_cloud_frames": (
                cloud_config.stationary_max_cloud_frames
            ),
        }
    )
    parameters["resolved_cloud_config"] = asdict(cloud_config)
    parameters["resolved_postprocess_config"] = postprocess_config.to_dict()
    parameters["raw_build_postprocess_config"] = postprocess_config.to_dict()
    parameters["pointcloud_format_version"] = 2
    parameters["cloud_raw_stage"] = "fused_prefiltered_raw"
    parameters["resolved_depth_quality_policy"] = depth_quality_policy.to_dict()
    parameters["pose_frame_audit"] = frame_audit.metrics
    parameters["calibration_assumptions"] = {
        "mount_rotation_deg": [
            float(args.mount_roll_deg),
            float(args.mount_pitch_deg),
            float(args.mount_yaw_deg),
        ],
        "gnss_to_camera_lever_arm_camera_axes_m": [
            float(args.camera_offset_right_m),
            float(args.camera_offset_down_m),
            float(args.camera_offset_forward_m),
        ],
        "rgbd_gnss_timestamp_offset_s": 0.0,
        "warning": (
            "Mount rotation, GNSS-camera lever arm and timestamp offset are not "
            "estimated by this pipeline. Zero values are assumptions, not calibration."
        ),
    }
    if postprocess_config.preset == "road-map-temporal" and all(
        float(value) == 0.0
        for value in (
            args.mount_roll_deg,
            args.mount_pitch_deg,
            args.mount_yaw_deg,
            args.camera_offset_right_m,
            args.camera_offset_down_m,
            args.camera_offset_forward_m,
        )
    ):
        print(
            "[warning] mount rotation and GNSS-camera lever arm are all zero; "
            "timestamp offset is also assumed zero. These are uncalibrated assumptions.",
            flush=True,
        )
    parameters["postprocess_output_options"] = asdict(output_options)
    base_frame_selection = (
        "keyframe"
        if any(
            value is not None
            for value in (
                cloud_config.keyframe_distance_m,
                cloud_config.keyframe_angle_deg,
                cloud_config.keyframe_max_dt_s,
            )
        )
        else "stride"
    )
    parameters["cloud_frame_selection"] = (
        base_frame_selection + "+gps-stationary-cap"
        if cloud_config.stationary_speed_threshold_m_s is not None
        else base_frame_selection
    )
    viewer_dir = Path(__file__).resolve().parent.parent / "viewer"
    output_dir = Path(args.output).expanduser().resolve()
    cloud_summary_override = None
    postprocess_execution = None
    if cloud is None:
        write_pose_frame_quality_csv(
            output_dir / "data" / "pose_frame_quality.csv", frame_audit
        )
    if cloud is not None:
        data_dir = output_dir / "data"
        write_pose_frame_quality_csv(
            data_dir / "pose_frame_quality.csv",
            frame_audit,
            cloud.frame_reports,
        )
        atomic_json_dump(
            data_dir / "depth_frame_quality.json",
            {
                "format_version": 1,
                "depth_quality_policy": depth_quality_policy.to_dict(),
                "pose_audit": frame_audit.metrics,
                "frames": list(cloud.frame_reports),
            },
        )
        write_frame_quality_diagnostics(
            output_dir / "diagnostics",
            frames,
            frame_audit,
            cloud.frame_reports,
        )
        sample_count = min(500_000, len(cloud.points_enu_m))
        sample_indices = (
            spatially_sample_indices(cloud.points_enu_m, sample_count)
            if sample_count and sample_count < len(cloud.points_enu_m)
            else np.arange(sample_count, dtype=np.int64)
        )
        if sample_count:
            sample_arrays: dict[str, np.ndarray] = {
                "points_xyz": cloud.points_enu_m[sample_indices],
                "colors_rgb": cloud.colors_rgb[sample_indices],
                "mean_depth_m": cloud.mean_depth_m[sample_indices],
                "source_frame_id": cloud.source_frame_id[sample_indices],
                "pose_quality_score": cloud.pose_quality_score[sample_indices],
                "temporal_support_count": cloud.temporal_support_count[sample_indices],
                "temporal_contradiction_count": (
                    cloud.temporal_contradiction_count[sample_indices]
                ),
            }
            if cloud.independent_view_count is not None:
                sample_arrays["independent_view_count"] = (
                    cloud.independent_view_count[sample_indices]
                )
                sample_arrays["support_position_std_m"] = (
                    cloud.support_position_std_m[sample_indices]
                )
            atomic_savez_compressed(
                data_dir / "cloud_provenance_sample.npz", **sample_arrays
            )
        atomic_json_dump(
            data_dir / "registration_quality.json",
            compute_adjacent_frame_registration_quality(
                cloud.points_enu_m,
                cloud.source_frame_id,
                cloud.mean_depth_m,
            ),
        )
        if (
            cloud.prefilter_removed_points_enu_m is not None
            and len(cloud.prefilter_removed_points_enu_m)
        ):
            atomic_savez_compressed(
                data_dir / "prefilter_removed_sample.npz",
                points_xyz=cloud.prefilter_removed_points_enu_m,
                colors_rgb=cloud.prefilter_removed_colors_rgb,
            )
        if output_options.keep_raw_cloud:
            write_raw_cloud_bundle(
                data_dir,
                cloud,
                origin,
                postprocess_preset=postprocess_config.preset,
            )
        # These build diagnostics have already been persisted and are not used by
        # filtering. Releasing them before KD-tree work bounds dense-run peak RSS.
        cloud = replace(
            cloud,
            depth_min_m=None,
            depth_max_m=None,
            depth_edge_pass_count=None,
            source_voxel_key=None,
        )
        base_cloud_summary: dict[str, object] = {
            **cloud_build_stats_summary(cloud),
            "raw_point_count": int(len(cloud.points_enu_m)),
            "confidence_map_available": bool(
                getattr(cloud.stats, "confidence_map_available", False)
            ),
            "confidence_filter_applied": bool(
                getattr(cloud.stats, "confidence_filter_applied", False)
            ),
        }
        # Persist trajectory/odometry/summary before the potentially expensive
        # cleanup so raw artifacts remain reusable after an interrupted stage.
        provisional_cloud = {
            **base_cloud_summary,
            "point_count": int(len(cloud.points_enu_m)),
            "ply_point_count": int(len(cloud.points_enu_m)),
            "browser_point_count": 0,
            "raw_ply": "cloud_raw_enu.ply" if output_options.keep_raw_cloud else None,
            "postprocess_status": "running",
            "postprocess_preset": postprocess_config.preset,
        }
        export_mapping(
            output_dir,
            viewer_dir,
            frames,
            gps,
            origin,
            trajectory,
            odometry,
            cloud,
            cloud_config.browser_max_points,
            parameters,
            cloud_summary_override=provisional_cloud,
        )
        try:
            postprocess_execution = execute_postprocess(
                cloud.points_enu_m,
                cloud.colors_rgb,
                cloud,
                trajectory.positions_enu_m,
                postprocess_config,
                voxel_size_m=cloud_config.voxel_size_m,
                config_overrides=args,
                neighbor_backend=getattr(args, "neighbor_backend", "auto"),
                ground_backend=getattr(args, "ground_backend", "auto"),
                auto_fallback=output_options.auto_fallback,
            )
        except Exception as exc:
            failed_cloud = {
                **provisional_cloud,
                "postprocess_status": "failed",
                "postprocess_error": f"{type(exc).__name__}: {exc}",
            }
            export_mapping(
                output_dir,
                viewer_dir,
                frames,
                gps,
                origin,
                trajectory,
                odometry,
                cloud,
                cloud_config.browser_max_points,
                parameters,
                cloud_summary_override=failed_cloud,
                write_support_files=False,
            )
            raise
        override_names = (
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
        explicit_overrides = {
            name: getattr(args, name)
            for name in override_names
            if getattr(args, name, None) is not None
        }
        cloud_summary_override, _postprocess_paths = write_postprocess_execution(
            output_dir,
            postprocess_execution,
            origin=origin,
            trajectory_enu_m=trajectory.positions_enu_m,
            browser_max_points=cloud_config.browser_max_points,
            output_options=output_options,
            base_cloud_summary=base_cloud_summary,
            parameter_context=explicit_overrides,
        )
        cloud_summary_override["postprocess_status"] = "complete"
    summary = export_mapping(
        output_dir,
        viewer_dir,
        frames,
        gps,
        origin,
        trajectory,
        odometry,
        cloud,
        cloud_config.browser_max_points,
        parameters,
        cloud_summary_override=cloud_summary_override,
        write_support_files=cloud is None,
    )
    if cloud is not None:
        cloud_summary = summary["cloud"]
        print(
            "[cloud-summary]\n"
            f"sampled frames: {cloud_summary['sampled_frame_count']:,} / "
            f"{cloud_summary.get('total_frame_count', len(frames)):,}\n"
            f"decoded frames: {cloud_summary['decoded_frame_count']:,}\n"
            "valid samples: "
            f"{cloud_summary['valid_depth_sample_count_before_voxel']:,}\n"
            "discarded by per-frame cap: "
            f"{cloud_summary.get('discarded_by_per_frame_cap', 0):,}\n"
            "unique voxels: "
            f"{cloud_summary.get('unique_voxel_count_before_final_cap', 0):,}\n"
            f"PLY points: {cloud_summary['ply_point_count']:,}\n"
            f"browser points: {cloud_summary['browser_point_count']:,}",
            flush=True,
        )
        if postprocess_execution is not None:
            quality = postprocess_execution.selected.quality
            print(
                "[postprocess-summary]\n"
                f"selected preset: {postprocess_execution.selected.config.preset}\n"
                f"removed points: {cloud_summary['removed_point_count']:,} "
                f"({cloud_summary['removal_ratio']:.2%})\n"
                f"quality guards passed: {quality.passed}\n"
                f"fallback attempted: "
                f"{postprocess_execution.fallback_preset is not None}, "
                f"selected={postprocess_execution.fallback_selected}",
                flush=True,
            )
    print(json.dumps(summary["trajectory"], ensure_ascii=False, indent=2), flush=True)
    print(f"[done] {Path(args.output).expanduser().resolve()}", flush=True)
    return summary


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        validate_frame_selection_args(args)
        if args.max_frames is not None and args.max_frames < 2:
            raise ValueError("--max-frames must be at least 2")
        resolve_cloud_build_config(args)
        cloud_config = resolve_cloud_build_config(args)
        resolve_postprocess_config(
            args.postprocess_preset,
            cloud_config.voxel_size_m,
            args,
        )
        resolve_neighbor_backend(args.neighbor_backend)
        resolve_ground_backend(args.ground_backend)
    except ValueError as exc:
        parser.error(str(exc))
    except RuntimeError as exc:
        parser.error(str(exc))
    run(args)


if __name__ == "__main__":
    main()
