from __future__ import annotations

import heapq
import math
from typing import Any

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull, QhullError

from .config import AdvancedGeometryConfig, DetectionConfig
from .detectors import roughness_proxy
from .models import Defect, SurfaceGrid


def _spacing(values: np.ndarray) -> float:
    if len(values) < 2:
        raise ValueError("advanced geometry requires at least two grid coordinates")
    return float(np.median(np.diff(values)))


def _polygon(s: np.ndarray, t: np.ndarray, cell_size_m: float) -> list[list[float]]:
    points = np.column_stack((s, t))
    if len(points) >= 3:
        try:
            return np.round(points[ConvexHull(points).vertices], 4).tolist()
        except QhullError:
            pass
    half = 0.5 * cell_size_m
    return [
        [float(np.min(s) - half), float(np.min(t) - half)],
        [float(np.max(s) + half), float(np.min(t) - half)],
        [float(np.max(s) + half), float(np.max(t) + half)],
        [float(np.min(s) - half), float(np.max(t) + half)],
    ]


def detect_step_manhole_candidates(
    grid: SurfaceGrid,
    config: AdvancedGeometryConfig,
) -> list[Defect]:
    """Find abrupt local height transitions; object identity remains unverified."""

    ds = _spacing(grid.s_values_m)
    dt = _spacing(grid.t_values_m)
    valid = grid.valid_mask & np.isfinite(grid.residual_m)
    residual = np.where(valid, grid.residual_m, 0.0).astype(np.float64)
    grad_s, grad_t = np.gradient(residual, ds, dt)
    gradient_percent = 100.0 * np.hypot(grad_s, grad_t)
    footprint = np.ones((3, 3), dtype=bool)
    local_high = ndimage.maximum_filter(residual, footprint=footprint, mode="nearest")
    local_low = ndimage.minimum_filter(residual, footprint=footprint, mode="nearest")
    local_range = local_high - local_low
    candidate = (
        valid
        & (local_range >= config.step_min_height_m)
        & (gradient_percent >= config.step_min_gradient_percent)
    )
    candidate = ndimage.binary_closing(
        ndimage.binary_dilation(candidate, iterations=1),
        structure=ndimage.generate_binary_structure(2, 2),
    )
    labels, count = ndimage.label(candidate)
    s_grid, t_grid = np.meshgrid(grid.s_values_m, grid.t_values_m, indexing="ij")
    defects: list[Defect] = []
    for label_index in range(1, count + 1):
        component = (labels == label_index) & valid
        edge_length = float(np.count_nonzero(component) * min(ds, dt))
        if edge_length < config.step_min_edge_length_m:
            continue
        s_values = s_grid[component]
        t_values = t_grid[component]
        step_height = float(np.quantile(local_range[component], 0.95))
        approach_slope = float(np.quantile(gradient_percent[component], 0.95))
        extent_s = float(np.ptp(s_values) + ds)
        extent_t = float(np.ptp(t_values) + dt)
        aspect = max(extent_s, extent_t) / max(min(extent_s, extent_t), 1e-9)
        equivalent_diameter = 2.0 * math.sqrt(
            max(extent_s * extent_t, 0.0) / math.pi
        )
        center_s = float(np.mean(s_values))
        forward = component & (s_grid >= center_s)
        reverse = component & (s_grid < center_s)
        forward_step = float(
            np.quantile(local_range[forward], 0.95)
            if np.any(forward)
            else step_height
        )
        reverse_step = float(
            np.quantile(local_range[reverse], 0.95)
            if np.any(reverse)
            else step_height
        )
        manhole_like = aspect <= 1.8 and 0.25 <= equivalent_diameter <= 2.5
        defect_type = "manhole_step_candidate" if manhole_like else "step_anomaly"
        support = float(np.median(grid.point_count[component]))
        confidence = float(np.clip(0.35 + 0.08 * support, 0.0, 0.85))
        defects.append(
            Defect(
                defect_id=f"step-{len(defects) + 1:04d}",
                defect_type=defect_type,
                severity=(
                    "high"
                    if step_height >= 0.04
                    else "medium"
                    if step_height >= 0.025
                    else "low"
                ),
                confidence=confidence,
                chainage_m=center_s,
                lateral_offset_m=float(np.mean(t_values)),
                local_polygon_st_m=_polygon(s_values, t_values, min(ds, dt)),
                metrics={
                    "step_height_m": step_height,
                    "approach_slope_percent": approach_slope,
                    "edge_length_m": edge_length,
                    "equivalent_diameter_m": equivalent_diameter,
                    "forward_approach_step_height_m": forward_step,
                    "reverse_approach_step_height_m": reverse_step,
                },
                quality_flags=[
                    "asset_or_rgb_confirmation_required",
                    "experimental_threshold",
                ],
                source="geometry_screening",
            )
        )
    defects.sort(key=lambda item: (item.chainage_m, item.lateral_offset_m))
    return defects


