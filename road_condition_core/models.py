from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SurfaceGrid:
    s_values_m: np.ndarray
    t_values_m: np.ndarray
    observed_local_up_m: np.ndarray
    reference_local_up_m: np.ndarray
    residual_m: np.ndarray
    point_count: np.ndarray
    position_std_m: np.ndarray
    valid_mask: np.ndarray
    trajectory_enu_m: np.ndarray
    trajectory_cumulative_m: np.ndarray
    source_origin: dict[str, float] | None = None
    roi_zone_code: np.ndarray | None = None
    roi_lane_index: np.ndarray | None = None
    roi_lane_ids: tuple[str, ...] = ()

    @property
    def cell_area_m2(self) -> float:
        if len(self.s_values_m) < 2 or len(self.t_values_m) < 2:
            return 0.0
        return float(
            np.median(np.diff(self.s_values_m)) * np.median(np.diff(self.t_values_m))
        )


@dataclass(frozen=True)
class Defect:
    defect_id: str
    defect_type: str
    severity: str
    confidence: float
    chainage_m: float
    lateral_offset_m: float
    local_polygon_st_m: list[list[float]]
    metrics: dict[str, float]
    quality_flags: list[str] = field(default_factory=list)
    source: str = "geometry"
    lane_id: str | None = None
    road_zone: str = "corridor_fallback"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_geojson_feature(self) -> dict[str, Any]:
        ring = [list(point) for point in self.local_polygon_st_m]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        properties = self.to_dict()
        properties.pop("local_polygon_st_m", None)
        return {
            "type": "Feature",
            "id": self.defect_id,
            "properties": properties,
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }


@dataclass(frozen=True)
class SegmentMetric:
    segment_id: str
    chainage_start_m: float
    chainage_end_m: float
    valid_coverage_ratio: float
    pothole_count: int
    pothole_area_m2: float
    pothole_volume_m3: float
    max_pothole_depth_m: float
    max_left_rut_depth_m: float
    max_right_rut_depth_m: float
    bump_count: int
    roughness_proxy_m: float
    geometry_score: float
    grade: str
    lane_id: str | None = None
    road_zone: str = "corridor_fallback"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalysisProducts:
    summary: dict[str, Any]
    defects: list[Defect]
    segments: list[SegmentMetric]
    surface: SurfaceGrid
