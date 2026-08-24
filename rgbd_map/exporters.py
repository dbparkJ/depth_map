from __future__ import annotations

import csv
import json
import os
import shutil
import struct
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .dataset import FrameRecord, InterpolatedGps
from .geodesy import LocalENU
from .odometry import OdometryResult
from .pointcloud import PointCloudResult, spatially_sample_indices
from .trajectory import TrajectoryResult


_WRITE_CHUNK_POINTS = 250_000
_BROWSER_POINT_DTYPE = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("r", "u1"),
        ("g", "u1"),
        ("b", "u1"),
        ("a", "u1"),
    ]
)
_PLY_POINT_DTYPE = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("r", "u1"),
        ("g", "u1"),
        ("b", "u1"),
    ]
)


def _json_dump(path: Path, value: object) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _float_list(points: np.ndarray, decimals: int = 4) -> list[list[float]]:
    return np.round(points.astype(np.float64), decimals=decimals).tolist()


def _point_color_arrays(
    cloud_or_points: PointCloudResult | np.ndarray,
    colors_rgb: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    if isinstance(cloud_or_points, PointCloudResult):
        if colors_rgb is not None:
            raise ValueError("colors_rgb must be omitted for PointCloudResult input")
        points = np.asarray(cloud_or_points.points_enu_m)
        colors = np.asarray(cloud_or_points.colors_rgb)
    else:
        points = np.asarray(cloud_or_points)
        if colors_rgb is None:
            raise ValueError("colors_rgb is required for point-array input")
        colors = np.asarray(colors_rgb)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points must have shape (N, 3)")
    if colors.shape != points.shape:
        raise ValueError("colors_rgb must have shape (N, 3)")
    return points, colors


def write_browser_points_arrays(
    path: Path,
    points_enu_m: np.ndarray,
    colors_rgb: np.ndarray,
    max_points: int | None = None,
) -> int:
    points_all, colors_all = _point_color_arrays(points_enu_m, colors_rgb)
    total = len(points_all)
    if max_points is not None and max_points <= 0:
        raise ValueError("max_points must be positive when specified")
    keep = (
        spatially_sample_indices(points_all, max_points)
        if max_points is not None and total > max_points
        else None
    )
    count = total if keep is None else len(keep)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(
            struct.pack("<4sIII", b"RGBD", 1, count, _BROWSER_POINT_DTYPE.itemsize)
        )
        for begin in range(0, count, _WRITE_CHUNK_POINTS):
            end = min(begin + _WRITE_CHUNK_POINTS, count)
            source = slice(begin, end) if keep is None else keep[begin:end]
            points = points_all[source]
            colors = colors_all[source]
            records = np.empty(end - begin, dtype=_BROWSER_POINT_DTYPE)
            records["x"] = points[:, 0]
            records["y"] = points[:, 1]
            records["z"] = points[:, 2]
            records["r"] = colors[:, 0]
            records["g"] = colors[:, 1]
            records["b"] = colors[:, 2]
            records["a"] = 255
            records.tofile(handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    return count


def write_browser_points(
    path: Path,
    cloud: PointCloudResult,
    max_points: int | None = None,
) -> int:
    return write_browser_points_arrays(
        path,
        cloud.points_enu_m,
        cloud.colors_rgb,
        max_points,
    )


def write_ply(
    path: Path,
    cloud_or_points: PointCloudResult | np.ndarray,
    colors_or_origin: np.ndarray | LocalENU,
    origin: LocalENU | None = None,
    comments: dict[str, object] | None = None,
) -> None:
    if isinstance(cloud_or_points, PointCloudResult):
        if not isinstance(colors_or_origin, LocalENU) or origin is not None:
            raise ValueError("PointCloudResult form is write_ply(path, cloud, origin)")
        points, colors = _point_color_arrays(cloud_or_points)
        resolved_origin = colors_or_origin
    else:
        if origin is None or isinstance(colors_or_origin, LocalENU):
            raise ValueError(
                "array form is write_ply(path, points, colors, origin, comments)"
            )
        points, colors = _point_color_arrays(cloud_or_points, colors_or_origin)
        resolved_origin = origin
    count = len(points)
    extra_comments = "".join(
        f"comment {key} {value}\n" for key, value in (comments or {}).items()
    )
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"comment coordinate_system local_ENU\n"
        f"comment origin_lon_deg {resolved_origin.origin_longitude_deg:.12f}\n"
        f"comment origin_lat_deg {resolved_origin.origin_latitude_deg:.12f}\n"
        f"comment origin_ellipsoid_height_m {resolved_origin.origin_ellipsoid_height_m:.4f}\n"
        f"{extra_comments}"
        f"element vertex {count}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    ).encode("ascii")
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(header)
        for begin in range(0, count, _WRITE_CHUNK_POINTS):
            end = min(begin + _WRITE_CHUNK_POINTS, count)
            point_chunk = points[begin:end]
            color_chunk = colors[begin:end]
            records = np.empty(end - begin, dtype=_PLY_POINT_DTYPE)
            records["x"] = point_chunk[:, 0]
            records["y"] = point_chunk[:, 1]
            records["z"] = point_chunk[:, 2]
            records["r"] = color_chunk[:, 0]
            records["g"] = color_chunk[:, 1]
            records["b"] = color_chunk[:, 2]
            records.tofile(handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def read_ply(path: Path) -> tuple[np.ndarray, np.ndarray, dict[str, str]]:
    """Read the binary local-ENU PLY layout written by :func:`write_ply`."""

    header_lines: list[str] = []
    with path.open("rb") as handle:
        while True:
            line = handle.readline()
            if not line:
                raise ValueError(f"PLY header is incomplete: {path}")
            try:
                decoded = line.decode("ascii").rstrip("\n")
            except UnicodeDecodeError as exc:
                raise ValueError(f"PLY header is not ASCII: {path}") from exc
            header_lines.append(decoded)
            if decoded == "end_header":
                body_offset = handle.tell()
                break
    if not header_lines or header_lines[0] != "ply":
        raise ValueError(f"Not a PLY file: {path}")
    if "format binary_little_endian 1.0" not in header_lines:
        raise ValueError("Only binary_little_endian PLY version 1.0 is supported")
    count_lines = [line for line in header_lines if line.startswith("element vertex ")]
    if len(count_lines) != 1:
        raise ValueError("PLY must contain one vertex element")
    count = int(count_lines[0].split()[-1])
    expected_properties = [
        "property float x",
        "property float y",
        "property float z",
        "property uchar red",
        "property uchar green",
        "property uchar blue",
    ]
    for property_line in expected_properties:
        if property_line not in header_lines:
            raise ValueError(f"PLY is missing {property_line!r}")
    expected_size = body_offset + count * _PLY_POINT_DTYPE.itemsize
    if path.stat().st_size != expected_size:
        raise ValueError(
            f"PLY size mismatch: expected {expected_size}, got {path.stat().st_size}"
        )
    if count == 0:
        comments: dict[str, str] = {}
        for line in header_lines:
            if line.startswith("comment "):
                pieces = line.split(maxsplit=2)
                if len(pieces) == 3:
                    comments[pieces[1]] = pieces[2]
        return (
            np.empty((0, 3), dtype=np.float32),
            np.empty((0, 3), dtype=np.uint8),
            comments,
        )
    records = np.memmap(
        path,
        mode="r",
        dtype=_PLY_POINT_DTYPE,
        offset=body_offset,
        shape=(count,),
    )
    points = np.empty((count, 3), dtype=np.float32)
    colors = np.empty((count, 3), dtype=np.uint8)
    for axis, name in enumerate(("x", "y", "z")):
        points[:, axis] = records[name]
    for axis, name in enumerate(("r", "g", "b")):
        colors[:, axis] = records[name]
    comments: dict[str, str] = {}
    for line in header_lines:
        if line.startswith("comment "):
            pieces = line.split(maxsplit=2)
            if len(pieces) == 3:
                comments[pieces[1]] = pieces[2]
    del records
    return points, colors, comments


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


def _cloud_build_stats_summary(cloud: PointCloudResult) -> dict[str, int]:
    stats = getattr(cloud, "stats", None)
    if stats is not None:
        result = {key: int(value) for key, value in asdict(stats).items()}
    else:
        # Compatibility for PointCloudResult objects created with the pre-statistics API.
        ply_count = len(cloud.points_enu_m)
        valid_count = int(cloud.valid_depth_sample_count)
        result = {
            "total_frame_count": int(cloud.sampled_frame_count),
            "sampled_frame_count": int(cloud.sampled_frame_count),
            "decoded_frame_count": int(cloud.decoded_frame_count),
            "candidate_pixel_sample_count": valid_count,
            "valid_depth_sample_count": valid_count,
            "invalid_depth_sample_count": 0,
            "discarded_by_per_frame_cap": 0,
            "points_before_voxel": valid_count,
            "unique_voxel_count": ply_count,
            "discarded_by_voxel": max(0, valid_count - ply_count),
            "points_before_final_cap": ply_count,
            "discarded_by_final_cap": 0,
        }
    result["valid_depth_sample_count_before_voxel"] = result[
        "valid_depth_sample_count"
    ]
    result["unique_voxel_count_before_final_cap"] = result["unique_voxel_count"]
    return result


def cloud_build_stats_summary(cloud: PointCloudResult) -> dict[str, int]:
    """Return the build-stage fields used by legacy and postprocessed summaries."""

    return _cloud_build_stats_summary(cloud)


def _cloud_summary(
    cloud: PointCloudResult,
    browser_point_count: int,
) -> dict[str, object]:
    ply_point_count = len(cloud.points_enu_m)
    summary: dict[str, object] = {
        "point_count": int(ply_point_count),
        "ply_point_count": int(ply_point_count),
        "browser_point_count": int(browser_point_count),
        **_cloud_build_stats_summary(cloud),
        "browser_binary": "points.bin",
        "ply": "cloud_enu.ply",
    }
    if ply_point_count:
        summary["bbox_enu_min_m"] = np.min(cloud.points_enu_m, axis=0).tolist()
        summary["bbox_enu_max_m"] = np.max(cloud.points_enu_m, axis=0).tolist()
    return summary


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
    *,
    cloud_summary_override: dict[str, object] | None = None,
    write_support_files: bool = True,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    if write_support_files:
        write_trajectory_files(data_dir, frames, gps, origin, trajectory)
        write_odometry_diagnostics(
            data_dir / "odometry.csv", frames, gps, trajectory, odometry
        )

    cloud_summary: dict[str, object]
    if cloud_summary_override is not None:
        cloud_summary = dict(cloud_summary_override)
    elif cloud is not None:
        browser_point_count = write_browser_points(
            data_dir / "points.bin", cloud, browser_max_points
        )
        write_ply(data_dir / "cloud_enu.ply", cloud, origin)
        cloud_summary = _cloud_summary(cloud, browser_point_count)
    else:
        cloud_summary = {
            "point_count": 0,
            "ply_point_count": 0,
            "browser_point_count": 0,
            "trajectory_only": True,
        }

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
