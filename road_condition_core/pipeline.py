from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .advanced_geometry import (
    crossfall_profile,
    detect_ponding_screening,
    detect_step_manhole_candidates,
    longitudinal_profile,
)
from .config import AnalysisConfig
from .detectors import (
    RutSeries,
    detect_bumps,
    detect_potholes,
    detect_rutting,
    roughness_proxy,
)
from .geometry import project_to_trajectory, rasterize_road_surface, track_to_enu_xy
from .models import AnalysisProducts, Defect, SegmentMetric, SurfaceGrid
from .method_basis import method_basis_contract
from .report import render_html_report
from .report_v2 import generate_report_bundle
from .roi import ZONE_TYPE_CODES, RoadRoi, classify_st


ALGORITHM_VERSION = "road-condition-geometry-mvp-3"
DEFAULT_SCORING_PROFILE_CONTRACT = {
    "profile_id": "internal-geometry-mvp-v1",
    "profile_version": "1.0.0",
    "profile_sha256": None,
    "source_document": None,
    "effective_date": None,
    "approval_status": "experimental",
    "standard_naming_allowed": False,
    "missing_metric_policy": "N/A_and_manual_review",
    "segment_length_m": 20.0,
    "lane_evaluation": "when_roi_available",
    "automatic_approval_confidence_threshold": None,
    "custom_override_applied": False,
}


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
    cell_mask: np.ndarray | None = None,
) -> float:
    band = (
        (np.abs(grid.t_values_m + wheel_offset_m) <= band_half_width_m)
        | (np.abs(grid.t_values_m - wheel_offset_m) <= band_half_width_m)
    )
    if not np.any(band) or not np.any(row_mask):
        return 0.0
    residual = grid.residual_m[row_mask][:, band].astype(np.float64)
    valid = grid.valid_mask[row_mask][:, band]
    if cell_mask is not None:
        valid &= np.asarray(cell_mask, dtype=bool)[row_mask][:, band]
    values = residual[valid]
    if len(values) == 0:
        return 0.0
    return float(np.sqrt(np.mean(values * values)))


