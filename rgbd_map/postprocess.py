from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from enum import IntFlag
from typing import Any, Mapping

import numpy as np
from scipy.spatial import cKDTree

from .ground_surface import GroundSurfaceResult, estimate_local_ground_surface
from .postprocess_backends import (
    DependencyInfo,
    NeighborFilterResult,
    inspect_dependencies,
    resolve_neighbor_backend,
    resolve_ground_backend,
    tiled_neighbor_filter,
)
from .postprocess_config import PostprocessConfig


_TRAJECTORY_QUALITY_SEGMENT_LENGTH_M = 10.0
_TRAJECTORY_QUERY_CHUNK_POINTS = 1_000_000


class RemovalReason(IntFlag):
    NONE = 0
    NON_FINITE = 1 << 0
    LOW_MULTI_FRAME_SUPPORT = 1 << 1
    HIGH_POSITION_SPREAD = 1 << 2
    RADIUS_OUTLIER = 1 << 3
    STATISTICAL_OUTLIER = 1 << 4
    BELOW_LOCAL_SURFACE = 1 << 5
    BRIGHT_LOW_SUPPORT = 1 << 6
    OUTSIDE_VALID_BOUNDS = 1 << 7


PRIMARY_REASON_PRIORITY = (
    RemovalReason.NON_FINITE,
    RemovalReason.OUTSIDE_VALID_BOUNDS,
    RemovalReason.BELOW_LOCAL_SURFACE,
    RemovalReason.HIGH_POSITION_SPREAD,
    RemovalReason.RADIUS_OUTLIER,
    RemovalReason.STATISTICAL_OUTLIER,
    RemovalReason.LOW_MULTI_FRAME_SUPPORT,
    RemovalReason.BRIGHT_LOW_SUPPORT,
)


REMOVAL_REASON_COLORS_RGB: Mapping[RemovalReason, tuple[int, int, int]] = {
    RemovalReason.NON_FINITE: (30, 90, 255),
    RemovalReason.OUTSIDE_VALID_BOUNDS: (30, 90, 255),
    RemovalReason.BELOW_LOCAL_SURFACE: (255, 35, 35),
    RemovalReason.HIGH_POSITION_SPREAD: (255, 30, 210),
    RemovalReason.RADIUS_OUTLIER: (255, 230, 25),
    RemovalReason.STATISTICAL_OUTLIER: (255, 135, 20),
    RemovalReason.LOW_MULTI_FRAME_SUPPORT: (20, 220, 220),
    RemovalReason.BRIGHT_LOW_SUPPORT: (255, 255, 255),
}


@dataclass(frozen=True)
class PointCloudMetadata:
    observation_count: np.ndarray
    distinct_frame_count: np.ndarray
    position_std_m: np.ndarray
    mean_depth_m: np.ndarray


@dataclass(frozen=True)
class PostprocessStage:
    stage: str
    input_count: int
    output_count: int
    removed_count: int
    seconds: float

    def to_dict(self) -> dict[str, str | int | float]:
        return asdict(self)


@dataclass(frozen=True)
class QualityGuardResult:
    xy_coverage_retention: float | None
    trajectory_corridor_coverage_retention: float | None
    high_structure_retention: float | None
    below_surface_reduction: float | None
    bright_isolated_reduction: float | None
    removal_ratio: float
    raw_xy_occupied_cells: int
    clean_xy_occupied_cells: int
    raw_corridor_occupied_cells: int
    clean_corridor_occupied_cells: int
    raw_high_structure_point_count: int
    clean_high_structure_point_count: int
    raw_below_surface_point_count: int
    clean_below_surface_point_count: int
    raw_bright_isolated_point_count: int
    clean_bright_isolated_point_count: int
    xy_bbox_length_retention: tuple[float | None, float | None]
    maximum_z_retention: float | None
    passed: bool
    warnings: tuple[str, ...]
    trajectory_segment_length_m: float = _TRAJECTORY_QUALITY_SEGMENT_LENGTH_M
    trajectory_segment_count: int = 0
    raw_supported_trajectory_segment_count: int = 0
    clean_supported_trajectory_segment_count: int = 0
    empty_clean_trajectory_segment_count: int = 0
    trajectory_segment_coverage_retention: float | None = None
    empty_clean_trajectory_segment_indices: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["warnings"] = list(self.warnings)
        value["xy_bbox_length_retention"] = list(self.xy_bbox_length_retention)
        value["empty_clean_trajectory_segment_indices"] = list(
            self.empty_clean_trajectory_segment_indices
        )
        return value


