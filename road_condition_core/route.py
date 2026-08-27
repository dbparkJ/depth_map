from __future__ import annotations

import hashlib
import json
import math
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial import ConvexHull, QhullError

from .config import AnalysisConfig
from .detectors import detect_rutting
from .geometry import project_to_trajectory
from .models import AnalysisProducts, Defect, SegmentMetric
from .pipeline import _segment_metrics, analyze_points, write_analysis_products
from .roi import RoadRoi


@dataclass(frozen=True)
class RouteConfig:
    core_tile_length_m: float = 10.0
    halo_m: float = 3.0
    report_segment_length_m: float = 20.0
    merge_chainage_tolerance_m: float = 1.0
    merge_lateral_tolerance_m: float = 0.75
    merge_polygon_distance_m: float = 0.75
    merge_metric_relative_tolerance: float = 0.50

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if not np.isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        if self.halo_m >= self.core_tile_length_m:
            raise ValueError("halo_m must be smaller than core_tile_length_m")
        ratio = self.report_segment_length_m / self.core_tile_length_m
        if not np.isclose(ratio, round(ratio), atol=1e-9):
            raise ValueError("report segment length must be a multiple of core tile length")


@dataclass(frozen=True)
class TileWindow:
    tile_id: str
    core_start_m: float
    core_end_m: float
    halo_start_m: float
    halo_end_m: float
    is_last: bool


@dataclass(frozen=True)
class RouteChunkInput:
    chunk_id: str
    route_result_dir: Path
    chainage_offset_m: float


