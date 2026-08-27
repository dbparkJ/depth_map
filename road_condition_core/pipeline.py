from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .config import AnalysisConfig
from .detectors import (
    RutSeries,
    detect_bumps,
    detect_potholes,
    detect_rutting,
    roughness_proxy,
)
from .geometry import rasterize_road_surface, track_to_enu_xy
from .models import AnalysisProducts, Defect, SegmentMetric, SurfaceGrid
from .report import render_html_report


ALGORITHM_VERSION = "road-condition-geometry-mvp-1"


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "E"


def _geometry_score(
    *,
    road_area_m2: float,
    potholes: list[Defect],
    rut_max_depth_m: float,
    bumps: list[Defect],
    roughness_m: float,
    config: AnalysisConfig,
) -> float:
    score_config = config.score
    pothole_area = sum(item.metrics.get("area_m2", 0.0) for item in potholes)
    pothole_depth = max(
        (item.metrics.get("max_depth_m", 0.0) for item in potholes), default=0.0
    )
    area_ratio = pothole_area / max(road_area_m2, 1e-9)
    pothole_penalty = min(
        100.0,
        60.0 * pothole_depth / score_config.high_pothole_depth_m
        + 40.0 * area_ratio / 0.02,
    )
    rut_penalty = min(100.0, 100.0 * rut_max_depth_m / score_config.high_rut_depth_m)
    bump_penalty = min(100.0, 25.0 * len(bumps))
    roughness_penalty = min(
        100.0, 100.0 * roughness_m / score_config.roughness_reference_m
    )
    weights = {
        "pothole": score_config.pothole_weight,
        "rutting": score_config.rutting_weight,
        "bump": score_config.bump_weight,
        "roughness": score_config.roughness_weight,
    }
    total_weight = sum(weights.values())
    penalty = (
        weights["pothole"] * pothole_penalty
        + weights["rutting"] * rut_penalty
        + weights["bump"] * bump_penalty
        + weights["roughness"] * roughness_penalty
    ) / total_weight
    return float(np.clip(100.0 - penalty, 0.0, 100.0))


def _band_roughness_for_rows(
    grid: SurfaceGrid,
    row_mask: np.ndarray,
    wheel_offset_m: float,
    band_half_width_m: float,
) -> float:
    band = (
        (np.abs(grid.t_values_m + wheel_offset_m) <= band_half_width_m)
        | (np.abs(grid.t_values_m - wheel_offset_m) <= band_half_width_m)
    )
    if not np.any(band) or not np.any(row_mask):
        return 0.0
    residual = grid.residual_m[row_mask][:, band].astype(np.float64)
    valid = grid.valid_mask[row_mask][:, band]
    values = residual[valid]
    if len(values) == 0:
        return 0.0
    return float(np.sqrt(np.mean(values * values)))


