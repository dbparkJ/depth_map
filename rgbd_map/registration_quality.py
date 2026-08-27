from __future__ import annotations

from typing import Any

import numpy as np
from scipy.spatial import cKDTree


_RANGE_BINS = (
    ("0-10", 0.0, 10.0),
    ("10-20", 10.0, 20.0),
    ("20-25", 20.0, 25.0),
    ("25-28.8", 25.0, 28.8),
    ("28.8+", 28.8, np.inf),
)


def _summary(values: list[np.ndarray]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "p50_m": None, "p95_m": None, "mean_m": None}
    array = np.concatenate(values)
    array = array[np.isfinite(array)]
    if len(array) == 0:
        return {"count": 0, "p50_m": None, "p95_m": None, "mean_m": None}
    return {
        "count": int(len(array)),
        "p50_m": float(np.percentile(array, 50)),
        "p95_m": float(np.percentile(array, 95)),
        "mean_m": float(np.mean(array)),
    }


def compute_adjacent_frame_registration_quality(
    points_enu_m: np.ndarray,
    source_frame_id: np.ndarray,
    mean_depth_m: np.ndarray,
    *,
    max_frame_pairs: int = 30,
    max_points_per_frame: int = 3000,
    overlap_distance_m: float = 0.30,
) -> dict[str, Any]:
    """Estimate adjacent-frame overlap and local point-to-plane residuals.

    Only compact single-source fine voxels are used. Multi-source voxels remain
    represented in the map but cannot be assigned to one frame without expanding
    provenance memory.
    """

    points = np.asarray(points_enu_m, dtype=np.float64)
    frames = np.asarray(source_frame_id)
    depths = np.asarray(mean_depth_m, dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_enu_m must have shape (N, 3)")
    if frames.shape != (len(points),) or depths.shape != (len(points),):
        raise ValueError("registration provenance arrays must align with points")
    valid_frames = np.unique(frames[frames >= 0]).astype(np.int64)
    available = set(int(value) for value in valid_frames)
    pairs = [(value, value + 1) for value in valid_frames if int(value + 1) in available]
    if len(pairs) > max_frame_pairs:
        positions = np.linspace(0, len(pairs) - 1, max_frame_pairs, dtype=np.int64)
        pairs = [pairs[int(index)] for index in positions]
    residuals: dict[str, list[np.ndarray]] = {name: [] for name, _low, _high in _RANGE_BINS}
    point_distances: list[np.ndarray] = []
    overlap_numerator = 0
    overlap_denominator = 0
    evaluated_pairs = 0
    frame_pair_metrics: list[dict[str, Any]] = []
    for source_frame, target_frame in pairs:
        source_indices = np.flatnonzero(frames == source_frame)
        target_indices = np.flatnonzero(frames == target_frame)
        if len(source_indices) < 3 or len(target_indices) < 3:
            continue
        if len(source_indices) > max_points_per_frame:
            source_indices = source_indices[
                np.linspace(
                    0, len(source_indices) - 1, max_points_per_frame, dtype=np.int64
                )
            ]
        if len(target_indices) > max_points_per_frame * 2:
            target_indices = target_indices[
                np.linspace(
                    0,
                    len(target_indices) - 1,
                    max_points_per_frame * 2,
                    dtype=np.int64,
                )
            ]
        source_points = points[source_indices]
        target_points = points[target_indices]
        tree = cKDTree(target_points)
        distances, neighbor_indices = tree.query(source_points, k=3, workers=1)
        nearest_distance = distances[:, 0]
        point_distances.append(nearest_distance)
        overlap_numerator += int(np.count_nonzero(nearest_distance <= overlap_distance_m))
        overlap_denominator += len(nearest_distance)
        neighbors = target_points[neighbor_indices]
        first = neighbors[:, 1] - neighbors[:, 0]
        second = neighbors[:, 2] - neighbors[:, 0]
        normals = np.cross(first, second)
        normal_norm = np.linalg.norm(normals, axis=1)
        valid_normal = normal_norm > 1e-8
        normals[valid_normal] /= normal_norm[valid_normal, None]
        displacement = source_points - neighbors[:, 0]
        plane_residual = np.full(len(source_points), np.nan, dtype=np.float64)
        plane_residual[valid_normal] = np.abs(
            np.sum(displacement[valid_normal] * normals[valid_normal], axis=1)
        )
        source_depth = depths[source_indices]
        pair_by_depth: dict[str, dict[str, float | int | None]] = {}
        for name, low, high in _RANGE_BINS:
            selected = valid_normal & (source_depth >= low) & (source_depth < high)
            if np.any(selected):
                residuals[name].append(plane_residual[selected])
                pair_by_depth[name] = _summary([plane_residual[selected]])
            else:
                pair_by_depth[name] = _summary([])
        frame_pair_metrics.append(
            {
                "source_frame": int(source_frame),
                "target_frame": int(target_frame),
                "source_point_count": int(len(source_points)),
                "overlap_ratio": float(
                    np.count_nonzero(nearest_distance <= overlap_distance_m)
                    / len(nearest_distance)
                ),
                "point_to_point": _summary([nearest_distance]),
                "point_to_plane_by_depth_m": pair_by_depth,
            }
        )
        evaluated_pairs += 1
    return {
        "format_version": 1,
        "method": "adjacent_single_source_voxel_local_3nn_point_to_plane",
        "evaluated_frame_pair_count": evaluated_pairs,
        "max_frame_pairs": int(max_frame_pairs),
        "max_points_per_frame": int(max_points_per_frame),
        "overlap_distance_m": float(overlap_distance_m),
        "overlap_ratio": (
            float(overlap_numerator / overlap_denominator)
            if overlap_denominator
            else None
        ),
        "point_to_point": _summary(point_distances),
        "point_to_plane_by_depth_m": {
            name: _summary(values) for name, values in residuals.items()
        },
        "frame_pairs": frame_pair_metrics,
        "limitations": (
            "This is an internal adjacent-frame consistency diagnostic, not an "
            "independent surveyed accuracy measurement. Dynamic objects and local "
            "3-NN normal degeneracy can bias it."
        ),
    }
