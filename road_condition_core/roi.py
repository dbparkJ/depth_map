from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


ZONE_TYPE_CODES = {"unknown": 0, "road": 1, "lane": 2, "shoulder": 3, "exclusion": 4}
_ZONE_PRECEDENCE = {"road": 1, "shoulder": 2, "lane": 3, "exclusion": 4}


@dataclass(frozen=True)
class RoiZone:
    zone_id: str
    zone_type: str
    lane_id: str | None
    source: str
    confidence: float
    chainage_start_m: float
    chainage_end_m: float
    polygons: tuple[np.ndarray, ...]


@dataclass(frozen=True)
class RoadRoi:
    zones: tuple[RoiZone, ...]
    source_path: Path | None = None

    @property
    def lane_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted({zone.lane_id for zone in self.zones if zone.lane_id is not None})
        )


@dataclass(frozen=True)
class RoiClassification:
    zone_type: np.ndarray
    zone_id: np.ndarray
    lane_id: np.ndarray

    @property
    def included_surface_mask(self) -> np.ndarray:
        return np.isin(self.zone_type, ("road", "lane"))


def _polygon_rings(geometry: Mapping[str, Any]) -> tuple[np.ndarray, ...]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        groups: Sequence[Any] = [coordinates]
    elif geometry_type == "MultiPolygon":
        groups = coordinates
    else:
        raise ValueError("road ROI geometry must be Polygon or MultiPolygon")
    result: list[np.ndarray] = []
    for polygon in groups:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("road ROI polygon coordinates are empty")
        if len(polygon) > 1:
            raise ValueError("road ROI polygon holes are not supported in format_version=1")
        ring = np.asarray(polygon[0], dtype=np.float64)
        if ring.ndim != 2 or ring.shape[1:] != (2,) or len(ring) < 4:
            raise ValueError("road ROI polygon ring must contain at least four ST points")
        if not np.all(np.isfinite(ring)):
            raise ValueError("road ROI polygon coordinates must be finite")
        if not np.allclose(ring[0], ring[-1], atol=1e-9):
            ring = np.vstack((ring, ring[0]))
        result.append(ring)
    return tuple(result)


