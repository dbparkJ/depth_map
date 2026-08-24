from __future__ import annotations

import csv
import json
import shutil
import struct
from pathlib import Path

import numpy as np

from .dataset import FrameRecord, InterpolatedGps
from .geodesy import LocalENU
from .odometry import OdometryResult
from .pointcloud import PointCloudResult
from .trajectory import TrajectoryResult


def _json_dump(path: Path, value: object) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)


def _float_list(points: np.ndarray, decimals: int = 4) -> list[list[float]]:
    return np.round(points.astype(np.float64), decimals=decimals).tolist()


def write_browser_points(
    path: Path,
    cloud: PointCloudResult,
    max_points: int | None = None,
) -> int:
    total = len(cloud.points_enu_m)
    if max_points is not None and total > max_points:
        keep = np.linspace(0, total - 1, max_points, dtype=np.int64)
        points = cloud.points_enu_m[keep]
        colors = cloud.colors_rgb[keep]
    else:
        points = cloud.points_enu_m
        colors = cloud.colors_rgb
    count = len(points)
    records = np.empty(
        count,
        dtype=np.dtype(
            [
                ("x", "<f4"),
                ("y", "<f4"),
                ("z", "<f4"),
                ("r", "u1"),
                ("g", "u1"),
                ("b", "u1"),
                ("a", "u1"),
            ]
        ),
    )
    records["x"] = points[:, 0]
    records["y"] = points[:, 1]
    records["z"] = points[:, 2]
    records["r"] = colors[:, 0]
    records["g"] = colors[:, 1]
    records["b"] = colors[:, 2]
    records["a"] = 255
    with path.open("wb") as handle:
        handle.write(struct.pack("<4sIII", b"RGBD", 1, count, records.dtype.itemsize))
        records.tofile(handle)
    return count


def write_ply(path: Path, cloud: PointCloudResult, origin: LocalENU) -> None:
    count = len(cloud.points_enu_m)
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"comment coordinate_system local_ENU\n"
        f"comment origin_lon_deg {origin.origin_longitude_deg:.12f}\n"
        f"comment origin_lat_deg {origin.origin_latitude_deg:.12f}\n"
        f"comment origin_ellipsoid_height_m {origin.origin_ellipsoid_height_m:.4f}\n"
        f"element vertex {count}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    ).encode("ascii")
    records = np.empty(
        count,
        dtype=np.dtype(
            [
                ("x", "<f4"),
                ("y", "<f4"),
                ("z", "<f4"),
                ("r", "u1"),
                ("g", "u1"),
                ("b", "u1"),
            ]
        ),
    )
    records["x"] = cloud.points_enu_m[:, 0]
    records["y"] = cloud.points_enu_m[:, 1]
    records["z"] = cloud.points_enu_m[:, 2]
    records["r"] = cloud.colors_rgb[:, 0]
    records["g"] = cloud.colors_rgb[:, 1]
    records["b"] = cloud.colors_rgb[:, 2]
    with path.open("wb") as handle:
        handle.write(header)
        records.tofile(handle)


