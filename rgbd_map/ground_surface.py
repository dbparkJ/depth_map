from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy.spatial import cKDTree

from .postprocess_config import PostprocessConfig
from .trajectory_geometry import project_to_trajectory_polyline


@dataclass(frozen=True)
class GroundSurfaceStats:
    ground_candidate_point_count: int
    valid_surface_cell_count: int
    unsupported_surface_cell_count: int
    corridor_point_count: int
    below_surface_candidate_count: int
    below_surface_removed_count: int
    surface_z_min: float | None
    surface_z_max: float | None
    surface_z_median: float | None
    measured_surface_cell_count: int = 0
    interpolated_surface_cell_count: int = 0
    fallback_surface_cell_count: int = 0
    unknown_surface_cell_count: int = 0
    reliable_apply_point_count: int = 0

    def to_dict(self) -> dict[str, int | float | None]:
        return asdict(self)


@dataclass(frozen=True)
class GroundSurfaceResult:
    point_surface_z_m: np.ndarray
    in_corridor_mask: np.ndarray
    below_surface_mask: np.ndarray
    ground_candidate_mask: np.ndarray
    cell_keys_xy: np.ndarray
    cell_surface_z_m: np.ndarray
    cell_in_corridor: np.ndarray
    stats: GroundSurfaceStats
    point_surface_uncertainty_m: np.ndarray
    point_surface_provenance: np.ndarray
    cell_surface_uncertainty_m: np.ndarray
    cell_surface_provenance: np.ndarray


def _empty_result(count: int) -> GroundSurfaceResult:
    return GroundSurfaceResult(
        point_surface_z_m=np.full(count, np.nan, dtype=np.float32),
        in_corridor_mask=np.zeros(count, dtype=bool),
        below_surface_mask=np.zeros(count, dtype=bool),
        ground_candidate_mask=np.zeros(count, dtype=bool),
        cell_keys_xy=np.empty((0, 2), dtype=np.int64),
        cell_surface_z_m=np.empty(0, dtype=np.float32),
        cell_in_corridor=np.empty(0, dtype=bool),
        stats=GroundSurfaceStats(
            ground_candidate_point_count=0,
            valid_surface_cell_count=0,
            unsupported_surface_cell_count=0,
            corridor_point_count=0,
            below_surface_candidate_count=0,
            below_surface_removed_count=0,
            surface_z_min=None,
            surface_z_max=None,
            surface_z_median=None,
        ),
        point_surface_uncertainty_m=np.full(count, np.inf, dtype=np.float32),
        point_surface_provenance=np.zeros(count, dtype=np.uint8),
        cell_surface_uncertainty_m=np.empty(0, dtype=np.float32),
        cell_surface_provenance=np.empty(0, dtype=np.uint8),
    )


def _cell_groups(
    points: np.ndarray, finite_indices: np.ndarray, grid_size_m: float
) -> tuple[np.ndarray, list[np.ndarray]]:
    keys = np.floor(points[finite_indices, :2] / grid_size_m).astype(np.int64)
    order = np.lexsort((keys[:, 1], keys[:, 0]))
    sorted_keys = keys[order]
    starts = np.empty(len(order), dtype=bool)
    starts[0] = True
    starts[1:] = np.any(sorted_keys[1:] != sorted_keys[:-1], axis=1)
    boundaries = np.flatnonzero(starts)
    ends = np.append(boundaries[1:], len(order))
    unique_keys = sorted_keys[boundaries]
    groups = [
        finite_indices[order[start:end]]
        for start, end in zip(boundaries, ends, strict=True)
    ]
    return unique_keys, groups


