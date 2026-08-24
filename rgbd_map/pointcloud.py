from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .dataset import CameraModel, FrameRecord
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
    )


def _local_voxel_run(
    points_enu_m: np.ndarray,
    colors_rgb: np.ndarray,
    voxel_size_m: float,
    depths_m: np.ndarray | None = None,
    edge_pass: np.ndarray | None = None,
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
    depth_edge_filter: bool = False,
    depth_edge_radius_px: int = 1,
    depth_edge_abs_m: float = 0.18,
    depth_edge_rel_ratio: float = 0.03,
    depth_edge_min_valid_neighbors: int = 4,
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
    decoded = 0
    candidate_samples = 0
    valid_depth_samples = 0
    invalid_depth_samples = 0
    depth_edge_rejected = 0
    depth_edge_retained = 0
    confidence_map_available = False
    discarded_by_per_frame_cap = 0
    points_before_voxel = 0

    for sequence_index, frame_index in enumerate(selected):
        frame = frames[frame_index]
        depth = cv2.imread(str(frame.depth_path), cv2.IMREAD_UNCHANGED)
        color_bgr = cv2.imread(str(frame.rgb_path), cv2.IMREAD_COLOR)
        if depth is None or color_bgr is None:
            continue
        if depth.ndim != 2 or depth.shape != (camera.height, camera.width):
            raise ValueError(f"Unexpected depth size at {frame.depth_path}: {depth.shape}")
        if color_bgr.shape != (camera.height, camera.width, 3):
            raise ValueError(f"Unexpected RGB size at {frame.rgb_path}: {color_bgr.shape}")
        decoded += 1
        if not confidence_map_available and frame.confidence_path is not None:
            confidence = None
            if frame.confidence_path.is_file():
                try:
                    confidence = cv2.imread(
                        str(frame.confidence_path), cv2.IMREAD_UNCHANGED
                    )
                except cv2.error:
                    confidence = None
            confidence_map_available = (
                confidence is not None and confidence.shape[:2] == depth.shape
            )
        candidate_samples += candidates_per_frame
        d = depth[sample_v, sample_u].astype(np.float64)
        depth_range_valid = (
            np.isfinite(d)
            & (d != 0.0)
            & (d != 65535.0)
            & (d >= min_depth_mm)
            & (d <= max_depth_mm)
        )
        frame_valid_count = int(np.count_nonzero(depth_range_valid))
        valid_depth_samples += frame_valid_count
        invalid_depth_samples += candidates_per_frame - frame_valid_count
        if bool(depth_edge_filter) and frame_valid_count:
            full_depth = depth.astype(np.float64, copy=False)
            full_valid = (
                np.isfinite(full_depth)
                & (full_depth != 0.0)
                & (full_depth != 65535.0)
                & (full_depth >= min_depth_mm)
                & (full_depth <= max_depth_mm)
            )
            edge_keep = depth_edge_keep_mask(
                depth,
                full_valid,
                radius_px=int(depth_edge_radius_px),
                abs_threshold_m=float(depth_edge_abs_m),
                rel_ratio=float(depth_edge_rel_ratio),
                min_valid_neighbors=int(depth_edge_min_valid_neighbors),
            )
            valid = depth_range_valid & edge_keep[sample_v, sample_u]
            frame_edge_retained = int(np.count_nonzero(valid))
            depth_edge_rejected += frame_valid_count - frame_edge_retained
        else:
            valid = depth_range_valid
            frame_edge_retained = frame_valid_count
        depth_edge_retained += frame_edge_retained
        if frame_edge_retained == 0:
            continue
        u = sample_u[valid].astype(np.float64)
        v = sample_v[valid].astype(np.float64)
        z = d[valid] / 1000.0
        edge_pass = np.ones(len(z), dtype=np.int64)
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
        frame_cap = int(per_frame_max_points)
        if frame_cap and len(points_camera) > frame_cap:
            keep = np.linspace(0, len(points_camera) - 1, frame_cap, dtype=np.int64)
            discarded_by_per_frame_cap += len(points_camera) - frame_cap
            points_camera = points_camera[keep]
            colors = colors[keep]
            z = z[keep]
            edge_pass = edge_pass[keep]
        points_before_voxel += len(points_camera)

        rotation = trajectory.rotations_enu_from_camera[frame_index]
        camera_position = trajectory.positions_enu_m[frame_index] + rotation @ camera_offset
        points_enu = points_camera @ rotation.T + camera_position
        accumulator.add(
            _local_voxel_run(
                points_enu,
                colors,
                float(voxel_size_m),
                depths_m=z,
                edge_pass=edge_pass,
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
        confidence_filter_applied=False,
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
    )
