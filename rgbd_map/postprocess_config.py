from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np


POSTPROCESS_PRESET_NAMES = (
    "off",
    "conservative",
    "road-map",
    "aggressive",
    "road-map-temporal",
)


@dataclass(frozen=True)
class PostprocessConfig:
    """Fully resolved, JSON-serializable point-cloud cleanup parameters."""

    preset: str
    enabled: bool
    voxel_size_m: float

    depth_edge_filter: bool
    depth_edge_radius_px: int
    depth_edge_abs_m: float
    depth_edge_rel_ratio: float
    depth_edge_min_valid_neighbors: int

    min_distinct_frames: int
    max_voxel_position_std_m: float
    single_frame_min_neighbors: int

    radius_outlier_radius_m: float
    radius_outlier_min_neighbors: int
    statistical_neighbors: int
    statistical_std_ratio: float

    tile_size_m: float
    tile_overlap_m: float

    road_corridor_half_width_m: float
    ground_grid_size_m: float
    ground_z_bin_m: float
    ground_min_cell_points: int
    ground_min_neighbor_cells: int
    ground_candidate_below_camera_m: float
    ground_candidate_above_surface_m: float
    ground_max_neighbor_height_delta_m: float
    below_ground_tolerance_m: float

    bright_min_rgb: int
    bright_max_chroma: int
    bright_filter_requires_low_support: bool

    quality_grid_size_m: float = 0.5

    # Projection-time depth quality. Defaults preserve the legacy presets.
    far_depth_policy: str = "off"
    far_depth_soft_start_m: float = 20.0
    far_depth_hard_m: float = 28.8
    depth_confidence_threshold: float | None = None
    depth_confidence_order: str = "higher-is-better"
    depth_edge_domain: str = "depth"
    invalid_boundary_erosion_px: int = 0
    far_speckle_max_pixels: int = 0

    # Quality support is deliberately coarser than the output voxel grid.
    support_enabled: bool = False
    support_voxel_size_m: float = 0.15
    support_far_voxel_size_m: float = 0.25
    support_far_start_m: float = 20.0
    support_min_independent_frames: int = 2
    support_min_baseline_m: float = 0.4
    support_min_time_separation_s: float = 0.5
    max_support_position_std_m: float = 0.18

    temporal_enabled: bool = False
    temporal_window_seconds: float = 0.25
    temporal_depth_abs_m: float = 0.15
    temporal_depth_rel_ratio: float = 0.02
    temporal_max_free_space_contradictions: int = 0

    pose_cloud_policy: str = "keep"
    pose_cloud_max_edge_dt_s: float = 0.25
    pose_cloud_min_inliers: int = 24
    pose_cloud_min_inlier_ratio: float = 0.20
    pose_cloud_max_reprojection_error_px: float = 2.5

    ground_seed_half_width_m: float = 7.0
    ground_apply_half_width_m: float = 7.0
    ground_max_interpolation_gap_m: float = 3.0
    ground_max_lateral_slope: float = 0.20
    ground_max_uncertainty_m: float = 0.35

    map_envelope_mode: str = "off"
    map_corridor_core_half_width_m: float = 10.0
    map_corridor_soft_half_width_m: float = 25.0
    map_envelope_end_buffer_m: float = 30.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _PresetDefinition:
    depth_edge_filter: bool
    depth_edge_radius_px: int
    depth_edge_abs_m: float
    depth_edge_rel_ratio: float
    depth_edge_min_valid_neighbors: int
    min_distinct_frames: int
    position_std_floor_m: float
    position_std_voxel_multiplier: float
    radius_floor_m: float
    radius_voxel_multiplier: float
    radius_outlier_min_neighbors: int
    single_frame_min_neighbors: int
    statistical_neighbors: int
    statistical_std_ratio: float
    tile_size_m: float
    tile_overlap_m: float
    road_corridor_half_width_m: float
    ground_grid_size_m: float
    ground_z_bin_m: float
    ground_min_cell_points: int
    ground_min_neighbor_cells: int
    below_ground_tolerance_m: float
    bright_min_rgb: int
    bright_max_chroma: int
    bright_filter_requires_low_support: bool