def _dense_lower_mode(z_values: np.ndarray, bin_size_m: float) -> float | None:
    if len(z_values) == 0:
        return None
    lower_limit = float(np.quantile(z_values, 0.60))
    lower = z_values[z_values <= lower_limit + 1e-12]
    if len(lower) == 0:
        return None
    origin = float(np.min(lower))
    bin_index = np.floor((lower - origin) / bin_size_m).astype(np.int64)
    unique_bins, counts = np.unique(bin_index, return_counts=True)
    densest_count = int(np.max(counts))
    required_support = max(3, int(np.ceil(len(z_values) * 0.15)))
    if densest_count < required_support:
        return None
    # np.unique sorts, so argmax chooses the lower bin when support ties.
    mode_bin = int(unique_bins[int(np.argmax(counts))])
    mode_center = origin + (mode_bin + 0.5) * bin_size_m
    adjacent = z_values[np.abs(z_values - mode_center) <= 1.5 * bin_size_m]
    if len(adjacent) < required_support:
        return None
    return float(np.median(adjacent))


def estimate_local_ground_surface(
    points_enu_m: np.ndarray,
    trajectory_enu_m: np.ndarray | None,
    config: PostprocessConfig,
    *,
    support_mask: np.ndarray | None = None,
) -> GroundSurfaceResult:
    """Estimate a supported lower surface only inside the trajectory corridor.

    The surface is estimated from a dense lower mode, never the minimum Z.
    ``support_mask`` can exclude points already rejected by earlier stages while
    surfaces are still mapped back to every raw input point for diagnostics.
    """

    points = np.asarray(points_enu_m)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_enu_m must have shape (N, 3)")
    if not np.issubdtype(points.dtype, np.number):
        raise ValueError("points_enu_m must be numeric")
    count = len(points)
    if not config.enabled or trajectory_enu_m is None or count == 0:
        return _empty_result(count)
    trajectory = np.asarray(trajectory_enu_m, dtype=np.float64)
    if trajectory.ndim != 2 or trajectory.shape[1:] != (3,):
        raise ValueError("trajectory_enu_m must have shape (M, 3)")
    trajectory = trajectory[np.all(np.isfinite(trajectory), axis=1)]
    if len(trajectory) < 2:
        return _empty_result(count)
    finite_mask = np.all(np.isfinite(points), axis=1)
    if support_mask is None:
        support = finite_mask.copy()
    else:
        support = np.asarray(support_mask, dtype=bool)
        if support.shape != (count,):
            raise ValueError("support_mask must have shape (N,)")
        support = support & finite_mask
    finite_indices = np.flatnonzero(finite_mask)
    if len(finite_indices) == 0:
        return _empty_result(count)

    cell_keys, groups = _cell_groups(
        points, finite_indices, config.ground_grid_size_m
    )
    centers_xy = (cell_keys.astype(np.float64) + 0.5) * config.ground_grid_size_m
    center_points = np.column_stack((centers_xy, np.zeros(len(centers_xy))))
    coordinates = project_to_trajectory_polyline(center_points, trajectory)
    if config.preset == "road-map-temporal":
        seed_width = config.ground_seed_half_width_m
        apply_width = config.ground_apply_half_width_m
    else:
        # Preserve the historical road_corridor_half_width_m contract.
        seed_width = config.road_corridor_half_width_m
        apply_width = config.road_corridor_half_width_m
    cell_seed_corridor = coordinates.cross_track_m <= seed_width
    cell_in_corridor = coordinates.cross_track_m <= apply_width
    nearest_camera_z = coordinates.trajectory_z_m

    ground_candidate_mask = np.zeros(count, dtype=bool)
    initial_surface = np.full(len(cell_keys), np.nan, dtype=np.float64)
    for cell_index, point_indices in enumerate(groups):
        if not cell_seed_corridor[cell_index]:
            continue
        usable = point_indices[support[point_indices]]
        if len(usable) == 0:
            continue
        camera_z = nearest_camera_z[cell_index]
        lower = camera_z - config.ground_candidate_below_camera_m
        upper = camera_z - config.ground_candidate_above_surface_m
        candidates = usable[
            (points[usable, 2] >= lower) & (points[usable, 2] <= upper)
        ]
        ground_candidate_mask[candidates] = True
        if len(candidates) < config.ground_min_cell_points:
            continue
        mode_surface = _dense_lower_mode(points[candidates, 2], config.ground_z_bin_m)
        if mode_surface is not None:
            initial_surface[cell_index] = mode_surface

    key_to_cell = {
        (int(key[0]), int(key[1])): index for index, key in enumerate(cell_keys)
    }
    smoothed_surface = np.full(len(cell_keys), np.nan, dtype=np.float64)
    for cell_index, key in enumerate(cell_keys):
        own_surface = initial_surface[cell_index]
        if not np.isfinite(own_surface):
            continue
        neighbor_values: list[float] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbor_index = key_to_cell.get(
                    (int(key[0]) + dx, int(key[1]) + dy)
                )
                if neighbor_index is None:
                    continue
                value = initial_surface[neighbor_index]
                if np.isfinite(value):
                    neighbor_values.append(float(value))
        if len(neighbor_values) < config.ground_min_neighbor_cells:
            continue
        neighbor_array = np.asarray(neighbor_values, dtype=np.float64)
        median = float(np.median(neighbor_array))
        if abs(float(own_surface) - median) > config.ground_max_neighbor_height_delta_m:
            continue
        if (
            float(np.max(neighbor_array) - np.min(neighbor_array))
            > 2.0 * config.ground_max_neighbor_height_delta_m
        ):
            continue
        smoothed_surface[cell_index] = median

    measured = np.isfinite(smoothed_surface)
    surface = smoothed_surface.copy()
    uncertainty = np.full(len(cell_keys), np.inf, dtype=np.float64)
    provenance = np.zeros(len(cell_keys), dtype=np.uint8)
    uncertainty[measured] = max(0.025, config.ground_z_bin_m)
    provenance[measured] = 1  # measured

    # Fill along-track gaps first, then allow conservative lateral extrapolation.
    target = cell_in_corridor & ~measured
    measured_indices = np.flatnonzero(measured)
    if len(measured_indices) and np.any(target):
        measured_along = coordinates.along_track_m[measured_indices]
        order = np.argsort(measured_along, kind="stable")
        measured_indices = measured_indices[order]
        measured_along = measured_along[order]
        target_indices = np.flatnonzero(target)
        insertion = np.searchsorted(
            measured_along, coordinates.along_track_m[target_indices]
        )
        left = np.clip(insertion - 1, 0, len(measured_indices) - 1)
        right = np.clip(insertion, 0, len(measured_indices) - 1)
        left_gap = np.abs(
            coordinates.along_track_m[target_indices] - measured_along[left]
        )
        right_gap = np.abs(
            coordinates.along_track_m[target_indices] - measured_along[right]
        )
        choose_right = right_gap < left_gap
        chosen_order = np.where(choose_right, right, left)
        chosen = measured_indices[chosen_order]
        along_gap = np.minimum(left_gap, right_gap)
        valid_gap = along_gap <= config.ground_max_interpolation_gap_m
        interpolation_targets = target_indices[valid_gap]
        interpolation_sources = chosen[valid_gap]
        if len(interpolation_targets):
            # Follow trajectory elevation longitudinally. Lateral ground slope is
            # bounded rather than guessed from a sparse cell minimum.
            surface[interpolation_targets] = (
                surface[interpolation_sources]
                + coordinates.trajectory_z_m[interpolation_targets]
                - coordinates.trajectory_z_m[interpolation_sources]
            )
            lateral_gap = np.abs(
                coordinates.signed_cross_track_m[interpolation_targets]
                - coordinates.signed_cross_track_m[interpolation_sources]
            )
            uncertainty[interpolation_targets] = (
                0.06 + 0.03 * along_gap[valid_gap] + 0.01 * lateral_gap
            )
            provenance[interpolation_targets] = 2  # interpolated

    # Camera-height fallback is explicitly marked and only available after a
    # supported profile has been measured in enough cells.
    remaining = cell_in_corridor & ~np.isfinite(surface)
    if np.count_nonzero(measured) >= 5 and np.any(remaining):
        camera_height = coordinates.trajectory_z_m[measured] - surface[measured]
        median_height = float(np.median(camera_height))
        robust_spread = float(
            1.4826 * np.median(np.abs(camera_height - median_height))
        )
        if np.isfinite(median_height) and 0.5 <= median_height <= 4.0:
            surface[remaining] = coordinates.trajectory_z_m[remaining] - median_height
            uncertainty[remaining] = max(0.20, robust_spread + 0.15)
            provenance[remaining] = 3  # fallback

    point_surface = np.full(count, np.nan, dtype=np.float32)
    in_corridor = np.zeros(count, dtype=bool)
    for cell_index, point_indices in enumerate(groups):
        if cell_in_corridor[cell_index]:
            in_corridor[point_indices] = True
        if np.isfinite(surface[cell_index]):
            point_surface[point_indices] = surface[cell_index]

    point_uncertainty = np.full(count, np.inf, dtype=np.float32)
    point_provenance = np.zeros(count, dtype=np.uint8)
    for cell_index, point_indices in enumerate(groups):
        if np.isfinite(surface[cell_index]):
            point_uncertainty[point_indices] = uncertainty[cell_index]
            point_provenance[point_indices] = provenance[cell_index]

    below_surface = (
        finite_mask
        & in_corridor
        & np.isfinite(point_surface)
        & (point_uncertainty <= config.ground_max_uncertainty_m)
        & (points[:, 2] < point_surface - config.below_ground_tolerance_m)
    )
    valid_surface = np.isfinite(surface)
    supported_corridor_cell_count = int(np.count_nonzero(cell_in_corridor))
    valid_values = surface[valid_surface]
    stats = GroundSurfaceStats(
        ground_candidate_point_count=int(np.count_nonzero(ground_candidate_mask)),
        valid_surface_cell_count=int(np.count_nonzero(valid_surface)),
        unsupported_surface_cell_count=(
            supported_corridor_cell_count - int(np.count_nonzero(valid_surface))
        ),
        corridor_point_count=int(np.count_nonzero(in_corridor)),
        below_surface_candidate_count=int(np.count_nonzero(below_surface)),
        below_surface_removed_count=int(np.count_nonzero(below_surface)),
        surface_z_min=(float(np.min(valid_values)) if len(valid_values) else None),
        surface_z_max=(float(np.max(valid_values)) if len(valid_values) else None),
        surface_z_median=(float(np.median(valid_values)) if len(valid_values) else None),
        measured_surface_cell_count=int(np.count_nonzero(provenance == 1)),
        interpolated_surface_cell_count=int(np.count_nonzero(provenance == 2)),
        fallback_surface_cell_count=int(np.count_nonzero(provenance == 3)),
        unknown_surface_cell_count=int(np.count_nonzero(cell_in_corridor & (provenance == 0))),
        reliable_apply_point_count=int(
            np.count_nonzero(
                in_corridor & (point_uncertainty <= config.ground_max_uncertainty_m)
            )
        ),
    )
    return GroundSurfaceResult(
        point_surface_z_m=point_surface,
        in_corridor_mask=in_corridor,
        below_surface_mask=below_surface,
        ground_candidate_mask=ground_candidate_mask,
        cell_keys_xy=cell_keys,
        cell_surface_z_m=surface.astype(np.float32),
        cell_in_corridor=cell_in_corridor,
        stats=stats,
        point_surface_uncertainty_m=point_uncertainty,
        point_surface_provenance=point_provenance,
        cell_surface_uncertainty_m=uncertainty.astype(np.float32),
        cell_surface_provenance=provenance,
    )
