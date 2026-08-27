from __future__ import annotations

import csv
import html
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


DEFAULT_DISTANCE_BANDS_M = (0.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0)


def _quadratic_design(points_xy: np.ndarray) -> np.ndarray:
    x = points_xy[:, 0]
    y = points_xy[:, 1]
    return np.column_stack((np.ones(len(x)), x, y, x * x, x * y, y * y))


def _robust_quadratic_residual(points_xyz: np.ndarray) -> np.ndarray:
    points = np.asarray(points_xyz, dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_xyz must have shape (N, 3)")
    points = points[np.all(np.isfinite(points), axis=1)]
    if len(points) < 30:
        raise ValueError("flat-surface noise analysis requires at least 30 finite points")
    center = np.median(points[:, :2], axis=0)
    scale = np.ptp(points[:, :2], axis=0)
    scale[scale < 1e-6] = 1.0
    normalized_xy = (points[:, :2] - center) / scale
    design = _quadratic_design(normalized_xy)
    keep = np.ones(len(points), dtype=bool)
    coefficients = np.zeros(6, dtype=np.float64)
    for _ in range(6):
        if np.count_nonzero(keep) < 12:
            break
        coefficients, *_ = np.linalg.lstsq(design[keep], points[keep, 2], rcond=None)
        residual = points[:, 2] - design @ coefficients
        median = float(np.median(residual[keep]))
        mad = float(np.median(np.abs(residual[keep] - median)))
        gate = max(4.5 * 1.4826 * mad, 1e-6)
        next_keep = np.abs(residual - median) <= gate
        if np.array_equal(next_keep, keep):
            break
        keep = next_keep
    return points[:, 2] - design @ coefficients


def _noise_metrics(values: np.ndarray) -> dict[str, float | int | None]:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if len(finite) == 0:
        return {"count": 0, "median_m": None, "mad_m": None, "rmse_m": None, "p95_abs_m": None}
    median = float(np.median(finite))
    centered = finite - median
    return {
        "count": int(len(finite)),
        "median_m": median,
        "mad_m": float(np.median(np.abs(centered))),
        "rmse_m": float(np.sqrt(np.mean(centered * centered))),
        "p95_abs_m": float(np.percentile(np.abs(centered), 95)),
    }


def analyze_flat_surface_noise(
    points_xyz: np.ndarray,
    distance_m: np.ndarray,
    *,
    distance_bands_m: Sequence[float] = DEFAULT_DISTANCE_BANDS_M,
) -> dict[str, Any]:
    points = np.asarray(points_xyz, dtype=np.float64)
    distances = np.asarray(distance_m, dtype=np.float64)
    if points.ndim != 2 or points.shape[1:] != (3,):
        raise ValueError("points_xyz must have shape (N, 3)")
    if distances.shape != (len(points),):
        raise ValueError("distance_m must align with points_xyz")
    edges = np.asarray(distance_bands_m, dtype=np.float64)
    if len(edges) < 2 or np.any(~np.isfinite(edges)) or np.any(np.diff(edges) <= 0):
        raise ValueError("distance bands must be finite and strictly increasing")
    finite = np.all(np.isfinite(points), axis=1) & np.isfinite(distances) & (distances >= 0.0)
    residual = _robust_quadratic_residual(points[finite])
    finite_distances = distances[finite]
    bands = []
    for lower, upper in zip(edges[:-1], edges[1:], strict=True):
        selected = (finite_distances >= lower) & (finite_distances < upper)
        bands.append(
            {
                "min_distance_m": float(lower),
                "max_distance_m_exclusive": float(upper),
                **_noise_metrics(residual[selected]),
            }
        )
    return {
        "format_version": 1,
        "reference_model": "iteratively_trimmed_quadratic_xy",
        "distance_basis": "per-point sensor distance supplied by mapping metadata",
        "global": _noise_metrics(residual),
        "distance_bands": bands,
    }


def recommend_thresholds(
    noise_report: Mapping[str, Any],
    *,
    calibration_status: str,
) -> dict[str, Any]:
    global_metrics = noise_report.get("global")
    if not isinstance(global_metrics, Mapping):
        raise ValueError("noise report does not contain global metrics")
    mad = global_metrics.get("mad_m")
    rmse = global_metrics.get("rmse_m")
    if mad is None or rmse is None:
        pothole = None
        rut = None
        bump = None
    else:
        robust_sigma = 1.4826 * float(mad)
        pothole = max(6.0 * robust_sigma, 3.0 * float(rmse))
        rut = max(4.0 * robust_sigma, 2.5 * float(rmse))
        bump = pothole
    measured = calibration_status == "measured"
    return {
        "format_version": 1,
        "method": "noise_scaled_experimental_v1",
        "calibration_status": calibration_status,
        "approval_status": "candidate" if measured else "manual_review_required",
        "pothole_min_depth_m": pothole,
        "rut_min_depth_m": rut,
        "bump_min_height_m": bump,
        "multipliers": {
            "pothole": "max(6 * 1.4826 * MAD, 3 * RMSE)",
            "rutting": "max(4 * 1.4826 * MAD, 2.5 * RMSE)",
        },
        "limitations": [
            "These are experimental candidates, not standard or automatically approved thresholds.",
            "Measured pothole/rut holdout comparison is required before production use.",
        ],
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _ensure_ground_truth_csv(path: Path, fieldnames: Sequence[str]) -> None:
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()


def write_calibration_bundle(
    output_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    noise_report: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    status = str(manifest.get("calibration_status", "unknown"))
    if noise_report is None:
        noise_report = {
            "format_version": 1,
            "status": "not_measured",
            "global": {"count": 0, "median_m": None, "mad_m": None, "rmse_m": None, "p95_abs_m": None},
            "distance_bands": [],
        }
    recommendation = recommend_thresholds(noise_report, calibration_status=status)
    _atomic_json(output / "manifest.json", dict(manifest))
    _atomic_json(output / "flat_surface_noise.json", dict(noise_report))
    _atomic_json(output / "threshold_recommendation.json", recommendation)
    _ensure_ground_truth_csv(
        output / "pothole_ground_truth.csv",
        (
            "sample_id",
            "chainage_m",
            "measured_max_depth_m",
            "measured_major_axis_m",
            "measured_minor_axis_m",
            "measured_area_m2",
            "measured_volume_m3",
            "automatic_max_depth_m",
            "absolute_depth_error_m",
            "condition",
            "notes",
        ),
    )
    _ensure_ground_truth_csv(
        output / "rut_ground_truth.csv",
        (
            "sample_id",
            "chainage_start_m",
            "chainage_end_m",
            "side",
            "measured_max_depth_m",
            "automatic_max_depth_m",
            "absolute_depth_error_m",
            "condition",
            "notes",
        ),
    )
    report_html = """<!doctype html><html><head><meta charset=\"utf-8\"><title>Calibration report</title>
<style>body{{font:15px system-ui;margin:2rem;max-width:960px}}pre{{background:#f4f6f8;padding:1rem;overflow:auto}}</style></head><body>
<h1>Road-condition calibration report</h1>
<p>Status: <strong>{status}</strong>; approval: <strong>{approval}</strong></p>
<p>Unknown or estimated calibration requires manual review. Thresholds are experimental geometry candidates.</p>
<h2>Flat surface noise</h2><pre>{noise}</pre>
<h2>Threshold recommendation</h2><pre>{recommendation}</pre>
</body></html>""".format(
        status=html.escape(status),
        approval=html.escape(str(recommendation["approval_status"])),
        noise=html.escape(json.dumps(noise_report, ensure_ascii=False, indent=2)),
        recommendation=html.escape(json.dumps(recommendation, ensure_ascii=False, indent=2)),
    )
    (output / "calibration_report.html").write_text(report_html, encoding="utf-8")
    return {
        "manifest": str(output / "manifest.json"),
        "flat_surface_noise": str(output / "flat_surface_noise.json"),
        "pothole_ground_truth": str(output / "pothole_ground_truth.csv"),
        "rut_ground_truth": str(output / "rut_ground_truth.csv"),
        "threshold_recommendation": str(output / "threshold_recommendation.json"),
        "calibration_report": str(output / "calibration_report.html"),
    }