_COMMON = {
    "depth_edge_filter": True,
    "depth_edge_radius_px": 1,
    "min_distinct_frames": 2,
    "tile_size_m": 20.0,
    "tile_overlap_m": 0.75,
    "ground_grid_size_m": 0.50,
    "ground_z_bin_m": 0.05,
    "ground_min_neighbor_cells": 3,
    "bright_filter_requires_low_support": True,
}


POSTPROCESS_PRESETS: Mapping[str, _PresetDefinition] = MappingProxyType({
    "off": _PresetDefinition(
        depth_edge_filter=False,
        depth_edge_radius_px=1,
        depth_edge_abs_m=0.0,
        depth_edge_rel_ratio=0.0,
        depth_edge_min_valid_neighbors=0,
        min_distinct_frames=1,
        position_std_floor_m=1.0e30,
        position_std_voxel_multiplier=0.0,
        radius_floor_m=0.0,
        radius_voxel_multiplier=0.0,
        radius_outlier_min_neighbors=0,
        single_frame_min_neighbors=0,
        statistical_neighbors=0,
        statistical_std_ratio=1.0e30,
        tile_size_m=20.0,
        tile_overlap_m=0.75,
        road_corridor_half_width_m=0.0,
        ground_grid_size_m=0.50,
        ground_z_bin_m=0.05,
        ground_min_cell_points=8,
        ground_min_neighbor_cells=3,
        below_ground_tolerance_m=1.0e30,
        bright_min_rgb=255,
        bright_max_chroma=0,
        bright_filter_requires_low_support=True,
    ),
    "conservative": _PresetDefinition(
        **_COMMON,
        depth_edge_abs_m=0.25,
        depth_edge_rel_ratio=0.04,
        depth_edge_min_valid_neighbors=3,
        position_std_floor_m=0.12,
        position_std_voxel_multiplier=2.5,
        radius_floor_m=0.18,
        radius_voxel_multiplier=3.5,
        radius_outlier_min_neighbors=2,
        single_frame_min_neighbors=2,
        statistical_neighbors=16,
        statistical_std_ratio=2.8,
        road_corridor_half_width_m=7.0,
        ground_min_cell_points=8,
        below_ground_tolerance_m=0.25,
        bright_min_rgb=248,
        bright_max_chroma=10,
    ),
    "road-map": _PresetDefinition(
        **_COMMON,
        depth_edge_abs_m=0.18,
        depth_edge_rel_ratio=0.03,
        depth_edge_min_valid_neighbors=4,
        position_std_floor_m=0.10,
        position_std_voxel_multiplier=2.0,
        radius_floor_m=0.15,
        radius_voxel_multiplier=3.0,
        radius_outlier_min_neighbors=3,
        single_frame_min_neighbors=3,
        statistical_neighbors=20,
        statistical_std_ratio=2.2,
        road_corridor_half_width_m=8.0,
        ground_min_cell_points=8,
        below_ground_tolerance_m=0.20,
        bright_min_rgb=245,
        bright_max_chroma=12,
    ),
    "aggressive": _PresetDefinition(
        **_COMMON,
        depth_edge_abs_m=0.12,
        depth_edge_rel_ratio=0.025,
        depth_edge_min_valid_neighbors=4,
        position_std_floor_m=0.08,
        position_std_voxel_multiplier=1.5,
        radius_floor_m=0.16,
        radius_voxel_multiplier=3.0,
        radius_outlier_min_neighbors=5,
        single_frame_min_neighbors=5,
        statistical_neighbors=24,
        statistical_std_ratio=1.8,
        road_corridor_half_width_m=8.0,
        ground_min_cell_points=8,
        below_ground_tolerance_m=0.15,
        bright_min_rgb=245,
        bright_max_chroma=12,
    ),
    "road-map-temporal": _PresetDefinition(
        **{**_COMMON, "depth_edge_radius_px": 2},
        depth_edge_abs_m=0.12,
        depth_edge_rel_ratio=0.02,
        depth_edge_min_valid_neighbors=8,
        position_std_floor_m=0.025,
        position_std_voxel_multiplier=0.0,
        radius_floor_m=0.18,
        radius_voxel_multiplier=3.5,
        radius_outlier_min_neighbors=2,
        single_frame_min_neighbors=2,
        statistical_neighbors=16,
        statistical_std_ratio=3.5,
        road_corridor_half_width_m=7.0,
        ground_min_cell_points=8,
        below_ground_tolerance_m=0.20,
        bright_min_rgb=248,
        bright_max_chroma=10,
    ),
})