def plan_tiles(
    chainage_start_m: float,
    chainage_end_m: float,
    config: RouteConfig | None = None,
) -> list[TileWindow]:
    resolved = config or RouteConfig()
    resolved.validate()
    start = float(chainage_start_m)
    end = float(chainage_end_m)
    if not np.isfinite(start) or not np.isfinite(end) or end <= start:
        raise ValueError("route chainage range must be finite and increasing")
    first = math.floor(start / resolved.core_tile_length_m) * resolved.core_tile_length_m
    windows: list[TileWindow] = []
    core_start = first
    index = 0
    while core_start < end - 1e-9:
        core_end = min(core_start + resolved.core_tile_length_m, end)
        windows.append(
            TileWindow(
                tile_id=f"tile-{index:06d}",
                core_start_m=float(core_start),
                core_end_m=float(core_end),
                halo_start_m=max(start, float(core_start - resolved.halo_m)),
                halo_end_m=min(end, float(core_end + resolved.halo_m)),
                is_last=core_end >= end - 1e-9,
            )
        )
        core_start += resolved.core_tile_length_m
        index += 1
    return windows


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _tile_signature(
    points: np.ndarray,
    trajectory: np.ndarray,
    analysis_config: AnalysisConfig,
    route_config: RouteConfig,
    point_metadata: Mapping[str, np.ndarray] | None,
    road_roi: RoadRoi | None,
    pose_context: Mapping[str, np.ndarray] | None,
    source: Mapping[str, Any] | None,
    quality_context: Mapping[str, Any] | None,
) -> str:
    sample_indices = np.linspace(
        0, len(points) - 1, num=min(len(points), 4096), dtype=np.int64
    )
    point_sample_hash = hashlib.sha256(
        np.ascontiguousarray(points[sample_indices]).view(np.uint8)
    ).hexdigest()
    metadata_hashes = {}
    for name, values in sorted((point_metadata or {}).items()):
        array = np.asarray(values)
        if array.shape == (len(points),) and not array.dtype.hasobject:
            metadata_hashes[name] = hashlib.sha256(
                np.ascontiguousarray(array[sample_indices]).view(np.uint8)
            ).hexdigest()
    roi_payload = None
    if road_roi is not None:
        roi_payload = [
            {
                "zone_id": zone.zone_id,
                "zone_type": zone.zone_type,
                "lane_id": zone.lane_id,
                "source": zone.source,
                "confidence": zone.confidence,
                "chainage_start_m": zone.chainage_start_m,
                "chainage_end_m": zone.chainage_end_m,
                "polygons": [ring.tolist() for ring in zone.polygons],
            }
            for zone in road_roi.zones
        ]
    pose_hashes = {}
    for name, values in sorted((pose_context or {}).items()):
        array = np.asarray(values)
        if not array.dtype.hasobject:
            pose_hashes[name] = hashlib.sha256(
                np.ascontiguousarray(array).view(np.uint8)
            ).hexdigest()
    payload = {
        "point_count": int(len(points)),
        "point_bbox_min": np.min(points, axis=0).astype(float).tolist(),
        "point_bbox_max": np.max(points, axis=0).astype(float).tolist(),
        "trajectory_count": int(len(trajectory)),
        "trajectory_first": np.asarray(trajectory[0], dtype=float).tolist(),
        "trajectory_last": np.asarray(trajectory[-1], dtype=float).tolist(),
        "trajectory_hash": hashlib.sha256(
            np.ascontiguousarray(trajectory).view(np.uint8)
        ).hexdigest(),
        "point_sample_hash": point_sample_hash,
        "metadata_sample_hashes": metadata_hashes,
        "road_roi": roi_payload,
        "pose_hashes": pose_hashes,
        "source": dict(source or {}),
        "quality_context": dict(quality_context or {}),
        "analysis_config": analysis_config.to_dict(),
        "route_config": asdict(route_config),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _owns_chainage(value: float, window: TileWindow) -> bool:
    if value < window.core_start_m - 1e-9:
        return False
    if window.is_last:
        return value <= window.core_end_m + 1e-9
    return value < window.core_end_m - 1e-9


def _core_segments(
    products: AnalysisProducts,
    owned_defects: list[Defect],
    analysis_config: AnalysisConfig,
    window: TileWindow,
) -> list[SegmentMetric]:
    grid = products.surface
    rows = (grid.s_values_m >= window.core_start_m) & (
        grid.s_values_m < window.core_end_m + (1e-9 if window.is_last else 0.0)
    )
    core_mask = np.broadcast_to(rows[:, None], grid.valid_mask.shape).copy()
    _ruts, rut_series = detect_rutting(grid, analysis_config.detection)
    core_detection = replace(
        analysis_config.detection,
        segment_length_m=window.core_end_m - window.core_start_m,
    )
    core_config = replace(analysis_config, detection=core_detection)
    segments = _segment_metrics(
        grid,
        owned_defects,
        rut_series,
        core_config,
        cell_mask=core_mask,
        road_zone=("road" if grid.roi_zone_code is not None else "corridor_fallback"),
    )
    if grid.roi_lane_index is not None:
        for lane_index, lane_id in enumerate(grid.roi_lane_ids, start=1):
            segments.extend(
                _segment_metrics(
                    grid,
                    owned_defects,
                    rut_series,
                    core_config,
                    cell_mask=core_mask & (grid.roi_lane_index == lane_index),
                    lane_id=lane_id,
                    road_zone="lane",
                )
            )
    return segments


def _owned_products(
    products: AnalysisProducts,
    window: TileWindow,
    analysis_config: AnalysisConfig,
) -> AnalysisProducts:
    defects = [
        item
        for item in products.defects
        if _owns_chainage(item.chainage_m, window)
    ]
    segments = _core_segments(products, defects, analysis_config, window)
    summary = deepcopy(products.summary)
    potholes = [item for item in defects if item.defect_type == "pothole"]
    ruts = [item for item in defects if item.defect_type == "rutting"]
    bumps = [item for item in defects if item.defect_type == "bump"]
    advanced = [item for item in defects if item.source == "geometry_screening"]
    summary["tile"] = {
        **asdict(window),
        "ownership": "defect centroid in core; halo used for fitting and detection",
    }
    summary["coverage"]["chainage_start_m"] = window.core_start_m
    summary["coverage"]["chainage_end_m"] = window.core_end_m
    summary["results"].update(
        {
            "defect_count": len(defects),
            "pothole_count": len(potholes),
            "pothole_area_m2": float(
                sum(item.metrics.get("area_m2", 0.0) for item in potholes)
            ),
            "pothole_volume_m3": float(
                sum(item.metrics.get("volume_m3", 0.0) for item in potholes)
            ),
            "max_pothole_depth_m": float(
                max(
                    (item.metrics.get("max_depth_m", 0.0) for item in potholes),
                    default=0.0,
                )
            ),
            "rutting_count": len(ruts),
            "max_rut_depth_m": float(
                max(
                    (item.metrics.get("max_depth_m", 0.0) for item in ruts),
                    default=0.0,
                )
            ),
            "bump_count": len(bumps),
            "max_bump_height_m": float(
                max(
                    (item.metrics.get("max_height_m", 0.0) for item in bumps),
                    default=0.0,
                )
            ),
            "advanced_geometry_candidate_count": len(advanced),
        }
    )
    detectors = summary.get("advanced_geometry", {}).get("detectors", {})
    if detectors.get("step_manhole", {}).get("state") == "completed":
        detectors["step_manhole"]["candidate_count"] = sum(
            item.defect_type in {"manhole_step_candidate", "step_anomaly"}
            for item in advanced
        )
    if detectors.get("ponding_screening", {}).get("state") == "completed":
        detectors["ponding_screening"]["candidate_count"] = sum(
            item.defect_type == "ponding_screening_proxy" for item in advanced
        )
    summary["limitations"].append(
        "Tile result owns only defects whose centroid is in the core; surface preview includes halo."
    )
    if any(
        detectors.get(name, {}).get("state") == "completed"
        for name in ("crossfall", "longitudinal")
    ):
        summary["limitations"].append(
            "Tile slope profiles include the halo surface and are contextual, not core report metrics."
        )
    return AnalysisProducts(summary=summary, defects=defects, segments=segments, surface=products.surface)


def _primary_metric(record: Mapping[str, Any]) -> float:
    metrics = record.get("metrics") or {}
    for name in (
        "max_depth_m",
        "max_height_m",
        "step_height_m",
        "potential_retention_depth_m",
        "area_m2",
        "length_m",
    ):
        if name in metrics:
            return abs(float(metrics[name]))
    return 0.0


def _polygon_distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    a = np.asarray(left.get("local_polygon_st_m") or [], dtype=np.float64)
    b = np.asarray(right.get("local_polygon_st_m") or [], dtype=np.float64)
    if a.ndim != 2 or b.ndim != 2 or a.shape[1:] != (2,) or b.shape[1:] != (2,):
        return math.inf
    delta = a[:, None, :] - b[None, :, :]
    return float(np.min(np.linalg.norm(delta, axis=2)))


def _records_match(left: Mapping[str, Any], right: Mapping[str, Any], config: RouteConfig) -> bool:
    if left.get("defect_type") != right.get("defect_type"):
        return False
    if abs(float(left.get("chainage_m", 0.0)) - float(right.get("chainage_m", 0.0))) > config.merge_chainage_tolerance_m:
        return False
    if abs(float(left.get("lateral_offset_m", 0.0)) - float(right.get("lateral_offset_m", 0.0))) > config.merge_lateral_tolerance_m:
        return False
    if _polygon_distance(left, right) > config.merge_polygon_distance_m:
        return False
    a = _primary_metric(left)
    b = _primary_metric(right)
    scale = max(a, b, 1e-6)
    return abs(a - b) / scale <= config.merge_metric_relative_tolerance


def _merged_polygon(records: Sequence[Mapping[str, Any]]) -> list[list[float]]:
    points = np.concatenate(
        [np.asarray(item.get("local_polygon_st_m"), dtype=np.float64) for item in records],
        axis=0,
    )
    if len(points) >= 3:
        try:
            return np.round(points[ConvexHull(points).vertices], 4).tolist()
        except QhullError:
            pass
    return np.round(points, 4).tolist()


def merge_defect_records(
    records: Sequence[Mapping[str, Any]],
    config: RouteConfig | None = None,
) -> list[dict[str, Any]]:
    resolved = config or RouteConfig()
    resolved.validate()
    ordered = [deepcopy(dict(item)) for item in records]
    ordered.sort(
        key=lambda item: (
            str(item.get("defect_type")),
            float(item.get("chainage_m", 0.0)),
            float(item.get("lateral_offset_m", 0.0)),
            str(item.get("defect_id", "")),
        )
    )
    parent = list(range(len(ordered)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for left in range(len(ordered)):
        for right in range(left + 1, len(ordered)):
            if ordered[right].get("defect_type") != ordered[left].get("defect_type"):
                break
            if float(ordered[right].get("chainage_m", 0.0)) - float(ordered[left].get("chainage_m", 0.0)) > resolved.merge_chainage_tolerance_m:
                break
            if _records_match(ordered[left], ordered[right], resolved):
                union(left, right)

    groups: dict[int, list[dict[str, Any]]] = {}
    for index, item in enumerate(ordered):
        groups.setdefault(find(index), []).append(item)
    result: list[dict[str, Any]] = []
    for group in groups.values():
        best = max(group, key=lambda item: (float(item.get("confidence", 0.0)), str(item.get("defect_id", ""))))
        merged = deepcopy(best)
        weights = np.asarray([max(float(item.get("confidence", 0.0)), 0.05) for item in group])
        merged["chainage_m"] = float(np.average([float(item.get("chainage_m", 0.0)) for item in group], weights=weights))
        merged["lateral_offset_m"] = float(np.average([float(item.get("lateral_offset_m", 0.0)) for item in group], weights=weights))
        merged["local_polygon_st_m"] = _merged_polygon(group)
        merged["merged_from"] = sorted(
            {
                source
                for item in group
                for source in item.get("merged_from", [str(item.get("defect_id", ""))])
                if source
            }
        )
        result.append(merged)
    result.sort(key=lambda item: (float(item["chainage_m"]), str(item["defect_type"]), float(item["lateral_offset_m"])))
    for index, item in enumerate(result, start=1):
        item["defect_id"] = f"route-defect-{index:06d}"
    return result


def _grade(score: float) -> str:
    return "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "E"


def aggregate_core_segments(
    records: Sequence[Mapping[str, Any]],
    report_length_m: float = 20.0,
) -> list[dict[str, Any]]:
    unique: dict[tuple[float, str], dict[str, Any]] = {}
    for record in records:
        start = float(record["chainage_start_m"])
        lane = str(record.get("lane_id") or "")
        key = (round(start, 6), lane)
        candidate = dict(record)
        current = unique.get(key)
        if current is None or (
            float(candidate.get("valid_coverage_ratio", 0.0)), str(candidate.get("segment_id", ""))
        ) > (
            float(current.get("valid_coverage_ratio", 0.0)), str(current.get("segment_id", ""))
        ):
            unique[key] = candidate
    groups: dict[tuple[float, str], list[dict[str, Any]]] = {}
    for record in unique.values():
        start = math.floor(float(record["chainage_start_m"]) / report_length_m) * report_length_m
        lane = str(record.get("lane_id") or "")
        groups.setdefault((start, lane), []).append(record)
    output: list[dict[str, Any]] = []
    for (start, lane), group in sorted(groups.items()):
        score = float(np.mean([float(item.get("geometry_score", 0.0)) for item in group]))
        roughness = float(np.sqrt(np.mean([float(item.get("roughness_proxy_m", 0.0)) ** 2 for item in group])))
        output.append(
            {
                "segment_id": f"route-{lane or 'all'}-{int(round(start)):08d}",
                "chainage_start_m": start,
                "chainage_end_m": max(float(item["chainage_end_m"]) for item in group),
                "valid_coverage_ratio": float(np.mean([float(item.get("valid_coverage_ratio", 0.0)) for item in group])),
                "pothole_count": int(sum(int(item.get("pothole_count", 0)) for item in group)),
                "pothole_area_m2": float(sum(float(item.get("pothole_area_m2", 0.0)) for item in group)),
                "pothole_volume_m3": float(sum(float(item.get("pothole_volume_m3", 0.0)) for item in group)),
                "max_pothole_depth_m": max(float(item.get("max_pothole_depth_m", 0.0)) for item in group),
                "max_left_rut_depth_m": max(float(item.get("max_left_rut_depth_m", 0.0)) for item in group),
                "max_right_rut_depth_m": max(float(item.get("max_right_rut_depth_m", 0.0)) for item in group),
                "bump_count": int(sum(int(item.get("bump_count", 0)) for item in group)),
                "roughness_proxy_m": roughness,
                "geometry_score": score,
                "grade": _grade(score),
                "lane_id": lane or None,
                "road_zone": str(group[0].get("road_zone", "corridor_fallback")),
                "core_segment_count": len(group),
            }
        )
    return output


def _write_parquet(path: Path, records: Sequence[Mapping[str, Any]], *, defects: bool) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment-specific guard
        raise RuntimeError("route Parquet output requires the 'route' optional extra") from exc
    rows = []
    for record in records:
        row = deepcopy(dict(record))
        if defects:
            row["metrics_json"] = json.dumps(row.pop("metrics", {}), sort_keys=True)
            row["local_polygon_st_json"] = json.dumps(row.pop("local_polygon_st_m", []))
            row["quality_flags"] = list(row.get("quality_flags") or [])
            row["merged_from"] = list(row.get("merged_from") or [])
        rows.append(row)
    if rows:
        table = pa.Table.from_pylist(rows)
    elif defects:
        table = pa.table(
            {
                "defect_id": pa.array([], type=pa.string()),
                "defect_type": pa.array([], type=pa.string()),
                "chainage_m": pa.array([], type=pa.float64()),
                "lateral_offset_m": pa.array([], type=pa.float64()),
                "metrics_json": pa.array([], type=pa.string()),
                "local_polygon_st_json": pa.array([], type=pa.string()),
                "merged_from": pa.array([], type=pa.list_(pa.string())),
            }
        )
    else:
        table = pa.table(
            {
                "segment_id": pa.array([], type=pa.string()),
                "chainage_start_m": pa.array([], type=pa.float64()),
                "chainage_end_m": pa.array([], type=pa.float64()),
            }
        )
    temporary = path.with_name(path.name + ".tmp")
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(path)


def aggregate_route_tiles(
    output_dir: str | Path,
    route_config: RouteConfig | None = None,
    *,
    run_stats: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    output = Path(output_dir).expanduser().resolve()
    resolved = route_config or RouteConfig()
    resolved.validate()
    statuses = []
    defect_records: list[dict[str, Any]] = []
    segment_records: list[dict[str, Any]] = []
    for status_path in sorted((output / "tiles").glob("*/status.json")):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        statuses.append(status)
        if status.get("state") != "completed":
            continue
        result_dir = status_path.parent / "result"
        defect_records.extend(json.loads((result_dir / "defects.json").read_text(encoding="utf-8")))
        segment_records.extend(json.loads((result_dir / "segments.json").read_text(encoding="utf-8")))
    merged = merge_defect_records(defect_records, resolved)
    segments = aggregate_core_segments(segment_records, resolved.report_segment_length_m)
    geojson = {
        "type": "FeatureCollection",
        "format_version": 1,
        "coordinate_system": "local_road_ST_metres",
        "features": [],
    }
    for item in merged:
        ring = [list(point) for point in item.get("local_polygon_st_m", [])]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        properties = deepcopy(item)
        properties.pop("local_polygon_st_m", None)
        geojson["features"].append(
            {
                "type": "Feature",
                "id": item["defect_id"],
                "properties": properties,
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    _atomic_json(output / "route_defects.geojson", geojson)
    _write_parquet(output / "route_defects.parquet", merged, defects=True)
    _write_parquet(output / "route_segments.parquet", segments, defects=False)
    completed = sum(status.get("state") == "completed" for status in statuses)
    failed = sum(status.get("state") == "failed" for status in statuses)
    manifest = {
        "format_version": 1,
        "route_config": asdict(resolved),
        "state": "completed" if statuses and failed == 0 else "partial",
        "tile_count": len(statuses),
        "completed_tile_count": completed,
        "failed_tile_count": failed,
        "defect_count": len(merged),
        "segment_count": len(segments),
        "tiles": statuses,
        "run_stats": dict(run_stats or {}),
        "outputs": {
            "defects_geojson": "route_defects.geojson",
            "defects_parquet": "route_defects.parquet",
            "segments_parquet": "route_segments.parquet",
        },
    }
    _atomic_json(output / "route_manifest.json", manifest)
    return manifest


def aggregate_chunk_routes(
    chunks: Sequence[RouteChunkInput],
    output_dir: str | Path,
    route_config: RouteConfig | None = None,
) -> dict[str, Any]:
    """Merge independently processed chunk routes without loading their point clouds."""

    resolved = route_config or RouteConfig()
    resolved.validate()
    if not chunks:
        raise ValueError("at least one route chunk is required")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("chunk route aggregation requires the 'route' optional extra") from exc
    defect_records: list[dict[str, Any]] = []
    segment_records: list[dict[str, Any]] = []
    chunk_manifests = []
    seen: set[str] = set()
    for chunk in sorted(chunks, key=lambda item: item.chunk_id):
        if not chunk.chunk_id or chunk.chunk_id in seen:
            raise ValueError("route chunk IDs must be non-empty and unique")
        if not np.isfinite(chunk.chainage_offset_m):
            raise ValueError("route chunk chainage offsets must be finite")
        root = Path(chunk.route_result_dir).expanduser().resolve()
        manifest = json.loads((root / "route_manifest.json").read_text(encoding="utf-8"))
        geojson = json.loads((root / "route_defects.geojson").read_text(encoding="utf-8"))
        for feature in geojson.get("features", []):
            properties = deepcopy(feature.get("properties") or {})
            ring = feature.get("geometry", {}).get("coordinates", [[]])[0]
            if ring and ring[0] == ring[-1]:
                ring = ring[:-1]
            properties["chainage_m"] = float(properties.get("chainage_m", 0.0)) + chunk.chainage_offset_m
            properties["local_polygon_st_m"] = [
                [float(point[0]) + chunk.chainage_offset_m, float(point[1])]
                for point in ring
            ]
            original_id = str(properties.get("defect_id") or feature.get("id") or "")
            properties["defect_id"] = f"{chunk.chunk_id}:{original_id}"
            properties["merged_from"] = [properties["defect_id"]]
            properties["source_chunk_id"] = chunk.chunk_id
            defect_records.append(properties)
        for segment in pq.read_table(root / "route_segments.parquet").to_pylist():
            item = dict(segment)
            item["segment_id"] = f"{chunk.chunk_id}:{item.get('segment_id', '')}"
            item["chainage_start_m"] = float(item["chainage_start_m"]) + chunk.chainage_offset_m
            item["chainage_end_m"] = float(item["chainage_end_m"]) + chunk.chainage_offset_m
            item["source_chunk_id"] = chunk.chunk_id
            segment_records.append(item)
        chunk_manifests.append(
            {
                "chunk_id": chunk.chunk_id,
                "route_result_dir": str(root),
                "chainage_offset_m": float(chunk.chainage_offset_m),
                "state": manifest.get("state"),
                "tile_count": manifest.get("tile_count"),
            }
        )
        seen.add(chunk.chunk_id)
    merged = merge_defect_records(defect_records, resolved)
    segments = aggregate_core_segments(segment_records, resolved.report_segment_length_m)
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    geojson = {
        "type": "FeatureCollection",
        "format_version": 1,
        "coordinate_system": "local_road_ST_metres",
        "features": [],
    }
    for item in merged:
        ring = [list(point) for point in item.get("local_polygon_st_m", [])]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        properties = deepcopy(item)
        properties.pop("local_polygon_st_m", None)
        geojson["features"].append(
            {
                "type": "Feature",
                "id": item["defect_id"],
                "properties": properties,
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    _atomic_json(output / "route_defects.geojson", geojson)
    _write_parquet(output / "route_defects.parquet", merged, defects=True)
    _write_parquet(output / "route_segments.parquet", segments, defects=False)
    manifest = {
        "format_version": 1,
        "route_config": asdict(resolved),
        "state": (
            "completed"
            if all(item.get("state") == "completed" for item in chunk_manifests)
            else "partial"
        ),
        "chunk_count": len(chunk_manifests),
        "chunks": chunk_manifests,
        "defect_count": len(merged),
        "segment_count": len(segments),
        "outputs": {
            "defects_geojson": "route_defects.geojson",
            "defects_parquet": "route_defects.parquet",
            "segments_parquet": "route_segments.parquet",
        },
    }
    _atomic_json(output / "route_manifest.json", manifest)
    return manifest


def run_tiled_analysis(
    points_enu_m: np.ndarray,
    colors_rgb: np.ndarray,
    trajectory_enu_m: np.ndarray,
    output_dir: str | Path,
    *,
    analysis_config: AnalysisConfig | None = None,
    route_config: RouteConfig | None = None,
    point_metadata: Mapping[str, np.ndarray] | None = None,
    source: Mapping[str, Any] | None = None,
    source_origin: dict[str, float] | None = None,
    road_roi: RoadRoi | None = None,
    pose_context: Mapping[str, np.ndarray] | None = None,
    quality_context: Mapping[str, Any] | None = None,
    tile_order: Sequence[int] | None = None,
) -> dict[str, Any]:
    resolved_analysis = analysis_config or AnalysisConfig()
    resolved_analysis.validate()
    resolved_route = route_config or RouteConfig()
    resolved_route.validate()
    points = np.asarray(points_enu_m)
    colors = np.asarray(colors_rgb)
    trajectory = np.asarray(trajectory_enu_m, dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (3,) or colors.shape != points.shape:
        raise ValueError("route points/colors must have aligned shape (N, 3)")
    coordinates = project_to_trajectory(points, trajectory)
    finite = np.isfinite(coordinates.along_track_m)
    if np.count_nonzero(finite) < resolved_analysis.surface.reference_min_cells:
        raise ValueError("route input has too few projected points")
    start = float(np.min(coordinates.along_track_m[finite]))
    end = float(np.max(coordinates.along_track_m[finite]))
    windows = plan_tiles(start, end, resolved_route)
    order = list(range(len(windows))) if tile_order is None else list(tile_order)
    if sorted(order) != list(range(len(windows))):
        raise ValueError("tile_order must contain every tile index exactly once")
    output = Path(output_dir).expanduser().resolve()
    signature = _tile_signature(
        points,
        trajectory,
        resolved_analysis,
        resolved_route,
        point_metadata,
        road_roi,
        pose_context,
        source,
        quality_context,
    )
    completed = 0
    skipped = 0
    failed = 0
    metadata = dict(point_metadata or {})
    for window_index in order:
        window = windows[window_index]
        tile_dir = output / "tiles" / window.tile_id
        status_path = tile_dir / "status.json"
        if status_path.is_file():
            previous = json.loads(status_path.read_text(encoding="utf-8"))
            if (
                previous.get("state") == "completed"
                and previous.get("input_signature") == signature
                and (tile_dir / "result" / "summary.json").is_file()
            ):
                skipped += 1
                continue
        selected = finite & (coordinates.along_track_m >= window.halo_start_m) & (
            coordinates.along_track_m <= window.halo_end_m
        )
        status = {
            "format_version": 1,
            **asdict(window),
            "input_signature": signature,
            "state": "running",
            "input_point_count": int(np.count_nonzero(selected)),
        }
        _atomic_json(status_path, status)
        try:
            tile_metadata = {
                name: np.asarray(values)[selected]
                for name, values in metadata.items()
                if np.asarray(values).shape == (len(points),)
            }
            products = analyze_points(
                points[selected],
                colors[selected],
                trajectory,
                config=resolved_analysis,
                point_metadata=tile_metadata,
                source={**dict(source or {}), "tile_id": window.tile_id},
                source_origin=source_origin,
                pose_context=pose_context,
                quality_context=quality_context,
                road_roi=road_roi,
            )
            owned = _owned_products(products, window, resolved_analysis)
            result_dir = tile_dir / "result"
            artifacts = write_analysis_products(result_dir, owned)
            _write_parquet(
                result_dir / "defects.parquet",
                [item.to_dict() for item in owned.defects],
                defects=True,
            )
            _write_parquet(
                result_dir / "segments.parquet",
                [item.to_dict() for item in owned.segments],
                defects=False,
            )
            artifacts.update(
                {
                    "defects_parquet": "defects.parquet",
                    "segments_parquet": "segments.parquet",
                }
            )
            status.update(
                {
                    "state": "completed",
                    "owned_defect_count": len(owned.defects),
                    "core_segment_count": len(owned.segments),
                    "artifacts": artifacts,
                }
            )
            completed += 1
        except Exception as exc:  # noqa: BLE001 - route keeps independent tile failures
            status.update(
                {
                    "state": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            failed += 1
        _atomic_json(status_path, status)
    return aggregate_route_tiles(
        output,
        resolved_route,
        run_stats={
            "executed_completed_tile_count": completed,
            "skipped_completed_tile_count": skipped,
            "executed_failed_tile_count": failed,
            "input_point_count": int(len(points)),
        },
    )