@dataclass(frozen=True)
class PostprocessResult:
    raw_points_enu_m: np.ndarray
    raw_colors_rgb: np.ndarray
    clean_points_enu_m: np.ndarray
    clean_colors_rgb: np.ndarray
    removed_points_enu_m: np.ndarray
    removed_original_colors_rgb: np.ndarray
    removed_diagnostic_colors_rgb: np.ndarray
    keep_mask: np.ndarray
    removed_mask: np.ndarray
    clean_indices: np.ndarray
    removed_indices: np.ndarray
    removal_reason_bits: np.ndarray
    primary_reason: np.ndarray
    metadata: PointCloudMetadata
    neighbor_result: NeighborFilterResult | None
    ground_surface: GroundSurfaceResult | None
    stages: tuple[PostprocessStage, ...]
    quality: QualityGuardResult
    config: PostprocessConfig
    neighbor_backend: str
    ground_backend: str
    dependencies: DependencyInfo
    report: dict[str, Any]


def _metadata_value(
    source: Mapping[str, Any] | object | None, name: str
) -> Any | None:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def coerce_point_metadata(
    metadata: Mapping[str, Any] | object | None,
    point_count: int,
    *,
    default_distinct_frame_count: int = 2,
) -> PointCloudMetadata:
    """Validate dict/object metadata without using pickle or object arrays.

    Missing frame support defaults to the configured minimum rather than one, so
    legacy clouds are not deleted merely because historical metadata is absent.
    """

    defaults: dict[str, np.ndarray] = {
        "observation_count": np.full(
            point_count, max(1, int(default_distinct_frame_count)), dtype=np.int32
        ),
        "distinct_frame_count": np.full(
            point_count, max(1, int(default_distinct_frame_count)), dtype=np.int32
        ),
        "position_std_m": np.zeros(point_count, dtype=np.float32),
        "mean_depth_m": np.full(point_count, np.nan, dtype=np.float32),
    }
    arrays: dict[str, np.ndarray] = {}
    for name, default in defaults.items():
        value = _metadata_value(metadata, name)
        array = default if value is None else np.asarray(value)
        if array.shape != (point_count,):
            raise ValueError(f"metadata {name} must have shape ({point_count},)")
        if (
            array.dtype == object
            or not np.issubdtype(array.dtype, np.number)
            or np.iscomplexobj(array)
        ):
            raise ValueError(f"metadata {name} must be a numeric array")
        arrays[name] = array

    raw_observation_count = np.asarray(arrays["observation_count"])
    raw_distinct_frame_count = np.asarray(arrays["distinct_frame_count"])
    count_arrays = (raw_observation_count, raw_distinct_frame_count)
    for count_array in count_arrays:
        if np.iscomplexobj(count_array):
            raise ValueError("metadata observation counts must be finite integers")
        if not np.issubdtype(count_array.dtype, np.integer) and (
            not np.all(np.isfinite(count_array))
            or not np.all(np.equal(count_array, np.floor(count_array)))
        ):
            raise ValueError("metadata observation counts must be finite integers")
    observation_count = (
        raw_observation_count
        if np.issubdtype(raw_observation_count.dtype, np.integer)
        else raw_observation_count.astype(np.int64)
    )
    distinct_frame_count = (
        raw_distinct_frame_count
        if np.issubdtype(raw_distinct_frame_count.dtype, np.integer)
        else raw_distinct_frame_count.astype(np.int64)
    )
    position_std_m = np.asarray(arrays["position_std_m"], dtype=np.float32)
    mean_depth_m = np.asarray(arrays["mean_depth_m"], dtype=np.float32)
    if np.any(observation_count < 0) or np.any(distinct_frame_count < 0):
        raise ValueError("metadata observation counts must be non-negative")
    if np.any(distinct_frame_count > observation_count):
        raise ValueError("distinct_frame_count cannot exceed observation_count")
    if np.any(np.isfinite(position_std_m) & (position_std_m < 0.0)):
        raise ValueError("position_std_m cannot contain finite negative values")
    if np.any(np.isfinite(mean_depth_m) & (mean_depth_m < 0.0)):
        raise ValueError("mean_depth_m cannot contain finite negative values")
    return PointCloudMetadata(
        observation_count=observation_count,
        distinct_frame_count=distinct_frame_count,
        position_std_m=position_std_m,
        mean_depth_m=mean_depth_m,
    )