def _segment_metrics(
    grid: SurfaceGrid,
    defects: list[Defect],
    rut_series: RutSeries,
    config: AnalysisConfig,
) -> list[SegmentMetric]:
    segment_length = config.detection.segment_length_m
    start = math.floor(float(grid.s_values_m[0]) / segment_length) * segment_length
    end = float(grid.s_values_m[-1]) + 0.5 * config.surface.grid_size_m
    segments: list[SegmentMetric] = []
    index = 0
    while start < end - 1e-6:
        stop = min(start + segment_length, end)
        rows = (grid.s_values_m >= start) & (grid.s_values_m < stop + 1e-9)
        total_cells = int(np.count_nonzero(rows)) * len(grid.t_values_m)
        valid_cells = int(np.count_nonzero(grid.valid_mask[rows]))
        coverage = valid_cells / total_cells if total_cells else 0.0
        local_defects = [item for item in defects if start <= item.chainage_m < stop]
        potholes = [item for item in local_defects if item.defect_type == "pothole"]
        bumps = [item for item in local_defects if item.defect_type == "bump"]
        left_rows = rows & np.isfinite(rut_series.left_depth_m)
        right_rows = rows & np.isfinite(rut_series.right_depth_m)
        max_left = (
            float(np.max(rut_series.left_depth_m[left_rows])) if np.any(left_rows) else 0.0
        )
        max_right = (
            float(np.max(rut_series.right_depth_m[right_rows])) if np.any(right_rows) else 0.0
        )
        roughness = _band_roughness_for_rows(
            grid,
            rows,
            config.detection.rut_wheel_offset_m,
            config.detection.rut_band_half_width_m,
        )
        road_area = valid_cells * grid.cell_area_m2
        score = _geometry_score(
            road_area_m2=road_area,
            potholes=potholes,
            rut_max_depth_m=max(max_left, max_right),
            bumps=bumps,
            roughness_m=roughness,
            config=config,
        )
        if coverage < config.detection.minimum_valid_coverage_ratio:
            score = min(score, 59.9)
        segments.append(
            SegmentMetric(
                segment_id=f"segment-{index:04d}",
                chainage_start_m=float(start),
                chainage_end_m=float(stop),
                valid_coverage_ratio=float(coverage),
                pothole_count=len(potholes),
                pothole_area_m2=float(
                    sum(item.metrics.get("area_m2", 0.0) for item in potholes)
                ),
                pothole_volume_m3=float(
                    sum(item.metrics.get("volume_m3", 0.0) for item in potholes)
                ),
                max_pothole_depth_m=float(
                    max(
                        (item.metrics.get("max_depth_m", 0.0) for item in potholes),
                        default=0.0,
                    )
                ),
                max_left_rut_depth_m=max_left,
                max_right_rut_depth_m=max_right,
                bump_count=len(bumps),
                roughness_proxy_m=roughness,
                geometry_score=score,
                grade=_grade(score),
            )
        )
        start += segment_length
        index += 1
    return segments


def _sample_points(
    points: np.ndarray,
    colors: np.ndarray,
    metadata: Mapping[str, np.ndarray] | None,
    maximum: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray], int]:
    count = len(points)
    aligned = dict(metadata or {})
    if count <= maximum:
        return points, colors, aligned, count
    keep = np.linspace(0, count - 1, num=maximum, dtype=np.int64)
    sampled_metadata: dict[str, np.ndarray] = {}
    for name, values in aligned.items():
        array = np.asarray(values)
        if array.shape == (count,):
            sampled_metadata[name] = array[keep]
    return points[keep], colors[keep], sampled_metadata, count


