from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .dataset import CameraModel, FrameRecord
from .trajectory import TrajectoryResult


POSE_FORMAT_VERSION = 1
POSE_METHOD_CODES: dict[str, int] = {
    "origin": 0,
    "pnp": 1,
    "essential_gps_scale": 2,
    "gps_course": 3,
    "gps_fallback": 4,
    "time_gap_fallback": 5,
    "gps_vector_rejected": 6,
}


def repository_commit_sha(repository_root: Path) -> str:
    """Return the mapping implementation SHA without making export depend on Git."""

    configured = os.getenv("DEPTH_MAP_COMMIT_SHA", "").strip()
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = result.stdout.strip()
    return value if len(value) == 40 else "unknown"


def camera_model_name(metadata: Mapping[str, Any]) -> str:
    camera = metadata.get("camera_model")
    if isinstance(camera, Mapping):
        for key in ("model", "name", "device_model", "product_name"):
            value = camera.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("camera_model_name", "device_model", "device_name"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _validate_transform(name: str, value: np.ndarray) -> np.ndarray:
    matrix = np.asarray(value, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
        raise ValueError(f"{name} must be a finite 4x4 matrix")
    if not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0], atol=1e-12):
        raise ValueError(f"{name} must have a homogeneous final row")
    rotation = matrix[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6):
        raise ValueError(f"{name} rotation must be orthonormal")
    if not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-6):
        raise ValueError(f"{name} rotation determinant must be +1")
    return matrix