def write_trajectory_files(
    data_dir: Path,
    frames: list[FrameRecord],
    gps: InterpolatedGps,
    origin: LocalENU,
    trajectory: TrajectoryResult,
) -> None:
    fused_llh = origin.enu_to_geodetic(trajectory.positions_enu_m)
    gps_llh = origin.enu_to_geodetic(trajectory.gps_positions_enu_m)
    raw_llh = origin.enu_to_geodetic(trajectory.raw_visual_positions_enu_m)
    with (data_dir / "trajectory.csv").open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "frame_index",
            "source_frame_index",
            "wall_time",
            "pose_method",
            "fix_quality",
            "fix_quality_name",
            "course_deg",
            "speed_m_s",
            "fused_e_m",
            "fused_n_m",
            "fused_u_m",
            "fused_longitude_deg",
            "fused_latitude_deg",
            "fused_ellipsoid_height_m",
            "gps_e_m",
            "gps_n_m",
            "gps_u_m",
            "gps_longitude_deg",
            "gps_latitude_deg",
            "gps_ellipsoid_height_m",
            "pre_graph_e_m",
            "pre_graph_n_m",
            "pre_graph_u_m",
            "pre_graph_longitude_deg",
            "pre_graph_latitude_deg",
            "pre_graph_ellipsoid_height_m",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, frame in enumerate(frames):
            row = {
                "frame_index": index,
                "source_frame_index": frame.source_index,
                "wall_time": frame.wall_time,
                "pose_method": trajectory.methods[index],
                "fix_quality": int(gps.fix_quality[index]),
                "fix_quality_name": gps.fix_quality_name[index],
                "course_deg": float(gps.course_deg[index]),
                "speed_m_s": float(gps.speed_m_s[index]),
            }
            for prefix, local, llh in (
                ("fused", trajectory.positions_enu_m[index], fused_llh[index]),
                ("gps", trajectory.gps_positions_enu_m[index], gps_llh[index]),
                ("pre_graph", trajectory.raw_visual_positions_enu_m[index], raw_llh[index]),
            ):
                row[f"{prefix}_e_m"] = float(local[0])
                row[f"{prefix}_n_m"] = float(local[1])
                row[f"{prefix}_u_m"] = float(local[2])
                row[f"{prefix}_longitude_deg"] = float(llh[0])
                row[f"{prefix}_latitude_deg"] = float(llh[1])
                row[f"{prefix}_ellipsoid_height_m"] = float(llh[2])
            writer.writerow(row)

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "fused_rgbd_gps", "imu_used": False},
                "geometry": {"type": "LineString", "coordinates": fused_llh.tolist()},
            },
            {
                "type": "Feature",
                "properties": {"name": "interpolated_gps", "imu_used": False},
                "geometry": {"type": "LineString", "coordinates": gps_llh.tolist()},
            },
            {
                "type": "Feature",
                "properties": {"name": "pre_graph_gps_aided", "imu_used": False},
                "geometry": {"type": "LineString", "coordinates": raw_llh.tolist()},
            },
        ],
    }
    _json_dump(data_dir / "trajectory.geojson", geojson)

    residual_step = max(1, len(frames) // 250)
    residuals = [
        [
            trajectory.gps_positions_enu_m[index].round(4).tolist(),
            trajectory.positions_enu_m[index].round(4).tolist(),
        ]
        for index in range(0, len(frames), residual_step)
    ]
    browser = {
        "coordinate_system": "local_ENU_m",
        "imu_used": False,
        "fused": _float_list(trajectory.positions_enu_m),
        "gps": _float_list(trajectory.gps_positions_enu_m),
        "pre_graph_gps_aided": _float_list(trajectory.raw_visual_positions_enu_m),
        "residuals": residuals,
        "methods": list(trajectory.methods),
        "fix_quality": gps.fix_quality.astype(int).tolist(),
    }
    _json_dump(data_dir / "trajectory.json", browser)


def write_odometry_diagnostics(
    path: Path,
    frames: list[FrameRecord],
    gps: InterpolatedGps,
    trajectory: TrajectoryResult,
    odometry: list[OdometryResult],
) -> None:
    fieldnames = [
        "frame_index",
        "source_frame_index",
        "time_delta_s",
        "gps_horizontal_delta_m",
        "estimator_success",
        "estimator_method",
        "pose_graph_method",
        "matches",
        "inliers",
        "inlier_ratio",
        "translation_norm_m",
        "translation_to_gps_ratio",
        "rotation_deg",
        "reprojection_error_px",
        "reason",
    ]
    gps_delta = np.linalg.norm(
        np.diff(trajectory.gps_positions_enu_m[:, :2], axis=0), axis=1
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, estimate in enumerate(odometry):
            if index == 0:
                time_delta_s = 0.0
                gps_distance = 0.0
            else:
                time_delta_s = float(gps.monotonic_ns[index] - gps.monotonic_ns[index - 1]) / 1e9
                gps_distance = float(gps_delta[index - 1])
            writer.writerow(
                {
                    "frame_index": index,
                    "source_frame_index": frames[index].source_index,
                    "time_delta_s": time_delta_s,
                    "gps_horizontal_delta_m": gps_distance,
                    "estimator_success": estimate.success,
                    "estimator_method": estimate.method,
                    "pose_graph_method": trajectory.methods[index],
                    "matches": estimate.matches,
                    "inliers": estimate.inliers,
                    "inlier_ratio": estimate.inlier_ratio,
                    "translation_norm_m": estimate.translation_norm_m,
                    "translation_to_gps_ratio": (
                        estimate.translation_norm_m / gps_distance
                        if gps_distance > 1e-9
                        else ""
                    ),
                    "rotation_deg": estimate.rotation_deg,
                    "reprojection_error_px": (
                        estimate.reprojection_error_px
                        if estimate.reprojection_error_px is not None
                        else ""
                    ),
                    "reason": estimate.reason,
                }
            )


def export_mapping(
    output_dir: Path,
    viewer_source_dir: Path,
    frames: list[FrameRecord],
    gps: InterpolatedGps,
    origin: LocalENU,
    trajectory: TrajectoryResult,
    odometry: list[OdometryResult],
    cloud: PointCloudResult | None,
    browser_max_points: int,
    parameters: dict,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    write_trajectory_files(data_dir, frames, gps, origin, trajectory)
    write_odometry_diagnostics(
        data_dir / "odometry.csv", frames, gps, trajectory, odometry
    )

    cloud_summary: dict[str, object]
    if cloud is not None:
        browser_point_count = write_browser_points(
            data_dir / "points.bin", cloud, browser_max_points
        )
        write_ply(data_dir / "cloud_enu.ply", cloud, origin)
        minimum = np.min(cloud.points_enu_m, axis=0)
        maximum = np.max(cloud.points_enu_m, axis=0)
        cloud_summary = {
            "point_count": int(browser_point_count),
            "browser_point_count": int(browser_point_count),
            "ply_point_count": int(len(cloud.points_enu_m)),
            "sampled_frame_count": int(cloud.sampled_frame_count),
            "decoded_frame_count": int(cloud.decoded_frame_count),
            "valid_depth_sample_count_before_voxel": int(cloud.valid_depth_sample_count),
            "bbox_enu_min_m": minimum.tolist(),
            "bbox_enu_max_m": maximum.tolist(),
            "browser_binary": "points.bin",
            "ply": "cloud_enu.ply",
        }
    else:
        cloud_summary = {"point_count": 0, "trajectory_only": True}

    summary = {
        "format_version": 1,
        "imu_used": False,
        "input_whitelist": [
            "metadata.json",
            "timestamps.csv",
            "gps.csv",
            "rgb/*.jpg",
            "depth_mm/*.png",
        ],
        "explicitly_excluded": ["imu.csv", "imu_events.csv", "external_imu.csv"],
        "frame_count": len(frames),
        "origin": {
            "longitude_deg": origin.origin_longitude_deg,
            "latitude_deg": origin.origin_latitude_deg,
            "ellipsoid_height_m": origin.origin_ellipsoid_height_m,
            "vertical_datum_note": (
                "NMEA GGA MSL altitude plus geoid separation was converted to ellipsoid height."
            ),
        },
        "trajectory": trajectory.metrics,
        "odometry_diagnostics": "odometry.csv",
        "cloud": cloud_summary,
        "parameters": parameters,
        "accuracy_note": (
            "This result is IMU-free. GPS is not survey ground truth and camera/GNSS lever arm "
            "defaults to zero unless configured. Judge absolute fit against visible road/building "
            "features and independently surveyed control points."
        ),
    }
    _json_dump(data_dir / "summary.json", summary)
    _json_dump(data_dir / "accuracy_report.json", trajectory.metrics)

    viewer_target = output_dir / "viewer"
    shutil.copytree(viewer_source_dir, viewer_target, dirs_exist_ok=True)
    return summary