def _apply_frame_reprojection(
    points: np.ndarray,
    colors: np.ndarray,
    metadata: Mapping[str, np.ndarray] | None,
    pose_context: Mapping[str, np.ndarray] | None,
    minimum_quality_score: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray], dict[str, Any]]:
    """Round-trip representative frame points and gate low-quality poses.

    Raw fused PLY stores ENU points rather than original pixels. This opt-in mode
    therefore validates the ENU↔camera transform and applies frame-pose quality
    gating; it does not claim to recreate the original RGB-D samples.
    """

    aligned = dict(metadata or {})
    if pose_context is None:
        raise ValueError("frame reprojection requires camera_poses.npz")
    source_ids = np.asarray(aligned.get("source_frame_id"))
    if source_ids.shape != (len(points),):
        raise ValueError(
            "frame reprojection requires point-aligned source_frame_id metadata"
        )
    transforms = np.asarray(pose_context.get("T_enu_camera"), dtype=np.float64)
    quality = np.asarray(pose_context.get("pose_quality_score"), dtype=np.float64)
    if transforms.ndim != 3 or transforms.shape[1:] != (4, 4):
        raise ValueError("frame reprojection pose transforms must have shape (N, 4, 4)")
    if quality.shape != (len(transforms),):
        raise ValueError("frame reprojection pose quality must align with transforms")
    source_ids = source_ids.astype(np.int64, copy=False)
    valid_source = (source_ids >= 0) & (source_ids < len(transforms))
    retained = valid_source.copy()
    retained[valid_source] &= quality[source_ids[valid_source]] >= minimum_quality_score
    if np.count_nonzero(retained) < 10_000:
        raise ValueError("frame reprojection retained fewer than 10,000 supported points")

    retained_points = np.asarray(points[retained], dtype=np.float64)
    retained_ids = source_ids[retained]
    maximum_error = 0.0
    for begin in range(0, len(retained_points), 250_000):
        end = min(begin + 250_000, len(retained_points))
        frame_transforms = transforms[retained_ids[begin:end]]
        rotations = frame_transforms[:, :3, :3]
        translations = frame_transforms[:, :3, 3]
        camera_points = np.einsum(
            "nij,nj->ni",
            np.swapaxes(rotations, 1, 2),
            retained_points[begin:end] - translations,
        )
        round_trip = np.einsum("nij,nj->ni", rotations, camera_points) + translations
        if len(round_trip):
            maximum_error = max(
                maximum_error,
                float(np.max(np.linalg.norm(round_trip - retained_points[begin:end], axis=1))),
            )
        retained_points[begin:end] = round_trip
    filtered_metadata = {
        name: np.asarray(values)[retained]
        for name, values in aligned.items()
        if np.asarray(values).shape == (len(points),)
    }
    return (
        retained_points.astype(points.dtype, copy=False),
        colors[retained],
        filtered_metadata,
        {
            "frame_reprojection_enabled": True,
            "input_point_count": int(len(points)),
            "retained_point_count": int(len(retained_points)),
            "minimum_pose_quality_score": float(minimum_quality_score),
            "round_trip_max_error_m": maximum_error,
            "mode_limit": (
                "Fused PLY representative points are pose-validated and quality-gated; "
                "original RGB-D pixels are not reconstructed."
            ),
        },
    )


