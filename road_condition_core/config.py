from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from math import isfinite
from typing import Any, Mapping


@dataclass(frozen=True)
class SurfaceConfig:
    """Parameters for road-coordinate projection and surface rasterization."""

    grid_size_m: float = 0.10
    corridor_half_width_m: float = 3.50
    min_points_per_cell: int = 3
    ground_histogram_bin_m: float = 0.02
    ground_lower_quantile: float = 0.75
    candidate_local_up_min_m: float = -4.0
    candidate_local_up_max_m: float = 0.25
    reference_tile_length_m: float = 12.0
    reference_tile_overlap_m: float = 4.0
    reference_min_cells: int = 120
    reference_robust_iterations: int = 4
    reference_mad_sigma: float = 2.8
    reference_min_residual_gate_m: float = 0.025
    # Experimental geometry plausibility guard applied after the robust reference
    # surface fit. It rejects non-road cells from detectors without deleting raw
    # points or changing the mapping bundle.
    plausibility_residual_min_m: float = -0.30
    plausibility_residual_max_m: float = 0.25
    max_input_points: int = 2_000_000
    preview_max_along_cells: int = 420
    preview_max_cross_cells: int = 140

    def validate(self) -> None:
        positive = {
            "grid_size_m": self.grid_size_m,
            "corridor_half_width_m": self.corridor_half_width_m,
            "ground_histogram_bin_m": self.ground_histogram_bin_m,
            "reference_tile_length_m": self.reference_tile_length_m,
            "reference_mad_sigma": self.reference_mad_sigma,
            "reference_min_residual_gate_m": self.reference_min_residual_gate_m,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.min_points_per_cell < 1:
            raise ValueError("min_points_per_cell must be at least 1")
        if not 0.0 < self.ground_lower_quantile <= 1.0:
            raise ValueError("ground_lower_quantile must be in (0, 1]")
        if self.candidate_local_up_min_m >= self.candidate_local_up_max_m:
            raise ValueError("candidate local-up range is invalid")
        if not 0.0 <= self.reference_tile_overlap_m < self.reference_tile_length_m:
            raise ValueError("reference_tile_overlap_m must be in [0, tile_length)")
        if self.reference_min_cells < 6:
            raise ValueError("reference_min_cells must be at least 6")
        if self.reference_robust_iterations < 1:
            raise ValueError("reference_robust_iterations must be at least 1")
        if not self.plausibility_residual_min_m < 0.0:
            raise ValueError("plausibility_residual_min_m must be negative")
        if not self.plausibility_residual_max_m > 0.0:
            raise ValueError("plausibility_residual_max_m must be positive")
        if self.plausibility_residual_min_m >= self.plausibility_residual_max_m:
            raise ValueError("plausibility residual range is invalid")
        if self.max_input_points < 10_000:
            raise ValueError("max_input_points must be at least 10,000")


@dataclass(frozen=True)
class DetectionConfig:
    """Geometry-detector thresholds.

    These defaults are deliberately conservative for the synthetic demo. They are
    not a legal or engineering standard and must be calibrated against a surveyed
    holdout route before production use.
    """

    pothole_min_depth_m: float = 0.035
    pothole_min_area_m2: float = 0.035
    pothole_close_radius_cells: int = 1
    bump_min_height_m: float = 0.045
    bump_min_area_m2: float = 0.040
    rut_wheel_offset_m: float = 0.90
    rut_band_half_width_m: float = 0.25
    rut_min_depth_m: float = 0.020
    rut_min_length_m: float = 1.00
    rut_gap_tolerance_m: float = 0.40
    segment_length_m: float = 20.0
    low_confidence_position_std_m: float = 0.050
    minimum_valid_coverage_ratio: float = 0.50

    def validate(self) -> None:
        positive = {
            "pothole_min_depth_m": self.pothole_min_depth_m,
            "pothole_min_area_m2": self.pothole_min_area_m2,
            "bump_min_height_m": self.bump_min_height_m,
            "bump_min_area_m2": self.bump_min_area_m2,
            "rut_wheel_offset_m": self.rut_wheel_offset_m,
            "rut_band_half_width_m": self.rut_band_half_width_m,
            "rut_min_depth_m": self.rut_min_depth_m,
            "rut_min_length_m": self.rut_min_length_m,
            "segment_length_m": self.segment_length_m,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.pothole_close_radius_cells < 0:
            raise ValueError("pothole_close_radius_cells must be non-negative")
        if self.rut_gap_tolerance_m < 0:
            raise ValueError("rut_gap_tolerance_m must be non-negative")
        if not 0.0 <= self.minimum_valid_coverage_ratio <= 1.0:
            raise ValueError("minimum_valid_coverage_ratio must be in [0, 1]")


@dataclass(frozen=True)
class ScoreConfig:
    pothole_weight: float = 0.45
    rutting_weight: float = 0.30
    bump_weight: float = 0.10
    roughness_weight: float = 0.15
    high_pothole_depth_m: float = 0.080
    high_rut_depth_m: float = 0.040
    roughness_reference_m: float = 0.025

    def validate(self) -> None:
        weights = [
            self.pothole_weight,
            self.rutting_weight,
            self.bump_weight,
            self.roughness_weight,
        ]
        if any(weight < 0 for weight in weights):
            raise ValueError("score weights must be non-negative")
        if sum(weights) <= 0:
            raise ValueError("at least one score weight must be positive")
        if self.high_pothole_depth_m <= 0 or self.high_rut_depth_m <= 0:
            raise ValueError("score depth references must be positive")
        if self.roughness_reference_m <= 0:
            raise ValueError("roughness_reference_m must be positive")


@dataclass(frozen=True)
class PoseConfig:
    """Optional frame-pose use; PLY-only remains the compatibility default."""

    frame_reprojection_enabled: bool = False
    minimum_quality_score: float = 0.50

    def validate(self) -> None:
        if not isinstance(self.frame_reprojection_enabled, bool):
            raise ValueError("frame_reprojection_enabled must be a boolean")
        if not 0.0 <= self.minimum_quality_score <= 1.0:
            raise ValueError("minimum_quality_score must be in [0, 1]")


@dataclass(frozen=True)
class AdvancedGeometryConfig:
    """Opt-in screening detectors that are not calibrated engineering products."""

    step_manhole_enabled: bool = False
    crossfall_enabled: bool = False
    longitudinal_enabled: bool = False
    ponding_screening_enabled: bool = False
    step_min_height_m: float = 0.015
    step_min_edge_length_m: float = 0.30
    step_min_gradient_percent: float = 8.0
    ponding_min_depth_m: float = 0.010
    ponding_min_area_m2: float = 0.050

    def validate(self) -> None:
        for name in (
            "step_manhole_enabled",
            "crossfall_enabled",
            "longitudinal_enabled",
            "ponding_screening_enabled",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        positive = {
            "step_min_height_m": self.step_min_height_m,
            "step_min_edge_length_m": self.step_min_edge_length_m,
            "step_min_gradient_percent": self.step_min_gradient_percent,
            "ponding_min_depth_m": self.ponding_min_depth_m,
            "ponding_min_area_m2": self.ponding_min_area_m2,
        }
        for name, value in positive.items():
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class AnalysisConfig:
    format_version: int = 1
    surface: SurfaceConfig = field(default_factory=SurfaceConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    score: ScoreConfig = field(default_factory=ScoreConfig)
    pose: PoseConfig = field(default_factory=PoseConfig)
    advanced_geometry: AdvancedGeometryConfig = field(
        default_factory=AdvancedGeometryConfig
    )

    def validate(self) -> None:
        if self.format_version != 1:
            raise ValueError("only analysis config format_version=1 is supported")
        self.surface.validate()
        self.detection.validate()
        self.score.validate()
        self.pose.validate()
        self.advanced_geometry.validate()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, Any] | None) -> "AnalysisConfig":
        """Create a validated config by applying nested override dictionaries."""

        base = cls()
        if not overrides:
            base.validate()
            return base
        allowed_top = {
            "format_version",
            "surface",
            "detection",
            "score",
            "pose",
            "advanced_geometry",
        }
        unknown_top = set(overrides) - allowed_top
        if unknown_top:
            raise ValueError(f"unknown config sections: {', '.join(sorted(unknown_top))}")

        def apply_section(instance: Any, values: Mapping[str, Any] | None, name: str) -> Any:
            if values is None:
                return instance
            if not isinstance(values, Mapping):
                raise ValueError(f"config section {name!r} must be an object")
            allowed = set(instance.__dataclass_fields__)
            unknown = set(values) - allowed
            if unknown:
                raise ValueError(
                    f"unknown {name} config keys: {', '.join(sorted(unknown))}"
                )
            return replace(instance, **dict(values))

        result = replace(
            base,
            format_version=int(overrides.get("format_version", base.format_version)),
            surface=apply_section(base.surface, overrides.get("surface"), "surface"),
            detection=apply_section(
                base.detection, overrides.get("detection"), "detection"
            ),
            score=apply_section(base.score, overrides.get("score"), "score"),
            pose=apply_section(base.pose, overrides.get("pose"), "pose"),
            advanced_geometry=apply_section(
                base.advanced_geometry,
                overrides.get("advanced_geometry"),
                "advanced_geometry",
            ),
        )
        result.validate()
        return result