def bright_neutral_mask(
    colors_rgb: np.ndarray, config: PostprocessConfig
) -> np.ndarray:
    colors = np.asarray(colors_rgb)
    if colors.ndim != 2 or colors.shape[1:] != (3,):
        raise ValueError("colors_rgb must have shape (N, 3)")
    color_min = np.min(colors, axis=1)
    color_max = np.max(colors, axis=1)
    return (color_min >= config.bright_min_rgb) & (
        color_max - color_min <= config.bright_max_chroma
    )


def _empty_neighbor_result(
    count: int, backend: str, source: str = "not_evaluated"
) -> NeighborFilterResult:
    return NeighborFilterResult(
        radius_outlier_mask=np.zeros(count, dtype=bool),
        statistical_outlier_mask=np.zeros(count, dtype=bool),
        neighbor_count=np.zeros(count, dtype=np.int32),
        mean_neighbor_distance_m=np.full(count, np.nan, dtype=np.float32),
        core_evaluation_count=np.zeros(count, dtype=np.uint8),
        statistical_evaluation_count=np.zeros(count, dtype=np.uint8),
        distance_proxy_m=np.zeros(count, dtype=np.float32),
        distance_proxy_source=source,
        backend=backend,
        tile_count=0,
    )


def _expand_neighbor_result(
    subset: NeighborFilterResult, indices: np.ndarray, count: int
) -> NeighborFilterResult:
    radius = np.zeros(count, dtype=bool)
    statistical = np.zeros(count, dtype=bool)
    neighbor_count = np.zeros(count, dtype=np.int32)
    means = np.full(count, np.nan, dtype=np.float32)
    core = np.zeros(count, dtype=np.uint8)
    stat_core = np.zeros(count, dtype=np.uint8)
    distance = np.zeros(count, dtype=np.float32)
    radius[indices] = subset.radius_outlier_mask
    statistical[indices] = subset.statistical_outlier_mask
    neighbor_count[indices] = subset.neighbor_count
    means[indices] = subset.mean_neighbor_distance_m
    core[indices] = subset.core_evaluation_count
    stat_core[indices] = subset.statistical_evaluation_count
    distance[indices] = subset.distance_proxy_m
    return NeighborFilterResult(
        radius_outlier_mask=radius,
        statistical_outlier_mask=statistical,
        neighbor_count=neighbor_count,
        mean_neighbor_distance_m=means,
        core_evaluation_count=core,
        statistical_evaluation_count=stat_core,
        distance_proxy_m=distance,
        distance_proxy_source=subset.distance_proxy_source,
        backend=subset.backend,
        tile_count=subset.tile_count,
        radius_seconds=subset.radius_seconds,
        statistical_seconds=subset.statistical_seconds,
    )


def _add_reason(
    reason_bits: np.ndarray, mask: np.ndarray, reason: RemovalReason
) -> None:
    reason_bits[mask] |= np.uint16(int(reason))


def _stage(
    name: str, before_keep: np.ndarray, after_keep: np.ndarray, seconds: float
) -> PostprocessStage:
    input_count = int(np.count_nonzero(before_keep))
    output_count = int(np.count_nonzero(after_keep))
    return PostprocessStage(
        stage=name,
        input_count=input_count,
        output_count=output_count,
        removed_count=input_count - output_count,
        seconds=float(seconds),
    )


def primary_removal_reasons(reason_bits: np.ndarray) -> np.ndarray:
    bits = np.asarray(reason_bits, dtype=np.uint16)
    primary = np.zeros(bits.shape, dtype=np.uint16)
    for reason in PRIMARY_REASON_PRIORITY:
        assign = (primary == 0) & ((bits & int(reason)) != 0)
        primary[assign] = np.uint16(int(reason))
    return primary


def diagnostic_removal_colors(primary_reason: np.ndarray) -> np.ndarray:
    primary = np.asarray(primary_reason, dtype=np.uint16)
    colors = np.zeros((len(primary), 3), dtype=np.uint8)
    for reason, color in REMOVAL_REASON_COLORS_RGB.items():
        colors[primary == int(reason)] = color
    return colors


def _occupied_xy_count(
    points: np.ndarray, mask: np.ndarray, grid_size_m: float
) -> int:
    selected = points[mask & np.all(np.isfinite(points), axis=1), :2]
    if len(selected) == 0:
        return 0
    keys = np.floor(selected / grid_size_m).astype(np.int64)
    return int(len(np.unique(keys, axis=0)))