def build_camera_pose_payload(
    frames: Sequence[FrameRecord],
    trajectory: TrajectoryResult,
    camera: CameraModel,
    *,
    pose_quality_score: np.ndarray | None = None,
    camera_offset_right_m: float = 0.0,
    camera_offset_down_m: float = 0.0,
    camera_offset_forward_m: float = 0.0,
    depth_intrinsics: np.ndarray | None = None,
    rgb_to_depth_transform: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Adapt the in-memory trajectory to the versioned road-analysis pose contract.

    ``T_enu_camera`` maps optical camera XYZ (right, down, forward) to local ENU.
    The GNSS-to-camera offset is expressed in those camera axes. The exported
    ``camera_to_gnss_transform`` maps camera coordinates into a GNSS-origin frame
    with parallel right/down/forward axes.
    """

    count = len(frames)
    positions = np.asarray(trajectory.positions_enu_m, dtype=np.float64)
    rotations = np.asarray(trajectory.rotations_enu_from_camera, dtype=np.float64)
    if positions.shape != (count, 3) or rotations.shape != (count, 3, 3):
        raise ValueError("trajectory positions/rotations must align with frames")
    if len(trajectory.methods) != count:
        raise ValueError("trajectory methods must align with frames")
    if not np.all(np.isfinite(positions)) or not np.all(np.isfinite(rotations)):
        raise ValueError("trajectory pose arrays must be finite")
    for rotation in rotations:
        _validate_transform(
            "trajectory rotation",
            np.block(
                [
                    [rotation, np.zeros((3, 1), dtype=np.float64)],
                    [np.zeros((1, 3), dtype=np.float64), np.ones((1, 1))],
                ]
            ),
        )

    offset = np.asarray(
        [camera_offset_right_m, camera_offset_down_m, camera_offset_forward_m],
        dtype=np.float64,
    )
    if not np.all(np.isfinite(offset)):
        raise ValueError("camera offset must be finite")
    camera_positions = positions + np.einsum("nij,j->ni", rotations, offset)
    transforms = np.repeat(np.eye(4, dtype=np.float64)[None, :, :], count, axis=0)
    transforms[:, :3, :3] = rotations
    transforms[:, :3, 3] = camera_positions

    quality = (
        np.ones(count, dtype=np.float32)
        if pose_quality_score is None
        else np.asarray(pose_quality_score, dtype=np.float32)
    )
    if quality.shape != (count,) or np.any(~np.isfinite(quality)):
        raise ValueError("pose_quality_score must be finite and align with frames")
    if np.any((quality < 0.0) | (quality > 1.0)):
        raise ValueError("pose_quality_score must be in [0, 1]")

    rgb_intrinsics = camera.matrix
    depth_matrix = (
        rgb_intrinsics.copy()
        if depth_intrinsics is None
        else np.asarray(depth_intrinsics, dtype=np.float64)
    )
    if depth_matrix.shape != (3, 3) or not np.all(np.isfinite(depth_matrix)):
        raise ValueError("depth_intrinsics must be a finite 3x3 matrix")
    rgb_to_depth = _validate_transform(
        "rgb_to_depth_transform",
        np.eye(4, dtype=np.float64)
        if rgb_to_depth_transform is None
        else rgb_to_depth_transform,
    )
    camera_to_gnss = np.eye(4, dtype=np.float64)
    camera_to_gnss[:3, 3] = offset

    return {
        "format_version": np.asarray(POSE_FORMAT_VERSION, dtype=np.uint16),
        "frame_index": np.arange(count, dtype=np.int32),
        "source_frame_index": np.asarray(
            [frame.source_index for frame in frames], dtype=np.int32
        ),
        "timestamp_monotonic_ns": np.asarray(
            [frame.monotonic_ns for frame in frames], dtype=np.int64
        ),
        "T_enu_camera": transforms,
        "pose_quality_score": quality,
        "pose_method_code": np.asarray(
            [POSE_METHOD_CODES.get(method, 255) for method in trajectory.methods],
            dtype=np.uint8,
        ),
        "rgb_intrinsics": rgb_intrinsics,
        "depth_intrinsics": depth_matrix,
        "rgb_to_depth_transform": rgb_to_depth,
        "camera_to_gnss_transform": camera_to_gnss,
    }


def _atomic_savez(path: Path, payload: Mapping[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp.npz")
    arrays = {name: np.asarray(value) for name, value in payload.items()}
    if any(value.dtype.hasobject for value in arrays.values()):
        raise ValueError("camera pose archive cannot contain object arrays")
    with temporary.open("wb") as stream:
        np.savez_compressed(stream, **arrays)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def write_camera_pose_bundle(
    data_dir: Path,
    payload: Mapping[str, np.ndarray],
    manifest: Mapping[str, Any],
) -> tuple[Path, Path]:
    pose_path = data_dir / "camera_poses.npz"
    manifest_path = data_dir / "analysis_source_manifest.json"
    _atomic_savez(pose_path, payload)
    _atomic_json(manifest_path, manifest)
    return pose_path, manifest_path


def build_analysis_source_manifest(
    *,
    dataset_id: str,
    mapping_commit_sha: str,
    camera_model: str,
    camera_height_m: float | None,
    mount_yaw_deg: float,
    mount_pitch_deg: float,
    mount_roll_deg: float,
    camera_offset_right_m: float,
    camera_offset_down_m: float,
    camera_offset_forward_m: float,
    rgb_depth_alignment: str,
    calibration_status: str,
) -> dict[str, Any]:
    if calibration_status not in {"unknown", "measured", "estimated"}:
        raise ValueError("calibration_status must be unknown, measured, or estimated")
    if calibration_status == "measured" and camera_height_m is None:
        raise ValueError("measured calibration requires camera_height_m")
    if calibration_status == "measured" and camera_model.strip().lower() == "unknown":
        raise ValueError("measured calibration requires an exact camera_model")
    measured = calibration_status != "unknown"
    return {
        "format_version": 1,
        "dataset_id": dataset_id,
        "mapping_commit_sha": mapping_commit_sha,
        "camera_model": camera_model,
        "camera_height_m": float(camera_height_m) if camera_height_m is not None else None,
        "mount_yaw_deg": float(mount_yaw_deg) if measured else None,
        "mount_pitch_deg": float(mount_pitch_deg) if measured else None,
        "mount_roll_deg": float(mount_roll_deg) if measured else None,
        "camera_offset_right_m": float(camera_offset_right_m) if measured else None,
        "camera_offset_down_m": float(camera_offset_down_m) if measured else None,
        "camera_offset_forward_m": float(camera_offset_forward_m) if measured else None,
        "rgb_depth_alignment": rgb_depth_alignment,
        "timestamp_basis": "monotonic_ns",
        "calibration_status": calibration_status,
        "manual_review_required": calibration_status != "measured",
        "axis_convention": {
            "camera": "optical XYZ: right(+X), down(+Y), forward(+Z)",
            "world": "local ENU metres: east(+X), north(+Y), up(+Z)",
            "transform": "T_target_source left-multiplies homogeneous column vectors",
            "lever_arm": "GNSS origin to camera origin in camera right/down/forward axes",
        },
    }
