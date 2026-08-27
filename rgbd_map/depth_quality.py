from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import cv2
import numpy as np


FarDepthPolicy = Literal["off", "fixed", "adaptive"]
ConfidenceOrder = Literal["lower-is-better", "higher-is-better"]
DepthEdgeDomain = Literal["depth", "inverse-depth"]


@dataclass(frozen=True)
class DepthQualityPolicy:
    """Projection-time depth validity rules shared by VO and dense fusion."""

    min_depth_m: float
    max_depth_m: float
    far_depth_policy: FarDepthPolicy = "off"
    far_depth_soft_start_m: float = 20.0
    far_depth_hard_m: float = 28.8
    confidence_threshold: float | None = None
    confidence_order: ConfidenceOrder = "higher-is-better"
    edge_enabled: bool = False
    edge_domain: DepthEdgeDomain = "depth"
    edge_radius_px: int = 1
    edge_abs_m: float = 0.18
    edge_rel_ratio: float = 0.03
    edge_min_valid_neighbors: int = 4
    invalid_boundary_erosion_px: int = 0
    far_speckle_max_pixels: int = 0
    adaptive_histogram_bin_m: float = 0.05
    adaptive_peak_ratio: float = 8.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DepthQualityResult:
    valid_mask: np.ndarray
    base_valid_mask: np.ndarray
    far_risk_mask: np.ndarray
    local_consistency_mask: np.ndarray
    resolved_hard_depth_m: float | None
    detected_far_peaks_m: tuple[float, ...]
    report: dict[str, Any]


def validate_depth_quality_policy(policy: DepthQualityPolicy) -> None:
    numeric_positive = (
        "min_depth_m",
        "max_depth_m",
        "adaptive_histogram_bin_m",
        "adaptive_peak_ratio",
    )
    for name in numeric_positive:
        value = float(getattr(policy, name))
        if not np.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be a finite positive value")
    if policy.min_depth_m >= policy.max_depth_m:
        raise ValueError("depth range must satisfy min_depth_m < max_depth_m")
    if policy.far_depth_policy not in {"off", "fixed", "adaptive"}:
        raise ValueError("far_depth_policy must be off, fixed, or adaptive")
    if policy.far_depth_policy != "off":
        if policy.far_depth_soft_start_m >= policy.max_depth_m:
            raise ValueError("far_depth_soft_start_m must be below max_depth_m")
        if policy.far_depth_hard_m <= policy.far_depth_soft_start_m:
            raise ValueError("far_depth_hard_m must exceed far_depth_soft_start_m")
    if policy.confidence_order not in {"lower-is-better", "higher-is-better"}:
        raise ValueError("invalid confidence_order")
    if policy.edge_domain not in {"depth", "inverse-depth"}:
        raise ValueError("edge_domain must be depth or inverse-depth")
    if policy.edge_radius_px < 1:
        raise ValueError("edge_radius_px must be at least 1")
    if policy.edge_enabled and (
        not np.isfinite(policy.edge_abs_m) or policy.edge_abs_m <= 0.0
    ):
        raise ValueError("edge_abs_m must be finite and positive when enabled")
    if policy.edge_rel_ratio < 0.0 or not np.isfinite(policy.edge_rel_ratio):
        raise ValueError("edge_rel_ratio must be finite and non-negative")
    available = (2 * policy.edge_radius_px + 1) ** 2 - 1
    if policy.edge_enabled and not 1 <= policy.edge_min_valid_neighbors <= available:
        raise ValueError("edge_min_valid_neighbors exceeds the neighborhood")
    if policy.invalid_boundary_erosion_px < 0:
        raise ValueError("invalid_boundary_erosion_px must be non-negative")
    if policy.far_speckle_max_pixels < 0:
        raise ValueError("far_speckle_max_pixels must be non-negative")
    if policy.confidence_threshold is not None and not np.isfinite(
        float(policy.confidence_threshold)
    ):
        raise ValueError("confidence_threshold must be finite when supplied")