def _override_mapping(overrides: Mapping[str, Any] | object | None) -> dict[str, Any]:
    if overrides is None:
        return {}
    if isinstance(overrides, Mapping):
        source = dict(overrides)
    else:
        try:
            source = vars(overrides)
        except TypeError as exc:  # pragma: no cover - defensive API guard
            raise TypeError("overrides must be a mapping or an attribute object") from exc
    aliases = {
        "postprocess_tile_size_m": "tile_size_m",
        "postprocess_tile_overlap_m": "tile_overlap_m",
    }
    valid = {field.name for field in fields(PostprocessConfig)} - {
        "preset",
        "enabled",
        "voxel_size_m",
    }
    result: dict[str, Any] = {}
    for original_name, value in source.items():
        if value is None:
            continue
        name = aliases.get(original_name, original_name)
        if name in valid:
            result[name] = value
    return result


def _off_config(voxel_size_m: float) -> PostprocessConfig:
    return PostprocessConfig(
        preset="off",
        enabled=False,
        voxel_size_m=voxel_size_m,
        depth_edge_filter=False,
        depth_edge_radius_px=1,
        depth_edge_abs_m=0.0,
        depth_edge_rel_ratio=0.0,
        depth_edge_min_valid_neighbors=0,
        min_distinct_frames=1,
        max_voxel_position_std_m=1.0e30,
        single_frame_min_neighbors=0,
        radius_outlier_radius_m=0.0,
        radius_outlier_min_neighbors=0,
        statistical_neighbors=0,
        statistical_std_ratio=1.0e30,
        tile_size_m=20.0,
        tile_overlap_m=0.75,
        road_corridor_half_width_m=0.0,
        ground_grid_size_m=0.50,
        ground_z_bin_m=0.05,
        ground_min_cell_points=8,
        ground_min_neighbor_cells=3,
        ground_candidate_below_camera_m=4.0,
        ground_candidate_above_surface_m=0.25,
        ground_max_neighbor_height_delta_m=0.75,
        below_ground_tolerance_m=1.0e30,
        bright_min_rgb=255,
        bright_max_chroma=0,
        bright_filter_requires_low_support=True,
    )


