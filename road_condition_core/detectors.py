from __future__ import annotations

import math
from dataclasses import dataclass
import warnings

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull, QhullError

from .config import DetectionConfig
from .models import Defect, SurfaceGrid


@dataclass(frozen=True)
class RutSeries:
    s_values_m: np.ndarray
    left_depth_m: np.ndarray
    right_depth_m: np.ndarray


def _remove_components_touching_exclusion(
    candidate_mask: np.ndarray,
    exclusion_mask: np.ndarray | None,
) -> tuple[np.ndarray, int, int]:
    """Remove cut-off positive components attached to a known non-road high cell."""

    candidate = np.asarray(candidate_mask, dtype=bool)
    if exclusion_mask is None or not np.any(exclusion_mask) or not np.any(candidate):
        return candidate.copy(), 0, 0
    structure = ndimage.generate_binary_structure(2, 2)
    labels, _count = ndimage.label(candidate, structure=structure)
    touching = np.unique(labels[ndimage.binary_dilation(exclusion_mask, structure=structure)])
    touching = touching[touching > 0]
    if not len(touching):
        return candidate.copy(), 0, 0
    removed = np.isin(labels, touching)
    return candidate & ~removed, int(len(touching)), int(np.count_nonzero(removed))


def bump_plausibility_boundary_guard_stats(
    grid: SurfaceGrid,
    config: DetectionConfig,
) -> dict[str, int | bool]:
    candidate = (
        grid.valid_mask
        & np.isfinite(grid.residual_m)
        & (grid.residual_m >= config.bump_min_height_m)
    )
    _retained, components, cells = _remove_components_touching_exclusion(
        candidate,
        grid.plausibility_excluded_high_mask,
    )
    return {
        "applied": grid.plausibility_excluded_high_mask is not None,
        "removed_component_count": components,
        "removed_candidate_cell_count": cells,
    }


def _severity_for_pothole(max_depth_m: float, area_m2: float) -> str:
    if max_depth_m >= 0.08 or area_m2 >= 1.0:
        return "high"
    if max_depth_m >= 0.05 or area_m2 >= 0.30:
        return "medium"
    return "low"


def _severity_for_bump(max_height_m: float, area_m2: float) -> str:
    if max_height_m >= 0.08 or area_m2 >= 1.0:
        return "high"
    if max_height_m >= 0.055 or area_m2 >= 0.30:
        return "medium"
    return "low"


def _severity_for_rut(max_depth_m: float) -> str:
    if max_depth_m >= 0.04:
        return "high"
    if max_depth_m >= 0.025:
        return "medium"
    return "low"


def _component_polygon(s: np.ndarray, t: np.ndarray, grid_size_m: float) -> list[list[float]]:
    centers = np.column_stack((s, t))
    if len(centers) >= 3:
        try:
            hull = ConvexHull(centers)
            polygon = centers[hull.vertices]
            return np.round(polygon, 4).tolist()
        except QhullError:
            pass
    half = 0.5 * grid_size_m
    s_min = float(np.min(s) - half)
    s_max = float(np.max(s) + half)
    t_min = float(np.min(t) - half)
    t_max = float(np.max(t) + half)
    return [[s_min, t_min], [s_max, t_min], [s_max, t_max], [s_min, t_max]]


def _component_confidence(
    grid: SurfaceGrid,
    mask: np.ndarray,
    config: DetectionConfig,
) -> tuple[float, list[str]]:
    spread = grid.position_std_m[mask]
    spread = spread[np.isfinite(spread)]
    counts = grid.point_count[mask].astype(np.float64)
    flags: list[str] = []
    spread_median = float(np.median(spread)) if len(spread) else math.inf
    count_median = float(np.median(counts)) if len(counts) else 0.0
    spread_score = (
        max(0.0, 1.0 - spread_median / config.low_confidence_position_std_m)
        if np.isfinite(spread_median)
        else 0.0
    )
    support_score = min(1.0, count_median / 8.0)
    confidence = 0.65 * spread_score + 0.35 * support_score
    if spread_median > config.low_confidence_position_std_m:
        flags.append("high_position_spread")
    if count_median < 3:
        flags.append("low_point_support")
    if confidence < 0.5:
        flags.append("manual_review_required")
    return float(np.clip(confidence, 0.0, 1.0)), flags


