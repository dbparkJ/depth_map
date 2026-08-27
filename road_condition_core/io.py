from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


_PLY_DTYPE = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("r", "u1"),
        ("g", "u1"),
        ("b", "u1"),
    ]
)

_STAGE_FILENAMES = {
    "raw": "cloud_raw_enu.ply",
    "clean": "cloud_clean_enu.ply",
    "removed": "cloud_removed_enu.ply",
}


@dataclass(frozen=True)
class CameraPoseBundle:
    frame_index: np.ndarray
    source_frame_index: np.ndarray
    timestamp_monotonic_ns: np.ndarray
    T_enu_camera: np.ndarray
    pose_quality_score: np.ndarray
    pose_method_code: np.ndarray
    rgb_intrinsics: np.ndarray
    depth_intrinsics: np.ndarray
    rgb_to_depth_transform: np.ndarray
    camera_to_gnss_transform: np.ndarray


@dataclass(frozen=True)
class MappingBundle:
    points_enu_m: np.ndarray
    colors_rgb: np.ndarray
    trajectory_enu_m: np.ndarray
    point_metadata: dict[str, np.ndarray]
    summary: dict[str, Any]
    source_path: Path
    camera_poses: CameraPoseBundle | None
    analysis_source_manifest: dict[str, Any] | None
    analysis_capabilities: dict[str, Any]
    analysis_quality: dict[str, Any]


def _read_ply_header(path: Path) -> tuple[int, int, list[str]]:
    lines: list[str] = []
    count: int | None = None
    with path.open("rb") as stream:
        while True:
            raw = stream.readline()
            if not raw:
                raise ValueError(f"PLY header is incomplete: {path}")
            try:
                line = raw.decode("ascii").rstrip("\r\n")
            except UnicodeDecodeError as exc:
                raise ValueError(f"PLY header is not ASCII: {path}") from exc
            lines.append(line)
            if line.startswith("element vertex "):
                count = int(line.split()[-1])
            if line == "end_header":
                offset = stream.tell()
                break
    if not lines or lines[0] != "ply":
        raise ValueError(f"not a PLY file: {path}")
    if "format binary_little_endian 1.0" not in lines:
        raise ValueError("only binary_little_endian PLY 1.0 is supported")
    expected_properties = [
        "property float x",
        "property float y",
        "property float z",
        "property uchar red",
        "property uchar green",
        "property uchar blue",
    ]
    for property_line in expected_properties:
        if property_line not in lines:
            raise ValueError(f"PLY is missing {property_line!r}")
    if count is None or count < 0:
        raise ValueError("PLY vertex count is missing or invalid")
    expected_size = offset + count * _PLY_DTYPE.itemsize
    actual_size = path.stat().st_size
    if expected_size != actual_size:
        raise ValueError(
            f"PLY size mismatch for {path}: expected {expected_size}, got {actual_size}"
        )
    return count, offset, lines


