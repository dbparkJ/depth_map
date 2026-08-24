from __future__ import annotations

import copy
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .debug_stages import write_debug_stage_artifacts
from .diagnostics import write_postprocess_diagnostics
from .geodesy import LocalENU
from .postprocess import (
    PointCloudMetadata,
    PostprocessResult,
    PostprocessStage,
    QualityGuardResult,
    RemovalReason,
    diagnostic_removal_colors,
    fallback_preset_for_quality,
    primary_removal_reasons,
    run_postprocess,
)
from .postprocess_backends import DependencyInfo
from .postprocess_backends import run_pdal_comparison
from .postprocess_args import PostprocessOutputOptions
from .postprocess_config import PostprocessConfig, resolve_postprocess_config
from .postprocess_io import (
    atomic_json_dump,
    write_processed_cloud_products,
)


@dataclass(frozen=True)
class PostprocessAttempt:
    preset: str
    seconds: float
    clean_point_count: int
    removed_point_count: int
    removal_ratio: float
    quality_guards_passed: bool
    quality_penalty: float


@dataclass(frozen=True)
class PostprocessExecution:
    selected: PostprocessResult
    attempts: tuple[PostprocessAttempt, ...]
    requested_preset: str
    fallback_preset: str | None
    fallback_selected: bool
    requested_ground_backend: str


@dataclass(frozen=True)
class _CompactResult:
    """First-attempt state retained while a fallback runs.

    Clean/removed point copies and neighbor/ground work arrays are intentionally
    dropped. If the first attempt wins, its point products are materialized once
    after the fallback has been released.
    """

    raw_points_enu_m: np.ndarray
    raw_colors_rgb: np.ndarray
    keep_mask: np.ndarray
    removal_reason_bits: np.ndarray
    metadata: PointCloudMetadata
    stages: tuple[PostprocessStage, ...]
    quality: QualityGuardResult
    config: PostprocessConfig
    neighbor_backend: str
    ground_backend: str
    dependencies: DependencyInfo
    report: dict[str, Any]


def _quality_penalty_values(
    quality: QualityGuardResult, *, enabled: bool
) -> float:
    penalty = 0.0
    for value, target, weight in (
        (quality.xy_coverage_retention, 0.90, 20.0),
        (quality.trajectory_corridor_coverage_retention, 0.90, 20.0),
        (quality.high_structure_retention, 0.85, 15.0),
    ):
        if value is not None:
            penalty += max(0.0, target - value) * weight
    penalty += max(0.0, quality.removal_ratio - 0.35) * 20.0
    if quality.removal_ratio > 0.50:
        penalty += 10.0
    if enabled and quality.removal_ratio < 0.002:
        penalty += 0.50
    if quality.below_surface_reduction is not None:
        penalty += max(0.0, 0.60 - quality.below_surface_reduction)
    if quality.bright_isolated_reduction is not None:
        penalty += max(0.0, 0.50 - quality.bright_isolated_reduction) * 0.5
    return penalty


def _quality_penalty(result: PostprocessResult) -> float:
    return _quality_penalty_values(result.quality, enabled=result.config.enabled)


def _fallback_is_better(
    first: _CompactResult,
    fallback: PostprocessResult,
    fallback_preset: str | None,
) -> bool:
    if first.quality.passed != fallback.quality.passed:
        return bool(fallback.quality.passed)

    first_penalty = _quality_penalty_values(
        first.quality, enabled=first.config.enabled
    )
    fallback_penalty = _quality_penalty(fallback)
    if fallback_penalty < first_penalty - 1e-12:
        return True
    if fallback_penalty > first_penalty + 1e-12:
        return False

    # For an equal safe score, conservative wins an over-removal fallback and
    # aggressive wins an under-cleaning fallback only when it removes more.
    if fallback_preset == "conservative":
        return fallback.quality.removal_ratio <= first.quality.removal_ratio
    if fallback_preset == "aggressive":
        return fallback.quality.removal_ratio >= first.quality.removal_ratio
    return False


def _compact_result(result: PostprocessResult) -> _CompactResult:
    return _CompactResult(
        raw_points_enu_m=result.raw_points_enu_m,
        raw_colors_rgb=result.raw_colors_rgb,
        keep_mask=result.keep_mask,
        removal_reason_bits=result.removal_reason_bits,
        metadata=result.metadata,
        stages=result.stages,
        quality=result.quality,
        config=result.config,
        neighbor_backend=result.neighbor_backend,
        ground_backend=result.ground_backend,
        dependencies=result.dependencies,
        report=result.report,
    )