def _component_defects(
    grid: SurfaceGrid,
    config: DetectionConfig,
    *,
    candidate_mask: np.ndarray,
    defect_type: str,
    minimum_area_m2: float,
    prefix: str,
) -> list[Defect]:
    structure = ndimage.generate_binary_structure(2, 2)
    radius = config.pothole_close_radius_cells
    processed = candidate_mask.copy()
    if radius > 0:
        processed = ndimage.binary_closing(processed, structure=structure, iterations=radius)
        processed = ndimage.binary_opening(processed, structure=structure, iterations=1)
    labels, count = ndimage.label(processed, structure=structure)
    defects: list[Defect] = []
    s_grid, t_grid = np.meshgrid(
        grid.s_values_m, grid.t_values_m, indexing="ij"
    )
    residual = grid.residual_m.astype(np.float64)
    for label_index in range(1, count + 1):
        component = (labels == label_index) & grid.valid_mask
        cell_count = int(np.count_nonzero(component))
        if cell_count == 0:
            continue
        area_m2 = cell_count * grid.cell_area_m2
        if area_m2 < minimum_area_m2:
            continue
        values = residual[component]
        s_values = s_grid[component].astype(np.float64)
        t_values = t_grid[component].astype(np.float64)
        confidence, flags = _component_confidence(grid, component, config)
        if defect_type == "pothole":
            depths = np.maximum(0.0, -values)
            max_value = float(np.max(depths))
            metrics = {
                "max_depth_m": max_value,
                "p95_depth_m": float(np.quantile(depths, 0.95)),
                "mean_depth_m": float(np.mean(depths)),
                "area_m2": float(area_m2),
                "volume_m3": float(np.sum(depths) * grid.cell_area_m2),
                "major_extent_m": float(np.ptp(s_values) + np.sqrt(grid.cell_area_m2)),
                "minor_extent_m": float(np.ptp(t_values) + np.sqrt(grid.cell_area_m2)),
            }
            severity = _severity_for_pothole(max_value, area_m2)
        else:
            heights = np.maximum(0.0, values)
            max_value = float(np.max(heights))
            metrics = {
                "max_height_m": max_value,
                "p95_height_m": float(np.quantile(heights, 0.95)),
                "mean_height_m": float(np.mean(heights)),
                "area_m2": float(area_m2),
                "positive_volume_m3": float(np.sum(heights) * grid.cell_area_m2),
                "major_extent_m": float(np.ptp(s_values) + np.sqrt(grid.cell_area_m2)),
                "minor_extent_m": float(np.ptp(t_values) + np.sqrt(grid.cell_area_m2)),
            }
            severity = _severity_for_bump(max_value, area_m2)
        defects.append(
            Defect(
                defect_id=f"{prefix}-{len(defects) + 1:04d}",
                defect_type=defect_type,
                severity=severity,
                confidence=confidence,
                chainage_m=float(np.average(s_values, weights=np.abs(values) + 1e-6)),
                lateral_offset_m=float(
                    np.average(t_values, weights=np.abs(values) + 1e-6)
                ),
                local_polygon_st_m=_component_polygon(
                    s_values, t_values, math.sqrt(grid.cell_area_m2)
                ),
                metrics=metrics,
                quality_flags=flags,
            )
        )
    defects.sort(key=lambda item: item.chainage_m)
    return defects


def detect_potholes(grid: SurfaceGrid, config: DetectionConfig) -> list[Defect]:
    candidate = (
        grid.valid_mask
        & np.isfinite(grid.residual_m)
        & (grid.residual_m <= -config.pothole_min_depth_m)
    )
    return _component_defects(
        grid,
        config,
        candidate_mask=candidate,
        defect_type="pothole",
        minimum_area_m2=config.pothole_min_area_m2,
        prefix="pothole",
    )


def detect_bumps(grid: SurfaceGrid, config: DetectionConfig) -> list[Defect]:
    candidate = (
        grid.valid_mask
        & np.isfinite(grid.residual_m)
        & (grid.residual_m >= config.bump_min_height_m)
    )
    candidate, _removed_components, _removed_cells = _remove_components_touching_exclusion(
        candidate,
        grid.plausibility_excluded_high_mask,
    )
    return _component_defects(
        grid,
        config,
        candidate_mask=candidate,
        defect_type="bump",
        minimum_area_m2=config.bump_min_area_m2,
        prefix="bump",
    )