def _segment_metrics(
    grid: SurfaceGrid,
    defects: list[Defect],
    rut_series: RutSeries,
    config: AnalysisConfig,
    *,
    cell_mask: np.ndarray | None = None,
    lane_id: str | None = None,
    road_zone: str = "corridor_fallback",
) -> list[SegmentMetric]:
    selected_cells = (
        np.ones_like(grid.valid_mask, dtype=bool)
        if cell_mask is None
        else np.asarray(cell_mask, dtype=bool)
    )
    if selected_cells.shape != grid.valid_mask.shape:
        raise ValueError("segment cell mask must align with surface grid")
    segment_length = config.detection.segment_length_m
    start = math.floor(float(grid.s_values_m[0]) / segment_length) * segment_length
    end = float(grid.s_values_m[-1]) + 0.5 * config.surface.grid_size_m
    segments: list[SegmentMetric] = []
    index = 0
    while start < end - 1e-6:
        stop = min(start + segment_length, end)
        rows = (grid.s_values_m >= start) & (grid.s_values_m < stop + 1e-9)
        total_cells = int(np.count_nonzero(selected_cells[rows]))
        if total_cells == 0:
            start += segment_length
            index += 1
            continue
        valid_cells = int(np.count_nonzero(grid.valid_mask[rows] & selected_cells[rows]))
        coverage = valid_cells / total_cells if total_cells else 0.0
        local_defects = [
            item
            for item in defects
            if start <= item.chainage_m < stop
            and (lane_id is None or item.lane_id == lane_id)
        ]
        potholes = [item for item in local_defects if item.defect_type == "pothole"]
        bumps = [item for item in local_defects if item.defect_type == "bump"]
        left_band = (
            np.abs(grid.t_values_m + config.detection.rut_wheel_offset_m)
            <= config.detection.rut_band_half_width_m
        )
        right_band = (
            np.abs(grid.t_values_m - config.detection.rut_wheel_offset_m)
            <= config.detection.rut_band_half_width_m
        )
        left_selected_rows = (
            np.any(selected_cells[:, left_band], axis=1)
            if np.any(left_band)
            else np.zeros(len(rows), dtype=bool)
        )
        right_selected_rows = (
            np.any(selected_cells[:, right_band], axis=1)
            if np.any(right_band)
            else np.zeros(len(rows), dtype=bool)
        )
        left_rows = rows & left_selected_rows & np.isfinite(rut_series.left_depth_m)
        right_rows = rows & right_selected_rows & np.isfinite(rut_series.right_depth_m)
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
            selected_cells,
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
                segment_id=(
                    f"lane-{lane_id}-segment-{index:04d}"
                    if lane_id is not None
                    else f"segment-{index:04d}"
                ),
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
                lane_id=lane_id,
                road_zone=road_zone,
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
    road_roi: RoadRoi | None = None,
    scoring_profile_contract: Mapping[str, Any] | None = None,
) -> AnalysisProducts:
    resolved_config = config or AnalysisConfig()
    resolved_config.validate()
    profile_contract = dict(
        scoring_profile_contract or DEFAULT_SCORING_PROFILE_CONTRACT
    )
    lane_evaluation = profile_contract.get("lane_evaluation", "when_roi_available")
    if lane_evaluation not in {"when_roi_available", "disabled", "required"}:
        raise ValueError("invalid scoring profile lane_evaluation")
    if lane_evaluation == "required" and (
        road_roi is None or not road_roi.lane_ids
    ):
        raise ValueError("scoring profile requires lane ROI evaluation")
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
    sampled_count = len(points)
    multiview_quality: dict[str, Any] = {
        "multiview_evidence_available": False,
        "multiview_filter_applied": False,
        "multiview_input_point_count": int(len(points)),
        "multiview_retained_point_count": int(len(points)),
        "minimum_independent_view_count": int(
            resolved_config.surface.minimum_independent_view_count
        ),
    }
    independent_views = metadata.get("independent_view_count")
    if independent_views is not None:
        independent_views = np.asarray(independent_views)
        if independent_views.shape != (len(points),):
            raise ValueError("independent_view_count must align with points")
        retained = (
            independent_views
            >= resolved_config.surface.minimum_independent_view_count
        )
        retained_count = int(np.count_nonzero(retained))
        if retained_count < resolved_config.surface.reference_min_cells:
            raise ValueError(
                "multi-view evidence retained too few points for surface fitting"
            )
        points = points[retained]
        colors = colors[retained]
        metadata = {
            name: np.asarray(values)[retained]
            for name, values in metadata.items()
            if np.asarray(values).shape == (len(retained),)
        }
        multiview_quality = {
            "multiview_evidence_available": True,
            "multiview_filter_applied": True,
            "multiview_input_point_count": int(len(retained)),
            "multiview_retained_point_count": retained_count,
            "multiview_excluded_point_count": int(len(retained) - retained_count),
            "multiview_retained_ratio": float(retained_count / len(retained)),
            "minimum_independent_view_count": int(
                resolved_config.surface.minimum_independent_view_count
            ),
        }
    roi_quality: dict[str, Any] = {
        "roi_applied": False,
        "roi_source": "trajectory_corridor_fallback",
        "roi_input_point_count": int(len(points)),
        "roi_retained_point_count": int(len(points)),
    }
    if road_roi is not None:
        coordinates = project_to_trajectory(points, trajectory_enu_m)
        point_zones = classify_st(
            coordinates.along_track_m,
            coordinates.signed_cross_track_m,
            road_roi,
        )
        retained = point_zones.included_surface_mask
        roi_input_count = len(retained)
        retained_count = int(np.count_nonzero(retained))
        if retained_count < resolved_config.surface.reference_min_cells:
            raise ValueError(
                "road ROI retained too few points for reference surface fitting"
            )
        points = points[retained]
        colors = colors[retained]
        metadata = {
            name: np.asarray(values)[retained]
            for name, values in metadata.items()
            if np.asarray(values).shape == (len(retained),)
        }
        roi_quality = {
            "roi_applied": True,
            "roi_source": (
                str(road_roi.source_path) if road_roi.source_path is not None else "inline"
            ),
            "roi_input_point_count": int(roi_input_count),
            "roi_retained_point_count": retained_count,
            "roi_excluded_point_count": int(roi_input_count - retained_count),
            "lane_ids": list(road_roi.lane_ids),
        }
    grid = rasterize_road_surface(
        points,
        trajectory_enu_m,
        resolved_config.surface,
        position_std_m=metadata.get("position_std_m"),
        source_origin=source_origin,
    )
    grid_zone_type: np.ndarray | None = None
    grid_lane_id: np.ndarray | None = None
    if road_roi is not None:
        s_grid, t_grid = np.meshgrid(grid.s_values_m, grid.t_values_m, indexing="ij")
        grid_zones = classify_st(s_grid.ravel(), t_grid.ravel(), road_roi)
        grid_zone_type = grid_zones.zone_type.reshape(grid.valid_mask.shape)
        grid_lane_id = grid_zones.lane_id.reshape(grid.valid_mask.shape)
        included_grid = np.isin(grid_zone_type, ("road", "lane"))
        lane_ids = road_roi.lane_ids
        lane_lookup = {value: index + 1 for index, value in enumerate(lane_ids)}
        lane_index = np.zeros(grid.valid_mask.shape, dtype=np.uint16)
        for value, index in lane_lookup.items():
            lane_index[grid_lane_id == value] = index
        zone_code = np.zeros(grid.valid_mask.shape, dtype=np.uint8)
        for zone_type, code in ZONE_TYPE_CODES.items():
            zone_code[grid_zone_type == zone_type] = code
        grid = replace(
            grid,
            valid_mask=grid.valid_mask & included_grid,
            residual_m=np.where(included_grid, grid.residual_m, np.nan).astype(np.float32),
            supported_mask=(
                grid.supported_mask & included_grid
                if grid.supported_mask is not None
                else grid.valid_mask & included_grid
            ),
            plausibility_excluded_low_mask=(
                grid.plausibility_excluded_low_mask & included_grid
                if grid.plausibility_excluded_low_mask is not None
                else None
            ),
            plausibility_excluded_high_mask=(
                grid.plausibility_excluded_high_mask & included_grid
                if grid.plausibility_excluded_high_mask is not None
                else None
            ),
            roi_zone_code=zone_code,
            roi_lane_index=lane_index,
            roi_lane_ids=lane_ids,
        )
        roi_quality.update(
            {
                "roi_corridor_coverage_ratio": float(np.mean(included_grid)),
                "roi_unknown_area_ratio": float(np.mean(grid_zone_type == "unknown")),
                "roi_exclusion_area_ratio": float(np.mean(grid_zone_type == "exclusion")),
                "roi_shoulder_area_ratio": float(np.mean(grid_zone_type == "shoulder")),
            }
        )
    potholes = detect_potholes(grid, resolved_config.detection)
    bumps = detect_bumps(grid, resolved_config.detection)
    ruts, rut_series = detect_rutting(grid, resolved_config.detection)
    advanced_defects: list[Defect] = []
    advanced_results: dict[str, Any] = {}
    advanced_errors: list[str] = []

    def run_advanced(name: str, enabled: bool, operation: Any) -> Any:
        if not enabled:
            advanced_results[name] = {"state": "disabled"}
            return None
        try:
            value = operation()
            advanced_results[name] = {"state": "completed"}
            return value
        except Exception as exc:  # noqa: BLE001 - detector isolation is contractual
            message = f"{type(exc).__name__}: {exc}"
            advanced_results[name] = {"state": "failed", "error": message}
            advanced_errors.append(f"{name}: {message}")
            return None

    step_candidates = run_advanced(
        "step_manhole",
        resolved_config.advanced_geometry.step_manhole_enabled,
        lambda: detect_step_manhole_candidates(
            grid, resolved_config.advanced_geometry
        ),
    )
    if step_candidates is not None:
        advanced_defects.extend(step_candidates)
        advanced_results["step_manhole"]["candidate_count"] = len(step_candidates)
    crossfall = run_advanced(
        "crossfall",
        resolved_config.advanced_geometry.crossfall_enabled,
        lambda: crossfall_profile(grid),
    )
    if crossfall is not None:
        advanced_results["crossfall"]["profile"] = crossfall
    longitudinal = run_advanced(
        "longitudinal",
        resolved_config.advanced_geometry.longitudinal_enabled,
        lambda: longitudinal_profile(grid, resolved_config.detection),
    )
    if longitudinal is not None:
        advanced_results["longitudinal"]["profile"] = longitudinal
    ponding_candidates = run_advanced(
        "ponding_screening",
        resolved_config.advanced_geometry.ponding_screening_enabled,
        lambda: detect_ponding_screening(
            grid, resolved_config.advanced_geometry
        ),
    )
    if ponding_candidates is not None:
        advanced_defects.extend(ponding_candidates)
        advanced_results["ponding_screening"]["candidate_count"] = len(
            ponding_candidates
        )
    defects = sorted(
        [*potholes, *bumps, *ruts, *advanced_defects],
        key=lambda item: (item.chainage_m, item.defect_type, item.defect_id),
    )
    if road_roi is not None and defects:
        defect_zones = classify_st(
            np.asarray([item.chainage_m for item in defects]),
            np.asarray([item.lateral_offset_m for item in defects]),
            road_roi,
        )
        defects = [
            replace(
                item,
                lane_id=(str(defect_zones.lane_id[index]) or None),
                road_zone=str(defect_zones.zone_type[index]),
            )
            for index, item in enumerate(defects)
        ]
    roughness = roughness_proxy(
        grid,
        resolved_config.detection.rut_wheel_offset_m,
        resolved_config.detection.rut_band_half_width_m,
    )
    valid_cells = int(np.count_nonzero(grid.valid_mask))
    total_cells = int(grid.valid_mask.size)
    supported_mask = (
        grid.supported_mask if grid.supported_mask is not None else grid.valid_mask
    )
    supported_cells = int(np.count_nonzero(supported_mask))
    excluded_low_cells = int(
        np.count_nonzero(grid.plausibility_excluded_low_mask)
        if grid.plausibility_excluded_low_mask is not None
        else 0
    )
    excluded_high_cells = int(
        np.count_nonzero(grid.plausibility_excluded_high_mask)
        if grid.plausibility_excluded_high_mask is not None
        else 0
    )
    excluded_cells = excluded_low_cells + excluded_high_cells
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
    segments = _segment_metrics(
        grid,
        defects,
        rut_series,
        resolved_config,
        road_zone="road" if road_roi is not None else "corridor_fallback",
    )
    if grid_lane_id is not None and lane_evaluation != "disabled":
        for lane_id in road_roi.lane_ids if road_roi is not None else ():
            segments.extend(
                _segment_metrics(
                    grid,
                    defects,
                    rut_series,
                    resolved_config,
                    cell_mask=grid_lane_id == lane_id,
                    lane_id=lane_id,
                    road_zone="lane",
                )
            )
    summary = {
        "format_version": 1,
        "algorithm_version": ALGORITHM_VERSION,
        "coordinate_system": "local_road_ST_metres",
        "source": dict(source or {"type": "unknown"}),
        "parameters": resolved_config.to_dict(),
        "quality": {
            "original_point_count": int(original_count),
            "analyzed_point_count": int(len(points)),
            "point_sampling_applied": bool(original_count != sampled_count),
            "supported_surface_cell_count": supported_cells,
            "usable_surface_cell_count": valid_cells,
            "plausibility_excluded_cell_count": excluded_cells,
            "plausibility_excluded_low_cell_count": excluded_low_cells,
            "plausibility_excluded_high_cell_count": excluded_high_cells,
            "total_surface_cell_count": total_cells,
            "median_points_per_valid_cell": float(
                np.median(grid.point_count[grid.valid_mask])
            ),
            "median_position_std_m": float(
                np.median(grid.position_std_m[np.isfinite(grid.position_std_m)])
            ),
            **dict(quality_context or {}),
            "pose_surface_mode": reprojection_quality,
            **multiview_quality,
            **roi_quality,
        },
        "coverage": {
            "chainage_start_m": float(grid.s_values_m[0]),
            "chainage_end_m": float(grid.s_values_m[-1]),
            "corridor_half_width_m": resolved_config.surface.corridor_half_width_m,
            "valid_coverage_ratio": float(coverage_ratio),
            "supported_coverage_ratio": float(
                supported_cells / total_cells if total_cells else 0.0
            ),
            "valid_surface_area_m2": float(valid_area),
            "plausibility_excluded_area_m2": float(
                excluded_cells * grid.cell_area_m2
            ),
            "plausibility_excluded_supported_ratio": float(
                excluded_cells / supported_cells if supported_cells else 0.0
            ),
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
            "advanced_geometry_candidate_count": len(advanced_defects),
        },
        "advanced_geometry": {
            "contract": "opt_in_experimental_screening",
            "detectors": advanced_results,
            "failure_count": len(advanced_errors),
        },
        "scores": {
            "geometry_score": float(score),
            "grade": _grade(score),
            "surface_score": None,
            "ride_score": None,
            "overall_score": float(score),
            "score_profile": "internal_geometry_mvp_v1",
        },
        "scoring_profile": profile_contract,
        "method_basis": method_basis_contract(),
        "limitations": [
            "This MVP analyzes geometry only; crack, patching, raveling, and bleeding require an RGB model.",
            "The roughness value is a project-specific proxy and must not be reported as standardized IRI.",
            "The score is an internal planning score and must not be reported as certified PCI.",
            "Thresholds must be calibrated with surveyed potholes, rut depths, and a flat-road noise holdout.",
            "Low coverage or pose/depth uncertainty requires manual review or recollection.",
            "The residual plausibility gate is an experimental non-road-cell guard, not a surveyed acceptance threshold.",
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
    if not multiview_quality["multiview_evidence_available"]:
        summary["limitations"].append(
            "Independent-view point evidence is unavailable; transient-object filtering was not applied."
        )
    if resolved_config.advanced_geometry.step_manhole_enabled:
        summary["limitations"].append(
            "Step/manhole geometry candidates require asset inventory or RGB confirmation."
        )
    if resolved_config.advanced_geometry.crossfall_enabled:
        summary["limitations"].append(
            "Crossfall values are experimental and intersection exclusion is not automatic without ROI semantics."
        )
    if resolved_config.advanced_geometry.longitudinal_enabled:
        summary["limitations"].append(
            "Longitudinal slope and roughness proxy are not standardized IRI."
        )
    if resolved_config.advanced_geometry.ponding_screening_enabled:
        summary["limitations"].append(
            "Ponding output is a closed-depression screening proxy; no drain capacity or flooding prediction is computed."
        )
    if advanced_errors:
        summary["limitations"].append(
            "Advanced detector failures were isolated and require review: "
            + "; ".join(advanced_errors)
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
    if products.surface.roi_zone_code is not None:
        preview["roi"] = {
            "applied": True,
            "zone_code": products.surface.roi_zone_code[np.ix_(s_keep, t_keep)]
            .astype(int)
            .tolist(),
            "zone_code_legend": {
                str(code): name for name, code in ZONE_TYPE_CODES.items()
            },
            "lane_index": products.surface.roi_lane_index[np.ix_(s_keep, t_keep)]
            .astype(int)
            .tolist(),
            "lane_ids": list(products.surface.roi_lane_ids),
        }
    else:
        preview["roi"] = {
            "applied": False,
            "fallback": "trajectory_corridor",
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
        supported_mask=(
            products.surface.supported_mask
            if products.surface.supported_mask is not None
            else products.surface.valid_mask
        ),
        plausibility_excluded_low_mask=(
            products.surface.plausibility_excluded_low_mask
            if products.surface.plausibility_excluded_low_mask is not None
            else np.zeros_like(products.surface.valid_mask)
        ),
        plausibility_excluded_high_mask=(
            products.surface.plausibility_excluded_high_mask
            if products.surface.plausibility_excluded_high_mask is not None
            else np.zeros_like(products.surface.valid_mask)
        ),
        roi_zone_code=(
            products.surface.roi_zone_code
            if products.surface.roi_zone_code is not None
            else np.empty((0, 0), dtype=np.uint8)
        ),
        roi_lane_index=(
            products.surface.roi_lane_index
            if products.surface.roi_lane_index is not None
            else np.empty((0, 0), dtype=np.uint16)
        ),
    )
    report = render_html_report(products.summary, defect_dicts, segment_dicts)
    (output / "report.html").write_text(report, encoding="utf-8")
    generate_report_bundle(output, output / "report")
    return {
        "summary": "summary.json",
        "defects": "defects.json",
        "defects_local_geojson": "defects.local.geojson",
        "defects_enu_geojson": "defects.enu.geojson",
        "segments": "segments.json",
        "surface_preview": "surface_preview.json",
        "surface_npz": "surface.npz",
        "report_html": "report.html",
        "report_v2_html": "report/report.html",
        "report_v2_manifest": "report/report_manifest.json",
        "report_v2_summary_csv": "report/summary.csv",
        "report_v2_segments_csv": "report/segments.csv",
        "report_v2_defects_csv": "report/defects.csv",
    }
