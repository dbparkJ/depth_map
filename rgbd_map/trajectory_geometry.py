from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree


@dataclass(frozen=True)
class TrajectoryCoordinates:
    along_track_m: np.ndarray
    cross_track_m: np.ndarray
    signed_cross_track_m: np.ndarray
    local_up_m: np.ndarray
    trajectory_z_m: np.ndarray
    nearest_segment_index: np.ndarray
    segment_fraction: np.ndarray
    in_endpoint_buffer: np.ndarray


def project_to_trajectory_polyline(
    points_enu_m: np.ndarray,
    trajectory_enu_m: np.ndarray,
    *,
    endpoint_buffer_m: float = 0.0,
    chunk_size: int = 1_000_000,
) -> TrajectoryCoordinates:
    """Project points onto adjacent segments around the nearest waypoint.

    The waypoint KD-tree makes this O(N log M); evaluating both adjacent
    segments avoids the corner error caused by using the nearest waypoint itself.
    """

    points = np.asarray(points_enu_m, dtype=np.float64)
    trajectory = np.asarray(trajectory_enu_m, dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_enu_m must have shape (N, 3)")
    if trajectory.ndim != 2 or trajectory.shape[1:] != (3,):
        raise ValueError("trajectory_enu_m must have shape (M, 3)")
    trajectory = trajectory[np.all(np.isfinite(trajectory), axis=1)]
    if len(trajectory) < 2:
        raise ValueError("trajectory must contain at least two finite points")
    if endpoint_buffer_m < 0.0 or not np.isfinite(endpoint_buffer_m):
        raise ValueError("endpoint_buffer_m must be finite and non-negative")
    segment_xy = trajectory[1:, :2] - trajectory[:-1, :2]
    segment_length_squared = np.sum(segment_xy * segment_xy, axis=1)
    segment_length = np.sqrt(segment_length_squared)
    valid_segment = segment_length_squared > 1e-12
    if not np.any(valid_segment):
        raise ValueError("trajectory has no non-degenerate horizontal segment")
    cumulative = np.concatenate(
        (np.zeros(1, dtype=np.float64), np.cumsum(segment_length))
    )
    waypoint_tree = cKDTree(trajectory[:, :2])
    count = len(points)
    along = np.full(count, np.nan, dtype=np.float64)
    cross = np.full(count, np.nan, dtype=np.float64)
    signed_cross = np.full(count, np.nan, dtype=np.float64)
    trajectory_z = np.full(count, np.nan, dtype=np.float64)
    segment_output = np.full(count, -1, dtype=np.int64)
    fraction_output = np.full(count, np.nan, dtype=np.float64)
    endpoint = np.zeros(count, dtype=bool)
    for begin in range(0, count, int(chunk_size)):
        end = min(begin + int(chunk_size), count)
        local = points[begin:end]
        finite = np.all(np.isfinite(local), axis=1)
        if not np.any(finite):
            continue
        local_indices = np.flatnonzero(finite)
        _, nearest_waypoint = waypoint_tree.query(
            local[local_indices, :2], k=1, workers=1
        )
        candidates = np.column_stack(
            (
                np.clip(nearest_waypoint - 1, 0, len(segment_xy) - 1),
                np.clip(nearest_waypoint, 0, len(segment_xy) - 1),
            )
        )
        candidate_distance = np.full((len(local_indices), 2), np.inf)
        candidate_fraction = np.zeros((len(local_indices), 2), dtype=np.float64)
        candidate_signed = np.zeros((len(local_indices), 2), dtype=np.float64)
        xy = local[local_indices, :2]
        for option in range(2):
            segment_index = candidates[:, option]
            start = trajectory[segment_index, :2]
            vector = segment_xy[segment_index]
            denom = segment_length_squared[segment_index]
            raw_fraction = np.zeros(len(local_indices), dtype=np.float64)
            usable = denom > 1e-12
            raw_fraction[usable] = np.sum(
                (xy[usable] - start[usable]) * vector[usable], axis=1
            ) / denom[usable]
            fraction = np.clip(raw_fraction, 0.0, 1.0)
            projected = start + fraction[:, None] * vector
            offset = xy - projected
            distance = np.linalg.norm(offset, axis=1)
            candidate_distance[:, option] = distance
            candidate_fraction[:, option] = fraction
            tangent_norm = np.maximum(segment_length[segment_index], 1e-12)
            candidate_signed[:, option] = (
                vector[:, 0] * offset[:, 1] - vector[:, 1] * offset[:, 0]
            ) / tangent_norm
        choice = np.argmin(candidate_distance, axis=1)
        row = np.arange(len(local_indices))
        chosen_segment = candidates[row, choice]
        chosen_fraction = candidate_fraction[row, choice]
        chosen_cross = candidate_distance[row, choice]
        global_indices = begin + local_indices
        along[global_indices] = (
            cumulative[chosen_segment]
            + chosen_fraction * segment_length[chosen_segment]
        )
        cross[global_indices] = chosen_cross
        signed_cross[global_indices] = candidate_signed[row, choice]
        trajectory_z[global_indices] = (
            (1.0 - chosen_fraction) * trajectory[chosen_segment, 2]
            + chosen_fraction * trajectory[chosen_segment + 1, 2]
        )
        segment_output[global_indices] = chosen_segment
        fraction_output[global_indices] = chosen_fraction
        if endpoint_buffer_m > 0.0:
            start_distance = np.linalg.norm(
                local[local_indices] - trajectory[0], axis=1
            )
            end_distance = np.linalg.norm(
                local[local_indices] - trajectory[-1], axis=1
            )
            endpoint[global_indices] = (
                np.minimum(start_distance, end_distance) <= endpoint_buffer_m
            )
    return TrajectoryCoordinates(
        along_track_m=along,
        cross_track_m=cross,
        signed_cross_track_m=signed_cross,
        local_up_m=points[:, 2] - trajectory_z,
        trajectory_z_m=trajectory_z,
        nearest_segment_index=segment_output,
        segment_fraction=fraction_output,
        in_endpoint_buffer=endpoint,
    )