def _materialize_compact_result(compact: _CompactResult) -> PostprocessResult:
    keep = compact.keep_mask
    removed = ~keep
    clean_indices = np.flatnonzero(keep).astype(np.int64, copy=False)
    removed_indices = np.flatnonzero(removed).astype(np.int64, copy=False)
    primary = primary_removal_reasons(compact.removal_reason_bits)
    return PostprocessResult(
        raw_points_enu_m=compact.raw_points_enu_m,
        raw_colors_rgb=compact.raw_colors_rgb,
        clean_points_enu_m=compact.raw_points_enu_m[clean_indices],
        clean_colors_rgb=compact.raw_colors_rgb[clean_indices],
        removed_points_enu_m=compact.raw_points_enu_m[removed_indices],
        removed_original_colors_rgb=compact.raw_colors_rgb[removed_indices],
        removed_diagnostic_colors_rgb=diagnostic_removal_colors(
            primary[removed_indices]
        ),
        keep_mask=keep,
        removed_mask=removed,
        clean_indices=clean_indices,
        removed_indices=removed_indices,
        removal_reason_bits=compact.removal_reason_bits,
        primary_reason=primary,
        metadata=compact.metadata,
        neighbor_result=None,
        ground_surface=None,
        stages=compact.stages,
        quality=compact.quality,
        config=compact.config,
        neighbor_backend=compact.neighbor_backend,
        ground_backend=compact.ground_backend,
        dependencies=compact.dependencies,
        report=compact.report,
    )


def _attempt_summary(
    preset: str, result: PostprocessResult, seconds: float
) -> PostprocessAttempt:
    return PostprocessAttempt(
        preset=preset,
        seconds=float(seconds),
        clean_point_count=int(len(result.clean_points_enu_m)),
        removed_point_count=int(len(result.removed_points_enu_m)),
        removal_ratio=float(result.quality.removal_ratio),
        quality_guards_passed=bool(result.quality.passed),
        quality_penalty=float(_quality_penalty(result)),
    )


def execute_postprocess(
    points_enu_m: np.ndarray,
    colors_rgb: np.ndarray,
    metadata: Mapping[str, Any] | object | None,
    trajectory_enu_m: np.ndarray | None,
    config: PostprocessConfig,
    *,
    voxel_size_m: float,
    config_overrides: Mapping[str, Any] | object | None = None,
    neighbor_backend: str = "auto",
    ground_backend: str = "auto",
    auto_fallback: bool = True,
    postprocess_only: bool = False,
    raw_build_depth_edge: Mapping[str, Any] | None = None,
) -> PostprocessExecution:
    """Run the requested preset and at most one documented fallback."""

    started = time.perf_counter()
    first_result = run_postprocess(
        points_enu_m,
        colors_rgb,
        metadata,
        trajectory_enu_m,
        config,
        neighbor_backend=neighbor_backend,
        ground_backend=ground_backend,
    )
    first_seconds = time.perf_counter() - started
    attempts = [_attempt_summary(config.preset, first_result, first_seconds)]
    fallback_name = (
        fallback_preset_for_quality(first_result.quality, config.preset)
        if auto_fallback and config.enabled
        else None
    )
    fallback_result: PostprocessResult | None = None
    fallback_selected = False
    if fallback_name is not None:
        compact_first = _compact_result(first_result)
        # Release clean/removed copies, tile neighbor arrays, and the surface grid
        # before constructing the fallback result.
        del first_result
        fallback_config = resolve_postprocess_config(
            fallback_name, voxel_size_m, config_overrides
        )
        started = time.perf_counter()
        fallback_result = run_postprocess(
            points_enu_m,
            colors_rgb,
            metadata,
            trajectory_enu_m,
            fallback_config,
            neighbor_backend=neighbor_backend,
            ground_backend=ground_backend,
        )
        attempts.append(
            _attempt_summary(
                fallback_name, fallback_result, time.perf_counter() - started
            )
        )
        fallback_selected = _fallback_is_better(
            compact_first, fallback_result, fallback_name
        )
        if fallback_selected:
            selected_result = fallback_result
            del compact_first
        else:
            del fallback_result
            fallback_result = None
            selected_result = _materialize_compact_result(compact_first)
            del compact_first
    else:
        selected_result = first_result
    report = copy.deepcopy(selected_result.report)
    report.setdefault("input", {})["voxel_size_m"] = float(voxel_size_m)
    report["input"]["raw_cloud_reused"] = bool(postprocess_only)
    report["input"]["depth_edge_stage"] = (
        "preserved_from_raw_build_not_reapplied"
        if postprocess_only
        else "applied_during_current_cloud_build"
    )
    if postprocess_only:
        report["input"]["raw_build_depth_edge"] = (
            dict(raw_build_depth_edge)
            if raw_build_depth_edge is not None
            else {"status": "not_recorded_in_existing_summary"}
        )
    else:
        report["input"]["raw_build_depth_edge"] = {
            "preset": config.preset,
            "enabled": config.depth_edge_filter,
            "radius_px": config.depth_edge_radius_px,
            "absolute_threshold_m": config.depth_edge_abs_m,
            "relative_ratio": config.depth_edge_rel_ratio,
            "minimum_valid_neighbors": config.depth_edge_min_valid_neighbors,
        }
    report["input"]["selected_3d_postprocess_preset"] = selected_result.config.preset
    if postprocess_only:
        report.setdefault("limitations", []).append(
            "Depth-edge settings belong to the RGB-D build stage and were not "
            "reapplied during this postprocess-only run."
        )
    elif fallback_selected:
        report.setdefault("limitations", []).append(
            "The fallback changed only 3-D postprocessing. Depth-edge filtering "
            f"was already built with the requested {config.preset!r} preset and "
            "was not reapplied."
        )
    report["selection"] = {
        "requested_preset": config.preset,
        "fallback_attempted": fallback_name is not None,
        "fallback_preset": fallback_name,
        "fallback_selected": fallback_selected,
        "maximum_attempt_count": 2,
        "attempts": [
            {
                "preset": attempt.preset,
                "seconds": float(attempt.seconds),
                "clean_point_count": attempt.clean_point_count,
                "removed_point_count": attempt.removed_point_count,
                "removal_ratio": attempt.removal_ratio,
                "quality_guards_passed": attempt.quality_guards_passed,
                "quality_penalty": attempt.quality_penalty,
            }
            for attempt in attempts
        ],
    }
    report["selected_result"] = selected_result.report["selected_result"]
    report["dependencies"]["pdal_result"] = (
        "available_not_run"
        if selected_result.dependencies.pdal_available
        else "not_run_unavailable"
    )
    selected_result = replace(selected_result, report=report)
    return PostprocessExecution(
        selected=selected_result,
        attempts=tuple(attempts),
        requested_preset=config.preset,
        fallback_preset=fallback_name,
        fallback_selected=fallback_selected,
        requested_ground_backend=ground_backend,
    )


