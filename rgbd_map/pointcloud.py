from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
from typing import Any

import cv2
import numpy as np

from .dataset import CameraModel, FrameRecord
from .depth_consistency import (
    TemporalDepthState,
    classify_projective_depth,
)
from .depth_quality import DepthQualityPolicy, evaluate_depth_quality
from .frame_quality import FrameAuditResult
from .trajectory import TrajectoryResult


@dataclass(frozen=True)
class PointCloudBuildStats:
    total_frame_count: int
    sampled_frame_count: int
    decoded_frame_count: int
    candidate_pixel_sample_count: int
    valid_depth_sample_count: int
    invalid_depth_sample_count: int
    discarded_by_per_frame_cap: int
    points_before_voxel: int
    unique_voxel_count: int
    discarded_by_voxel: int
    points_before_final_cap: int
    discarded_by_final_cap: int
    depth_edge_rejected_count: int = 0
    depth_edge_retained_count: int = 0
    confidence_map_available: bool = False
    confidence_filter_applied: bool = False
    depth_quality_rejected_count: int = 0
    far_depth_rejected_count: int = 0
    temporal_test_count: int = 0
    temporal_support_count: int = 0
    temporal_contradiction_count: int = 0
    temporal_rejected_count: int = 0
    coarse_support_rejected_count: int = 0
    points_before_quality_prefilter: int = 0
    points_after_quality_prefilter: int = 0
    stationary_run_count: int = 0
    stationary_candidate_frame_count: int = 0
    stationary_retained_frame_count: int = 0
    stationary_skipped_frame_count: int = 0


@dataclass(frozen=True)
class StationaryFrameSelection:
    selected_indices: tuple[int, ...]
    stationary_mask: np.ndarray
    limited_stationary_mask: np.ndarray
    skipped_mask: np.ndarray
    run_count: int
    candidate_frame_count: int
    retained_frame_count: int
    skipped_frame_count: int


@dataclass(frozen=True)
class PointCloudResult:
    points_enu_m: np.ndarray
    colors_rgb: np.ndarray
    sampled_frame_count: int
    decoded_frame_count: int
    valid_depth_sample_count: int
    stats: PointCloudBuildStats | None = None
    observation_count: np.ndarray | None = None
    distinct_frame_count: np.ndarray | None = None
    position_std_m: np.ndarray | None = None
    mean_depth_m: np.ndarray | None = None
    depth_min_m: np.ndarray | None = None
    depth_max_m: np.ndarray | None = None
    depth_edge_pass_count: np.ndarray | None = None
    source_voxel_key: np.ndarray | None = None
    support_observation_count: np.ndarray | None = None
    support_distinct_frame_count: np.ndarray | None = None
    independent_view_count: np.ndarray | None = None
    support_time_span_s: np.ndarray | None = None
    support_path_span_m: np.ndarray | None = None
    support_position_std_m: np.ndarray | None = None
    support_depth_std_m: np.ndarray | None = None
    temporal_test_count: np.ndarray | None = None
    temporal_support_count: np.ndarray | None = None
    temporal_contradiction_count: np.ndarray | None = None
    far_depth_risk_count: np.ndarray | None = None
    source_frame_id: np.ndarray | None = None
    mean_source_time_s: np.ndarray | None = None
    pose_quality_score: np.ndarray | None = None
    frame_reports: tuple[dict[str, Any], ...] = ()
    prefilter_removed_points_enu_m: np.ndarray | None = None
    prefilter_removed_colors_rgb: np.ndarray | None = None