def _trajectory_corridor_segments(
    points: np.ndarray,
    finite_mask: np.ndarray,
    keep_mask: np.ndarray,
    trajectory_enu_m: np.ndarray | None,
    corridor_half_width_m: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Mark the corridor and summarize raw/clean support in 10 m path bins."""

    corridor = np.zeros(len(points), dtype=bool)
    empty_metrics: dict[str, Any] = {
        "trajectory_segment_length_m": _TRAJECTORY_QUALITY_SEGMENT_LENGTH_M,
        "trajectory_segment_count": 0,
        "raw_supported_trajectory_segment_count": 0,
        "clean_supported_trajectory_segment_count": 0,
        "empty_clean_trajectory_segment_count": 0,
        "trajectory_segment_coverage_retention": None,
        "empty_clean_trajectory_segment_indices": (),
    }
    if trajectory_enu_m is None or not np.any(finite_mask):
        return corridor, empty_metrics
    trajectory = np.asarray(trajectory_enu_m, dtype=np.float64)
    if trajectory.ndim != 2 or trajectory.shape[1:] != (3,):
        raise ValueError("trajectory_enu_m must have shape (M, 3)")
    trajectory = trajectory[np.all(np.isfinite(trajectory), axis=1)]
    if len(trajectory) == 0:
        return corridor, empty_metrics

    step_distance = np.linalg.norm(np.diff(trajectory[:, :2], axis=0), axis=1)
    cumulative_distance = np.concatenate(
        (np.zeros(1, dtype=np.float64), np.cumsum(step_distance, dtype=np.float64))
    )
    segment_count = max(
        1,
        int(
            np.ceil(
                float(cumulative_distance[-1])
                / _TRAJECTORY_QUALITY_SEGMENT_LENGTH_M
            )
        ),
    )
    trajectory_segment = np.minimum(
        np.floor(
            cumulative_distance / _TRAJECTORY_QUALITY_SEGMENT_LENGTH_M
        ).astype(np.int64),
        segment_count - 1,
    )
    raw_supported = np.zeros(segment_count, dtype=bool)
    clean_supported = np.zeros(segment_count, dtype=bool)
    tree = cKDTree(trajectory[:, :2])
    for begin in range(0, len(points), _TRAJECTORY_QUERY_CHUNK_POINTS):
        end = min(begin + _TRAJECTORY_QUERY_CHUNK_POINTS, len(points))
        finite_local = finite_mask[begin:end]
        if not np.any(finite_local):
            continue
        local_indices = np.flatnonzero(finite_local)
        distances, nearest = tree.query(
            points[begin:end][local_indices, :2],
            k=1,
            workers=1,
        )
        # Preserve the existing aggregate corridor's float32 distance semantics.
        distances = np.asarray(distances, dtype=np.float32)
        within = distances <= corridor_half_width_m
        if not np.any(within):
            continue
        global_indices = begin + local_indices[within]
        corridor[global_indices] = True
        supported_segments = trajectory_segment[nearest[within]]
        raw_supported[supported_segments] = True
        clean_segments = supported_segments[keep_mask[global_indices]]
        clean_supported[clean_segments] = True

    missing = np.flatnonzero(raw_supported & ~clean_supported)
    raw_count = int(np.count_nonzero(raw_supported))
    clean_count = int(np.count_nonzero(clean_supported & raw_supported))
    return corridor, {
        "trajectory_segment_length_m": _TRAJECTORY_QUALITY_SEGMENT_LENGTH_M,
        "trajectory_segment_count": segment_count,
        "raw_supported_trajectory_segment_count": raw_count,
        "clean_supported_trajectory_segment_count": clean_count,
        "empty_clean_trajectory_segment_count": int(len(missing)),
        "trajectory_segment_coverage_retention": (
            float(clean_count / raw_count) if raw_count else None
        ),
        "empty_clean_trajectory_segment_indices": tuple(
            int(index) for index in missing
        ),
    }


def _retention(clean_count: int, raw_count: int) -> float | None:
    return None if raw_count == 0 else float(clean_count / raw_count)


def _reduction(clean_count: int, raw_count: int) -> float | None:
    return None if raw_count == 0 else float(1.0 - clean_count / raw_count)


def compute_quality_guards(
    points_enu_m: np.ndarray,
    keep_mask: np.ndarray,
    metadata: PointCloudMetadata,
    neighbor_result: NeighborFilterResult,
    ground_surface: GroundSurfaceResult | None,
    trajectory_enu_m: np.ndarray | None,
    colors_rgb: np.ndarray,
    config: PostprocessConfig,
) -> QualityGuardResult:
    points = np.asarray(points_enu_m)
    keep = np.asarray(keep_mask, dtype=bool)
    finite = np.all(np.isfinite(points), axis=1)
    raw_xy_cells = _occupied_xy_count(points, finite, config.quality_grid_size_m)
    clean_xy_cells = _occupied_xy_count(
        points, finite & keep, config.quality_grid_size_m
    )
    xy_retention = _retention(clean_xy_cells, raw_xy_cells)

    corridor, trajectory_segment_metrics = _trajectory_corridor_segments(
        points,
        finite,
        keep,
        trajectory_enu_m,
        config.road_corridor_half_width_m,
    )
    raw_corridor_cells = _occupied_xy_count(
        points, finite & corridor, config.quality_grid_size_m
    )
    clean_corridor_cells = _occupied_xy_count(
        points, finite & corridor & keep, config.quality_grid_size_m
    )
    corridor_retention = _retention(clean_corridor_cells, raw_corridor_cells)

    high_structure = np.zeros(len(points), dtype=bool)
    below = np.zeros(len(points), dtype=bool)
    if ground_surface is not None:
        surface = ground_surface.point_surface_z_m
        has_surface = np.isfinite(surface) & finite
        high_structure = has_surface & (points[:, 2] >= surface + 0.5)
        below = ground_surface.below_surface_mask & finite
    raw_high = int(np.count_nonzero(high_structure))
    clean_high = int(np.count_nonzero(high_structure & keep))
    raw_below = int(np.count_nonzero(below))
    clean_below = int(np.count_nonzero(below & keep))

    low_support = metadata.distinct_frame_count < config.min_distinct_frames
    isolated = (
        neighbor_result.core_evaluation_count > 0
    ) & (neighbor_result.neighbor_count < config.single_frame_min_neighbors)
    bright_isolated = bright_neutral_mask(colors_rgb, config) & (
        low_support | isolated
    )
    raw_bright = int(np.count_nonzero(bright_isolated))
    clean_bright = int(np.count_nonzero(bright_isolated & keep))

    raw_count = len(points)
    removal_ratio = float(1.0 - np.count_nonzero(keep) / raw_count) if raw_count else 0.0
    warnings: list[str] = []
    failed = False
    if xy_retention is not None and xy_retention < 0.90:
        warnings.append("XY coverage retention is below 0.90")
        failed = True
    if corridor_retention is not None and corridor_retention < 0.90:
        warnings.append("trajectory corridor coverage retention is below 0.90")
        failed = True
    if trajectory_segment_metrics["empty_clean_trajectory_segment_count"]:
        warnings.append(
            f"{trajectory_segment_metrics['empty_clean_trajectory_segment_count']} "
            "trajectory segment(s) had raw corridor points but no clean points"
        )
    high_retention = _retention(clean_high, raw_high)
    if high_retention is not None and high_retention < 0.85:
        warnings.append("high-structure retention is below 0.85")
        failed = True
    below_reduction = _reduction(clean_below, raw_below)
    if below_reduction is not None and below_reduction < 0.60:
        warnings.append("below-surface candidate reduction is below 0.60")
    bright_reduction = _reduction(clean_bright, raw_bright)
    if bright_reduction is not None and bright_reduction < 0.50:
        warnings.append("bright isolated-point reduction is below 0.50")
    if removal_ratio < 0.002 and config.enabled:
        warnings.append("removal ratio is below 0.2%; filtering may have little effect")
    if removal_ratio > 0.35:
        warnings.append("removal ratio exceeds 35%; over-removal is possible")
        failed = True
    if removal_ratio > 0.50:
        warnings.append("removal ratio exceeds the 50% failure threshold")

    raw_finite = points[finite]
    clean_finite = points[finite & keep]
    bbox_retention: list[float | None] = [None, None]
    maximum_z_retention: float | None = None
    if len(raw_finite) and len(clean_finite):
        raw_extent = np.ptp(raw_finite, axis=0)
        clean_extent = np.ptp(clean_finite, axis=0)
        for axis in (0, 1):
            if raw_extent[axis] > 0.0:
                bbox_retention[axis] = float(clean_extent[axis] / raw_extent[axis])
                if bbox_retention[axis] < 0.90:
                    warnings.append(
                        f"{'XY'[axis]} bbox length decreased by more than 10%"
                    )
        raw_max_z = float(np.max(raw_finite[:, 2]))
        clean_max_z = float(np.max(clean_finite[:, 2]))
        if raw_max_z != 0.0:
            maximum_z_retention = clean_max_z / raw_max_z
        if raw_extent[2] > 0.0 and clean_max_z < raw_max_z - 0.20 * raw_extent[2]:
            warnings.append("maximum Z decreased by more than 20% of raw Z extent")

    return QualityGuardResult(
        xy_coverage_retention=xy_retention,
        trajectory_corridor_coverage_retention=corridor_retention,
        high_structure_retention=high_retention,
        below_surface_reduction=below_reduction,
        bright_isolated_reduction=bright_reduction,
        removal_ratio=removal_ratio,
        raw_xy_occupied_cells=raw_xy_cells,
        clean_xy_occupied_cells=clean_xy_cells,
        raw_corridor_occupied_cells=raw_corridor_cells,
        clean_corridor_occupied_cells=clean_corridor_cells,
        raw_high_structure_point_count=raw_high,
        clean_high_structure_point_count=clean_high,
        raw_below_surface_point_count=raw_below,
        clean_below_surface_point_count=clean_below,
        raw_bright_isolated_point_count=raw_bright,
        clean_bright_isolated_point_count=clean_bright,
        xy_bbox_length_retention=(bbox_retention[0], bbox_retention[1]),
        maximum_z_retention=maximum_z_retention,
        passed=not failed,
        warnings=tuple(warnings),
        **trajectory_segment_metrics,
    )


def fallback_preset_for_quality(
    quality: QualityGuardResult, current_preset: str
) -> str | None:
    """Recommend at most one documented postprocess-only fallback preset."""

    over_removed = (
        (
            quality.xy_coverage_retention is not None
            and quality.xy_coverage_retention < 0.90
        )
        or (
            quality.trajectory_corridor_coverage_retention is not None
            and quality.trajectory_corridor_coverage_retention < 0.90
        )
        or (
            quality.high_structure_retention is not None
            and quality.high_structure_retention < 0.85
        )
        or quality.removal_ratio > 0.35
    )
    if over_removed and current_preset != "conservative":
        return "conservative"
    insufficient_cleanup = quality.raw_xy_occupied_cells >= 3 and quality.removal_ratio < 0.002 and (
        quality.below_surface_reduction is None
        or quality.below_surface_reduction < 0.30
    )
    if insufficient_cleanup and current_preset not in {"off", "aggressive"}:
        return "aggressive"
    return None


def _reason_count_report(
    reason_bits: np.ndarray, primary_reason: np.ndarray
) -> dict[str, dict[str, int]]:
    any_counts: dict[str, int] = {}
    primary_counts: dict[str, int] = {}
    for reason in RemovalReason:
        if reason == RemovalReason.NONE:
            continue
        any_counts[reason.name] = int(np.count_nonzero(reason_bits & int(reason)))
        primary_counts[reason.name] = int(
            np.count_nonzero(primary_reason == int(reason))
        )
    return {
        "any_reason_count": any_counts,
        "primary_reason_count": primary_counts,
    }


def run_postprocess(
    points_enu_m: np.ndarray,
    colors_rgb: np.ndarray,
    metadata: Mapping[str, Any] | object | None,
    trajectory_enu_m: np.ndarray | None,
    config: PostprocessConfig,
    *,
    neighbor_backend: str = "auto",
    ground_backend: str = "auto",
) -> PostprocessResult:
    """Apply the complete in-memory local point-cloud cleanup pipeline."""

    points = np.asarray(points_enu_m)
    colors = np.asarray(colors_rgb)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_enu_m must have shape (N, 3)")
    if colors.shape != points.shape:
        raise ValueError("colors_rgb must have shape (N, 3) and match points")
    if not np.issubdtype(points.dtype, np.number):
        raise ValueError("points_enu_m must be numeric")
    if not np.issubdtype(colors.dtype, np.number) or not np.all(np.isfinite(colors)):
        raise ValueError("colors_rgb must contain finite numeric values")
    if np.any(colors < 0) or np.any(colors > 255):
        raise ValueError("colors_rgb values must be in [0, 255]")
    colors = colors.astype(np.uint8, copy=False)
    count = len(points)
    point_metadata = coerce_point_metadata(
        metadata,
        count,
        default_distinct_frame_count=config.min_distinct_frames,
    )
    dependencies = inspect_dependencies()
    selected_neighbor_backend = resolve_neighbor_backend(neighbor_backend)
    selected_ground_backend = resolve_ground_backend(ground_backend)
    # PDAL is executed later against the atomically written raw PLY. The local
    # result remains the deterministic baseline and supplies complete guards.
    if selected_ground_backend == "pdal":
        selected_ground_backend = "local"
    reason_bits = np.zeros(count, dtype=np.uint16)
    stages: list[PostprocessStage] = [
        PostprocessStage("raw", count, count, 0, 0.0)
    ]

    before = reason_bits == 0
    started = time.perf_counter()
    non_finite = ~np.all(np.isfinite(points), axis=1)
    _add_reason(reason_bits, non_finite, RemovalReason.NON_FINITE)
    after = reason_bits == 0
    stages.append(_stage("non_finite", before, after, time.perf_counter() - started))

    before = after.copy()
    started = time.perf_counter()
    high_spread = (
        (point_metadata.observation_count >= 2)
        & np.isfinite(point_metadata.position_std_m)
        & (point_metadata.position_std_m > config.max_voxel_position_std_m)
        & before
        & config.enabled
    )
    _add_reason(reason_bits, high_spread, RemovalReason.HIGH_POSITION_SPREAD)
    after = reason_bits == 0
    stages.append(
        _stage("high_position_spread", before, after, time.perf_counter() - started)
    )

    before_radius = after.copy()
    neighbor_indices = np.flatnonzero(before_radius)
    if config.enabled and len(neighbor_indices):
        all_points_survived = len(neighbor_indices) == count
        neighbor_points = points if all_points_survived else points[neighbor_indices]
        mean_depth = (
            point_metadata.mean_depth_m
            if all_points_survived
            else point_metadata.mean_depth_m[neighbor_indices]
        )
        subset_neighbor = tiled_neighbor_filter(
            neighbor_points,
            config,
            backend=selected_neighbor_backend,
            mean_depth_m=mean_depth,
            trajectory_enu_m=trajectory_enu_m,
        )
        neighbor_result = (
            subset_neighbor
            if all_points_survived
            else _expand_neighbor_result(subset_neighbor, neighbor_indices, count)
        )
    else:
        neighbor_result = _empty_neighbor_result(
            count,
            selected_neighbor_backend,
            "disabled" if not config.enabled else "empty",
        )
    _add_reason(
        reason_bits,
        neighbor_result.radius_outlier_mask,
        RemovalReason.RADIUS_OUTLIER,
    )
    after_radius = reason_bits == 0
    stages.append(
        _stage(
            "radius_outlier",
            before_radius,
            after_radius,
            neighbor_result.radius_seconds,
        )
    )
    before_statistical = after_radius.copy()
    _add_reason(
        reason_bits,
        neighbor_result.statistical_outlier_mask,
        RemovalReason.STATISTICAL_OUTLIER,
    )
    after_statistical = reason_bits == 0
    stages.append(
        _stage(
            "statistical_outlier",
            before_statistical,
            after_statistical,
            neighbor_result.statistical_seconds,
        )
    )

    before_ground = after_statistical.copy()
    ground_started = time.perf_counter()
    ground_surface: GroundSurfaceResult | None = None
    if config.enabled and selected_ground_backend == "local":
        ground_surface = estimate_local_ground_surface(
            points,
            trajectory_enu_m,
            config,
            support_mask=before_ground,
        )
        _add_reason(
            reason_bits,
            ground_surface.below_surface_mask,
            RemovalReason.BELOW_LOCAL_SURFACE,
        )
    after_ground = reason_bits == 0
    stages.append(
        _stage(
            "local_surface",
            before_ground,
            after_ground,
            time.perf_counter() - ground_started,
        )
    )

    # LOW_MULTI_FRAME_SUPPORT is fatal only when another geometric problem is
    # already present. Bright color can annotate such a point, never delete it alone.
    before_combined = after_ground.copy()
    combined_started = time.perf_counter()
    low_support = point_metadata.distinct_frame_count < config.min_distinct_frames
    geometric_problem_bits = int(
        RemovalReason.HIGH_POSITION_SPREAD
        | RemovalReason.RADIUS_OUTLIER
        | RemovalReason.STATISTICAL_OUTLIER
        | RemovalReason.BELOW_LOCAL_SURFACE
    )
    geometric_problem = (reason_bits & geometric_problem_bits) != 0
    combined_low_support = low_support & geometric_problem & config.enabled
    _add_reason(
        reason_bits,
        combined_low_support,
        RemovalReason.LOW_MULTI_FRAME_SUPPORT,
    )
    bright = bright_neutral_mask(colors, config)
    bright_support_condition = (
        low_support
        if config.bright_filter_requires_low_support
        else np.ones(count, dtype=bool)
    )
    bright_low_support = bright & bright_support_condition & geometric_problem
    _add_reason(
        reason_bits,
        bright_low_support & config.enabled,
        RemovalReason.BRIGHT_LOW_SUPPORT,
    )
    after_combined = reason_bits == 0
    stages.append(
        _stage(
            "low_support_bright_combined",
            before_combined,
            after_combined,
            time.perf_counter() - combined_started,
        )
    )

    keep_mask = reason_bits == 0
    removed_mask = ~keep_mask
    clean_indices = np.flatnonzero(keep_mask).astype(np.int64, copy=False)
    removed_indices = np.flatnonzero(removed_mask).astype(np.int64, copy=False)
    primary_reason = primary_removal_reasons(reason_bits)
    diagnostic_colors = diagnostic_removal_colors(primary_reason[removed_indices])
    quality = compute_quality_guards(
        points,
        keep_mask,
        point_metadata,
        neighbor_result,
        ground_surface,
        trajectory_enu_m,
        colors,
        config,
    )
    reason_report = _reason_count_report(reason_bits, primary_reason)
    timing = {stage.stage: stage.seconds for stage in stages}
    report: dict[str, Any] = {
        "format_version": 1,
        "selected_result": f"{selected_ground_backend}_{config.preset.replace('-', '_')}",
        "input": {
            "raw_point_count": count,
            "voxel_size_m": config.voxel_size_m,
        },
        "output": {
            "clean_point_count": int(len(clean_indices)),
            "removed_point_count": int(len(removed_indices)),
            "removal_ratio": quality.removal_ratio,
        },
        "reasons": reason_report,
        "bright_points": {
            "bright_total_count": int(np.count_nonzero(bright)),
            "bright_low_support_count": int(np.count_nonzero(bright & low_support)),
            "bright_removed_count": int(np.count_nonzero(bright & removed_mask)),
            "bright_retained_count": int(np.count_nonzero(bright & keep_mask)),
        },
        "quality_guards": quality.to_dict(),
        "timing_seconds": timing,
        "dependencies": dependencies.to_dict(),
        "parameters": config.to_dict(),
        "neighbor_backend": neighbor_result.backend,
        "ground_backend": selected_ground_backend,
        "neighbor_distance_proxy": neighbor_result.distance_proxy_source,
        "ground_surface": (
            ground_surface.stats.to_dict() if ground_surface is not None else None
        ),
        "radius_survivors_used_for_statistical": True,
    }
    if count != len(clean_indices) + len(removed_indices):
        raise RuntimeError("postprocess accounting invariant failed")
    return PostprocessResult(
        raw_points_enu_m=points,
        raw_colors_rgb=colors,
        clean_points_enu_m=points[clean_indices],
        clean_colors_rgb=colors[clean_indices],
        removed_points_enu_m=points[removed_indices],
        removed_original_colors_rgb=colors[removed_indices],
        removed_diagnostic_colors_rgb=diagnostic_colors,
        keep_mask=keep_mask,
        removed_mask=removed_mask,
        clean_indices=clean_indices,
        removed_indices=removed_indices,
        removal_reason_bits=reason_bits,
        primary_reason=primary_reason,
        metadata=point_metadata,
        neighbor_result=neighbor_result,
        ground_surface=ground_surface,
        stages=tuple(stages),
        quality=quality,
        config=config,
        neighbor_backend=neighbor_result.backend,
        ground_backend=selected_ground_backend,
        dependencies=dependencies,
        report=report,
    )


def run_postprocess_cloud(
    cloud: object,
    trajectory: object | np.ndarray | None,
    config: PostprocessConfig,
    *,
    metadata: Mapping[str, Any] | object | None = None,
    neighbor_backend: str = "auto",
    ground_backend: str = "auto",
) -> PostprocessResult:
    """Convenience adapter for a PointCloudResult-like object."""

    points = getattr(cloud, "points_enu_m")
    colors = getattr(cloud, "colors_rgb")
    metadata_source = cloud if metadata is None else metadata
    if trajectory is None or isinstance(trajectory, np.ndarray):
        trajectory_points = trajectory
    else:
        trajectory_points = getattr(trajectory, "positions_enu_m")
    return run_postprocess(
        points,
        colors,
        metadata_source,
        trajectory_points,
        config,
        neighbor_backend=neighbor_backend,
        ground_backend=ground_backend,
    )
