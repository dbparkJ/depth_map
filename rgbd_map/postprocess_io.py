from __future__ import annotations

import json
import os
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np

from .exporters import read_ply, write_browser_points_arrays, write_ply
from .geodesy import LocalENU
from .pointcloud import PointCloudResult


_BROWSER_HEADER = struct.Struct("<4sIII")


@dataclass(frozen=True)
class RawCloudBundle:
    points_enu_m: np.ndarray
    colors_rgb: np.ndarray
    metadata: dict[str, np.ndarray]
    origin: LocalENU
    trajectory_xyz: np.ndarray
    summary: dict


def atomic_json_dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_savez_compressed(path: Path, **arrays: np.ndarray) -> None:
    """Write a numeric-only NPZ without exposing a partial final archive."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.stem}.tmp{path.suffix}")
    checked: dict[str, np.ndarray] = {}
    for name, values in arrays.items():
        array = np.asarray(values)
        if array.dtype.hasobject:
            raise ValueError(f"NPZ array {name!r} must not use object dtype")
        checked[name] = array
    np.savez_compressed(temporary, **checked)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp")
    with source.open("rb") as input_handle, temporary.open("wb") as output_handle:
        shutil.copyfileobj(input_handle, output_handle, length=8 * 1024 * 1024)
        output_handle.flush()
        os.fsync(output_handle.fileno())
    temporary.replace(destination)


def cloud_metadata_arrays(cloud: PointCloudResult) -> dict[str, np.ndarray]:
    count = len(cloud.points_enu_m)

    def required(name: str, dtype: np.dtype) -> np.ndarray:
        value = getattr(cloud, name, None)
        if value is None:
            # Compatibility for callers constructing the pre-metadata result type.
            if name in {"observation_count", "distinct_frame_count"}:
                return np.ones(count, dtype=dtype)
            return np.zeros(count, dtype=dtype)
        array = np.asarray(value, dtype=dtype)
        if array.shape != (count,):
            raise ValueError(f"cloud metadata {name} must have shape ({count},)")
        return array

    metadata = {
        "observation_count": required("observation_count", np.uint32),
        "distinct_frame_count": required("distinct_frame_count", np.uint32),
        "position_std_m": required("position_std_m", np.float32),
        "mean_depth_m": required("mean_depth_m", np.float32),
    }
    for name, dtype in (
        ("depth_min_m", np.float32),
        ("depth_max_m", np.float32),
        ("depth_edge_pass_count", np.uint32),
    ):
        value = getattr(cloud, name, None)
        if value is not None:
            array = np.asarray(value, dtype=dtype)
            if array.shape != (count,):
                raise ValueError(f"cloud metadata {name} must have shape ({count},)")
            metadata[name] = array
    for name, dtype in (
        ("support_observation_count", np.uint32),
        ("support_distinct_frame_count", np.uint16),
        ("independent_view_count", np.uint16),
        ("support_time_span_s", np.float32),
        ("support_path_span_m", np.float32),
        ("support_position_std_m", np.float32),
        ("support_depth_std_m", np.float32),
        ("temporal_test_count", np.uint16),
        ("temporal_support_count", np.uint16),
        ("temporal_contradiction_count", np.uint16),
        ("far_depth_risk_count", np.uint32),
        ("source_frame_id", np.int32),
        ("mean_source_time_s", np.float64),
        ("pose_quality_score", np.float32),
    ):
        value = getattr(cloud, name, None)
        if value is not None:
            array = np.asarray(value, dtype=dtype)
            if array.shape != (count,):
                raise ValueError(f"cloud metadata {name} must have shape ({count},)")
            metadata[name] = array
    # source_voxel_key is derivable from XYZ and voxel size and can add 24 bytes
    # per dense point. Keep it in PointCloudResult for in-process alignment tests,
    # but omit the optional redundant array from the required rerun archive.
    return metadata


def write_raw_cloud_bundle(
    data_dir: Path,
    cloud: PointCloudResult,
    origin: LocalENU,
    *,
    postprocess_preset: str,
) -> dict[str, Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    raw_ply = data_dir / "cloud_raw_enu.ply"
    write_ply(
        raw_ply,
        cloud.points_enu_m,
        cloud.colors_rgb,
        origin,
        comments={
            "pointcloud_stage": "fused_prefiltered_raw",
            "pointcloud_format_version": "2",
            "postprocess_preset": postprocess_preset,
        },
    )
    metadata_path = data_dir / "cloud_raw_metadata.npz"
    atomic_savez_compressed(metadata_path, **cloud_metadata_arrays(cloud))
    return {"raw_ply": raw_ply, "raw_metadata": metadata_path}


def _validate_point_arrays(
    points: np.ndarray, colors: np.ndarray, *, name: str
) -> tuple[np.ndarray, np.ndarray]:
    points = np.asarray(points)
    colors = np.asarray(colors)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError(f"{name} points must have shape (N, 3)")
    if colors.shape != points.shape:
        raise ValueError(f"{name} colors must have shape (N, 3)")
    return points, colors


def write_processed_cloud_products(
    data_dir: Path,
    *,
    raw_points: np.ndarray,
    raw_colors: np.ndarray,
    clean_points: np.ndarray,
    clean_colors: np.ndarray,
    removed_points: np.ndarray,
    removed_diagnostic_colors: np.ndarray,
    removed_original_colors: np.ndarray,
    removal_reason_bits: np.ndarray,
    primary_reason: np.ndarray,
    origin: LocalENU,
    postprocess_preset: str,
    browser_max_points: int,
    keep_raw_cloud: bool = True,
    save_removed_cloud: bool = True,
    reuse_existing_raw_products: bool = False,
) -> dict[str, object]:
    """Write raw/clean/removed products and return summary-ready counts."""

    data_dir.mkdir(parents=True, exist_ok=True)
    raw_points, raw_colors = _validate_point_arrays(
        raw_points, raw_colors, name="raw"
    )
    clean_points, clean_colors = _validate_point_arrays(
        clean_points, clean_colors, name="clean"
    )
    removed_points, removed_diagnostic_colors = _validate_point_arrays(
        removed_points, removed_diagnostic_colors, name="removed"
    )
    removed_original_colors = np.asarray(removed_original_colors)
    removal_reason_bits = np.asarray(removal_reason_bits, dtype=np.uint16)
    primary_reason = np.asarray(primary_reason, dtype=np.uint16)
    removed_count = len(removed_points)
    if removed_original_colors.shape != (removed_count, 3):
        raise ValueError("removed original RGB must align with removed points")
    if removal_reason_bits.shape != (removed_count,):
        raise ValueError("removal reason bits must align with removed points")
    if primary_reason.shape != (removed_count,):
        raise ValueError("primary reason must align with removed points")
    if len(raw_points) != len(clean_points) + removed_count:
        raise ValueError("raw point count must equal clean plus removed point counts")

    clean_ply = data_dir / "cloud_clean_enu.ply"
    write_ply(
        clean_ply,
        clean_points,
        clean_colors,
        origin,
        comments={
            "pointcloud_stage": "clean",
            "postprocess_preset": postprocess_preset,
        },
    )
    atomic_copy(clean_ply, data_dir / "cloud_enu.ply")

    raw_ply = data_dir / "cloud_raw_enu.ply"
    if keep_raw_cloud and not raw_ply.exists():
        write_ply(
            raw_ply,
            raw_points,
            raw_colors,
            origin,
            comments={
                "pointcloud_stage": "raw",
                "postprocess_preset": postprocess_preset,
            },
        )

    removed_ply = data_dir / "cloud_removed_enu.ply"
    removed_metadata = data_dir / "removed_points_metadata.npz"
    if save_removed_cloud:
        write_ply(
            removed_ply,
            removed_points,
            removed_diagnostic_colors,
            origin,
            comments={
                "pointcloud_stage": "removed",
                "postprocess_preset": postprocess_preset,
                "colors": "primary_removal_reason",
            },
        )
        atomic_savez_compressed(
            removed_metadata,
            points_xyz=np.asarray(removed_points, dtype=np.float32),
            original_rgb=np.asarray(removed_original_colors, dtype=np.uint8),
            removal_reason_bits=removal_reason_bits,
            primary_reason=primary_reason,
        )

    raw_browser_limit = min(400_000, browser_max_points)
    removed_browser_limit = min(300_000, browser_max_points)
    raw_browser_path = data_dir / "points_raw.bin"
    raw_browser_count = 0
    if keep_raw_cloud:
        if reuse_existing_raw_products and raw_browser_path.is_file():
            raw_browser_count = int(validate_browser_binary(raw_browser_path)["count"])
        else:
            raw_browser_count = write_browser_points_arrays(
                raw_browser_path, raw_points, raw_colors, raw_browser_limit
            )
    clean_browser_path = data_dir / "points_clean.bin"
    clean_browser_count = write_browser_points_arrays(
        clean_browser_path, clean_points, clean_colors, browser_max_points
    )
    removed_browser_count = 0
    if save_removed_cloud:
        removed_browser_count = write_browser_points_arrays(
            data_dir / "points_removed.bin",
            removed_points,
            removed_diagnostic_colors,
            removed_browser_limit,
        )
    atomic_copy(clean_browser_path, data_dir / "points.bin")

    raw_count = len(raw_points)
    clean_count = len(clean_points)
    removal_ratio = float(removed_count / raw_count) if raw_count else 0.0
    result: dict[str, object] = {
        "raw_point_count": int(raw_count),
        "clean_point_count": int(clean_count),
        "removed_point_count": int(removed_count),
        "removal_ratio": removal_ratio,
        "point_count": int(clean_count),
        "ply_point_count": int(clean_count),
        "browser_point_count": int(clean_browser_count),
        "raw_browser_point_count": int(raw_browser_count),
        "clean_browser_point_count": int(clean_browser_count),
        "removed_browser_point_count": int(removed_browser_count),
        "raw_ply": "cloud_raw_enu.ply" if keep_raw_cloud else None,
        "clean_ply": "cloud_clean_enu.ply",
        "removed_ply": "cloud_removed_enu.ply" if save_removed_cloud else None,
        "ply": "cloud_enu.ply",
        "raw_browser_binary": "points_raw.bin" if keep_raw_cloud else None,
        "clean_browser_binary": "points_clean.bin",
        "removed_browser_binary": "points_removed.bin" if save_removed_cloud else None,
        "browser_binary": "points_clean.bin",
    }
    if clean_count:
        result["bbox_enu_min_m"] = np.min(clean_points, axis=0).tolist()
        result["bbox_enu_max_m"] = np.max(clean_points, axis=0).tolist()
    return result


def validate_browser_binary(path: Path, expected_count: int | None = None) -> dict:
    size = path.stat().st_size
    if size < _BROWSER_HEADER.size:
        raise ValueError(f"browser point file is too short: {path}")
    with path.open("rb") as handle:
        magic, version, count, stride = _BROWSER_HEADER.unpack(handle.read(16))
    if magic != b"RGBD" or version != 1 or stride != 16:
        raise ValueError(
            f"invalid browser header for {path}: "
            f"magic={magic!r}, version={version}, stride={stride}"
        )
    expected_size = 16 + count * stride
    if size != expected_size:
        raise ValueError(
            f"browser point file size mismatch for {path}: "
            f"expected {expected_size}, got {size}"
        )
    if expected_count is not None and count != expected_count:
        raise ValueError(
            f"browser point count mismatch for {path}: "
            f"expected {expected_count}, got {count}"
        )
    return {"magic": "RGBD", "version": version, "count": count, "stride": stride}


def _load_numeric_npz(
    path: Path, names: set[str] | None = None
) -> dict[str, np.ndarray]:
    result: dict[str, np.ndarray] = {}
    with np.load(path, allow_pickle=False) as archive:
        selected_names = archive.files if names is None else sorted(names)
        missing = set(selected_names) - set(archive.files)
        if missing:
            raise ValueError(
                f"raw metadata is missing arrays: {', '.join(sorted(missing))}"
            )
        for name in selected_names:
            value = np.asarray(archive[name])
            if value.dtype.hasobject:
                raise ValueError(f"metadata {name!r} unexpectedly uses object dtype")
            result[name] = value
    return result


def _origin_from_summary(summary: Mapping[str, object]) -> LocalENU:
    value = summary.get("origin")
    if not isinstance(value, Mapping):
        raise ValueError("summary.json does not contain an origin object")
    return LocalENU(
        origin_longitude_deg=float(value["longitude_deg"]),
        origin_latitude_deg=float(value["latitude_deg"]),
        origin_ellipsoid_height_m=float(value["ellipsoid_height_m"]),
    )


def _trajectory_from_json(path: Path) -> np.ndarray:
    payload = json.loads(path.read_text(encoding="utf-8"))
    fused = np.asarray(payload.get("fused"), dtype=np.float64)
    if fused.ndim != 2 or fused.shape[1:] != (3,) or len(fused) == 0:
        raise ValueError("trajectory.json fused points must have shape (N, 3)")
    return fused


def load_raw_cloud_bundle(output_dir: Path) -> RawCloudBundle:
    data_dir = output_dir.expanduser().resolve() / "data"
    required = {
        "raw PLY": data_dir / "cloud_raw_enu.ply",
        "raw metadata": data_dir / "cloud_raw_metadata.npz",
        "trajectory": data_dir / "trajectory.json",
        "summary": data_dir / "summary.json",
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "postprocess-only input is incomplete; missing " + ", ".join(missing)
        )
    summary = json.loads(required["summary"].read_text(encoding="utf-8"))
    points, colors, _comments = read_ply(required["raw PLY"])
    required_names = {
        "observation_count",
        "distinct_frame_count",
        "position_std_m",
        "mean_depth_m",
    }
    # Build-only diagnostic arrays may also be present in the archive. Loading
    # just the four filter inputs avoids hundreds of MB of unnecessary dense-run
    # memory while preserving those extras on disk.
    optional_names = {
        "support_observation_count",
        "support_distinct_frame_count",
        "independent_view_count",
        "support_time_span_s",
        "support_path_span_m",
        "support_position_std_m",
        "support_depth_std_m",
        "temporal_test_count",
        "temporal_support_count",
        "temporal_contradiction_count",
        "far_depth_risk_count",
        "source_frame_id",
        "mean_source_time_s",
        "pose_quality_score",
    }
    with np.load(required["raw metadata"], allow_pickle=False) as archive:
        available_names = set(archive.files)
    metadata = _load_numeric_npz(
        required["raw metadata"],
        required_names | (optional_names & available_names),
    )
    for name, values in metadata.items():
        expected_shape = (len(points),)
        if values.shape != expected_shape:
            raise ValueError(
                f"raw metadata {name!r} must have shape {expected_shape}, "
                f"got {values.shape}"
            )
    return RawCloudBundle(
        points_enu_m=points,
        colors_rgb=colors,
        metadata=metadata,
        origin=_origin_from_summary(summary),
        trajectory_xyz=_trajectory_from_json(required["trajectory"]),
        summary=summary,
    )