def analyze_points(
    points_enu_m: np.ndarray,
    colors_rgb: np.ndarray,
    trajectory_enu_m: np.ndarray,
    *,
    config: AnalysisConfig | None = None,
    point_metadata: Mapping[str, np.ndarray] | None = None,
    source: Mapping[str, Any] | None = None,
    source_origin: dict[str, float] | None = None,
    pose_context: Mapping[str, np.ndarray] | None = None,
    quality_context: Mapping[str, Any] | None = None,
) -> AnalysisProducts:
    resolved_config = config or AnalysisConfig()
    resolved_config.validate()
    points = np.asarray(points_enu_m)
    colors = np.asarray(colors_rgb)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_enu_m must have shape (N, 3)")
    if colors.shape != points.shape:
        raise ValueError("colors_rgb must have shape (N, 3)")
    reprojection_quality: dict[str, Any] = {
        "frame_reprojection_enabled": False,
        "mode": "ply_only",
    }
    prepared_metadata = dict(point_metadata or {})
    if resolved_config.pose.frame_reprojection_enabled:
        points, colors, prepared_metadata, reprojection_quality = _apply_frame_reprojection(
            points,
            colors,
            prepared_metadata,
            pose_context,
            resolved_config.pose.minimum_quality_score,
        )
    points, colors, metadata, original_count = _sample_points(
        points,
        colors,
        prepared_metadata,
        resolved_config.surface.max_input_points,
    )
    grid = rasterize_road_surface(
        points,
        trajectory_enu_m,
        resolved_config.surface,
        position_std_m=metadata.get("position_std_m"),
        source_origin=source_origin,
    )
    potholes = detect_potholes(grid, resolved_config.detection)
    bumps = detect_bumps(grid, resolved_config.detection)
    ruts, rut_series = detect_rutting(grid, resolved_config.detection)
    defects = sorted(
        [*potholes, *bumps, *ruts],
        key=lambda item: (item.chainage_m, item.defect_type, item.defect_id),
    )
    roughness = roughness_proxy(
        grid,
        resolved_config.detection.rut_wheel_offset_m,
        resolved_config.detection.rut_band_half_width_m,
    )
    valid_cells = int(np.count_nonzero(grid.valid_mask))
    total_cells = int(grid.valid_mask.size)
    valid_area = valid_cells * grid.cell_area_m2
    max_rut = max(
        max((item.metrics.get("max_depth_m", 0.0) for item in ruts), default=0.0),
        0.0,
    )
    score = _geometry_score(
        road_area_m2=valid_area,
        potholes=potholes,
        rut_max_depth_m=max_rut,
        bumps=bumps,
        roughness_m=roughness,
        config=resolved_config,
    )
    coverage_ratio = valid_cells / total_cells if total_cells else 0.0
    if coverage_ratio < resolved_config.detection.minimum_valid_coverage_ratio:
        score = min(score, 59.9)
    segments = _segment_metrics(grid, defects, rut_series, resolved_config)
    summary = {
        "format_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "coordinate_system": "local_road_ST_metres",
        "source": dict(source or {"type": "unknown"}),
        "parameters": resolved_config.to_dict(),
        "quality": {
            "original_point_count": int(original_count),
            "analyzed_point_count": int(len(points)),
            "point_sampling_applied": bool(original_count != len(points)),
            "supported_surface_cell_count": valid_cells,
            "total_surface_cell_count": total_cells,
            "median_points_per_valid_cell": float(
                np.median(grid.point_count[grid.valid_mask])
            ),
            "median_position_std_m": float(
                np.median(grid.position_std_m[np.isfinite(grid.position_std_m)])
            ),
            **dict(quality_context or {}),
            "pose_surface_mode": reprojection_quality,
        },
        "coverage": {
            "chainage_start_m": float(grid.s_values_m[0]),
            "chainage_end_m": float(grid.s_values_m[-1]),
            "corridor_half_width_m": resolved_config.surface.corridor_half_width_m,
            "valid_coverage_ratio": float(coverage_ratio),
            "valid_surface_area_m2": float(valid_area),
            "grid_size_m": resolved_config.surface.grid_size_m,
        },
        "results": {
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
            "max_rut_depth_m": float(max_rut),
            "bump_count": len(bumps),
            "max_bump_height_m": float(
                max(
                    (item.metrics.get("max_height_m", 0.0) for item in bumps),
                    default=0.0,
                )
            ),
            "roughness_proxy_m": float(roughness),
        },
        "scores": {
            "geometry_score": float(score),
            "grade": _grade(score),
            "surface_score": None,
            "ride_score": None,
            "overall_score": float(score),
            "score_profile": "internal_geometry_mvp_v1",
        },
        "limitations": [
            "This MVP analyzes geometry only; crack, patching, raveling, and bleeding require an RGB model.",
            "The roughness value is a project-specific proxy and must not be reported as standardized IRI.",
            "The score is an internal planning score and must not be reported as certified PCI.",
            "Thresholds must be calibrated with surveyed potholes, rut depths, and a flat-road noise holdout.",
            "Low coverage or pose/depth uncertainty requires manual review or recollection.",
        ],
    }
    if (source or {}).get("type") == "mapping_bundle" and not bool(
        (quality_context or {}).get("pose_file_available", False)
    ):
        summary["limitations"].append(
            "camera_poses.npz is unavailable; PLY-only geometry cannot provide frame-level precision claims."
        )
    if bool((quality_context or {}).get("manual_review_required", False)):
        summary["limitations"].append(
            "Camera calibration is unknown or estimated; geometry results require manual review."
        )
    return AnalysisProducts(summary=summary, defects=defects, segments=segments, surface=grid)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _downsample_indices(count: int, maximum: int) -> np.ndarray:
    if count <= maximum:
        return np.arange(count, dtype=np.int64)
    return np.unique(np.linspace(0, count - 1, num=maximum, dtype=np.int64))


def _json_matrix(values: np.ndarray) -> list[list[float | None]]:
    array = np.asarray(values, dtype=np.float64)
    return [
        [float(value) if np.isfinite(value) else None for value in row]
        for row in array
    ]