def resolve_postprocess_config(
    preset: str,
    voxel_size_m: float,
    overrides: Mapping[str, Any] | object | None = None,
) -> PostprocessConfig:
    """Resolve a named preset, then apply non-``None`` per-field overrides."""

    name = str(preset)
    if name not in POSTPROCESS_PRESET_NAMES:
        choices = ", ".join(POSTPROCESS_PRESET_NAMES)
        raise ValueError(f"postprocess preset must be one of: {choices}")
    voxel_size = float(voxel_size_m)
    if not np.isfinite(voxel_size) or voxel_size <= 0.0:
        raise ValueError("voxel_size_m must be a finite positive value")

    override_values = _override_mapping(overrides)
    if name == "off":
        config = _off_config(voxel_size)
    else:
        definition = POSTPROCESS_PRESETS[name]
        resolved_radius_m = max(
            definition.radius_floor_m,
            voxel_size * definition.radius_voxel_multiplier,
        )
        config = PostprocessConfig(
            preset=name,
            enabled=True,
            voxel_size_m=voxel_size,
            depth_edge_filter=definition.depth_edge_filter,
            depth_edge_radius_px=definition.depth_edge_radius_px,
            depth_edge_abs_m=definition.depth_edge_abs_m,
            depth_edge_rel_ratio=definition.depth_edge_rel_ratio,
            depth_edge_min_valid_neighbors=definition.depth_edge_min_valid_neighbors,
            min_distinct_frames=definition.min_distinct_frames,
            max_voxel_position_std_m=max(
                definition.position_std_floor_m,
                voxel_size * definition.position_std_voxel_multiplier,
            ),
            single_frame_min_neighbors=definition.single_frame_min_neighbors,
            radius_outlier_radius_m=resolved_radius_m,
            radius_outlier_min_neighbors=definition.radius_outlier_min_neighbors,
            statistical_neighbors=definition.statistical_neighbors,
            statistical_std_ratio=definition.statistical_std_ratio,
            tile_size_m=definition.tile_size_m,
            tile_overlap_m=max(
                definition.tile_overlap_m,
                resolved_radius_m * 1.5,
            ),
            road_corridor_half_width_m=definition.road_corridor_half_width_m,
            ground_grid_size_m=definition.ground_grid_size_m,
            ground_z_bin_m=definition.ground_z_bin_m,
            ground_min_cell_points=definition.ground_min_cell_points,
            ground_min_neighbor_cells=definition.ground_min_neighbor_cells,
            ground_candidate_below_camera_m=4.0,
            ground_candidate_above_surface_m=0.25,
            ground_max_neighbor_height_delta_m=0.75,
            below_ground_tolerance_m=definition.below_ground_tolerance_m,
            bright_min_rgb=definition.bright_min_rgb,
            bright_max_chroma=definition.bright_max_chroma,
            bright_filter_requires_low_support=(
                definition.bright_filter_requires_low_support
            ),
            ground_seed_half_width_m=definition.road_corridor_half_width_m,
            ground_apply_half_width_m=definition.road_corridor_half_width_m,
        )
        if name == "road-map-temporal":
            values = config.to_dict()
            values.update(
                {
                    "far_depth_policy": "adaptive",
                    "depth_edge_domain": "inverse-depth",
                    "invalid_boundary_erosion_px": 1,
                    "far_speckle_max_pixels": 12,
                    "support_enabled": True,
                    "temporal_enabled": True,
                    "pose_cloud_policy": "interpolate",
                    "ground_seed_half_width_m": 8.0,
                    "ground_apply_half_width_m": 30.0,
                    "ground_max_interpolation_gap_m": 4.0,
                    "map_envelope_mode": "soft",
                }
            )
            config = PostprocessConfig(**values)
    if override_values:
        values = config.to_dict()
        values.update(override_values)
        if (
            "radius_outlier_radius_m" in override_values
            and "tile_overlap_m" not in override_values
        ):
            values["tile_overlap_m"] = max(
                float(values["tile_overlap_m"]),
                float(values["radius_outlier_radius_m"]) * 1.5,
            )
        config = PostprocessConfig(**values)
    _validate_config(config)
    return config