def _reason_name_mapping() -> dict[int, str]:
    return {
        int(reason): reason.name.lower()
        for reason in RemovalReason
        if reason != RemovalReason.NONE
    }


def write_postprocess_execution(
    output_dir: Path,
    execution: PostprocessExecution,
    *,
    origin: LocalENU,
    trajectory_enu_m: np.ndarray | None,
    browser_max_points: int,
    output_options: PostprocessOutputOptions,
    base_cloud_summary: Mapping[str, Any] | None = None,
    parameter_context: Mapping[str, Any] | None = None,
    reuse_existing_raw_products: bool = False,
) -> tuple[dict[str, object], dict[str, Path]]:
    """Persist the selected result and return summary fields plus output paths."""

    output_dir = output_dir.expanduser().resolve()
    data_dir = output_dir / "data"
    result = execution.selected
    removed_reason_bits = result.removal_reason_bits[result.removed_indices]
    removed_primary_reason = result.primary_reason[result.removed_indices]
    products = write_processed_cloud_products(
        data_dir,
        raw_points=result.raw_points_enu_m,
        raw_colors=result.raw_colors_rgb,
        clean_points=result.clean_points_enu_m,
        clean_colors=result.clean_colors_rgb,
        removed_points=result.removed_points_enu_m,
        removed_diagnostic_colors=result.removed_diagnostic_colors_rgb,
        removed_original_colors=result.removed_original_colors_rgb,
        removal_reason_bits=removed_reason_bits,
        primary_reason=removed_primary_reason,
        origin=origin,
        postprocess_preset=result.config.preset,
        browser_max_points=browser_max_points,
        keep_raw_cloud=output_options.keep_raw_cloud,
        save_removed_cloud=output_options.save_removed_cloud,
        reuse_existing_raw_products=reuse_existing_raw_products,
    )

    report = copy.deepcopy(result.report)
    if (
        execution.requested_ground_backend in {"auto", "pdal"}
        and result.dependencies.pdal_available
        and output_options.keep_raw_cloud
    ):
        pdal_comparison = run_pdal_comparison(
            data_dir / "cloud_raw_enu.ply",
            output_dir / "diagnostics",
            result.config,
        )
    elif not result.dependencies.pdal_available:
        pdal_comparison = {"status": "not_run_unavailable", "selected": False}
    elif not output_options.keep_raw_cloud:
        pdal_comparison = {"status": "not_run_raw_not_kept", "selected": False}
    else:
        pdal_comparison = {"status": "not_requested", "selected": False}
    report["pdal_comparison"] = pdal_comparison
    report["dependencies"]["pdal_result"] = pdal_comparison["status"]
    result = replace(result, report=report)

    report_path = data_dir / "postprocess_report.json"
    stages_path = data_dir / "postprocess_stages.json"
    parameters_path = data_dir / "postprocess_parameters.json"
    atomic_json_dump(report_path, result.report)
    atomic_json_dump(stages_path, [stage.to_dict() for stage in result.stages])
    resolved_parameters = {
        "format_version": 1,
        "requested_preset": execution.requested_preset,
        "selected_preset": result.config.preset,
        "resolved_config": result.config.to_dict(),
        "neighbor_backend": result.neighbor_backend,
        "ground_backend": result.ground_backend,
        "output": {
            "keep_raw_cloud": output_options.keep_raw_cloud,
            "save_removed_cloud": output_options.save_removed_cloud,
            "write_postprocess_diagnostics": output_options.write_diagnostics,
            "write_debug_stages": output_options.write_debug_stages,
            "debug_stage_max_points": output_options.debug_stage_max_points,
        },
        "automatic_fallback": {
            "enabled": output_options.auto_fallback,
            "attempted": execution.fallback_preset is not None,
            "preset": execution.fallback_preset,
            "selected": execution.fallback_selected,
            "attempt_count": len(execution.attempts),
        },
        "build_stage_provenance": {
            "raw_cloud_reused": result.report["input"].get("raw_cloud_reused"),
            "depth_edge_stage": result.report["input"].get("depth_edge_stage"),
            "raw_build_depth_edge": result.report["input"].get(
                "raw_build_depth_edge"
            ),
        },
    }
    if parameter_context:
        resolved_parameters["cli_overrides"] = dict(parameter_context)
    atomic_json_dump(parameters_path, resolved_parameters)

    debug_stage_paths: dict[str, Path] = {}
    if output_options.write_debug_stages:
        debug_stage_paths = write_debug_stage_artifacts(
            output_dir,
            raw_points=result.raw_points_enu_m,
            raw_colors=result.raw_colors_rgb,
            removal_reason_bits=result.removal_reason_bits,
            keep_mask=result.keep_mask,
            stages=result.stages,
            origin=origin,
            trajectory_points=trajectory_enu_m,
            max_points=output_options.debug_stage_max_points,
            selected_preset=result.config.preset,
            neighbor_backend=result.neighbor_backend,
            ground_backend=result.ground_backend,
            report=result.report,
        )

    diagnostic_paths: dict[str, Path] = {}
    if output_options.write_diagnostics:
        diagnostic_paths = write_postprocess_diagnostics(
            output_dir / "diagnostics",
            result.raw_points_enu_m,
            result.clean_points_enu_m,
            result.removed_points_enu_m,
            removed_reason_bits,
            raw_colors=result.raw_colors_rgb,
            clean_colors=result.clean_colors_rgb,
            removed_colors=result.removed_diagnostic_colors_rgb,
            trajectory_points=trajectory_enu_m,
            primary_reason=removed_primary_reason,
            reason_names=_reason_name_mapping(),
            report=result.report,
            tile_size_m=result.config.tile_size_m,
        )

    cloud_summary: dict[str, object] = dict(base_cloud_summary or {})
    if not len(result.clean_points_enu_m):
        cloud_summary.pop("bbox_enu_min_m", None)
        cloud_summary.pop("bbox_enu_max_m", None)
    cloud_summary.update(products)
    cloud_summary.update(
        {
            "postprocess_report": "postprocess_report.json",
            "postprocess_stages": "postprocess_stages.json",
            "postprocess_parameters": "postprocess_parameters.json",
            "postprocess_preset": result.config.preset,
            "requested_postprocess_preset": execution.requested_preset,
            "neighbor_backend": result.neighbor_backend,
            "ground_backend": result.ground_backend,
            "quality_guards_passed": bool(result.quality.passed),
            "fallback_attempted": execution.fallback_preset is not None,
            "fallback_selected": execution.fallback_selected,
            "debug_stages": (
                "debug_stages/index.json"
                if output_options.write_debug_stages
                else None
            ),
            "debug_stage_max_points": (
                output_options.debug_stage_max_points
                if output_options.write_debug_stages
                else None
            ),
        }
    )
    paths: dict[str, Path] = {
        "raw_ply": data_dir / "cloud_raw_enu.ply",
        "clean_ply": data_dir / "cloud_clean_enu.ply",
        "removed_ply": data_dir / "cloud_removed_enu.ply",
        "report": report_path,
        "stages": stages_path,
        "parameters": parameters_path,
        **debug_stage_paths,
        **diagnostic_paths,
    }
    return cloud_summary, paths


def install_current_viewer(output_dir: Path, viewer_source_dir: Path) -> None:
    shutil.copytree(
        viewer_source_dir,
        output_dir.expanduser().resolve() / "viewer",
        dirs_exist_ok=True,
    )