def _local_geojson(defects: list[Defect]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": "road_condition_defects_local_st",
        "coordinate_system": "local_road_ST_metres",
        "features": [item.to_geojson_feature() for item in defects],
    }


def _enu_geojson(defects: list[Defect], grid: SurfaceGrid) -> dict[str, Any]:
    features = []
    for item in defects:
        polygon = np.asarray(item.local_polygon_st_m, dtype=np.float64)
        if len(polygon) == 0:
            continue
        xy = track_to_enu_xy(
            polygon[:, 0],
            polygon[:, 1],
            grid.trajectory_enu_m,
            grid.trajectory_cumulative_m,
        )
        ring = xy.round(4).tolist()
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        properties = item.to_dict()
        properties.pop("local_polygon_st_m", None)
        features.append(
            {
                "type": "Feature",
                "id": item.defect_id,
                "properties": properties,
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    return {
        "type": "FeatureCollection",
        "name": "road_condition_defects_enu",
        "coordinate_system": "local_ENU_metres",
        "origin": grid.source_origin,
        "features": features,
    }


def write_analysis_products(output_dir: str | Path, products: AnalysisProducts) -> dict[str, str]:
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    defect_dicts = [item.to_dict() for item in products.defects]
    segment_dicts = [item.to_dict() for item in products.segments]
    _atomic_json(output / "summary.json", products.summary)
    _atomic_json(output / "defects.json", defect_dicts)
    _atomic_json(output / "segments.json", segment_dicts)
    _atomic_json(output / "defects.local.geojson", _local_geojson(products.defects))
    _atomic_json(output / "defects.enu.geojson", _enu_geojson(products.defects, products.surface))

    s_keep = _downsample_indices(
        len(products.surface.s_values_m),
        int(products.summary["parameters"]["surface"]["preview_max_along_cells"]),
    )
    t_keep = _downsample_indices(
        len(products.surface.t_values_m),
        int(products.summary["parameters"]["surface"]["preview_max_cross_cells"]),
    )
    residual_mm = 1000.0 * products.surface.residual_m[np.ix_(s_keep, t_keep)]
    observed = products.surface.observed_local_up_m[np.ix_(s_keep, t_keep)]
    reference = products.surface.reference_local_up_m[np.ix_(s_keep, t_keep)]
    valid = products.surface.valid_mask[np.ix_(s_keep, t_keep)]
    residual_mm = np.where(valid, residual_mm, np.nan)
    preview = {
        "format_version": 1,
        "coordinate_system": "local_road_ST_metres",
        "s_values_m": products.surface.s_values_m[s_keep].astype(float).tolist(),
        "t_values_m": products.surface.t_values_m[t_keep].astype(float).tolist(),
        "residual_mm": _json_matrix(residual_mm),
        "observed_local_up_m": _json_matrix(np.where(valid, observed, np.nan)),
        "reference_local_up_m": _json_matrix(np.where(valid, reference, np.nan)),
        "display_range_mm": [-120.0, 120.0],
    }
    _atomic_json(output / "surface_preview.json", preview)
    np.savez_compressed(
        output / "surface.npz",
        s_values_m=products.surface.s_values_m,
        t_values_m=products.surface.t_values_m,
        observed_local_up_m=products.surface.observed_local_up_m,
        reference_local_up_m=products.surface.reference_local_up_m,
        residual_m=products.surface.residual_m,
        point_count=products.surface.point_count,
        position_std_m=products.surface.position_std_m,
        valid_mask=products.surface.valid_mask,
    )
    report = render_html_report(products.summary, defect_dicts, segment_dicts)
    (output / "report.html").write_text(report, encoding="utf-8")
    return {
        "summary": "summary.json",
        "defects": "defects.json",
        "defects_local_geojson": "defects.local.geojson",
        "defects_enu_geojson": "defects.enu.geojson",
        "segments": "segments.json",
        "surface_preview": "surface_preview.json",
        "surface_npz": "surface.npz",
        "report_html": "report.html",
    }