def _band_depth(
    grid: SurfaceGrid,
    center_m: float,
    half_width_m: float,
) -> np.ndarray:
    band = np.abs(grid.t_values_m - center_m) <= half_width_m
    if not np.any(band):
        return np.full(len(grid.s_values_m), np.nan, dtype=np.float64)
    residual = grid.residual_m[:, band].astype(np.float64)
    valid = grid.valid_mask[:, band]
    values = np.where(valid, np.maximum(0.0, -residual), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmedian(values, axis=1)


def _close_short_gaps(mask: np.ndarray, maximum_gap_rows: int) -> np.ndarray:
    if maximum_gap_rows <= 0:
        return mask.copy()
    return ndimage.binary_closing(
        mask,
        structure=np.ones(maximum_gap_rows + 2, dtype=bool),
    )


def _rut_defects_for_side(
    grid: SurfaceGrid,
    config: DetectionConfig,
    depth: np.ndarray,
    *,
    side: str,
    center_t_m: float,
) -> list[Defect]:
    if len(grid.s_values_m) < 2:
        return []
    ds = float(np.median(np.diff(grid.s_values_m)))
    gap_rows = int(round(config.rut_gap_tolerance_m / max(ds, 1e-9)))
    candidate = np.isfinite(depth) & (depth >= config.rut_min_depth_m)
    candidate = _close_short_gaps(candidate, gap_rows)
    labels, count = ndimage.label(candidate)
    defects: list[Defect] = []
    for label_index in range(1, count + 1):
        rows = np.flatnonzero(labels == label_index)
        if len(rows) == 0:
            continue
        length_m = float((rows[-1] - rows[0] + 1) * ds)
        if length_m < config.rut_min_length_m:
            continue
        local_depth = depth[rows]
        local_depth = local_depth[np.isfinite(local_depth)]
        if len(local_depth) == 0:
            continue
        s_start = float(grid.s_values_m[rows[0]] - 0.5 * ds)
        s_end = float(grid.s_values_m[rows[-1]] + 0.5 * ds)
        band_min = center_t_m - config.rut_band_half_width_m
        band_max = center_t_m + config.rut_band_half_width_m
        max_depth = float(np.max(local_depth))
        mean_depth = float(np.mean(local_depth))
        confidence = float(np.clip(len(rows) / max(10.0, config.rut_min_length_m / ds), 0.4, 1.0))
        defects.append(
            Defect(
                defect_id=f"rut-{side}-{len(defects) + 1:04d}",
                defect_type="rutting",
                severity=_severity_for_rut(max_depth),
                confidence=confidence,
                chainage_m=0.5 * (s_start + s_end),
                lateral_offset_m=center_t_m,
                local_polygon_st_m=[
                    [s_start, band_min],
                    [s_end, band_min],
                    [s_end, band_max],
                    [s_start, band_max],
                ],
                metrics={
                    "side": -1.0 if side == "left" else 1.0,
                    "max_depth_m": max_depth,
                    "mean_depth_m": mean_depth,
                    "length_m": length_m,
                    "band_width_m": 2.0 * config.rut_band_half_width_m,
                    "area_m2": length_m * 2.0 * config.rut_band_half_width_m,
                },
            )
        )
    return defects


def detect_rutting(grid: SurfaceGrid, config: DetectionConfig) -> tuple[list[Defect], RutSeries]:
    left = _band_depth(
        grid,
        center_m=-config.rut_wheel_offset_m,
        half_width_m=config.rut_band_half_width_m,
    )
    right = _band_depth(
        grid,
        center_m=config.rut_wheel_offset_m,
        half_width_m=config.rut_band_half_width_m,
    )
    if len(grid.s_values_m) > 2:
        ds = float(np.median(np.diff(grid.s_values_m)))
        filter_rows = max(3, int(round(1.0 / max(ds, 1e-9))))
        if filter_rows % 2 == 0:
            filter_rows += 1
        for values in (left, right):
            finite = np.isfinite(values)
            if np.any(finite):
                filled = values.copy()
                valid_indices = np.flatnonzero(finite)
                filled[~finite] = np.interp(
                    np.flatnonzero(~finite), valid_indices, values[finite]
                )
                values[:] = ndimage.median_filter(filled, size=filter_rows, mode="nearest")
                values[~finite] = np.nan
    defects = _rut_defects_for_side(
        grid,
        config,
        left,
        side="left",
        center_t_m=-config.rut_wheel_offset_m,
    )
    defects.extend(
        _rut_defects_for_side(
            grid,
            config,
            right,
            side="right",
            center_t_m=config.rut_wheel_offset_m,
        )
    )
    defects.sort(key=lambda item: (item.chainage_m, item.lateral_offset_m))
    return defects, RutSeries(
        s_values_m=grid.s_values_m.astype(np.float64),
        left_depth_m=left,
        right_depth_m=right,
    )


def roughness_proxy(grid: SurfaceGrid, wheel_offset_m: float, band_half_width_m: float) -> float:
    left = _band_depth(grid, -wheel_offset_m, band_half_width_m)
    right = _band_depth(grid, wheel_offset_m, band_half_width_m)
    values = np.concatenate((left[np.isfinite(left)], right[np.isfinite(right)]))
    if len(values) == 0:
        return 0.0
    # This is intentionally named a proxy, not IRI. It measures RMS short-wave
    # depression in the two wheel-path bands after reference-surface removal.
    return float(np.sqrt(np.mean(values * values)))
