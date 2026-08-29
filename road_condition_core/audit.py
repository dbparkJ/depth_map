from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .models import AnalysisProducts, Defect


@dataclass(frozen=True)
class AuditWindow:
    label: str
    target_fraction: float
    route_length_m: float
    core_start_m: float
    core_end_m: float
    halo_start_m: float
    halo_end_m: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_audit_windows(
    route_length_m: float,
    *,
    core_length_m: float = 10.0,
    halo_m: float = 3.0,
) -> list[AuditWindow]:
    """Choose deterministic route windows without excluding short/control chunks."""

    length = float(route_length_m)
    core = float(core_length_m)
    halo = float(halo_m)
    if not np.isfinite(length) or length <= 0:
        raise ValueError("route_length_m must be finite and positive")
    if not np.isfinite(core) or core <= 0:
        raise ValueError("core_length_m must be finite and positive")
    if not np.isfinite(halo) or halo < 0:
        raise ValueError("halo_m must be finite and non-negative")

    if length <= core:
        targets = [("full", 0.5, 0.0, length)]
    elif length < 4.0 * core:
        start = max(0.0, min(length - core, length * 0.5 - core * 0.5))
        targets = [("central", 0.5, start, start + core)]
    else:
        targets = []
        for label, fraction in (("q25", 0.25), ("q50", 0.50), ("q75", 0.75)):
            start = max(0.0, min(length - core, length * fraction - core * 0.5))
            targets.append((label, fraction, start, start + core))

    return [
        AuditWindow(
            label=label,
            target_fraction=fraction,
            route_length_m=length,
            core_start_m=float(start),
            core_end_m=float(end),
            halo_start_m=max(0.0, float(start - halo)),
            halo_end_m=min(length, float(end + halo)),
        )
        for label, fraction, start, end in targets
    ]


def owned_defects(defects: list[Defect], window: AuditWindow) -> list[Defect]:
    include_end = np.isclose(window.core_end_m, window.route_length_m, atol=1e-9)
    return [
        defect
        for defect in defects
        if defect.chainage_m >= window.core_start_m - 1e-9
        and (
            defect.chainage_m <= window.core_end_m + 1e-9
            if include_end
            else defect.chainage_m < window.core_end_m - 1e-9
        )
    ]


def _metric_max(defects: list[Defect], defect_type: str, metric: str) -> float:
    return float(
        max(
            (
                abs(float(defect.metrics.get(metric, 0.0)))
                for defect in defects
                if defect.defect_type == defect_type
            ),
            default=0.0,
        )
    )


def summarize_audit_tile(
    products: AnalysisProducts,
    window: AuditWindow,
    *,
    alternative_potholes: list[Defect] | None = None,
    edge_margin_m: float = 0.75,
) -> dict[str, Any]:
    defects = owned_defects(products.defects, window)
    alternative = owned_defects(alternative_potholes or [], window)
    quality = products.summary.get("quality") or {}
    coverage = products.summary.get("coverage") or {}
    surface = products.surface
    supported = (
        surface.supported_mask
        if surface.supported_mask is not None
        else np.isfinite(surface.residual_m)
    )
    supported_residual = surface.residual_m[supported]
    supported_residual = supported_residual[np.isfinite(supported_residual)]
    usable_residual = surface.residual_m[surface.valid_mask]
    usable_residual = usable_residual[np.isfinite(usable_residual)]

    def quantiles(values: np.ndarray) -> dict[str, float | None]:
        if not len(values):
            return {name: None for name in ("p01", "p05", "p50", "p95", "p99")}
        result = np.quantile(values.astype(np.float64), [0.01, 0.05, 0.5, 0.95, 0.99])
        return {
            name: float(value)
            for name, value in zip(("p01", "p05", "p50", "p95", "p99"), result, strict=True)
        }

    counts = Counter(defect.defect_type for defect in defects)
    flags = Counter(flag for defect in defects for flag in defect.quality_flags)
    half_width = float(
        (products.summary.get("parameters") or {})
        .get("surface", {})
        .get("corridor_half_width_m", 3.5)
    )
    edge_start = max(0.0, half_width - float(edge_margin_m))
    edge_defects = [defect for defect in defects if abs(defect.lateral_offset_m) >= edge_start]
    lateral = np.asarray([abs(defect.lateral_offset_m) for defect in defects], dtype=float)
    return {
        "window": window.to_dict(),
        "quality": {
            "original_point_count": int(quality.get("original_point_count", 0)),
            "analyzed_point_count": int(quality.get("analyzed_point_count", 0)),
            "multiview_input_point_count": int(quality.get("multiview_input_point_count", 0)),
            "multiview_retained_point_count": int(quality.get("multiview_retained_point_count", 0)),
            "multiview_excluded_point_count": int(quality.get("multiview_excluded_point_count", 0)),
            "supported_surface_cell_count": int(quality.get("supported_surface_cell_count", 0)),
            "usable_surface_cell_count": int(quality.get("usable_surface_cell_count", 0)),
            "plausibility_excluded_cell_count": int(quality.get("plausibility_excluded_cell_count", 0)),
            "plausibility_excluded_low_cell_count": int(quality.get("plausibility_excluded_low_cell_count", 0)),
            "plausibility_excluded_high_cell_count": int(quality.get("plausibility_excluded_high_cell_count", 0)),
            "bump_boundary_guard_removed_component_count": int(
                quality.get(
                    "bump_plausibility_boundary_guard_removed_component_count", 0
                )
            ),
            "bump_boundary_guard_removed_candidate_cell_count": int(
                quality.get(
                    "bump_plausibility_boundary_guard_removed_candidate_cell_count",
                    0,
                )
            ),
            "median_points_per_valid_cell": float(quality.get("median_points_per_valid_cell", 0.0)),
            "manual_review_required": bool(quality.get("manual_review_required", False)),
        },
        "coverage": {
            "supported_coverage_ratio": float(coverage.get("supported_coverage_ratio", 0.0)),
            "valid_coverage_ratio": float(coverage.get("valid_coverage_ratio", 0.0)),
            "plausibility_excluded_supported_ratio": float(
                coverage.get("plausibility_excluded_supported_ratio", 0.0)
            ),
        },
        "residual_m": {
            "supported_quantiles": quantiles(supported_residual),
            "usable_quantiles": quantiles(usable_residual),
        },
        "defects": {
            "count": len(defects),
            "by_type": dict(sorted(counts.items())),
            "high_severity_count": sum(defect.severity == "high" for defect in defects),
            "edge_band_start_abs_t_m": edge_start,
            "edge_candidate_count": len(edge_defects),
            "edge_candidate_ratio": float(len(edge_defects) / len(defects)) if defects else 0.0,
            "absolute_lateral_offset_p50_m": float(np.quantile(lateral, 0.5)) if len(lateral) else None,
            "absolute_lateral_offset_p95_m": float(np.quantile(lateral, 0.95)) if len(lateral) else None,
            "max_pothole_depth_m": _metric_max(defects, "pothole", "max_depth_m"),
            "max_rutting_depth_m": _metric_max(defects, "rutting", "max_depth_m"),
            "max_bump_height_m": _metric_max(defects, "bump", "max_height_m"),
            "quality_flags": dict(sorted(flags.items())),
        },
        "literature_comparison": {
            "label": "FHWA_KICT_25mm_0.02m2_comparison_not_approved_default",
            "pothole_count": len(alternative),
            "count_delta_vs_current": len(alternative) - int(counts.get("pothole", 0)),
            "max_pothole_depth_m": _metric_max(alternative, "pothole", "max_depth_m"),
        },
    }