def read_depth_map_ply(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Read the fixed XYZ/RGB PLY layout produced by ``depth_map``.

    The function intentionally rejects arbitrary PLY variants instead of guessing
    property order. This protects large offline jobs from silently interpreting a
    different binary layout as valid XYZ/RGB data.
    """

    resolved = Path(path).expanduser().resolve()
    count, offset, _lines = _read_ply_header(resolved)
    if count == 0:
        return (
            np.empty((0, 3), dtype=np.float32),
            np.empty((0, 3), dtype=np.uint8),
        )
    records = np.memmap(
        resolved,
        mode="r",
        dtype=_PLY_DTYPE,
        offset=offset,
        shape=(count,),
    )
    points = np.column_stack((records["x"], records["y"], records["z"])).astype(
        np.float32,
        copy=False,
    )
    colors = np.column_stack((records["r"], records["g"], records["b"])).astype(
        np.uint8,
        copy=False,
    )
    del records
    return np.asarray(points), np.asarray(colors)


def _load_numeric_npz(path: Path, expected_count: int) -> dict[str, np.ndarray]:
    if not path.is_file():
        return {}
    result: dict[str, np.ndarray] = {}
    with np.load(path, allow_pickle=False) as archive:
        for name in archive.files:
            value = np.asarray(archive[name])
            if value.dtype.hasobject:
                raise ValueError(f"metadata array {name!r} uses object dtype")
            if value.shape != (expected_count,):
                # Diagnostic-only arrays that are not point aligned are ignored.
                continue
            result[name] = value
    return result


def _finite_matrix(name: str, value: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape or not np.all(np.isfinite(array)):
        raise ValueError(f"camera pose field {name!r} must have shape {shape} and be finite")
    return array


def _validate_homogeneous_transforms(name: str, matrices: np.ndarray) -> None:
    if not np.allclose(matrices[..., 3, :], [0.0, 0.0, 0.0, 1.0], atol=1e-12):
        raise ValueError(f"camera pose field {name!r} has invalid homogeneous rows")
    rotations = matrices[..., :3, :3].reshape(-1, 3, 3)
    products = np.matmul(np.swapaxes(rotations, 1, 2), rotations)
    if not np.allclose(products, np.eye(3), atol=1e-6):
        raise ValueError(f"camera pose field {name!r} rotations are not orthonormal")
    if not np.allclose(np.linalg.det(rotations), 1.0, atol=1e-6):
        raise ValueError(f"camera pose field {name!r} rotation determinant must be +1")


def _load_camera_poses(path: Path) -> CameraPoseBundle:
    required = {
        "format_version",
        "frame_index",
        "source_frame_index",
        "timestamp_monotonic_ns",
        "T_enu_camera",
        "pose_quality_score",
        "pose_method_code",
        "rgb_intrinsics",
        "depth_intrinsics",
        "rgb_to_depth_transform",
        "camera_to_gnss_transform",
    }
    with np.load(path, allow_pickle=False) as archive:
        missing = required - set(archive.files)
        if missing:
            raise ValueError(
                "camera_poses.npz is missing fields: " + ", ".join(sorted(missing))
            )
        version = np.asarray(archive["format_version"])
        if version.shape != () or int(version) != 1:
            raise ValueError("camera_poses.npz format_version must be scalar 1")
        frame_index = np.asarray(archive["frame_index"], dtype=np.int32)
        if frame_index.ndim != 1:
            raise ValueError("camera pose frame_index must be a one-dimensional array")
        count = len(frame_index)
        if frame_index.shape != (count,) or not np.array_equal(
            frame_index, np.arange(count, dtype=np.int32)
        ):
            raise ValueError("camera pose frame_index must be contiguous from zero")
        source_index = np.asarray(archive["source_frame_index"], dtype=np.int32)
        timestamp = np.asarray(archive["timestamp_monotonic_ns"], dtype=np.int64)
        quality = np.asarray(archive["pose_quality_score"], dtype=np.float32)
        method = np.asarray(archive["pose_method_code"], dtype=np.uint8)
        for name, value in (
            ("source_frame_index", source_index),
            ("timestamp_monotonic_ns", timestamp),
            ("pose_quality_score", quality),
            ("pose_method_code", method),
        ):
            if value.shape != (count,):
                raise ValueError(f"camera pose field {name!r} must align with frame_index")
        if np.any(~np.isfinite(quality)) or np.any((quality < 0.0) | (quality > 1.0)):
            raise ValueError("camera pose quality scores must be finite and in [0, 1]")
        transforms = _finite_matrix(
            "T_enu_camera", archive["T_enu_camera"], (count, 4, 4)
        )
        _validate_homogeneous_transforms("T_enu_camera", transforms)
        rgb_intrinsics = _finite_matrix(
            "rgb_intrinsics", archive["rgb_intrinsics"], (3, 3)
        )
        depth_intrinsics = _finite_matrix(
            "depth_intrinsics", archive["depth_intrinsics"], (3, 3)
        )
        rgb_to_depth = _finite_matrix(
            "rgb_to_depth_transform", archive["rgb_to_depth_transform"], (4, 4)
        )
        _validate_homogeneous_transforms("rgb_to_depth_transform", rgb_to_depth)
        camera_to_gnss = _finite_matrix(
            "camera_to_gnss_transform",
            archive["camera_to_gnss_transform"],
            (4, 4),
        )
        _validate_homogeneous_transforms("camera_to_gnss_transform", camera_to_gnss)
    return CameraPoseBundle(
        frame_index=frame_index,
        source_frame_index=source_index,
        timestamp_monotonic_ns=timestamp,
        T_enu_camera=transforms,
        pose_quality_score=quality,
        pose_method_code=method,
        rgb_intrinsics=rgb_intrinsics,
        depth_intrinsics=depth_intrinsics,
        rgb_to_depth_transform=rgb_to_depth,
        camera_to_gnss_transform=camera_to_gnss,
    )


def _load_analysis_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        raise ValueError("analysis_source_manifest.json must be a format_version=1 object")
    status = payload.get("calibration_status")
    if status not in {"unknown", "measured", "estimated"}:
        raise ValueError("analysis source calibration_status is invalid")
    return payload


def load_mapping_bundle(
    output_dir: str | Path,
    *,
    stage: str = "raw",
) -> MappingBundle:
    resolved = Path(output_dir).expanduser().resolve()
    data_dir = resolved / "data"
    if stage not in _STAGE_FILENAMES:
        raise ValueError(f"stage must be one of: {', '.join(_STAGE_FILENAMES)}")
    required = {
        "point cloud": data_dir / _STAGE_FILENAMES[stage],
        "trajectory": data_dir / "trajectory.json",
        "summary": data_dir / "summary.json",
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError("mapping bundle is incomplete; missing " + ", ".join(missing))

    points, colors = read_depth_map_ply(required["point cloud"])
    trajectory_payload = json.loads(required["trajectory"].read_text(encoding="utf-8"))
    trajectory = np.asarray(trajectory_payload.get("fused"), dtype=np.float64)
    if trajectory.ndim != 2 or trajectory.shape[1:] != (3,) or len(trajectory) < 2:
        raise ValueError("trajectory.json fused must contain at least two XYZ points")
    trajectory = trajectory[np.all(np.isfinite(trajectory), axis=1)]
    if len(trajectory) < 2:
        raise ValueError("trajectory has fewer than two finite points")

    summary = json.loads(required["summary"].read_text(encoding="utf-8"))
    metadata_path = data_dir / "cloud_raw_metadata.npz"
    metadata = _load_numeric_npz(metadata_path, len(points)) if stage == "raw" else {}
    pose_path = data_dir / "camera_poses.npz"
    camera_poses = _load_camera_poses(pose_path) if pose_path.is_file() else None
    if camera_poses is not None and len(camera_poses.frame_index) != len(
        np.asarray(trajectory_payload.get("fused"))
    ):
        raise ValueError("camera pose frame count does not match trajectory.json")
    analysis_manifest = _load_analysis_manifest(
        data_dir / "analysis_source_manifest.json"
    )
    calibration_status = (
        str(analysis_manifest.get("calibration_status"))
        if analysis_manifest is not None
        else "unknown"
    )
    pose_available = camera_poses is not None
    manual_review = calibration_status != "measured"
    quality_flags: list[str] = []
    if not pose_available:
        quality_flags.append("ply_only_pose_unavailable")
    if manual_review:
        quality_flags.append("manual_review_required")
    quality = {
        "pose_file_available": pose_available,
        "pose_frame_count": int(len(camera_poses.frame_index)) if pose_available else 0,
        "pose_quality_p50": (
            float(np.median(camera_poses.pose_quality_score)) if pose_available else None
        ),
        "calibration_status": calibration_status,
        "manual_review_required": manual_review,
        "quality_flags": quality_flags,
    }
    return MappingBundle(
        points_enu_m=points,
        colors_rgb=colors,
        trajectory_enu_m=trajectory,
        point_metadata=metadata,
        summary=summary,
        source_path=resolved,
        camera_poses=camera_poses,
        analysis_source_manifest=analysis_manifest,
        analysis_capabilities={
            "camera_pose_contract": pose_available,
            "frame_reprojection_feature_flag": True,
            "ply_only_mode": True,
        },
        analysis_quality=quality,
    )


def resolve_relative_path(root: str | Path, user_path: str) -> Path:
    """Resolve a user-provided relative path under a configured read-only root."""

    if not user_path or user_path.strip() in {".", "./"}:
        candidate = Path(root).expanduser().resolve()
    else:
        raw = Path(user_path)
        if raw.is_absolute():
            raise ValueError("mapping_output_path must be relative to the workspace root")
        candidate = (Path(root).expanduser().resolve() / raw).resolve()
    root_resolved = Path(root).expanduser().resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("mapping_output_path escapes the workspace root") from exc
    return candidate