def _validate_config(config: PostprocessConfig) -> None:
    if config.preset not in POSTPROCESS_PRESET_NAMES:
        raise ValueError("invalid postprocess preset in resolved config")
    if config.depth_edge_radius_px < 1:
        raise ValueError("depth_edge_radius_px must be at least 1")
    integer_minima = {
        "depth_edge_min_valid_neighbors": 0,
        "min_distinct_frames": 1,
        "single_frame_min_neighbors": 0,
        "radius_outlier_min_neighbors": 0,
        "statistical_neighbors": 0,
        "ground_min_cell_points": 1,
        "ground_min_neighbor_cells": 1,
        "invalid_boundary_erosion_px": 0,
        "far_speckle_max_pixels": 0,
        "support_min_independent_frames": 1,
        "temporal_max_free_space_contradictions": 0,
        "pose_cloud_min_inliers": 0,
    }
    for name, minimum in integer_minima.items():
        value = getattr(config, name)
        if isinstance(value, (bool, np.bool_)) or int(value) != value or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    finite_positive = (
        "voxel_size_m",
        "tile_size_m",
        "tile_overlap_m",
        "ground_grid_size_m",
        "ground_z_bin_m",
        "ground_candidate_below_camera_m",
        "ground_candidate_above_surface_m",
        "ground_max_neighbor_height_delta_m",
        "quality_grid_size_m",
        "far_depth_soft_start_m",
        "far_depth_hard_m",
        "support_voxel_size_m",
        "support_far_voxel_size_m",
        "support_far_start_m",
        "support_min_baseline_m",
        "support_min_time_separation_s",
        "max_support_position_std_m",
        "temporal_window_seconds",
        "temporal_depth_abs_m",
        "pose_cloud_max_edge_dt_s",
        "pose_cloud_min_inlier_ratio",
        "pose_cloud_max_reprojection_error_px",
        "ground_seed_half_width_m",
        "ground_apply_half_width_m",
        "ground_max_interpolation_gap_m",
        "ground_max_lateral_slope",
        "ground_max_uncertainty_m",
        "map_corridor_core_half_width_m",
        "map_corridor_soft_half_width_m",
        "map_envelope_end_buffer_m",
    )
    for name in finite_positive:
        value = float(getattr(config, name))
        if not np.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be a finite positive value")
    if config.enabled:
        if config.radius_outlier_min_neighbors < 1:
            raise ValueError("radius_outlier_min_neighbors must be at least 1")
        if config.single_frame_min_neighbors < 1:
            raise ValueError("single_frame_min_neighbors must be at least 1")
        if config.statistical_neighbors < 1:
            raise ValueError("statistical_neighbors must be at least 1")
        for name in (
            "depth_edge_abs_m",
            "depth_edge_rel_ratio",
            "max_voxel_position_std_m",
            "radius_outlier_radius_m",
            "statistical_std_ratio",
            "road_corridor_half_width_m",
            "below_ground_tolerance_m",
        ):
            value = float(getattr(config, name))
            if not np.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be a finite positive value")
        required_overlap = config.radius_outlier_radius_m * 1.5
        if config.tile_overlap_m + 1e-12 < required_overlap:
            raise ValueError(
                "tile_overlap_m must cover the far-distance radius "
                f"({required_overlap:.6g} m)"
            )
    if config.tile_overlap_m >= config.tile_size_m:
        raise ValueError("tile_overlap_m must be smaller than tile_size_m")
    if not 0 <= config.bright_min_rgb <= 255:
        raise ValueError("bright_min_rgb must be in [0, 255]")
    if not 0 <= config.bright_max_chroma <= 255:
        raise ValueError("bright_max_chroma must be in [0, 255]")
    if config.far_depth_policy not in {"off", "fixed", "adaptive"}:
        raise ValueError("far_depth_policy must be off, fixed, or adaptive")
    if config.depth_confidence_order not in {
        "lower-is-better",
        "higher-is-better",
    }:
        raise ValueError("invalid depth_confidence_order")
    if config.depth_edge_domain not in {"depth", "inverse-depth"}:
        raise ValueError("invalid depth_edge_domain")
    if config.pose_cloud_policy not in {"keep", "skip", "interpolate"}:
        raise ValueError("invalid pose_cloud_policy")
    if config.map_envelope_mode not in {"off", "soft", "road-only"}:
        raise ValueError("invalid map_envelope_mode")
    if config.far_depth_hard_m <= config.far_depth_soft_start_m:
        raise ValueError("far_depth_hard_m must exceed far_depth_soft_start_m")
    if config.support_far_voxel_size_m < config.support_voxel_size_m:
        raise ValueError(
            "support_far_voxel_size_m must be at least support_voxel_size_m"
        )
    if config.ground_apply_half_width_m < config.ground_seed_half_width_m:
        raise ValueError("ground_apply_half_width_m must cover the seed corridor")
    if config.map_corridor_soft_half_width_m < config.map_corridor_core_half_width_m:
        raise ValueError("soft map corridor must cover the core corridor")
    if (
        not np.isfinite(config.temporal_depth_rel_ratio)
        or config.temporal_depth_rel_ratio < 0
    ):
        raise ValueError("temporal_depth_rel_ratio must be finite and non-negative")
    if config.depth_confidence_threshold is not None and not np.isfinite(
        float(config.depth_confidence_threshold)
    ):
        raise ValueError("depth_confidence_threshold must be finite")