@dataclass(frozen=True)
class _VoxelRun:
    """A sorted, unique-key run of voxel sums used by the LSM-style merger."""

    keys: np.ndarray
    xyz_sums: np.ndarray
    xyz_squared_sums: np.ndarray
    rgb_sums: np.ndarray
    counts: np.ndarray
    distinct_frame_counts: np.ndarray
    depth_sums: np.ndarray
    depth_min: np.ndarray
    depth_max: np.ndarray
    edge_pass_counts: np.ndarray
    temporal_test_sums: np.ndarray
    temporal_support_sums: np.ndarray
    temporal_contradiction_sums: np.ndarray
    far_risk_counts: np.ndarray
    frame_id_min: np.ndarray
    frame_id_max: np.ndarray
    source_time_sums: np.ndarray
    pose_quality_sums: np.ndarray


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    result = int(value)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _optional_positive_float(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not np.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be a finite positive value")
    return result


def voxel_position_std_upper_bound(voxel_size_m: float) -> float:
    """Combined XYZ standard-deviation bound inside one cubic output voxel."""

    size = float(voxel_size_m)
    if not np.isfinite(size) or size <= 0.0:
        raise ValueError("voxel_size_m must be a finite positive value")
    return float(np.sqrt(3.0) * size / 2.0)


def depth_edge_keep_mask(
    depth_mm: np.ndarray,
    valid_mask: np.ndarray | None = None,
    *,
    radius_px: int = 1,
    abs_threshold_m: float = 0.18,
    rel_ratio: float = 0.03,
    min_valid_neighbors: int = 4,
) -> np.ndarray:
    """Return the valid pixels whose neighboring depths are locally consistent.

    Invalid neighbors are excluded instead of being interpreted as zero depth.
    Work is vectorized over bounded row chunks so larger radii do not create an
    unbounded full-image neighborhood tensor.
    """

    depth = np.asarray(depth_mm)
    if depth.ndim != 2 or not np.issubdtype(depth.dtype, np.number):
        raise ValueError("depth_mm must be a numeric 2D array")
    radius = _positive_int("radius_px", radius_px)
    if isinstance(min_valid_neighbors, (bool, np.bool_)) or not isinstance(
        min_valid_neighbors, (int, np.integer)
    ):
        raise ValueError("min_valid_neighbors must be an integer")
    minimum_neighbors = int(min_valid_neighbors)
    available_neighbors = (2 * radius + 1) ** 2 - 1
    if not 1 <= minimum_neighbors <= available_neighbors:
        raise ValueError(
            "min_valid_neighbors must be between 1 and the neighborhood size"
        )
    absolute_threshold = float(abs_threshold_m)
    relative_ratio = float(rel_ratio)
    if not np.isfinite(absolute_threshold) or absolute_threshold <= 0.0:
        raise ValueError("abs_threshold_m must be a finite positive value")
    if not np.isfinite(relative_ratio) or relative_ratio < 0.0:
        raise ValueError("rel_ratio must be a finite non-negative value")

    finite_depth = np.isfinite(depth)
    if valid_mask is None:
        valid = finite_depth & (depth != 0) & (depth != 65535)
    else:
        supplied_valid = np.asarray(valid_mask)
        if supplied_valid.shape != depth.shape:
            raise ValueError("valid_mask must match depth_mm shape")
        valid = (
            supplied_valid.astype(bool, copy=False)
            & finite_depth
            & (depth != 0)
            & (depth != 65535)
        )

    depth_m = depth.astype(np.float64, copy=False) / 1000.0
    height, width = depth.shape
    retained = np.zeros((height, width), dtype=bool)
    if height == 0 or width == 0:
        return retained

    padded_depth = np.pad(depth_m, radius, mode="constant")
    padded_valid = np.pad(valid, radius, mode="constant", constant_values=False)
    offsets = [
        (dy, dx)
        for dy in range(-radius, radius + 1)
        for dx in range(-radius, radius + 1)
        if dy != 0 or dx != 0
    ]
    # Keep each stacked neighborhood around one million scalar elements.
    rows_per_chunk = max(
        1,
        min(height, 1_000_000 // max(1, width * available_neighbors)),
    )
    column_start = radius
    column_stop = radius + width
    for row_start in range(0, height, rows_per_chunk):
        row_stop = min(height, row_start + rows_per_chunk)
        neighbor_depths = np.stack(
            [
                padded_depth[
                    row_start + radius + dy : row_stop + radius + dy,
                    column_start + dx : column_stop + dx,
                ]
                for dy, dx in offsets
            ],
            axis=0,
        )
        neighbor_valid = np.stack(
            [
                padded_valid[
                    row_start + radius + dy : row_stop + radius + dy,
                    column_start + dx : column_stop + dx,
                ]
                for dy, dx in offsets
            ],
            axis=0,
        )
        neighbor_counts = np.count_nonzero(neighbor_valid, axis=0)
        neighbor_depths[~neighbor_valid] = np.inf
        neighbor_depths.sort(axis=0)

        gather_shape = (1, row_stop - row_start, width)
        low_index = np.clip((neighbor_counts - 1) // 2, 0, available_neighbors - 1)
        high_index = np.clip(neighbor_counts // 2, 0, available_neighbors - 1)
        median_low = np.take_along_axis(
            neighbor_depths, low_index.reshape(gather_shape), axis=0
        )[0]
        median_high = np.take_along_axis(
            neighbor_depths, high_index.reshape(gather_shape), axis=0
        )[0]
        neighbor_median = (median_low + median_high) * 0.5
        neighbor_min = neighbor_depths[0]
        max_index = np.clip(neighbor_counts - 1, 0, available_neighbors - 1)
        neighbor_max = np.take_along_axis(
            neighbor_depths, max_index.reshape(gather_shape), axis=0
        )[0]

        current = depth_m[row_start:row_stop]
        has_neighbors = neighbor_counts > 0
        neighbor_median = np.where(has_neighbors, neighbor_median, current)
        neighbor_min = np.where(has_neighbors, neighbor_min, current)
        neighbor_max = np.where(has_neighbors, neighbor_max, current)
        threshold = np.maximum(absolute_threshold, current * relative_ratio)
        enough_neighbors = neighbor_counts >= minimum_neighbors
        depth_span = neighbor_max - neighbor_min
        unstable = (depth_span > threshold) | (
            np.abs(neighbor_median - current) > threshold
        )
        retained[row_start:row_stop] = (
            valid[row_start:row_stop] & enough_neighbors & ~unstable
        )
    return retained


def _validate_trajectory(frames: list[FrameRecord], trajectory: TrajectoryResult) -> None:
    count = len(frames)
    positions = np.asarray(trajectory.positions_enu_m)
    rotations = np.asarray(trajectory.rotations_enu_from_camera)
    if positions.shape != (count, 3):
        raise ValueError("trajectory.positions_enu_m must have shape (frame_count, 3)")
    if rotations.shape != (count, 3, 3):
        raise ValueError(
            "trajectory.rotations_enu_from_camera must have shape (frame_count, 3, 3)"
        )
    if not np.all(np.isfinite(positions)) or not np.all(np.isfinite(rotations)):
        raise ValueError("trajectory positions and rotations must be finite")


def select_frame_indices(
    frames: list[FrameRecord],
    trajectory: TrajectoryResult,
    frame_stride: int = 10,
    *,
    keyframe_distance_m: float | None = None,
    keyframe_angle_deg: float | None = None,
    keyframe_max_dt_s: float | None = None,
) -> list[int]:
    """Select cloud frames using either stride or motion/time keyframe criteria.

    Supplying any keyframe threshold switches selection from stride mode to an OR
    of the supplied criteria. Comparisons are made against the most recently
    selected keyframe. The first and last frames are always included once.
    """

    frame_stride = _positive_int("frame_stride", frame_stride)
    if not frames:
        raise ValueError("frames must not be empty")
    _validate_trajectory(frames, trajectory)
    distance_threshold = _optional_positive_float(
        "keyframe_distance_m", keyframe_distance_m
    )
    angle_threshold = _optional_positive_float("keyframe_angle_deg", keyframe_angle_deg)
    max_dt_threshold = _optional_positive_float("keyframe_max_dt_s", keyframe_max_dt_s)
    if angle_threshold is not None and angle_threshold > 180.0:
        raise ValueError("keyframe_angle_deg must not exceed 180 degrees")

    if all(
        value is None
        for value in (distance_threshold, angle_threshold, max_dt_threshold)
    ):
        selected = list(range(0, len(frames), frame_stride))
        if selected[-1] != len(frames) - 1:
            selected.append(len(frames) - 1)
        return selected

    timestamps_ns = np.asarray([frame.monotonic_ns for frame in frames], dtype=np.int64)
    if max_dt_threshold is not None and np.any(np.diff(timestamps_ns) < 0):
        raise ValueError("frame monotonic timestamps must be non-decreasing")

    positions = np.asarray(trajectory.positions_enu_m, dtype=np.float64)
    rotations = np.asarray(trajectory.rotations_enu_from_camera, dtype=np.float64)
    selected = [0]
    for candidate_index in range(1, len(frames)):
        previous_index = selected[-1]
        should_select = False
        if distance_threshold is not None:
            distance = float(
                np.linalg.norm(positions[candidate_index] - positions[previous_index])
            )
            should_select = distance >= distance_threshold
        if not should_select and angle_threshold is not None:
            relative_rotation = rotations[previous_index].T @ rotations[candidate_index]
            cosine = float(np.clip((np.trace(relative_rotation) - 1.0) * 0.5, -1.0, 1.0))
            angle_deg = float(np.rad2deg(np.arccos(cosine)))
            should_select = angle_deg >= angle_threshold
        if not should_select and max_dt_threshold is not None:
            elapsed_s = float(timestamps_ns[candidate_index] - timestamps_ns[previous_index]) / 1e9
            should_select = elapsed_s >= max_dt_threshold
        if should_select:
            selected.append(candidate_index)

    last_index = len(frames) - 1
    if selected[-1] != last_index:
        selected.append(last_index)
    return selected


def limit_stationary_frame_indices(
    frames: list[FrameRecord],
    selected_indices: list[int],
    gps_speed_m_s: np.ndarray,
    *,
    speed_threshold_m_s: float,
    min_duration_s: float,
    max_frames_per_run: int,
) -> StationaryFrameSelection:
    """Cap cloud frames in sustained GPS-stationary intervals.

    Short low-speed samples are left unchanged. For every qualifying contiguous
    interval, cloud candidates are sampled uniformly so its beginning, interior,
    and end remain represented without accumulating every stopped frame.
    """

    if not frames:
        raise ValueError("frames must not be empty")
    speeds = np.asarray(gps_speed_m_s, dtype=np.float64)
    if speeds.shape != (len(frames),):
        raise ValueError("gps_speed_m_s must align with frames")
    threshold = float(speed_threshold_m_s)
    duration_threshold = float(min_duration_s)
    if not np.isfinite(threshold) or threshold < 0.0:
        raise ValueError("speed_threshold_m_s must be finite and non-negative")
    if not np.isfinite(duration_threshold) or duration_threshold <= 0.0:
        raise ValueError("min_duration_s must be finite and positive")
    cap = _positive_int("max_frames_per_run", max_frames_per_run)

    selected = np.asarray(selected_indices, dtype=np.int64)
    if selected.ndim != 1 or len(selected) == 0:
        raise ValueError("selected_indices must not be empty")
    if np.any(selected < 0) or np.any(selected >= len(frames)):
        raise ValueError("selected_indices contains an out-of-range frame")
    if np.any(np.diff(selected) <= 0):
        raise ValueError("selected_indices must be strictly increasing")

    timestamps_ns = np.asarray(
        [frame.monotonic_ns for frame in frames], dtype=np.int64
    )
    if np.any(np.diff(timestamps_ns) < 0):
        raise ValueError("frame timestamps must be non-decreasing")
    stationary = np.isfinite(speeds) & (speeds <= threshold)
    limited = np.zeros(len(frames), dtype=bool)
    skipped = np.zeros(len(frames), dtype=bool)
    run_count = 0
    candidate_count = 0
    retained_count = 0

    padded = np.concatenate(([False], stationary, [False]))
    transitions = np.diff(padded.astype(np.int8))
    run_starts = np.flatnonzero(transitions == 1)
    run_ends = np.flatnonzero(transitions == -1) - 1
    for start, end in zip(run_starts, run_ends, strict=True):
        duration_s = float(timestamps_ns[end] - timestamps_ns[start]) / 1e9
        if duration_s < duration_threshold:
            continue
        positions = np.flatnonzero((selected >= start) & (selected <= end))
        if len(positions) == 0:
            continue
        run_count += 1
        limited[start : end + 1] = True
        candidate_count += len(positions)
        retain_count = min(cap, len(positions))
        retain_positions = np.linspace(
            0, len(positions) - 1, retain_count, dtype=np.int64
        )
        retained_positions = positions[retain_positions]
        retained_count += len(retained_positions)
        discard_positions = np.setdiff1d(
            positions, retained_positions, assume_unique=True
        )
        skipped[selected[discard_positions]] = True

    final_selected = tuple(int(index) for index in selected[~skipped[selected]])
    return StationaryFrameSelection(
        selected_indices=final_selected,
        stationary_mask=stationary,
        limited_stationary_mask=limited,
        skipped_mask=skipped,
        run_count=run_count,
        candidate_frame_count=candidate_count,
        retained_frame_count=retained_count,
        skipped_frame_count=int(np.count_nonzero(skipped)),
    )


def _validate_point_color_arrays(
    points_enu_m: np.ndarray, colors_rgb: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    points = np.asarray(points_enu_m)
    colors = np.asarray(colors_rgb)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points must have shape (N, 3)")
    if colors.ndim != 2 or colors.shape != points.shape:
        raise ValueError("colors must have shape (N, 3) and match points")
    if not np.issubdtype(points.dtype, np.number) or not np.all(np.isfinite(points)):
        raise ValueError("points must contain finite numeric values")
    if not np.issubdtype(colors.dtype, np.number) or not np.all(np.isfinite(colors)):
        raise ValueError("colors must contain finite numeric values")
    if np.any(colors < 0) or np.any(colors > 255):
        raise ValueError("colors must be in the uint8 range [0, 255]")
    return points.astype(np.float64, copy=False), colors


def _reduce_voxel_entries(
    keys: np.ndarray,
    xyz_sums: np.ndarray,
    xyz_squared_sums: np.ndarray,
    rgb_sums: np.ndarray,
    counts: np.ndarray,
    distinct_frame_counts: np.ndarray,
    depth_sums: np.ndarray,
    depth_min: np.ndarray,
    depth_max: np.ndarray,
    edge_pass_counts: np.ndarray,
    temporal_test_sums: np.ndarray,
    temporal_support_sums: np.ndarray,
    temporal_contradiction_sums: np.ndarray,
    far_risk_counts: np.ndarray,
    frame_id_min: np.ndarray,
    frame_id_max: np.ndarray,
    source_time_sums: np.ndarray,
    pose_quality_sums: np.ndarray,
    *,
    distinct_per_key: bool = False,
) -> _VoxelRun:
    unique_keys, inverse = np.unique(keys, axis=0, return_inverse=True)
    unique_count = len(unique_keys)
    reduced_xyz = np.empty((unique_count, 3), dtype=np.float64)
    reduced_xyz_squared = np.empty((unique_count, 3), dtype=np.float64)
    reduced_rgb = np.empty((unique_count, 3), dtype=np.float64)
    for axis in range(3):
        reduced_xyz[:, axis] = np.bincount(
            inverse, weights=xyz_sums[:, axis], minlength=unique_count
        )
        reduced_xyz_squared[:, axis] = np.bincount(
            inverse, weights=xyz_squared_sums[:, axis], minlength=unique_count
        )
        reduced_rgb[:, axis] = np.bincount(
            inverse, weights=rgb_sums[:, axis], minlength=unique_count
        )
    reduced_counts = np.zeros(unique_count, dtype=np.int64)
    np.add.at(reduced_counts, inverse, counts)
    if distinct_per_key:
        reduced_distinct_frames = np.ones(unique_count, dtype=np.int64)
    else:
        reduced_distinct_frames = np.zeros(unique_count, dtype=np.int64)
        np.add.at(reduced_distinct_frames, inverse, distinct_frame_counts)
    reduced_depth_sums = np.bincount(
        inverse, weights=depth_sums, minlength=unique_count
    )
    reduced_depth_min = np.full(unique_count, np.inf, dtype=np.float64)
    reduced_depth_max = np.full(unique_count, -np.inf, dtype=np.float64)
    np.minimum.at(reduced_depth_min, inverse, depth_min)
    np.maximum.at(reduced_depth_max, inverse, depth_max)
    reduced_edge_pass_counts = np.zeros(unique_count, dtype=np.int64)
    np.add.at(reduced_edge_pass_counts, inverse, edge_pass_counts)
    reduced_temporal_test = np.zeros(unique_count, dtype=np.int64)
    reduced_temporal_support = np.zeros(unique_count, dtype=np.int64)
    reduced_temporal_contradiction = np.zeros(unique_count, dtype=np.int64)
    reduced_far_risk = np.zeros(unique_count, dtype=np.int64)
    np.add.at(reduced_temporal_test, inverse, temporal_test_sums)
    np.add.at(reduced_temporal_support, inverse, temporal_support_sums)
    np.add.at(reduced_temporal_contradiction, inverse, temporal_contradiction_sums)
    np.add.at(reduced_far_risk, inverse, far_risk_counts)
    reduced_frame_min = np.full(unique_count, np.iinfo(np.int64).max, dtype=np.int64)
    reduced_frame_max = np.full(unique_count, np.iinfo(np.int64).min, dtype=np.int64)
    np.minimum.at(reduced_frame_min, inverse, frame_id_min)
    np.maximum.at(reduced_frame_max, inverse, frame_id_max)
    reduced_source_time = np.bincount(
        inverse, weights=source_time_sums, minlength=unique_count
    )
    reduced_pose_quality = np.bincount(
        inverse, weights=pose_quality_sums, minlength=unique_count
    )
    return _VoxelRun(
        keys=unique_keys.astype(np.int64, copy=False),
        xyz_sums=reduced_xyz,
        xyz_squared_sums=reduced_xyz_squared,
        rgb_sums=reduced_rgb,
        counts=reduced_counts,
        distinct_frame_counts=reduced_distinct_frames,
        depth_sums=reduced_depth_sums,
        depth_min=reduced_depth_min,
        depth_max=reduced_depth_max,
        edge_pass_counts=reduced_edge_pass_counts,
        temporal_test_sums=reduced_temporal_test,
        temporal_support_sums=reduced_temporal_support,
        temporal_contradiction_sums=reduced_temporal_contradiction,
        far_risk_counts=reduced_far_risk,
        frame_id_min=reduced_frame_min,
        frame_id_max=reduced_frame_max,
        source_time_sums=reduced_source_time,
        pose_quality_sums=reduced_pose_quality,
    )


def _local_voxel_run(
    points_enu_m: np.ndarray,
    colors_rgb: np.ndarray,
    voxel_size_m: float,
    depths_m: np.ndarray | None = None,
    edge_pass: np.ndarray | None = None,
    temporal_test_count: np.ndarray | None = None,
    temporal_support_count: np.ndarray | None = None,
    temporal_contradiction_count: np.ndarray | None = None,
    far_depth_risk: np.ndarray | None = None,
    frame_id: int = -1,
    source_time_s: float = 0.0,
    pose_quality_score: float = 1.0,
) -> _VoxelRun:
    points, colors = _validate_point_color_arrays(points_enu_m, colors_rgb)
    voxel_size = float(voxel_size_m)
    if not np.isfinite(voxel_size) or voxel_size <= 0.0:
        raise ValueError("voxel_size_m must be a finite positive value")
    if len(points) == 0:
        return _VoxelRun(
            keys=np.empty((0, 3), dtype=np.int64),
            xyz_sums=np.empty((0, 3), dtype=np.float64),
            xyz_squared_sums=np.empty((0, 3), dtype=np.float64),
            rgb_sums=np.empty((0, 3), dtype=np.float64),
            counts=np.empty(0, dtype=np.int64),
            distinct_frame_counts=np.empty(0, dtype=np.int64),
            depth_sums=np.empty(0, dtype=np.float64),
            depth_min=np.empty(0, dtype=np.float64),
            depth_max=np.empty(0, dtype=np.float64),
            edge_pass_counts=np.empty(0, dtype=np.int64),
            temporal_test_sums=np.empty(0, dtype=np.int64),
            temporal_support_sums=np.empty(0, dtype=np.int64),
            temporal_contradiction_sums=np.empty(0, dtype=np.int64),
            far_risk_counts=np.empty(0, dtype=np.int64),
            frame_id_min=np.empty(0, dtype=np.int64),
            frame_id_max=np.empty(0, dtype=np.int64),
            source_time_sums=np.empty(0, dtype=np.float64),
            pose_quality_sums=np.empty(0, dtype=np.float64),
        )
    if depths_m is None:
        depths = np.zeros(len(points), dtype=np.float64)
    else:
        depths = np.asarray(depths_m, dtype=np.float64)
        if depths.shape != (len(points),) or not np.all(np.isfinite(depths)):
            raise ValueError("depths_m must be a finite array with shape (N,)")
    if edge_pass is None:
        edge_pass_counts = np.ones(len(points), dtype=np.int64)
    else:
        edge_pass_array = np.asarray(edge_pass)
        if edge_pass_array.shape != (len(points),):
            raise ValueError("edge_pass must have shape (N,)")
        edge_pass_counts = edge_pass_array.astype(np.int64, copy=False)
        if np.any(edge_pass_counts < 0) or np.any(edge_pass_counts > 1):
            raise ValueError("edge_pass values must be boolean or 0/1")
    scaled = np.floor(points / voxel_size)
    int64_info = np.iinfo(np.int64)
    if np.any(scaled < int64_info.min) or np.any(scaled > int64_info.max):
        raise ValueError("voxel coordinates exceed the int64 key range")
    keys = scaled.astype(np.int64)
    def aligned_counts(value: np.ndarray | None, name: str) -> np.ndarray:
        if value is None:
            return np.zeros(len(points), dtype=np.int64)
        array = np.asarray(value)
        if array.shape != (len(points),) or not (
            np.issubdtype(array.dtype, np.number)
            or np.issubdtype(array.dtype, np.bool_)
        ):
            raise ValueError(f"{name} must be numeric with shape (N,)")
        if np.any(array < 0):
            raise ValueError(f"{name} must be non-negative")
        return array.astype(np.int64, copy=False)

    temporal_tests = aligned_counts(temporal_test_count, "temporal_test_count")
    temporal_support = aligned_counts(
        temporal_support_count, "temporal_support_count"
    )
    temporal_contradictions = aligned_counts(
        temporal_contradiction_count, "temporal_contradiction_count"
    )
    far_risk = aligned_counts(far_depth_risk, "far_depth_risk")
    return _reduce_voxel_entries(
        keys,
        points,
        points * points,
        colors.astype(np.float64, copy=False),
        np.ones(len(points), dtype=np.int64),
        np.ones(len(points), dtype=np.int64),
        depths,
        depths,
        depths,
        edge_pass_counts,
        temporal_tests,
        temporal_support,
        temporal_contradictions,
        far_risk,
        np.full(len(points), int(frame_id), dtype=np.int64),
        np.full(len(points), int(frame_id), dtype=np.int64),
        np.full(len(points), float(source_time_s), dtype=np.float64),
        np.full(len(points), float(pose_quality_score), dtype=np.float64),
        distinct_per_key=True,
    )


def _merge_voxel_runs(left: _VoxelRun, right: _VoxelRun) -> _VoxelRun:
    if len(left.keys) == 0:
        return right
    if len(right.keys) == 0:
        return left
    return _reduce_voxel_entries(
        np.concatenate((left.keys, right.keys), axis=0),
        np.concatenate((left.xyz_sums, right.xyz_sums), axis=0),
        np.concatenate((left.xyz_squared_sums, right.xyz_squared_sums), axis=0),
        np.concatenate((left.rgb_sums, right.rgb_sums), axis=0),
        np.concatenate((left.counts, right.counts), axis=0),
        np.concatenate(
            (left.distinct_frame_counts, right.distinct_frame_counts), axis=0
        ),
        np.concatenate((left.depth_sums, right.depth_sums), axis=0),
        np.concatenate((left.depth_min, right.depth_min), axis=0),
        np.concatenate((left.depth_max, right.depth_max), axis=0),
        np.concatenate((left.edge_pass_counts, right.edge_pass_counts), axis=0),
        np.concatenate((left.temporal_test_sums, right.temporal_test_sums), axis=0),
        np.concatenate((left.temporal_support_sums, right.temporal_support_sums), axis=0),
        np.concatenate(
            (left.temporal_contradiction_sums, right.temporal_contradiction_sums),
            axis=0,
        ),
        np.concatenate((left.far_risk_counts, right.far_risk_counts), axis=0),
        np.concatenate((left.frame_id_min, right.frame_id_min), axis=0),
        np.concatenate((left.frame_id_max, right.frame_id_max), axis=0),
        np.concatenate((left.source_time_sums, right.source_time_sums), axis=0),
        np.concatenate((left.pose_quality_sums, right.pose_quality_sums), axis=0),
    )


class _VoxelRunAccumulator:
    """Binary-level merge accumulator; raw points never span multiple frames."""

    def __init__(self) -> None:
        self._levels: list[_VoxelRun | None] = []

    def add(self, run: _VoxelRun) -> None:
        if len(run.keys) == 0:
            return
        level = 0
        while True:
            if level == len(self._levels):
                self._levels.append(run)
                return
            previous = self._levels[level]
            if previous is None:
                self._levels[level] = run
                return
            self._levels[level] = None
            run = _merge_voxel_runs(previous, run)
            level += 1

    @property
    def run_entry_count(self) -> int:
        return sum(len(run.keys) for run in self._levels if run is not None)

    def finish(self) -> _VoxelRun | None:
        result: _VoxelRun | None = None
        # Higher levels contain older frames, so merge them first for stable sums.
        for run in reversed(self._levels):
            if run is not None:
                result = run if result is None else _merge_voxel_runs(result, run)
        return result


def _averages_from_run(run: _VoxelRun) -> tuple[np.ndarray, np.ndarray]:
    divisor = run.counts[:, None]
    points = np.empty(run.xyz_sums.shape, dtype=np.float32)
    np.divide(run.xyz_sums, divisor, out=points, casting="unsafe")
    mean_rgb = np.divide(run.rgb_sums, divisor)
    np.rint(mean_rgb, out=mean_rgb)
    np.clip(mean_rgb, 0.0, 255.0, out=mean_rgb)
    return points, mean_rgb.astype(np.uint8)


def _metadata_from_run(run: _VoxelRun) -> dict[str, np.ndarray]:
    divisor = run.counts[:, None]
    mean_xyz = np.divide(run.xyz_sums, divisor)
    variance_xyz = np.divide(run.xyz_squared_sums, divisor) - mean_xyz * mean_xyz
    np.maximum(variance_xyz, 0.0, out=variance_xyz)
    position_std = np.sqrt(np.sum(variance_xyz, axis=1))
    source_frame = np.where(
        run.frame_id_min == run.frame_id_max, run.frame_id_min, -1
    ).astype(np.int32)
    return {
        "observation_count": run.counts.astype(np.int64, copy=True),
        "distinct_frame_count": run.distinct_frame_counts.astype(
            np.int64, copy=True
        ),
        "position_std_m": position_std.astype(np.float32),
        "mean_depth_m": np.divide(run.depth_sums, run.counts).astype(np.float32),
        "depth_min_m": run.depth_min.astype(np.float32, copy=True),
        "depth_max_m": run.depth_max.astype(np.float32, copy=True),
        "depth_edge_pass_count": run.edge_pass_counts.astype(np.int64, copy=True),
        "source_voxel_key": run.keys.astype(np.int64, copy=True),
        "temporal_test_count": np.rint(
            np.divide(run.temporal_test_sums, run.counts)
        ).astype(np.uint16),
        "temporal_support_count": np.rint(
            np.divide(run.temporal_support_sums, run.counts)
        ).astype(np.uint16),
        "temporal_contradiction_count": np.rint(
            np.divide(run.temporal_contradiction_sums, run.counts)
        ).astype(np.uint16),
        "far_depth_risk_count": run.far_risk_counts.astype(np.uint32, copy=True),
        "source_frame_id": source_frame,
        "mean_source_time_s": np.divide(
            run.source_time_sums, run.counts
        ).astype(np.float64),
        "pose_quality_score": np.divide(
            run.pose_quality_sums, run.counts
        ).astype(np.float32),
    }


def voxel_average_points(
    points_enu_m: np.ndarray,
    colors_rgb: np.ndarray,
    voxel_size_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return deterministic XYZ/RGB means sorted by floor-based voxel key."""

    run = _local_voxel_run(points_enu_m, colors_rgb, voxel_size_m)
    return _averages_from_run(run)


def _coarse_grid_shape(extents: np.ndarray, target_cell_count: int) -> np.ndarray:
    shape = np.ones(3, dtype=np.int64)
    active = np.flatnonzero(extents > 0.0)
    if len(active) == 0:
        return shape
    weights = extents[active] / float(np.max(extents[active]))
    log_scale = (
        np.log(float(target_cell_count)) - float(np.sum(np.log(weights)))
    ) / len(active)
    log_target = np.log(float(target_cell_count))
    desired = np.exp(np.minimum(np.log(weights) + log_scale, log_target))
    for axis, value in zip(active, desired, strict=True):
        shape[axis] = max(
            1,
            min(target_cell_count, int(np.floor(float(value) + 1e-9))),
        )

    product = int(np.prod(shape, dtype=object))
    while product > target_cell_count:
        axis = int(np.argmax(shape))
        other_product = product // int(shape[axis])
        shape[axis] = max(1, target_cell_count // other_product)
        product = int(np.prod(shape, dtype=object))
    return shape


def spatially_sample_indices(points: np.ndarray, max_points: int) -> np.ndarray:
    """Select exactly the cap with deterministic coarse-grid round-robin coverage."""

    cap = _positive_int("max_points", max_points)
    point_array = np.asarray(points)
    if point_array.ndim != 2 or point_array.shape[1:] != (3,):
        raise ValueError("points must have shape (N, 3)")
    if not np.issubdtype(point_array.dtype, np.number) or not np.all(
        np.isfinite(point_array)
    ):
        raise ValueError("points must contain finite numeric values")
    count = len(point_array)
    if count <= cap:
        return np.arange(count, dtype=np.int64)

    minimum = np.min(point_array, axis=0).astype(np.float64)
    maximum = np.max(point_array, axis=0).astype(np.float64)
    extents = maximum - minimum
    grid_shape = _coarse_grid_shape(extents, cap)
    cell_ids = np.zeros(count, dtype=np.int64)
    for axis in range(3):
        cell_ids *= grid_shape[axis]
        if extents[axis] > 0.0:
            coordinate = np.floor(
                (point_array[:, axis] - minimum[axis])
                * float(grid_shape[axis])
                / extents[axis]
            ).astype(np.int64)
            np.clip(coordinate, 0, int(grid_shape[axis]) - 1, out=coordinate)
            cell_ids += coordinate
    order = np.argsort(cell_ids, kind="stable")
    sorted_cell_ids = cell_ids[order]
    is_start = np.empty(count, dtype=bool)
    is_start[0] = True
    is_start[1:] = sorted_cell_ids[1:] != sorted_cell_ids[:-1]
    starts = np.flatnonzero(is_start).astype(np.int64, copy=False)
    ends = np.append(starts[1:], count)
    cell_counts = ends - starts

    # Find the largest number of complete rounds that stays within the cap.
    low = 1
    high = int(np.max(cell_counts)) + 1
    while low + 1 < high:
        middle = (low + high) // 2
        selected_count = int(np.minimum(cell_counts, middle).sum())
        if selected_count <= cap:
            low = middle
        else:
            high = middle

    take_per_cell = np.minimum(cell_counts, low)
    base_count = int(take_per_cell.sum())
    block_prefix = np.cumsum(take_per_cell, dtype=np.int64) - take_per_cell
    within_cell = np.arange(base_count, dtype=np.int64)
    within_cell -= np.repeat(block_prefix, take_per_cell)
    selected_sorted_positions = np.repeat(starts, take_per_cell) + within_cell
    selected = order[selected_sorted_positions]

    remaining = cap - base_count
    if remaining:
        eligible = np.flatnonzero(cell_counts > low)[:remaining]
        extra = order[starts[eligible] + low]
        selected = np.concatenate((selected, extra))
    if len(selected) != cap:
        raise RuntimeError("spatial sampler failed to produce the requested point cap")
    return selected.astype(np.int64, copy=False)


@dataclass(frozen=True)
class _CoarseSupportRun:
    keys: np.ndarray
    observation_counts: np.ndarray
    frame_counts: np.ndarray
    centroid_sums: np.ndarray
    centroid_squared_sums: np.ndarray
    depth_sums: np.ndarray
    depth_squared_sums: np.ndarray
    time_min_s: np.ndarray
    time_max_s: np.ndarray
    path_min_m: np.ndarray
    path_max_m: np.ndarray


def _empty_coarse_run() -> _CoarseSupportRun:
    return _CoarseSupportRun(
        keys=np.empty((0, 3), dtype=np.int64),
        observation_counts=np.empty(0, dtype=np.int64),
        frame_counts=np.empty(0, dtype=np.int64),
        centroid_sums=np.empty((0, 3), dtype=np.float64),
        centroid_squared_sums=np.empty((0, 3), dtype=np.float64),
        depth_sums=np.empty(0, dtype=np.float64),
        depth_squared_sums=np.empty(0, dtype=np.float64),
        time_min_s=np.empty(0, dtype=np.float64),
        time_max_s=np.empty(0, dtype=np.float64),
        path_min_m=np.empty(0, dtype=np.float64),
        path_max_m=np.empty(0, dtype=np.float64),
    )


def _reduce_coarse_runs(entries: _CoarseSupportRun) -> _CoarseSupportRun:
    if len(entries.keys) == 0:
        return entries
    keys, inverse = np.unique(entries.keys, axis=0, return_inverse=True)
    count = len(keys)
    observation = np.zeros(count, dtype=np.int64)
    frames = np.zeros(count, dtype=np.int64)
    np.add.at(observation, inverse, entries.observation_counts)
    np.add.at(frames, inverse, entries.frame_counts)
    centroid = np.empty((count, 3), dtype=np.float64)
    centroid_squared = np.empty((count, 3), dtype=np.float64)
    for axis in range(3):
        centroid[:, axis] = np.bincount(
            inverse, weights=entries.centroid_sums[:, axis], minlength=count
        )
        centroid_squared[:, axis] = np.bincount(
            inverse,
            weights=entries.centroid_squared_sums[:, axis],
            minlength=count,
        )
    depth = np.bincount(inverse, weights=entries.depth_sums, minlength=count)
    depth_squared = np.bincount(
        inverse, weights=entries.depth_squared_sums, minlength=count
    )
    time_min = np.full(count, np.inf, dtype=np.float64)
    time_max = np.full(count, -np.inf, dtype=np.float64)
    path_min = np.full(count, np.inf, dtype=np.float64)
    path_max = np.full(count, -np.inf, dtype=np.float64)
    np.minimum.at(time_min, inverse, entries.time_min_s)
    np.maximum.at(time_max, inverse, entries.time_max_s)
    np.minimum.at(path_min, inverse, entries.path_min_m)
    np.maximum.at(path_max, inverse, entries.path_max_m)
    return _CoarseSupportRun(
        keys=keys,
        observation_counts=observation,
        frame_counts=frames,
        centroid_sums=centroid,
        centroid_squared_sums=centroid_squared,
        depth_sums=depth,
        depth_squared_sums=depth_squared,
        time_min_s=time_min,
        time_max_s=time_max,
        path_min_m=path_min,
        path_max_m=path_max,
    )


def _local_coarse_support_run(
    points: np.ndarray,
    depths_m: np.ndarray,
    voxel_size_m: float,
    *,
    time_s: float,
    path_m: float,
) -> _CoarseSupportRun:
    if len(points) == 0:
        return _empty_coarse_run()
    keys = np.floor(np.asarray(points, dtype=np.float64) / voxel_size_m).astype(
        np.int64
    )
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    count = len(unique)
    observations = np.bincount(inverse, minlength=count).astype(np.int64)
    sums = np.empty((count, 3), dtype=np.float64)
    for axis in range(3):
        sums[:, axis] = np.bincount(
            inverse, weights=points[:, axis], minlength=count
        )
    centroids = sums / observations[:, None]
    depth_sums = np.bincount(inverse, weights=depths_m, minlength=count)
    depth_means = depth_sums / observations
    return _CoarseSupportRun(
        keys=unique,
        observation_counts=observations,
        frame_counts=np.ones(count, dtype=np.int64),
        centroid_sums=centroids,
        centroid_squared_sums=centroids * centroids,
        depth_sums=depth_means,
        depth_squared_sums=depth_means * depth_means,
        time_min_s=np.full(count, float(time_s), dtype=np.float64),
        time_max_s=np.full(count, float(time_s), dtype=np.float64),
        path_min_m=np.full(count, float(path_m), dtype=np.float64),
        path_max_m=np.full(count, float(path_m), dtype=np.float64),
    )


def _merge_coarse_runs(
    left: _CoarseSupportRun, right: _CoarseSupportRun
) -> _CoarseSupportRun:
    if len(left.keys) == 0:
        return right
    if len(right.keys) == 0:
        return left
    return _reduce_coarse_runs(
        _CoarseSupportRun(
            **{
                field: np.concatenate((getattr(left, field), getattr(right, field)), axis=0)
                for field in _CoarseSupportRun.__dataclass_fields__
            }
        )
    )


class _CoarseSupportAccumulator:
    def __init__(self) -> None:
        self._levels: list[_CoarseSupportRun | None] = []

    def add(self, run: _CoarseSupportRun) -> None:
        if len(run.keys) == 0:
            return
        level = 0
        while True:
            if level == len(self._levels):
                self._levels.append(run)
                return
            previous = self._levels[level]
            if previous is None:
                self._levels[level] = run
                return
            self._levels[level] = None
            run = _merge_coarse_runs(previous, run)
            level += 1

    def finish(self) -> _CoarseSupportRun:
        result: _CoarseSupportRun | None = None
        for run in reversed(self._levels):
            if run is not None:
                result = run if result is None else _merge_coarse_runs(result, run)
        return _empty_coarse_run() if result is None else result


def _structured_keys(keys: np.ndarray) -> np.ndarray:
    contiguous = np.ascontiguousarray(keys, dtype=np.int64)
    dtype = np.dtype([("x", np.int64), ("y", np.int64), ("z", np.int64)])
    return contiguous.view(dtype).reshape(-1)


def _map_coarse_support(
    points: np.ndarray,
    mean_depth_m: np.ndarray,
    near_run: _CoarseSupportRun,
    far_run: _CoarseSupportRun,
    *,
    near_voxel_size_m: float,
    far_voxel_size_m: float,
    far_start_m: float,
    min_baseline_m: float,
    min_time_separation_s: float,
) -> dict[str, np.ndarray]:
    count = len(points)
    result: dict[str, np.ndarray] = {
        "support_observation_count": np.zeros(count, dtype=np.uint32),
        "support_distinct_frame_count": np.zeros(count, dtype=np.uint16),
        "independent_view_count": np.zeros(count, dtype=np.uint16),
        "support_time_span_s": np.zeros(count, dtype=np.float32),
        "support_path_span_m": np.zeros(count, dtype=np.float32),
        "support_position_std_m": np.full(count, np.nan, dtype=np.float32),
        "support_depth_std_m": np.full(count, np.nan, dtype=np.float32),
    }
    far = np.asarray(mean_depth_m) >= far_start_m
    for selection, run, size in (
        (~far, near_run, near_voxel_size_m),
        (far, far_run, far_voxel_size_m),
    ):
        target = np.flatnonzero(selection)
        if len(target) == 0 or len(run.keys) == 0:
            continue
        query = np.floor(points[target] / size).astype(np.int64)
        source_keys = _structured_keys(run.keys)
        query_keys = _structured_keys(query)
        locations = np.searchsorted(source_keys, query_keys)
        within = locations < len(source_keys)
        matched = np.zeros(len(target), dtype=bool)
        matched[within] = source_keys[locations[within]] == query_keys[within]
        output_indices = target[matched]
        source_indices = locations[matched]
        frames = run.frame_counts[source_indices]
        time_span = run.time_max_s[source_indices] - run.time_min_s[source_indices]
        path_span = run.path_max_m[source_indices] - run.path_min_m[source_indices]
        baseline_views = 1 + np.floor(path_span / min_baseline_m).astype(np.int64)
        time_views = 1 + np.floor(
            time_span / min_time_separation_s
        ).astype(np.int64)
        independent = np.minimum(frames, np.maximum(baseline_views, time_views))
        mean_xyz = run.centroid_sums[source_indices] / frames[:, None]
        variance_xyz = (
            run.centroid_squared_sums[source_indices] / frames[:, None]
            - mean_xyz * mean_xyz
        )
        np.maximum(variance_xyz, 0.0, out=variance_xyz)
        mean_depth = run.depth_sums[source_indices] / frames
        depth_variance = (
            run.depth_squared_sums[source_indices] / frames - mean_depth * mean_depth
        )
        np.maximum(depth_variance, 0.0, out=depth_variance)
        result["support_observation_count"][output_indices] = run.observation_counts[
            source_indices
        ].astype(np.uint32)
        result["support_distinct_frame_count"][output_indices] = frames.astype(np.uint16)
        result["independent_view_count"][output_indices] = independent.astype(np.uint16)
        result["support_time_span_s"][output_indices] = time_span.astype(np.float32)
        result["support_path_span_m"][output_indices] = path_span.astype(np.float32)
        result["support_position_std_m"][output_indices] = np.sqrt(
            np.sum(variance_xyz, axis=1)
        ).astype(np.float32)
        result["support_depth_std_m"][output_indices] = np.sqrt(
            depth_variance
        ).astype(np.float32)
    return result


def _validate_build_inputs(
    frames: list[FrameRecord],
    camera: CameraModel,
    trajectory: TrajectoryResult,
    *,
    frame_stride: int,
    pixel_stride: int,
    voxel_size_m: float,
    max_points: int,
    per_frame_max_points: int,
    min_depth_m: float,
    max_depth_m: float,
    roi_top_ratio: float,
    roi_bottom_ratio: float,
    progress_every: int,
    depth_edge_filter: bool,
    depth_edge_radius_px: int,
    depth_edge_abs_m: float,
    depth_edge_rel_ratio: float,
    depth_edge_min_valid_neighbors: int,
) -> None:
    if not frames:
        raise ValueError("frames must not be empty")
    _positive_int("frame_stride", frame_stride)
    _positive_int("pixel_stride", pixel_stride)
    _positive_int("max_points", max_points)
    if isinstance(per_frame_max_points, (bool, np.bool_)) or not isinstance(
        per_frame_max_points, (int, np.integer)
    ):
        raise ValueError("per_frame_max_points must be an integer")
    if int(per_frame_max_points) < 0:
        raise ValueError("per_frame_max_points must be non-negative")
    if isinstance(progress_every, (bool, np.bool_)) or not isinstance(
        progress_every, (int, np.integer)
    ):
        raise ValueError("progress_every must be an integer")
    if int(progress_every) < 0:
        raise ValueError("progress_every must be non-negative")
    if not isinstance(depth_edge_filter, (bool, np.bool_)):
        raise ValueError("depth_edge_filter must be a boolean")
    radius = _positive_int("depth_edge_radius_px", depth_edge_radius_px)
    if isinstance(depth_edge_min_valid_neighbors, (bool, np.bool_)) or not isinstance(
        depth_edge_min_valid_neighbors, (int, np.integer)
    ):
        raise ValueError("depth_edge_min_valid_neighbors must be an integer")
    minimum_neighbors = int(depth_edge_min_valid_neighbors)
    available_neighbors = (2 * radius + 1) ** 2 - 1
    edge_absolute_threshold = float(depth_edge_abs_m)
    edge_relative_ratio = float(depth_edge_rel_ratio)
    if not np.isfinite(edge_absolute_threshold) or edge_absolute_threshold < 0.0:
        raise ValueError("depth_edge_abs_m must be a finite non-negative value")
    if not np.isfinite(edge_relative_ratio) or edge_relative_ratio < 0.0:
        raise ValueError("depth_edge_rel_ratio must be a finite non-negative value")
    if minimum_neighbors < 0:
        raise ValueError("depth_edge_min_valid_neighbors must be non-negative")
    if bool(depth_edge_filter):
        if not 1 <= minimum_neighbors <= available_neighbors:
            raise ValueError(
                "depth_edge_min_valid_neighbors must be between 1 and the "
                "neighborhood size"
            )
        if edge_absolute_threshold <= 0.0:
            raise ValueError("depth_edge_abs_m must be a finite positive value")
    voxel_size = float(voxel_size_m)
    minimum_depth = float(min_depth_m)
    maximum_depth = float(max_depth_m)
    top = float(roi_top_ratio)
    bottom = float(roi_bottom_ratio)
    if not np.isfinite(voxel_size) or voxel_size <= 0.0:
        raise ValueError("voxel_size_m must be a finite positive value")
    if (
        not np.isfinite(minimum_depth)
        or not np.isfinite(maximum_depth)
        or not 0.0 < minimum_depth < maximum_depth
    ):
        raise ValueError("depth range must satisfy 0 < min_depth_m < max_depth_m")
    if (
        not np.isfinite(top)
        or not np.isfinite(bottom)
        or not 0.0 <= top < bottom <= 1.0
    ):
        raise ValueError("ROI must satisfy 0 <= top < bottom <= 1")
    camera_values = (camera.fx, camera.fy, camera.cx, camera.cy)
    if camera.width <= 0 or camera.height <= 0:
        raise ValueError("camera width and height must be positive")
    if not all(np.isfinite(float(value)) for value in camera_values):
        raise ValueError("camera intrinsics must be finite")
    if camera.fx <= 0.0 or camera.fy <= 0.0:
        raise ValueError("camera fx and fy must be positive")
    _validate_trajectory(frames, trajectory)


def build_point_cloud(
    frames: list[FrameRecord],
    camera: CameraModel,
    trajectory: TrajectoryResult,
    frame_stride: int = 10,
    pixel_stride: int = 10,
    voxel_size_m: float = 0.25,
    max_points: int = 300_000,
    per_frame_max_points: int = 5_000,
    min_depth_m: float = 1.0,
    max_depth_m: float = 30.0,
    roi_top_ratio: float = 0.15,
    roi_bottom_ratio: float = 0.90,
    camera_offset_right_m: float = 0.0,
    camera_offset_down_m: float = 0.0,
    camera_offset_forward_m: float = 0.0,
    progress_every: int = 25,
    keyframe_distance_m: float | None = None,
    keyframe_angle_deg: float | None = None,
    keyframe_max_dt_s: float | None = None,
    gps_speed_m_s: np.ndarray | None = None,
    stationary_speed_threshold_m_s: float | None = None,
    stationary_min_duration_s: float = 2.0,
    stationary_max_cloud_frames: int = 5,
    depth_edge_filter: bool = False,
    depth_edge_radius_px: int = 1,
    depth_edge_abs_m: float = 0.18,
    depth_edge_rel_ratio: float = 0.03,
    depth_edge_min_valid_neighbors: int = 4,
    depth_quality_policy: DepthQualityPolicy | None = None,
    frame_audit: FrameAuditResult | None = None,
    support_enabled: bool = False,
    support_voxel_size_m: float = 0.15,
    support_far_voxel_size_m: float = 0.25,
    support_far_start_m: float = 20.0,
    support_min_independent_frames: int = 2,
    support_min_baseline_m: float = 0.4,
    support_min_time_separation_s: float = 0.5,
    max_support_position_std_m: float = 0.18,
    temporal_enabled: bool = False,
    temporal_window_seconds: float = 0.25,
    temporal_depth_abs_m: float = 0.15,
    temporal_depth_rel_ratio: float = 0.02,
    temporal_max_free_space_contradictions: int = 0,
    prefilter_removed_sample_max_points: int = 200_000,
) -> PointCloudResult:
    _validate_build_inputs(
        frames,
        camera,
        trajectory,
        frame_stride=frame_stride,
        pixel_stride=pixel_stride,
        voxel_size_m=voxel_size_m,
        max_points=max_points,
        per_frame_max_points=per_frame_max_points,
        min_depth_m=min_depth_m,
        max_depth_m=max_depth_m,
        roi_top_ratio=roi_top_ratio,
        roi_bottom_ratio=roi_bottom_ratio,
        progress_every=progress_every,
        depth_edge_filter=depth_edge_filter,
        depth_edge_radius_px=depth_edge_radius_px,
        depth_edge_abs_m=depth_edge_abs_m,
        depth_edge_rel_ratio=depth_edge_rel_ratio,
        depth_edge_min_valid_neighbors=depth_edge_min_valid_neighbors,
    )
    selected = select_frame_indices(
        frames,
        trajectory,
        frame_stride,
        keyframe_distance_m=keyframe_distance_m,
        keyframe_angle_deg=keyframe_angle_deg,
        keyframe_max_dt_s=keyframe_max_dt_s,
    )
    if frame_audit is not None:
        if frame_audit.use_for_cloud.shape != (len(frames),):
            raise ValueError("frame audit does not align with frames")
        selected = [index for index in selected if frame_audit.use_for_cloud[index]]
        if not selected:
            raise RuntimeError("frame audit excluded every cloud frame")
    stationary_mask = np.zeros(len(frames), dtype=bool)
    limited_stationary_mask = np.zeros(len(frames), dtype=bool)
    stationary_skipped_mask = np.zeros(len(frames), dtype=bool)
    stationary_run_count = 0
    stationary_candidate_count = 0
    stationary_retained_count = 0
    stationary_skipped_count = 0
    if stationary_speed_threshold_m_s is not None:
        if gps_speed_m_s is None:
            raise ValueError(
                "gps_speed_m_s is required when stationary filtering is enabled"
            )
        stationary_selection = limit_stationary_frame_indices(
            frames,
            selected,
            gps_speed_m_s,
            speed_threshold_m_s=stationary_speed_threshold_m_s,
            min_duration_s=stationary_min_duration_s,
            max_frames_per_run=stationary_max_cloud_frames,
        )
        selected = list(stationary_selection.selected_indices)
        stationary_mask = stationary_selection.stationary_mask
        limited_stationary_mask = stationary_selection.limited_stationary_mask
        stationary_skipped_mask = stationary_selection.skipped_mask
        stationary_run_count = stationary_selection.run_count
        stationary_candidate_count = stationary_selection.candidate_frame_count
        stationary_retained_count = stationary_selection.retained_frame_count
        stationary_skipped_count = stationary_selection.skipped_frame_count
    if depth_quality_policy is None:
        depth_quality_policy = DepthQualityPolicy(
            min_depth_m=float(min_depth_m),
            max_depth_m=float(max_depth_m),
            edge_enabled=bool(depth_edge_filter),
            edge_radius_px=int(depth_edge_radius_px),
            edge_abs_m=float(depth_edge_abs_m),
            edge_rel_ratio=float(depth_edge_rel_ratio),
            edge_min_valid_neighbors=int(depth_edge_min_valid_neighbors),
        )
    for name, value in (
        ("support_voxel_size_m", support_voxel_size_m),
        ("support_far_voxel_size_m", support_far_voxel_size_m),
        ("support_min_baseline_m", support_min_baseline_m),
        ("support_min_time_separation_s", support_min_time_separation_s),
        ("temporal_window_seconds", temporal_window_seconds),
        ("temporal_depth_abs_m", temporal_depth_abs_m),
    ):
        if not np.isfinite(float(value)) or float(value) <= 0.0:
            raise ValueError(f"{name} must be finite and positive")
    if int(support_min_independent_frames) < 1:
        raise ValueError("support_min_independent_frames must be at least 1")
    if int(temporal_max_free_space_contradictions) < 0:
        raise ValueError("temporal contradiction allowance must be non-negative")
    min_depth_mm = float(min_depth_m) * 1000.0
    max_depth_mm = float(max_depth_m) * 1000.0
    camera_offset_values = (
        camera_offset_right_m,
        camera_offset_down_m,
        camera_offset_forward_m,
    )
    if not all(np.isfinite(float(value)) for value in camera_offset_values):
        raise ValueError("camera offsets must be finite")
    camera_offset = np.asarray(camera_offset_values, dtype=np.float64)

    y0 = max(0, int(round(camera.height * float(roi_top_ratio))))
    y1 = min(camera.height, int(round(camera.height * float(roi_bottom_ratio))))
    yy, xx = np.mgrid[y0:y1:int(pixel_stride), 0:camera.width:int(pixel_stride)]
    sample_u = xx.reshape(-1)
    sample_v = yy.reshape(-1)
    candidates_per_frame = len(sample_u)
    accumulator = _VoxelRunAccumulator()
    near_support_accumulator = _CoarseSupportAccumulator()
    far_support_accumulator = _CoarseSupportAccumulator()
    depth_cache: OrderedDict[int, tuple[np.ndarray, object, bool]] = OrderedDict()
    frame_reports: list[dict[str, Any]] = [
        {
            "cloud_selected": int(index in selected),
            "gps_stationary": int(stationary_mask[index]),
            "stationary_episode_limited": int(limited_stationary_mask[index]),
            "stationary_cloud_skipped": int(stationary_skipped_mask[index]),
            "cloud_decoded": 0,
            "projection_candidate_count": 0,
            "depth_base_valid_count": 0,
            "depth_quality_removed_count": 0,
            "temporal_removed_count": 0,
            "fusion_contribution_count": 0,
        }
        for index in range(len(frames))
    ]
    cloud_positions = (
        np.asarray(frame_audit.positions_enu_m, dtype=np.float64)
        if frame_audit is not None
        else np.asarray(trajectory.positions_enu_m, dtype=np.float64)
    )
    cloud_rotations = (
        np.asarray(frame_audit.rotations_enu_from_camera, dtype=np.float64)
        if frame_audit is not None
        else np.asarray(trajectory.rotations_enu_from_camera, dtype=np.float64)
    )
    pose_scores = (
        np.asarray(frame_audit.quality_scores, dtype=np.float64)
        if frame_audit is not None
        else np.ones(len(frames), dtype=np.float64)
    )
    path_coordinate = np.concatenate(
        (
            np.zeros(1, dtype=np.float64),
            np.cumsum(np.linalg.norm(np.diff(cloud_positions[:, :2], axis=0), axis=1)),
        )
    )
    time_origin_ns = int(frames[0].monotonic_ns)

    def load_depth_quality(frame_index: int):
        cached = depth_cache.get(frame_index)
        if cached is not None:
            depth_cache.move_to_end(frame_index)
            return cached
        source = frames[frame_index]
        depth_image = cv2.imread(str(source.depth_path), cv2.IMREAD_UNCHANGED)
        if depth_image is None:
            raise ValueError(f"Failed to decode depth image: {source.depth_path}")
        confidence_image = None
        confidence_available = False
        if source.confidence_path is not None and source.confidence_path.is_file():
            confidence_image = cv2.imread(
                str(source.confidence_path), cv2.IMREAD_UNCHANGED
            )
            confidence_available = confidence_image is not None
        quality_result = evaluate_depth_quality(
            depth_image,
            depth_quality_policy,
            confidence_image,
        )
        value = (depth_image, quality_result, confidence_available)
        depth_cache[frame_index] = value
        while len(depth_cache) > 8:
            depth_cache.popitem(last=False)
        return value
    decoded = 0
    candidate_samples = 0
    valid_depth_samples = 0
    invalid_depth_samples = 0
    depth_edge_rejected = 0
    depth_edge_retained = 0
    confidence_map_available = False
    confidence_filter_applied = False
    depth_quality_rejected = 0
    far_depth_rejected = 0
    temporal_test_total = 0
    temporal_support_total = 0
    temporal_contradiction_total = 0
    temporal_rejected = 0
    discarded_by_per_frame_cap = 0
    points_before_voxel = 0

    for sequence_index, frame_index in enumerate(selected):
        frame = frames[frame_index]
        depth, depth_quality, frame_confidence_available = load_depth_quality(
            frame_index
        )
        color_bgr = cv2.imread(str(frame.rgb_path), cv2.IMREAD_COLOR)
        if color_bgr is None:
            continue
        if depth.ndim != 2 or depth.shape != (camera.height, camera.width):
            raise ValueError(f"Unexpected depth size at {frame.depth_path}: {depth.shape}")
        if color_bgr.shape != (camera.height, camera.width, 3):
            raise ValueError(f"Unexpected RGB size at {frame.rgb_path}: {color_bgr.shape}")
        decoded += 1
        frame_reports[frame_index]["cloud_decoded"] = 1
        confidence_map_available |= bool(frame_confidence_available)
        confidence_filter_applied |= (
            depth_quality.report["confidence_status"] == "applied"
        )
        candidate_samples += candidates_per_frame
        frame_reports[frame_index]["projection_candidate_count"] = candidates_per_frame
        d = depth[sample_v, sample_u].astype(np.float64)
        depth_range_valid = depth_quality.base_valid_mask[sample_v, sample_u]
        frame_valid_count = int(np.count_nonzero(depth_range_valid))
        frame_reports[frame_index]["depth_base_valid_count"] = frame_valid_count
        valid_depth_samples += frame_valid_count
        invalid_depth_samples += candidates_per_frame - frame_valid_count
        valid = depth_quality.valid_mask[sample_v, sample_u]
        frame_edge_retained = int(np.count_nonzero(valid))
        local_rejected = int(
            np.count_nonzero(
                depth_range_valid
                & ~depth_quality.local_consistency_mask[sample_v, sample_u]
            )
        )
        quality_removed = frame_valid_count - frame_edge_retained
        depth_edge_rejected += local_rejected
        depth_quality_rejected += quality_removed
        frame_reports[frame_index]["depth_quality_removed_count"] = quality_removed
        resolved_hard = depth_quality.resolved_hard_depth_m
        frame_far_rejected = (
            int(np.count_nonzero(depth_range_valid & (d / 1000.0 >= resolved_hard)))
            if resolved_hard is not None
            else 0
        )
        far_depth_rejected += frame_far_rejected
        depth_edge_retained += frame_edge_retained
        frame_reports[frame_index].update(
            {
                "far_risk_count": int(
                    np.count_nonzero(depth_quality.far_risk_mask[sample_v, sample_u])
                ),
                "far_hard_removed_count": frame_far_rejected,
                "resolved_far_hard_m": resolved_hard,
                "detected_far_peaks_m": ";".join(
                    f"{value:.3f}" for value in depth_quality.detected_far_peaks_m
                ),
                "confidence_status": depth_quality.report["confidence_status"],
                "depth_p50_m": depth_quality.report["depth_quantiles_m"].get("0.5"),
                "depth_p95_m": depth_quality.report["depth_quantiles_m"].get("0.95"),
                "depth_p99_m": depth_quality.report["depth_quantiles_m"].get("0.99"),
            }
        )
        if frame_edge_retained == 0:
            continue
        u = sample_u[valid].astype(np.float64)
        v = sample_v[valid].astype(np.float64)
        z = d[valid] / 1000.0
        edge_pass = np.ones(len(z), dtype=np.int64)
        far_risk = depth_quality.far_risk_mask[sample_v[valid], sample_u[valid]]
        points_camera = np.column_stack(
            (
                (u - camera.cx) * z / camera.fx,
                (v - camera.cy) * z / camera.fy,
                z,
            )
        )
        colors = color_bgr[sample_v[valid], sample_u[valid], ::-1].astype(
            np.uint8, copy=False
        )
        rotation = cloud_rotations[frame_index]
        camera_position = cloud_positions[frame_index] + rotation @ camera_offset
        points_enu = points_camera @ rotation.T + camera_position

        temporal_tests = np.zeros(len(points_enu), dtype=np.uint8)
        temporal_support = np.zeros(len(points_enu), dtype=np.uint8)
        temporal_contradictions = np.zeros(len(points_enu), dtype=np.uint8)
        if temporal_enabled and len(points_enu):
            neighbor_sequence_indices = range(
                max(0, sequence_index - 2),
                min(len(selected), sequence_index + 3),
            )
            for neighbor_sequence_index in neighbor_sequence_indices:
                if neighbor_sequence_index == sequence_index:
                    continue
                neighbor_index = selected[neighbor_sequence_index]
                time_delta_s = abs(
                    float(frames[neighbor_index].monotonic_ns - frame.monotonic_ns)
                    / 1e9
                )
                if time_delta_s > temporal_window_seconds:
                    continue
                neighbor_depth, neighbor_quality, _available = load_depth_quality(
                    neighbor_index
                )
                neighbor_rotation = cloud_rotations[neighbor_index]
                neighbor_position = (
                    cloud_positions[neighbor_index]
                    + neighbor_rotation @ camera_offset
                )
                states = classify_projective_depth(
                    points_enu,
                    camera,
                    neighbor_position,
                    neighbor_rotation,
                    neighbor_depth,
                    neighbor_quality.valid_mask,
                    absolute_tolerance_m=temporal_depth_abs_m,
                    relative_tolerance_ratio=temporal_depth_rel_ratio,
                )
                temporal_support += (
                    states == int(TemporalDepthState.SUPPORT)
                ).astype(np.uint8)
                temporal_contradictions += (
                    states == int(TemporalDepthState.FREE_SPACE_CONTRADICTION)
                ).astype(np.uint8)
            temporal_tests = temporal_support + temporal_contradictions
            reject_temporal = (
                (temporal_contradictions > temporal_max_free_space_contradictions)
                & (temporal_support == 0)
            )
            removed_temporal = int(np.count_nonzero(reject_temporal))
            temporal_rejected += removed_temporal
            frame_reports[frame_index]["temporal_removed_count"] = removed_temporal
            if removed_temporal:
                retain_temporal = ~reject_temporal
                points_camera = points_camera[retain_temporal]
                points_enu = points_enu[retain_temporal]
                colors = colors[retain_temporal]
                z = z[retain_temporal]
                edge_pass = edge_pass[retain_temporal]
                far_risk = far_risk[retain_temporal]
                temporal_tests = temporal_tests[retain_temporal]
                temporal_support = temporal_support[retain_temporal]
                temporal_contradictions = temporal_contradictions[retain_temporal]
        temporal_test_total += int(np.sum(temporal_tests, dtype=np.int64))
        temporal_support_total += int(np.sum(temporal_support, dtype=np.int64))
        temporal_contradiction_total += int(
            np.sum(temporal_contradictions, dtype=np.int64)
        )
        frame_cap = int(per_frame_max_points)
        if frame_cap and len(points_camera) > frame_cap:
            keep = np.linspace(0, len(points_camera) - 1, frame_cap, dtype=np.int64)
            discarded_by_per_frame_cap += len(points_camera) - frame_cap
            points_camera = points_camera[keep]
            colors = colors[keep]
            z = z[keep]
            edge_pass = edge_pass[keep]
            points_enu = points_enu[keep]
            far_risk = far_risk[keep]
            temporal_tests = temporal_tests[keep]
            temporal_support = temporal_support[keep]
            temporal_contradictions = temporal_contradictions[keep]
        points_before_voxel += len(points_camera)
        frame_reports[frame_index]["fusion_contribution_count"] = len(points_camera)
        if len(points_enu):
            relative_height = points_enu[:, 2] - camera_position[2]
            cross_track = np.linalg.norm(points_enu[:, :2] - camera_position[:2], axis=1)
            bounds_min = np.min(points_enu, axis=0)
            bounds_max = np.max(points_enu, axis=0)
            frame_reports[frame_index].update(
                {
                    "bbox_min_x": float(bounds_min[0]),
                    "bbox_min_y": float(bounds_min[1]),
                    "bbox_min_z": float(bounds_min[2]),
                    "bbox_max_x": float(bounds_max[0]),
                    "bbox_max_y": float(bounds_max[1]),
                    "bbox_max_z": float(bounds_max[2]),
                    "relative_z_p01_m": float(np.quantile(relative_height, 0.01)),
                    "relative_z_p99_m": float(np.quantile(relative_height, 0.99)),
                    "cross_track_p95_m": float(np.quantile(cross_track, 0.95)),
                    "cross_track_p99_m": float(np.quantile(cross_track, 0.99)),
                }
            )
        source_time_s = float(frame.monotonic_ns - time_origin_ns) / 1e9
        accumulator.add(
            _local_voxel_run(
                points_enu,
                colors,
                float(voxel_size_m),
                depths_m=z,
                edge_pass=edge_pass,
                temporal_test_count=temporal_tests,
                temporal_support_count=temporal_support,
                temporal_contradiction_count=temporal_contradictions,
                far_depth_risk=far_risk,
                frame_id=frame_index,
                source_time_s=source_time_s,
                pose_quality_score=float(pose_scores[frame_index]),
            )
        )
        if support_enabled:
            near = z < float(support_far_start_m)
            near_support_accumulator.add(
                _local_coarse_support_run(
                    points_enu[near],
                    z[near],
                    float(support_voxel_size_m),
                    time_s=source_time_s,
                    path_m=float(path_coordinate[frame_index]),
                )
            )
            far_support_accumulator.add(
                _local_coarse_support_run(
                    points_enu[~near],
                    z[~near],
                    float(support_far_voxel_size_m),
                    time_s=source_time_s,
                    path_m=float(path_coordinate[frame_index]),
                )
            )

        if progress_every and (
            (sequence_index + 1) % int(progress_every) == 0
            or sequence_index + 1 == len(selected)
        ):
            print(
                f"[cloud] {sequence_index + 1}/{len(selected)} frames, "
                f"{accumulator.run_entry_count:,} voxel-run entries",
                flush=True,
            )

    final_run = accumulator.finish()
    if final_run is None or len(final_run.keys) == 0:
        raise RuntimeError("No valid depth points survived point-cloud filtering")
    unique_voxel_count = len(final_run.keys)
    points, colors = _averages_from_run(final_run)
    metadata = _metadata_from_run(final_run)
    del final_run, accumulator

    points_before_quality_prefilter = len(points)
    coarse_support_rejected = 0
    prefilter_removed_points = np.empty((0, 3), dtype=np.float32)
    prefilter_removed_colors = np.empty((0, 3), dtype=np.uint8)
    if support_enabled:
        near_support_run = near_support_accumulator.finish()
        far_support_run = far_support_accumulator.finish()
        support_metadata = _map_coarse_support(
            points,
            metadata["mean_depth_m"],
            near_support_run,
            far_support_run,
            near_voxel_size_m=float(support_voxel_size_m),
            far_voxel_size_m=float(support_far_voxel_size_m),
            far_start_m=float(support_far_start_m),
            min_baseline_m=float(support_min_baseline_m),
            min_time_separation_s=float(support_min_time_separation_s),
        )
        metadata.update(support_metadata)
        independent = metadata["independent_view_count"]
        low_support = independent < int(support_min_independent_frames)
        mean_depth = metadata["mean_depth_m"]
        far = mean_depth >= float(support_far_start_m)
        middle = (mean_depth >= 12.0) & ~far
        contradiction = (
            metadata["temporal_contradiction_count"]
            > int(temporal_max_free_space_contradictions)
        ) & (metadata["temporal_support_count"] == 0)
        high_residual = (
            np.isfinite(metadata["support_position_std_m"])
            & (metadata["support_position_std_m"] > max_support_position_std_m)
        )
        poor_pose = metadata["pose_quality_score"] < 0.5
        far_untrusted = (
            far
            & low_support
            & (
                (metadata["far_depth_risk_count"] > 0)
                | contradiction
                | high_residual
                | poor_pose
            )
        )
        middle_untrusted = middle & low_support & (
            contradiction | high_residual | poor_pose
        )
        support_reject = far_untrusted | middle_untrusted | contradiction
        coarse_support_rejected = int(np.count_nonzero(support_reject))
        if coarse_support_rejected:
            removed_indices = np.flatnonzero(support_reject)
            sample_cap = int(prefilter_removed_sample_max_points)
            if sample_cap > 0 and len(removed_indices) > sample_cap:
                local_keep = spatially_sample_indices(points[removed_indices], sample_cap)
                removed_indices = removed_indices[local_keep]
            prefilter_removed_points = points[removed_indices].copy()
            prefilter_removed_colors = colors[removed_indices].copy()
            quality_keep = ~support_reject
            points = points[quality_keep]
            colors = colors[quality_keep]
            metadata = {
                name: values[quality_keep] for name, values in metadata.items()
            }
    else:
        del near_support_accumulator, far_support_accumulator

    points_before_final_cap = len(points)
    if points_before_final_cap > int(max_points):
        keep = np.sort(spatially_sample_indices(points, int(max_points)))
        points = points[keep]
        colors = colors[keep]
        metadata = {name: values[keep] for name, values in metadata.items()}
    discarded_by_final_cap = points_before_final_cap - len(points)
    stats = PointCloudBuildStats(
        total_frame_count=len(frames),
        sampled_frame_count=len(selected),
        decoded_frame_count=decoded,
        candidate_pixel_sample_count=candidate_samples,
        valid_depth_sample_count=valid_depth_samples,
        invalid_depth_sample_count=invalid_depth_samples,
        discarded_by_per_frame_cap=discarded_by_per_frame_cap,
        points_before_voxel=points_before_voxel,
        unique_voxel_count=unique_voxel_count,
        discarded_by_voxel=points_before_voxel - unique_voxel_count,
        points_before_final_cap=points_before_final_cap,
        discarded_by_final_cap=discarded_by_final_cap,
        depth_edge_rejected_count=depth_edge_rejected,
        depth_edge_retained_count=depth_edge_retained,
        confidence_map_available=confidence_map_available,
        confidence_filter_applied=confidence_filter_applied,
        depth_quality_rejected_count=depth_quality_rejected,
        far_depth_rejected_count=far_depth_rejected,
        temporal_test_count=temporal_test_total,
        temporal_support_count=temporal_support_total,
        temporal_contradiction_count=temporal_contradiction_total,
        temporal_rejected_count=temporal_rejected,
        coarse_support_rejected_count=coarse_support_rejected,
        points_before_quality_prefilter=points_before_quality_prefilter,
        points_after_quality_prefilter=points_before_final_cap,
        stationary_run_count=stationary_run_count,
        stationary_candidate_frame_count=stationary_candidate_count,
        stationary_retained_frame_count=stationary_retained_count,
        stationary_skipped_frame_count=stationary_skipped_count,
    )
    return PointCloudResult(
        points_enu_m=points,
        colors_rgb=colors,
        sampled_frame_count=len(selected),
        decoded_frame_count=decoded,
        valid_depth_sample_count=valid_depth_samples,
        stats=stats,
        observation_count=metadata["observation_count"],
        distinct_frame_count=metadata["distinct_frame_count"],
        position_std_m=metadata["position_std_m"],
        mean_depth_m=metadata["mean_depth_m"],
        depth_min_m=metadata["depth_min_m"],
        depth_max_m=metadata["depth_max_m"],
        depth_edge_pass_count=metadata["depth_edge_pass_count"],
        source_voxel_key=metadata["source_voxel_key"],
        support_observation_count=metadata.get("support_observation_count"),
        support_distinct_frame_count=metadata.get("support_distinct_frame_count"),
        independent_view_count=metadata.get("independent_view_count"),
        support_time_span_s=metadata.get("support_time_span_s"),
        support_path_span_m=metadata.get("support_path_span_m"),
        support_position_std_m=metadata.get("support_position_std_m"),
        support_depth_std_m=metadata.get("support_depth_std_m"),
        temporal_test_count=metadata["temporal_test_count"],
        temporal_support_count=metadata["temporal_support_count"],
        temporal_contradiction_count=metadata["temporal_contradiction_count"],
        far_depth_risk_count=metadata["far_depth_risk_count"],
        source_frame_id=metadata["source_frame_id"],
        mean_source_time_s=metadata["mean_source_time_s"],
        pose_quality_score=metadata["pose_quality_score"],
        frame_reports=tuple(frame_reports),
        prefilter_removed_points_enu_m=prefilter_removed_points,
        prefilter_removed_colors_rgb=prefilter_removed_colors,
    )
