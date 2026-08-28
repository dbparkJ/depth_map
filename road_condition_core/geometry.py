from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import distance_transform_edt, gaussian_filter
from scipy.spatial import cKDTree

from .config import SurfaceConfig
from .models import SurfaceGrid


@dataclass(frozen=True)
class TrackCoordinates:
    along_track_m: np.ndarray
    signed_cross_track_m: np.ndarray
    trajectory_z_m: np.ndarray
    nearest_segment_index: np.ndarray
    segment_fraction: np.ndarray
    trajectory_cumulative_m: np.ndarray


def _trajectory_lengths(trajectory_enu_m: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    trajectory = np.asarray(trajectory_enu_m, dtype=np.float64)
    segment_xy = trajectory[1:, :2] - trajectory[:-1, :2]
    segment_length = np.linalg.norm(segment_xy, axis=1)
    cumulative = np.concatenate((np.zeros(1, dtype=np.float64), np.cumsum(segment_length)))
    return segment_xy, segment_length, cumulative


def project_to_trajectory(
    points_enu_m: np.ndarray,
    trajectory_enu_m: np.ndarray,
    *,
    chunk_size: int = 500_000,
) -> TrackCoordinates:
    """Project ENU points to along-track and signed cross-track coordinates."""

    points = np.asarray(points_enu_m, dtype=np.float64)
    trajectory = np.asarray(trajectory_enu_m, dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_enu_m must have shape (N, 3)")
    if trajectory.ndim != 2 or trajectory.shape[1:] != (3,):
        raise ValueError("trajectory_enu_m must have shape (M, 3)")
    trajectory = trajectory[np.all(np.isfinite(trajectory), axis=1)]
    if len(trajectory) < 2:
        raise ValueError("trajectory must contain at least two finite points")

    segment_xy, segment_length, cumulative = _trajectory_lengths(trajectory)
    segment_length_sq = segment_length * segment_length
    valid_segment = segment_length_sq > 1e-12
    if not np.any(valid_segment):
        raise ValueError("trajectory has no non-degenerate horizontal segment")
    waypoint_tree = cKDTree(trajectory[:, :2])

    count = len(points)
    along = np.full(count, np.nan, dtype=np.float64)
    signed_cross = np.full(count, np.nan, dtype=np.float64)
    trajectory_z = np.full(count, np.nan, dtype=np.float64)
    segment_output = np.full(count, -1, dtype=np.int64)
    fraction_output = np.full(count, np.nan, dtype=np.float64)

    for begin in range(0, count, int(chunk_size)):
        end = min(begin + int(chunk_size), count)
        local = points[begin:end]
        finite = np.all(np.isfinite(local), axis=1)
        if not np.any(finite):
            continue
        local_indices = np.flatnonzero(finite)
        xy = local[local_indices, :2]
        _, nearest_waypoint = waypoint_tree.query(xy, k=1, workers=1)
        candidates = np.column_stack(
            (
                np.clip(nearest_waypoint - 1, 0, len(segment_xy) - 1),
                np.clip(nearest_waypoint, 0, len(segment_xy) - 1),
            )
        )
        candidate_distance = np.full((len(local_indices), 2), np.inf)
        candidate_fraction = np.zeros((len(local_indices), 2), dtype=np.float64)
        candidate_signed = np.zeros((len(local_indices), 2), dtype=np.float64)
        for option in range(2):
            segment_index = candidates[:, option]
            start = trajectory[segment_index, :2]
            vector = segment_xy[segment_index]
            denom = segment_length_sq[segment_index]
            fraction = np.zeros(len(local_indices), dtype=np.float64)
            usable = denom > 1e-12
            fraction[usable] = np.sum(
                (xy[usable] - start[usable]) * vector[usable], axis=1
            ) / denom[usable]
            fraction = np.clip(fraction, 0.0, 1.0)
            projected = start + fraction[:, None] * vector
            offset = xy - projected
            candidate_distance[:, option] = np.linalg.norm(offset, axis=1)
            candidate_fraction[:, option] = fraction
            tangent_norm = np.maximum(segment_length[segment_index], 1e-12)
            candidate_signed[:, option] = (
                vector[:, 0] * offset[:, 1] - vector[:, 1] * offset[:, 0]
            ) / tangent_norm
        choice = np.argmin(candidate_distance, axis=1)
        row = np.arange(len(local_indices))
        chosen_segment = candidates[row, choice]
        chosen_fraction = candidate_fraction[row, choice]
        global_indices = begin + local_indices
        along[global_indices] = (
            cumulative[chosen_segment]
            + chosen_fraction * segment_length[chosen_segment]
        )
        signed_cross[global_indices] = candidate_signed[row, choice]
        trajectory_z[global_indices] = (
            (1.0 - chosen_fraction) * trajectory[chosen_segment, 2]
            + chosen_fraction * trajectory[chosen_segment + 1, 2]
        )
        segment_output[global_indices] = chosen_segment
        fraction_output[global_indices] = chosen_fraction

    return TrackCoordinates(
        along_track_m=along,
        signed_cross_track_m=signed_cross,
        trajectory_z_m=trajectory_z,
        nearest_segment_index=segment_output,
        segment_fraction=fraction_output,
        trajectory_cumulative_m=cumulative,
    )


def track_to_enu_xy(
    s_m: np.ndarray,
    t_m: np.ndarray,
    trajectory_enu_m: np.ndarray,
    trajectory_cumulative_m: np.ndarray | None = None,
) -> np.ndarray:
    """Map local road coordinates back to ENU XY using the trajectory normal."""

    s = np.asarray(s_m, dtype=np.float64)
    t = np.asarray(t_m, dtype=np.float64)
    if s.shape != t.shape:
        raise ValueError("s_m and t_m must have the same shape")
    trajectory = np.asarray(trajectory_enu_m, dtype=np.float64)
    segment_xy, segment_length, cumulative = _trajectory_lengths(trajectory)
    if trajectory_cumulative_m is not None:
        cumulative = np.asarray(trajectory_cumulative_m, dtype=np.float64)
    clipped = np.clip(s, cumulative[0], cumulative[-1])
    segment_index = np.searchsorted(cumulative, clipped, side="right") - 1
    segment_index = np.clip(segment_index, 0, len(segment_xy) - 1)
    length = np.maximum(segment_length[segment_index], 1e-12)
    fraction = (clipped - cumulative[segment_index]) / length
    center = (
        trajectory[segment_index, :2]
        + fraction[..., None] * segment_xy[segment_index]
    )
    tangent = segment_xy[segment_index] / length[..., None]
    left_normal = np.stack((-tangent[..., 1], tangent[..., 0]), axis=-1)
    return center + t[..., None] * left_normal


def _dense_lower_mode(values: np.ndarray, config: SurfaceConfig) -> tuple[float, float]:
    if len(values) < config.min_points_per_cell:
        return np.nan, np.inf
    cutoff = float(np.quantile(values, config.ground_lower_quantile))
    lower = values[values <= cutoff + 1e-12]
    if len(lower) < config.min_points_per_cell:
        return np.nan, np.inf
    origin = float(np.min(lower))
    bin_index = np.floor((lower - origin) / config.ground_histogram_bin_m).astype(
        np.int64
    )
    unique_bins, counts = np.unique(bin_index, return_counts=True)
    densest = int(unique_bins[int(np.argmax(counts))])
    center = origin + (densest + 0.5) * config.ground_histogram_bin_m
    adjacent = values[
        np.abs(values - center) <= 1.5 * config.ground_histogram_bin_m
    ]
    if len(adjacent) < config.min_points_per_cell:
        return np.nan, np.inf
    estimate = float(np.median(adjacent))
    mad = float(1.4826 * np.median(np.abs(adjacent - estimate)))
    return estimate, max(mad, config.ground_histogram_bin_m * 0.5)


def rasterize_road_surface(
    points_enu_m: np.ndarray,
    trajectory_enu_m: np.ndarray,
    config: SurfaceConfig,
    *,
    position_std_m: np.ndarray | None = None,
    source_origin: dict[str, float] | None = None,
) -> SurfaceGrid:
    """Aggregate point observations into a road-coordinate elevation grid."""

    config.validate()
    points = np.asarray(points_enu_m, dtype=np.float64)
    if len(points) == 0:
        raise ValueError("point cloud is empty")
    coordinates = project_to_trajectory(points, trajectory_enu_m)
    local_up = points[:, 2] - coordinates.trajectory_z_m
    finite = (
        np.all(np.isfinite(points), axis=1)
        & np.isfinite(coordinates.along_track_m)
        & np.isfinite(coordinates.signed_cross_track_m)
        & np.isfinite(local_up)
    )
    candidate = (
        finite
        & (np.abs(coordinates.signed_cross_track_m) <= config.corridor_half_width_m)
        & (local_up >= config.candidate_local_up_min_m)
        & (local_up <= config.candidate_local_up_max_m)
    )
    indices = np.flatnonzero(candidate)
    if len(indices) < config.reference_min_cells:
        raise ValueError(
            "too few road-corridor candidate points; verify trajectory, camera height, "
            "and corridor width"
        )

    s = coordinates.along_track_m[indices]
    t = coordinates.signed_cross_track_m[indices]
    z = local_up[indices]
    grid = config.grid_size_m
    s_min = float(np.floor(np.min(s) / grid) * grid)
    s_max = float(np.ceil(np.max(s) / grid) * grid)
    t_min = -float(config.corridor_half_width_m)
    t_max = float(config.corridor_half_width_m)
    s_count = max(1, int(np.ceil((s_max - s_min) / grid)))
    t_count = max(1, int(np.ceil((t_max - t_min) / grid)))
    s_values = s_min + (np.arange(s_count, dtype=np.float64) + 0.5) * grid
    t_values = t_min + (np.arange(t_count, dtype=np.float64) + 0.5) * grid

    s_index = np.floor((s - s_min) / grid).astype(np.int64)
    t_index = np.floor((t - t_min) / grid).astype(np.int64)
    in_bounds = (
        (s_index >= 0)
        & (s_index < s_count)
        & (t_index >= 0)
        & (t_index < t_count)
    )
    indices = indices[in_bounds]
    s_index = s_index[in_bounds]
    t_index = t_index[in_bounds]
    z = z[in_bounds]
    flat = s_index * t_count + t_index
    order = np.argsort(flat, kind="stable")
    flat_sorted = flat[order]
    starts = np.empty(len(order), dtype=bool)
    starts[0] = True
    starts[1:] = flat_sorted[1:] != flat_sorted[:-1]
    boundaries = np.flatnonzero(starts)
    ends = np.append(boundaries[1:], len(order))

    observed = np.full((s_count, t_count), np.nan, dtype=np.float32)
    counts = np.zeros((s_count, t_count), dtype=np.uint32)
    spread = np.full((s_count, t_count), np.inf, dtype=np.float32)
    aligned_std = None
    if position_std_m is not None:
        aligned_std = np.asarray(position_std_m, dtype=np.float64)
        if aligned_std.shape != (len(points),):
            raise ValueError("position_std_m must align with points")

    for start, end in zip(boundaries, ends, strict=True):
        local_order = order[start:end]
        cell_flat = int(flat_sorted[start])
        cell_s = cell_flat // t_count
        cell_t = cell_flat % t_count
        values = z[local_order]
        estimate, local_spread = _dense_lower_mode(values, config)
        counts[cell_s, cell_t] = len(values)
        if not np.isfinite(estimate):
            continue
        observed[cell_s, cell_t] = estimate
        if aligned_std is not None:
            source_indices = indices[local_order]
            metadata_values = aligned_std[source_indices]
            metadata_values = metadata_values[np.isfinite(metadata_values)]
            if len(metadata_values):
                local_spread = max(local_spread, float(np.median(metadata_values)))
        spread[cell_s, cell_t] = local_spread

    supported = np.isfinite(observed) & (counts >= config.min_points_per_cell)
    if np.count_nonzero(supported) < config.reference_min_cells:
        raise ValueError(
            "road surface has too few supported grid cells; use a coarser grid or "
            "collect denser observations"
        )
    reference = fit_reference_surface(s_values, t_values, observed, supported, config)
    residual = observed.astype(np.float64) - reference
    residual[~supported] = np.nan
    excluded_low = supported & (
        residual < config.plausibility_residual_min_m
    )
    excluded_high = supported & (
        residual > config.plausibility_residual_max_m
    )
    valid = supported & ~excluded_low & ~excluded_high
    if np.count_nonzero(valid) < config.reference_min_cells:
        raise ValueError(
            "road surface has too few plausible grid cells after residual gating; "
            "verify trajectory, calibration, ROI, and plausibility thresholds"
        )
    return SurfaceGrid(
        s_values_m=s_values.astype(np.float32),
        t_values_m=t_values.astype(np.float32),
        observed_local_up_m=observed,
        reference_local_up_m=reference.astype(np.float32),
        residual_m=residual.astype(np.float32),
        point_count=counts,
        position_std_m=spread,
        valid_mask=valid,
        trajectory_enu_m=np.asarray(trajectory_enu_m, dtype=np.float64),
        trajectory_cumulative_m=coordinates.trajectory_cumulative_m,
        source_origin=source_origin,
        supported_mask=supported,
        plausibility_excluded_low_mask=excluded_low,
        plausibility_excluded_high_mask=excluded_high,
    )


def _design_matrix(s_normalized: np.ndarray, t_normalized: np.ndarray) -> np.ndarray:
    return np.column_stack(
        (
            np.ones_like(s_normalized),
            s_normalized,
            t_normalized,
            s_normalized * s_normalized,
            s_normalized * t_normalized,
            t_normalized * t_normalized,
        )
    )


def _robust_polynomial_fit(
    s: np.ndarray,
    t: np.ndarray,
    z: np.ndarray,
    config: SurfaceConfig,
) -> tuple[np.ndarray, tuple[float, float, float, float]] | None:
    if len(z) < max(config.reference_min_cells, 6):
        return None
    s_center = float(np.median(s))
    t_center = float(np.median(t))
    s_scale = max(float(np.ptp(s)) * 0.5, config.grid_size_m)
    t_scale = max(float(np.ptp(t)) * 0.5, config.grid_size_m)
    sn = (s - s_center) / s_scale
    tn = (t - t_center) / t_scale
    design = _design_matrix(sn, tn)
    keep = np.ones(len(z), dtype=bool)
    coefficients: np.ndarray | None = None
    for _ in range(config.reference_robust_iterations):
        if np.count_nonzero(keep) < 6:
            break
        coefficients, *_ = np.linalg.lstsq(design[keep], z[keep], rcond=None)
        residual = z - design @ coefficients
        center = float(np.median(residual[keep]))
        mad = float(1.4826 * np.median(np.abs(residual[keep] - center)))
        gate = max(config.reference_min_residual_gate_m, config.reference_mad_sigma * mad)
        updated = np.abs(residual - center) <= gate
        if np.array_equal(updated, keep):
            break
        keep = updated
    if coefficients is None or np.count_nonzero(keep) < 6:
        return None
    return coefficients, (s_center, s_scale, t_center, t_scale)


def _predict_polynomial(
    coefficients: np.ndarray,
    normalization: tuple[float, float, float, float],
    s: np.ndarray,
    t: np.ndarray,
) -> np.ndarray:
    s_center, s_scale, t_center, t_scale = normalization
    design = _design_matrix((s - s_center) / s_scale, (t - t_center) / t_scale)
    return design @ coefficients


def _nearest_fill(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    if not np.any(valid):
        raise ValueError("cannot fill an entirely invalid surface")
    invalid = ~valid
    nearest = distance_transform_edt(invalid, return_distances=False, return_indices=True)
    return values[tuple(nearest)]


def fit_reference_surface(
    s_values_m: np.ndarray,
    t_values_m: np.ndarray,
    observed_m: np.ndarray,
    valid_mask: np.ndarray,
    config: SurfaceConfig,
) -> np.ndarray:
    """Fit overlapping robust quadratic tiles as the defect-free reference surface."""

    observed = np.asarray(observed_m, dtype=np.float64)
    valid = np.asarray(valid_mask, dtype=bool)
    s_grid, t_grid = np.meshgrid(
        np.asarray(s_values_m, dtype=np.float64),
        np.asarray(t_values_m, dtype=np.float64),
        indexing="ij",
    )
    prediction_sum = np.zeros_like(observed, dtype=np.float64)
    weight_sum = np.zeros_like(observed, dtype=np.float64)
    step = config.reference_tile_length_m - config.reference_tile_overlap_m
    start = float(s_values_m[0] - 0.5 * config.grid_size_m)
    stop = float(s_values_m[-1] + 0.5 * config.grid_size_m)
    tile_start = start
    fitted_tiles = 0
    while tile_start < stop + 1e-9:
        tile_end = tile_start + config.reference_tile_length_m
        tile_rows = (s_grid[:, 0] >= tile_start) & (s_grid[:, 0] <= tile_end)
        tile_mask = valid & tile_rows[:, None]
        if np.count_nonzero(tile_mask) >= config.reference_min_cells:
            fit = _robust_polynomial_fit(
                s_grid[tile_mask], t_grid[tile_mask], observed[tile_mask], config
            )
            if fit is not None:
                coefficients, normalization = fit
                target_mask = tile_rows[:, None] & np.ones_like(valid, dtype=bool)
                predicted = _predict_polynomial(
                    coefficients,
                    normalization,
                    s_grid[target_mask],
                    t_grid[target_mask],
                )
                center = 0.5 * (tile_start + tile_end)
                half = 0.5 * config.reference_tile_length_m
                row_weights = np.clip(
                    1.0 - np.abs(s_values_m - center) / max(half, 1e-9),
                    0.05,
                    1.0,
                )
                weights = np.broadcast_to(row_weights[:, None], observed.shape)[target_mask]
                prediction_sum[target_mask] += predicted * weights
                weight_sum[target_mask] += weights
                fitted_tiles += 1
        tile_start += step

    reference = np.full_like(observed, np.nan, dtype=np.float64)
    predicted = weight_sum > 0
    reference[predicted] = prediction_sum[predicted] / weight_sum[predicted]
    if fitted_tiles == 0 or not np.any(predicted):
        fit = _robust_polynomial_fit(
            s_grid[valid], t_grid[valid], observed[valid], config
        )
        if fit is None:
            filled = _nearest_fill(observed, valid)
            return gaussian_filter(filled, sigma=max(1.0, 1.0 / config.grid_size_m))
        coefficients, normalization = fit
        reference[:] = _predict_polynomial(
            coefficients,
            normalization,
            s_grid.ravel(),
            t_grid.ravel(),
        ).reshape(observed.shape)
        return reference

    if np.any(~predicted):
        filled = _nearest_fill(reference, predicted)
        reference[~predicted] = filled[~predicted]
    return reference
