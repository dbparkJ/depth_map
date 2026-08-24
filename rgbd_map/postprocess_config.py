from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np


POSTPROCESS_PRESET_NAMES = ("off", "conservative", "road-map", "aggressive")


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
        )
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