def _linear_slopes(
    coordinate: np.ndarray,
    values: np.ndarray,
    valid: np.ndarray,
    *,
    axis: int,
) -> np.ndarray:
    count = values.shape[0 if axis == 1 else 1]
    output = np.full(count, np.nan, dtype=np.float64)
    for index in range(count):
        mask = valid[index, :] if axis == 1 else valid[:, index]
        sample = values[index, :] if axis == 1 else values[:, index]
        if np.count_nonzero(mask) >= 3:
            output[index] = np.polyfit(coordinate[mask], sample[mask], 1)[0]
    return output


def _profile_stats(values_percent: np.ndarray) -> dict[str, float | int | None]:
    finite = values_percent[np.isfinite(values_percent)]
    if len(finite) == 0:
        return {
            "sample_count": 0,
            "median_percent": None,
            "p05_percent": None,
            "p95_percent": None,
        }
    return {
        "sample_count": int(len(finite)),
        "median_percent": float(np.median(finite)),
        "p05_percent": float(np.quantile(finite, 0.05)),
        "p95_percent": float(np.quantile(finite, 0.95)),
    }


def crossfall_profile(grid: SurfaceGrid) -> dict[str, Any]:
    reference = grid.reference_local_up_m.astype(np.float64)
    valid = grid.valid_mask & np.isfinite(reference)
    profiles: dict[str, Any] = {}

    def calculate(mask: np.ndarray) -> dict[str, Any]:
        slopes = 100.0 * _linear_slopes(
            grid.t_values_m,
            reference,
            valid & mask,
            axis=1,
        )
        stats = _profile_stats(slopes)
        crown_values = []
        selected = valid & mask
        for row in range(len(grid.s_values_m)):
            columns = np.flatnonzero(selected[row])
            if len(columns):
                local_peak = int(np.argmax(reference[row, columns]))
                if 0 < local_peak < len(columns) - 1:
                    crown_values.append(
                        float(grid.t_values_m[columns[local_peak]])
                    )
        stats["median_crown_offset_m"] = (
            float(np.median(crown_values)) if crown_values else None
        )
        return stats

    profiles["road"] = calculate(np.ones_like(valid, dtype=bool))
    if grid.roi_lane_index is not None:
        profiles["lanes"] = {
            lane_id: calculate(grid.roi_lane_index == lane_index)
            for lane_index, lane_id in enumerate(grid.roi_lane_ids, start=1)
        }
    return {
        "metric": "reference_surface_dz_dt_percent",
        "profiles": profiles,
        "calibration_status": "experimental",
    }


def longitudinal_profile(
    grid: SurfaceGrid,
    detection: DetectionConfig,
) -> dict[str, Any]:
    if len(grid.trajectory_cumulative_m) != len(grid.trajectory_enu_m):
        raise ValueError("trajectory chainage must align with trajectory coordinates")
    trajectory_z = np.interp(
        grid.s_values_m,
        grid.trajectory_cumulative_m,
        grid.trajectory_enu_m[:, 2],
    )
    reference = (
        grid.reference_local_up_m.astype(np.float64) + trajectory_z[:, None]
    )
    valid = grid.valid_mask & np.isfinite(reference)
    center = np.full(len(grid.s_values_m), np.nan, dtype=np.float64)
    for row in range(len(center)):
        if np.any(valid[row]):
            center[row] = float(np.median(reference[row, valid[row]]))
    finite = np.isfinite(center)
    slopes = np.full_like(center, np.nan)
    if np.count_nonzero(finite) >= 3:
        interpolated = np.interp(
            grid.s_values_m,
            grid.s_values_m[finite],
            center[finite],
        )
        slopes = 100.0 * np.gradient(interpolated, _spacing(grid.s_values_m))
        slopes[~finite] = np.nan
    return {
        "metric": "trajectory_restored_road_surface_dz_ds_percent",
        "slope": _profile_stats(slopes),
        "roughness_proxy_m": roughness_proxy(
            grid,
            detection.rut_wheel_offset_m,
            detection.rut_band_half_width_m,
        ),
        "roughness_name_guard": "project_specific_proxy_not_standardized_IRI",
        "calibration_status": "experimental",
    }