def _local_consistency_mask(
    depth_m: np.ndarray,
    valid_mask: np.ndarray,
    *,
    radius_px: int,
    abs_threshold_m: float,
    rel_ratio: float,
    min_valid_neighbors: int,
    domain: DepthEdgeDomain,
) -> np.ndarray:
    """Range-adaptive local consistency without treating invalid pixels as zero."""

    depth = np.asarray(depth_m, dtype=np.float64)
    valid = np.asarray(valid_mask, dtype=bool)
    if depth.ndim != 2 or valid.shape != depth.shape:
        raise ValueError("depth_m and valid_mask must be matching 2D arrays")
    radius = int(radius_px)
    offsets = [
        (dy, dx)
        for dy in range(-radius, radius + 1)
        for dx in range(-radius, radius + 1)
        if dy != 0 or dx != 0
    ]
    height, width = depth.shape
    result = np.zeros_like(valid)
    if not height or not width:
        return result

    if domain == "inverse-depth":
        comparison = np.zeros_like(depth)
        np.divide(1.0, depth, out=comparison, where=valid & (depth > 0.0))
    else:
        comparison = depth
    padded_values = np.pad(comparison, radius, mode="constant")
    padded_depth = np.pad(depth, radius, mode="constant")
    padded_valid = np.pad(valid, radius, mode="constant", constant_values=False)
    available = len(offsets)
    rows_per_chunk = max(1, min(height, 1_000_000 // max(1, width * available)))
    for row0 in range(0, height, rows_per_chunk):
        row1 = min(height, row0 + rows_per_chunk)
        values = np.stack(
            [
                padded_values[
                    row0 + radius + dy : row1 + radius + dy,
                    radius + dx : radius + width + dx,
                ]
                for dy, dx in offsets
            ],
            axis=0,
        )
        depths = np.stack(
            [
                padded_depth[
                    row0 + radius + dy : row1 + radius + dy,
                    radius + dx : radius + width + dx,
                ]
                for dy, dx in offsets
            ],
            axis=0,
        )
        neighbors_valid = np.stack(
            [
                padded_valid[
                    row0 + radius + dy : row1 + radius + dy,
                    radius + dx : radius + width + dx,
                ]
                for dy, dx in offsets
            ],
            axis=0,
        )
        counts = np.count_nonzero(neighbors_valid, axis=0)
        values[~neighbors_valid] = np.inf
        values.sort(axis=0)
        depth_values = depths.copy()
        depth_values[~neighbors_valid] = np.inf
        depth_values.sort(axis=0)
        shape = (1, row1 - row0, width)
        lo = np.clip((counts - 1) // 2, 0, available - 1)
        hi = np.clip(counts // 2, 0, available - 1)
        median = 0.5 * (
            np.take_along_axis(values, lo.reshape(shape), axis=0)[0]
            + np.take_along_axis(values, hi.reshape(shape), axis=0)[0]
        )
        current_depth = depth[row0:row1]
        current_value = comparison[row0:row1]
        depth_tolerance = np.maximum(abs_threshold_m, current_depth * rel_ratio)
        if domain == "inverse-depth":
            tolerance = depth_tolerance / np.maximum(current_depth * current_depth, 1e-12)
        else:
            tolerance = depth_tolerance

        # A robust central span catches discontinuities without letting one noisy
        # neighbor erase a sloped road, pole, branch, or guardrail pixel.
        low_span_index = np.clip(
            np.floor(0.10 * np.maximum(counts - 1, 0)).astype(np.int64),
            0,
            available - 1,
        )
        high_span_index = np.clip(
            np.ceil(0.90 * np.maximum(counts - 1, 0)).astype(np.int64),
            0,
            available - 1,
        )
        depth_low = np.take_along_axis(
            depth_values, low_span_index.reshape(shape), axis=0
        )[0]
        depth_high = np.take_along_axis(
            depth_values, high_span_index.reshape(shape), axis=0
        )[0]
        with np.errstate(invalid="ignore"):
            depth_span = depth_high - depth_low
        depth_span[counts == 0] = 0.0
        enough = counts >= int(min_valid_neighbors)
        consistent = (
            np.abs(median - current_value) <= tolerance
        ) & (depth_span <= depth_tolerance)
        result[row0:row1] = valid[row0:row1] & enough & consistent
    return result


def depth_local_consistency_mask(
    depth_mm: np.ndarray,
    valid_mask: np.ndarray | None = None,
    *,
    radius_px: int = 1,
    abs_threshold_m: float = 0.18,
    rel_ratio: float = 0.03,
    min_valid_neighbors: int = 4,
    domain: DepthEdgeDomain = "depth",
) -> np.ndarray:
    depth = np.asarray(depth_mm)
    if depth.ndim != 2 or not np.issubdtype(depth.dtype, np.number):
        raise ValueError("depth_mm must be a numeric 2D array")
    finite = np.isfinite(depth)
    if valid_mask is None:
        valid = finite & (depth != 0) & (depth != 65535)
    else:
        supplied = np.asarray(valid_mask)
        if supplied.shape != depth.shape:
            raise ValueError("valid_mask must match depth_mm shape")
        valid = supplied.astype(bool, copy=False) & finite & (depth != 0) & (depth != 65535)
    policy = DepthQualityPolicy(
        min_depth_m=1e-6,
        max_depth_m=max(1.0, float(np.nanmax(depth)) / 1000.0 + 1.0),
        edge_enabled=True,
        edge_domain=domain,
        edge_radius_px=int(radius_px),
        edge_abs_m=float(abs_threshold_m),
        edge_rel_ratio=float(rel_ratio),
        edge_min_valid_neighbors=int(min_valid_neighbors),
    )
    validate_depth_quality_policy(policy)
    return _local_consistency_mask(
        depth.astype(np.float64, copy=False) / 1000.0,
        valid,
        radius_px=policy.edge_radius_px,
        abs_threshold_m=policy.edge_abs_m,
        rel_ratio=policy.edge_rel_ratio,
        min_valid_neighbors=policy.edge_min_valid_neighbors,
        domain=policy.edge_domain,
    )


def detect_far_depth_peaks(
    depth_m: np.ndarray,
    valid_mask: np.ndarray,
    *,
    soft_start_m: float,
    max_depth_m: float,
    bin_size_m: float = 0.05,
    peak_ratio: float = 8.0,
) -> tuple[float, ...]:
    values = np.asarray(depth_m, dtype=np.float64)[np.asarray(valid_mask, dtype=bool)]
    values = values[(values >= soft_start_m) & (values <= max_depth_m)]
    if len(values) < 64:
        return ()
    edges = np.arange(soft_start_m, max_depth_m + bin_size_m * 1.01, bin_size_m)
    if len(edges) < 3:
        return ()
    hist, edges = np.histogram(values, bins=edges)
    positive = hist[hist > 0]
    if len(positive) == 0:
        return ()
    if len(positive) <= 2:
        background = 1.0
    else:
        background = max(
            1.0, float(np.median(np.sort(positive)[: max(1, len(positive) - 1)]))
        )
    required = max(32.0, background * float(peak_ratio))
    candidates = np.flatnonzero(hist >= required)
    # Require a local spike; broad real surfaces populate adjacent bins too.
    peaks: list[float] = []
    for index in candidates:
        left = int(hist[index - 1]) if index else 0
        right = int(hist[index + 1]) if index + 1 < len(hist) else 0
        if hist[index] >= max(left, right) * 2.0:
            peaks.append(float(0.5 * (edges[index] + edges[index + 1])))
    return tuple(peaks)


def _remove_far_speckles(
    valid: np.ndarray,
    depth_m: np.ndarray,
    soft_start_m: float,
    max_pixels: int,
) -> tuple[np.ndarray, int]:
    if max_pixels <= 0 or not np.any(valid):
        return valid, 0
    labels_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        valid.astype(np.uint8), connectivity=8
    )
    keep = valid.copy()
    removed = 0
    for label in range(1, labels_count):
        size = int(stats[label, cv2.CC_STAT_AREA])
        if size > max_pixels:
            continue
        component = labels == label
        if float(np.median(depth_m[component])) < soft_start_m:
            continue
        removed += int(np.count_nonzero(component))
        keep[component] = False
    return keep, removed


def _split_far_peaks_by_spatial_coherence(
    depth_m: np.ndarray,
    peaks: tuple[float, ...],
    bin_size_m: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Separate fragmented quantization spikes from coherent distant planes."""

    suspicious: list[float] = []
    coherent: list[float] = []
    half_width = max(bin_size_m * 0.55, 1e-6)
    for peak in peaks:
        mask = np.abs(depth_m - peak) <= half_width
        count = int(np.count_nonzero(mask))
        if count < 32:
            continue
        labels_count, _labels, stats, _ = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), connectivity=8
        )
        largest = (
            int(np.max(stats[1:, cv2.CC_STAT_AREA])) if labels_count > 1 else 0
        )
        # A dominant connected patch is consistent with a real planar surface.
        # Fragmented repeated bins are treated as the quantization-risk candidate.
        if largest / count >= 0.60:
            coherent.append(peak)
        else:
            suspicious.append(peak)
    return tuple(suspicious), tuple(coherent)


def evaluate_depth_quality(
    depth_mm: np.ndarray,
    policy: DepthQualityPolicy,
    confidence: np.ndarray | None = None,
) -> DepthQualityResult:
    """Resolve and apply a deterministic per-frame depth policy."""

    validate_depth_quality_policy(policy)
    depth_raw = np.asarray(depth_mm)
    if depth_raw.ndim != 2 or not np.issubdtype(depth_raw.dtype, np.number):
        raise ValueError("depth_mm must be a numeric 2D array")
    depth_m = depth_raw.astype(np.float64, copy=False) / 1000.0
    finite = np.isfinite(depth_m)
    base = (
        finite
        & (depth_raw != 0)
        & (depth_raw != 65535)
        & (depth_m >= policy.min_depth_m)
        & (depth_m <= policy.max_depth_m)
    )
    base_count = int(np.count_nonzero(base))
    confidence_status = "disabled"
    confidence_removed = 0
    valid = base.copy()
    if policy.confidence_threshold is not None:
        if confidence is None:
            confidence_status = "missing_fallback"
        else:
            confidence_array = np.asarray(confidence)
            if confidence_array.shape[:2] != depth_raw.shape:
                raise ValueError("confidence map shape must match depth image")
            if confidence_array.ndim != 2:
                raise ValueError("confidence map must be a single-channel image")
            if not np.issubdtype(confidence_array.dtype, np.number):
                raise ValueError("confidence map must be numeric")
            if policy.confidence_order == "higher-is-better":
                confidence_keep = confidence_array >= policy.confidence_threshold
            else:
                confidence_keep = confidence_array <= policy.confidence_threshold
            confidence_removed = int(np.count_nonzero(valid & ~confidence_keep))
            valid &= confidence_keep
            confidence_status = "applied"

    peaks: tuple[float, ...] = ()
    coherent_peaks: tuple[float, ...] = ()
    resolved_hard: float | None = None
    if policy.far_depth_policy == "fixed":
        resolved_hard = min(policy.max_depth_m, policy.far_depth_hard_m)
    elif policy.far_depth_policy == "adaptive":
        histogram_peaks = detect_far_depth_peaks(
            depth_m,
            valid,
            soft_start_m=policy.far_depth_soft_start_m,
            max_depth_m=policy.max_depth_m,
            bin_size_m=policy.adaptive_histogram_bin_m,
            peak_ratio=policy.adaptive_peak_ratio,
        )
        peaks, coherent_peaks = _split_far_peaks_by_spatial_coherence(
            depth_m,
            histogram_peaks,
            policy.adaptive_histogram_bin_m,
        )
        if peaks:
            resolved_hard = max(
                policy.far_depth_soft_start_m,
                min(policy.far_depth_hard_m, min(peaks) - policy.adaptive_histogram_bin_m),
            )
    far_risk = valid & (depth_m >= policy.far_depth_soft_start_m)
    hard_removed = 0
    if resolved_hard is not None:
        hard_reject = valid & (depth_m >= resolved_hard)
        hard_removed = int(np.count_nonzero(hard_reject))
        valid &= ~hard_reject

    local = valid.copy()
    local_removed = 0
    if policy.edge_enabled and np.any(valid):
        local = _local_consistency_mask(
            depth_m,
            valid,
            radius_px=policy.edge_radius_px,
            abs_threshold_m=policy.edge_abs_m,
            rel_ratio=policy.edge_rel_ratio,
            min_valid_neighbors=policy.edge_min_valid_neighbors,
            domain=policy.edge_domain,
        )
        local_removed = int(np.count_nonzero(valid & ~local))
        valid &= local

    erosion_removed = 0
    if policy.invalid_boundary_erosion_px > 0 and np.any(valid):
        radius = int(policy.invalid_boundary_erosion_px)
        kernel = np.ones((2 * radius + 1, 2 * radius + 1), dtype=np.uint8)
        eroded = cv2.erode(base.astype(np.uint8), kernel, iterations=1).astype(bool)
        # Keep near-range thin structures; boundary erosion is only a far-risk cue.
        erosion_reject = valid & far_risk & ~eroded
        erosion_removed = int(np.count_nonzero(erosion_reject))
        valid &= ~erosion_reject

    valid, speckle_removed = _remove_far_speckles(
        valid,
        depth_m,
        policy.far_depth_soft_start_m,
        int(policy.far_speckle_max_pixels),
    )
    values = depth_m[base]
    quantiles = (
        {str(q): float(np.quantile(values, q)) for q in (0.5, 0.9, 0.95, 0.99)}
        if len(values)
        else {}
    )
    report: dict[str, Any] = {
        "pixel_count": int(depth_raw.size),
        "base_valid_count": base_count,
        "invalid_count": int(depth_raw.size - base_count),
        "confidence_status": confidence_status,
        "confidence_removed_count": confidence_removed,
        "far_depth_policy": policy.far_depth_policy,
        "far_risk_count": int(np.count_nonzero(far_risk)),
        "detected_far_peaks_m": list(peaks),
        "coherent_far_surface_peaks_m": list(coherent_peaks),
        "resolved_hard_depth_m": resolved_hard,
        "far_hard_removed_count": hard_removed,
        "local_consistency_removed_count": local_removed,
        "invalid_boundary_removed_count": erosion_removed,
        "far_speckle_removed_count": speckle_removed,
        "final_valid_count": int(np.count_nonzero(valid)),
        "depth_quantiles_m": quantiles,
    }
    return DepthQualityResult(
        valid_mask=valid,
        base_valid_mask=base,
        far_risk_mask=far_risk,
        local_consistency_mask=local,
        resolved_hard_depth_m=resolved_hard,
        detected_far_peaks_m=peaks,
        report=report,
    )