def parse_road_roi(payload: Mapping[str, Any], *, source_path: Path | None = None) -> RoadRoi:
    if payload.get("type") != "FeatureCollection":
        raise ValueError("road_roi.geojson must be a FeatureCollection")
    if payload.get("format_version") != 1:
        raise ValueError("road ROI format_version must be 1")
    coordinate_system = payload.get("coordinate_system")
    if coordinate_system != "local_road_ST_metres":
        raise ValueError("road ROI coordinate_system must be local_road_ST_metres")
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError("road ROI must contain at least one feature")
    if len(features) > 10_000:
        raise ValueError("road ROI feature count exceeds 10,000")
    zones: list[RoiZone] = []
    seen: set[str] = set()
    coordinate_count = 0
    for feature in features:
        if not isinstance(feature, Mapping) or feature.get("type") != "Feature":
            raise ValueError("road ROI features must be GeoJSON Feature objects")
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, Mapping) or not isinstance(geometry, Mapping):
            raise ValueError("road ROI feature properties and geometry are required")
        zone_id = str(properties.get("zone_id", "")).strip()
        zone_type = str(properties.get("zone_type", "")).strip()
        source = str(properties.get("source", "")).strip()
        if not zone_id or zone_id in seen:
            raise ValueError("road ROI zone_id must be non-empty and unique")
        if zone_type not in {"road", "lane", "shoulder", "exclusion"}:
            raise ValueError(f"road ROI zone {zone_id!r} has invalid zone_type")
        if source not in {"manual", "centerline_buffer", "rgb_ai"}:
            raise ValueError(f"road ROI zone {zone_id!r} has invalid source")
        confidence = float(properties.get("confidence", 0.0))
        if not np.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError(f"road ROI zone {zone_id!r} confidence must be in [0, 1]")
        lane_value = properties.get("lane_id")
        lane_id = str(lane_value).strip() if lane_value is not None else None
        if zone_type == "lane" and not lane_id:
            raise ValueError(f"lane zone {zone_id!r} requires lane_id")
        try:
            chainage_start = float(properties["chainage_start_m"])
            chainage_end = float(properties["chainage_end_m"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"road ROI zone {zone_id!r} requires numeric chainage bounds"
            ) from exc
        if (
            not np.isfinite(chainage_start)
            or not np.isfinite(chainage_end)
            or chainage_start < 0.0
            or chainage_end <= chainage_start
        ):
            raise ValueError(f"road ROI zone {zone_id!r} has invalid chainage bounds")
        polygons = _polygon_rings(geometry)
        coordinate_count += sum(len(ring) for ring in polygons)
        if coordinate_count > 100_000:
            raise ValueError("road ROI coordinate count exceeds 100,000")
        zones.append(
            RoiZone(
                zone_id=zone_id,
                zone_type=zone_type,
                lane_id=lane_id,
                source=source,
                confidence=confidence,
                chainage_start_m=chainage_start,
                chainage_end_m=chainage_end,
                polygons=polygons,
            )
        )
        seen.add(zone_id)
    return RoadRoi(zones=tuple(zones), source_path=source_path)


def load_road_roi(path: str | Path) -> RoadRoi:
    resolved = Path(path).expanduser().resolve()
    if resolved.stat().st_size > 5_000_000:
        raise ValueError("road_roi.geojson exceeds the 5 MB limit")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("road_roi.geojson root must be an object")
    return parse_road_roi(payload, source_path=resolved)


def resolve_roi_path(bundle_root: str | Path, user_path: str) -> Path:
    root = Path(bundle_root).expanduser().resolve()
    raw = Path(user_path)
    if raw.is_absolute():
        raise ValueError("road_roi_path must be relative to the mapping bundle")
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("road_roi_path escapes the mapping bundle") from exc
    return candidate


def _points_in_ring(s: np.ndarray, t: np.ndarray, ring: np.ndarray) -> np.ndarray:
    inside = np.zeros(len(s), dtype=bool)
    x = ring[:, 0]
    y = ring[:, 1]
    for index in range(len(ring) - 1):
        x1, y1 = x[index], y[index]
        x2, y2 = x[index + 1], y[index + 1]
        crosses = (y1 > t) != (y2 > t)
        x_intersection = (x2 - x1) * (t - y1) / ((y2 - y1) or 1e-15) + x1
        inside ^= crosses & (s < x_intersection)
    return inside


def classify_st(s_m: np.ndarray, t_m: np.ndarray, roi: RoadRoi) -> RoiClassification:
    s = np.asarray(s_m, dtype=np.float64).reshape(-1)
    t = np.asarray(t_m, dtype=np.float64).reshape(-1)
    if s.shape != t.shape:
        raise ValueError("s_m and t_m must have the same shape")
    zone_type = np.full(len(s), "unknown", dtype="U10")
    zone_id = np.full(len(s), "", dtype="U96")
    lane_id = np.full(len(s), "", dtype="U64")
    precedence = np.zeros(len(s), dtype=np.uint8)
    for zone in roi.zones:
        selected = np.zeros(len(s), dtype=bool)
        for ring in zone.polygons:
            selected |= _points_in_ring(s, t, ring)
        selected &= (s >= zone.chainage_start_m) & (s <= zone.chainage_end_m)
        priority = _ZONE_PRECEDENCE[zone.zone_type]
        apply = selected & (priority >= precedence)
        zone_type[apply] = zone.zone_type
        zone_id[apply] = zone.zone_id
        lane_id[apply] = zone.lane_id or ""
        precedence[apply] = priority
    return RoiClassification(zone_type=zone_type, zone_id=zone_id, lane_id=lane_id)