def _priority_flood_fill(elevation: np.ndarray, valid: np.ndarray) -> np.ndarray:
    rows, columns = elevation.shape
    filled = np.full_like(elevation, np.nan, dtype=np.float64)
    visited = np.zeros_like(valid, dtype=bool)
    boundary = valid & (
        ~ndimage.binary_erosion(valid, structure=ndimage.generate_binary_structure(2, 1))
    )
    queue: list[tuple[float, int, int]] = []
    for row, column in np.argwhere(boundary):
        height = float(elevation[row, column])
        filled[row, column] = height
        visited[row, column] = True
        heapq.heappush(queue, (height, int(row), int(column)))
    while queue:
        spill, row, column = heapq.heappop(queue)
        for d_row, d_column in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbor_row = row + d_row
            neighbor_column = column + d_column
            if not (0 <= neighbor_row < rows and 0 <= neighbor_column < columns):
                continue
            if not valid[neighbor_row, neighbor_column] or visited[neighbor_row, neighbor_column]:
                continue
            visited[neighbor_row, neighbor_column] = True
            height = max(float(elevation[neighbor_row, neighbor_column]), spill)
            filled[neighbor_row, neighbor_column] = height
            heapq.heappush(queue, (height, neighbor_row, neighbor_column))
    return filled


def detect_ponding_screening(
    grid: SurfaceGrid,
    config: AdvancedGeometryConfig,
) -> list[Defect]:
    """Screen closed DEM depressions without claiming drainage capacity or flooding."""

    valid = grid.valid_mask & np.isfinite(grid.observed_local_up_m)
    elevation = grid.observed_local_up_m.astype(np.float64)
    filled = _priority_flood_fill(elevation, valid)
    depth = np.where(valid, filled - elevation, 0.0)
    candidate = valid & (depth >= config.ponding_min_depth_m)
    labels, count = ndimage.label(
        candidate,
        structure=ndimage.generate_binary_structure(2, 2),
    )
    s_grid, t_grid = np.meshgrid(grid.s_values_m, grid.t_values_m, indexing="ij")
    cell_size = math.sqrt(grid.cell_area_m2)
    defects: list[Defect] = []
    for label_index in range(1, count + 1):
        component = labels == label_index
        area = float(np.count_nonzero(component) * grid.cell_area_m2)
        if area < config.ponding_min_area_m2:
            continue
        local_depth = depth[component]
        s_values = s_grid[component]
        t_values = t_grid[component]
        maximum = float(np.max(local_depth))
        defects.append(
            Defect(
                defect_id=f"ponding-screening-{len(defects) + 1:04d}",
                defect_type="ponding_screening_proxy",
                severity=(
                    "high"
                    if maximum >= 0.08
                    else "medium"
                    if maximum >= 0.03
                    else "low"
                ),
                confidence=0.5,
                chainage_m=float(np.average(s_values, weights=local_depth)),
                lateral_offset_m=float(np.average(t_values, weights=local_depth)),
                local_polygon_st_m=_polygon(s_values, t_values, cell_size),
                metrics={
                    "potential_retention_depth_m": maximum,
                    "potential_retention_area_m2": area,
                    "potential_retention_volume_m3": float(
                        np.sum(local_depth) * grid.cell_area_m2
                    ),
                },
                quality_flags=[
                    "screening_only",
                    "drain_locations_unavailable",
                    "drainage_capacity_not_computed",
                    "experimental_threshold",
                ],
                source="geometry_screening",
            )
        )
    defects.sort(key=lambda item: (item.chainage_m, item.lateral_offset_m))
    return defects
