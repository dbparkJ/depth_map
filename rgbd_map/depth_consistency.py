from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from .dataset import CameraModel


class TemporalDepthState(IntEnum):
    OCCLUDED_OR_UNKNOWN = 0
    SUPPORT = 1
    FREE_SPACE_CONTRADICTION = 2


@dataclass(frozen=True)
class TemporalConsistencyCounts:
    test_count: np.ndarray
    support_count: np.ndarray
    contradiction_count: np.ndarray


def classify_projective_depth(
    points_enu_m: np.ndarray,
    camera: CameraModel,
    neighbor_position_enu_m: np.ndarray,
    neighbor_rotation_enu_from_camera: np.ndarray,
    neighbor_depth_mm: np.ndarray,
    neighbor_valid_mask: np.ndarray | None = None,
    *,
    absolute_tolerance_m: float = 0.15,
    relative_tolerance_ratio: float = 0.02,
    chunk_size: int = 1_000_000,
) -> np.ndarray:
    """Classify visibility of ENU points in one neighboring RGB-D frame.

    A farther observed surface means the candidate location was measured as free
    space and is therefore a contradiction. A nearer surface is an occluder and
    is deliberately not evidence for deletion.
    """

    points = np.asarray(points_enu_m, dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_enu_m must have shape (N, 3)")
    position = np.asarray(neighbor_position_enu_m, dtype=np.float64)
    rotation = np.asarray(neighbor_rotation_enu_from_camera, dtype=np.float64)
    depth = np.asarray(neighbor_depth_mm)
    if position.shape != (3,) or rotation.shape != (3, 3):
        raise ValueError("neighbor pose has an invalid shape")
    if depth.shape != (camera.height, camera.width):
        raise ValueError("neighbor depth shape does not match the camera")
    if neighbor_valid_mask is None:
        valid_image = (
            np.isfinite(depth) & (depth != 0) & (depth != 65535)
        )
    else:
        valid_image = np.asarray(neighbor_valid_mask, dtype=bool)
        if valid_image.shape != depth.shape:
            raise ValueError("neighbor_valid_mask must match neighbor depth")
    absolute = float(absolute_tolerance_m)
    relative = float(relative_tolerance_ratio)
    if not np.isfinite(absolute) or absolute <= 0.0:
        raise ValueError("absolute_tolerance_m must be finite and positive")
    if not np.isfinite(relative) or relative < 0.0:
        raise ValueError("relative_tolerance_ratio must be finite and non-negative")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    states = np.full(
        len(points), int(TemporalDepthState.OCCLUDED_OR_UNKNOWN), dtype=np.uint8
    )
    depth_m = depth.astype(np.float64, copy=False) / 1000.0
    for begin in range(0, len(points), int(chunk_size)):
        end = min(begin + int(chunk_size), len(points))
        local = points[begin:end]
        finite = np.all(np.isfinite(local), axis=1)
        camera_points = (local - position) @ rotation
        predicted = camera_points[:, 2]
        projectable = finite & (predicted > 1e-6)
        u = np.zeros(len(local), dtype=np.int64)
        v = np.zeros(len(local), dtype=np.int64)
        u_float = np.zeros(len(local), dtype=np.float64)
        v_float = np.zeros(len(local), dtype=np.float64)
        u_float[projectable] = (
            camera.fx * camera_points[projectable, 0] / predicted[projectable]
            + camera.cx
        )
        v_float[projectable] = (
            camera.fy * camera_points[projectable, 1] / predicted[projectable]
            + camera.cy
        )
        u[projectable] = np.rint(u_float[projectable]).astype(np.int64)
        v[projectable] = np.rint(v_float[projectable]).astype(np.int64)
        inside = (
            projectable
            & (u >= 0)
            & (u < camera.width)
            & (v >= 0)
            & (v < camera.height)
        )
        local_indices = np.flatnonzero(inside)
        if len(local_indices) == 0:
            continue
        observed_valid = valid_image[v[local_indices], u[local_indices]]
        tested = local_indices[observed_valid]
        if len(tested) == 0:
            continue
        observed = depth_m[v[tested], u[tested]]
        expected = predicted[tested]
        tolerance = np.maximum(absolute, expected * relative)
        difference = observed - expected
        local_states = states[begin:end]
        local_states[tested[np.abs(difference) <= tolerance]] = int(
            TemporalDepthState.SUPPORT
        )
        local_states[tested[difference > tolerance]] = int(
            TemporalDepthState.FREE_SPACE_CONTRADICTION
        )
    return states


def accumulate_temporal_counts(states: list[np.ndarray]) -> TemporalConsistencyCounts:
    if not states:
        empty = np.empty(0, dtype=np.uint8)
        return TemporalConsistencyCounts(empty, empty.copy(), empty.copy())
    arrays = [np.asarray(state, dtype=np.uint8) for state in states]
    shape = arrays[0].shape
    if any(array.shape != shape for array in arrays):
        raise ValueError("all temporal state arrays must have the same shape")
    stacked = np.stack(arrays, axis=0)
    support = np.count_nonzero(
        stacked == int(TemporalDepthState.SUPPORT), axis=0
    )
    contradiction = np.count_nonzero(
        stacked == int(TemporalDepthState.FREE_SPACE_CONTRADICTION), axis=0
    )
    tested = support + contradiction
    dtype = np.uint8 if len(arrays) <= np.iinfo(np.uint8).max else np.uint16
    return TemporalConsistencyCounts(
        test_count=tested.astype(dtype),
        support_count=support.astype(dtype),
        contradiction_count=contradiction.astype(dtype),
    )
