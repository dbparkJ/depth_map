from __future__ import annotations

from typing import Any, Mapping


DEFAULT_UNIT_PRICES = {
    "pothole_patch_krw_per_m2": 85_000.0,
    "pothole_fill_krw_per_m3": 420_000.0,
    "rut_overlay_krw_per_m2": 52_000.0,
    "bump_grinding_krw_per_m2": 38_000.0,
    "mobilization_krw": 500_000.0,
}


def calculate_maintenance_scenario(
    summary: Mapping[str, Any],
    defects: list[Mapping[str, Any]],
    *,
    unit_prices: Mapping[str, float] | None = None,
    include_types: set[str] | None = None,
    rainfall_mm: float = 30.0,
) -> dict[str, Any]:
    prices = dict(DEFAULT_UNIT_PRICES)
    if unit_prices:
        unknown = set(unit_prices) - set(prices)
        if unknown:
            raise ValueError(f"unknown unit price keys: {', '.join(sorted(unknown))}")
        for key, value in unit_prices.items():
            numeric = float(value)
            if numeric < 0:
                raise ValueError(f"unit price {key} must be non-negative")
            prices[key] = numeric
    selected = include_types or {"pothole", "rutting", "bump"}
    pothole_area = 0.0
    pothole_volume = 0.0
    rut_area = 0.0
    bump_area = 0.0
    selected_count = 0
    for defect in defects:
        defect_type = str(defect.get("defect_type"))
        if defect_type not in selected:
            continue
        selected_count += 1
        metrics = defect.get("metrics") or {}
        if defect_type == "pothole":
            pothole_area += float(metrics.get("area_m2", 0.0))
            pothole_volume += float(metrics.get("volume_m3", 0.0))
        elif defect_type == "rutting":
            rut_area += float(metrics.get("area_m2", 0.0))
        elif defect_type == "bump":
            bump_area += float(metrics.get("area_m2", 0.0))

    pothole_cost = max(
        pothole_area * prices["pothole_patch_krw_per_m2"],
        pothole_volume * prices["pothole_fill_krw_per_m3"],
    )
    rut_cost = rut_area * prices["rut_overlay_krw_per_m2"]
    bump_cost = bump_area * prices["bump_grinding_krw_per_m2"]
    direct_cost = pothole_cost + rut_cost + bump_cost
    mobilization = prices["mobilization_krw"] if selected_count else 0.0
    total_cost = direct_cost + mobilization

    score = float((summary.get("scores") or {}).get("geometry_score", 0.0))
    recoverable_penalty = max(0.0, 100.0 - score)
    treated_types = len(selected & {"pothole", "rutting", "bump"})
    expected_recovery = recoverable_penalty * min(0.90, 0.25 * treated_types + 0.15)
    expected_score = min(100.0, score + expected_recovery)

    # Pothole volume is reused as a deliberately simple upper-bound storage proxy.
    # It is not a hydrologic or drainage-network model.
    rainfall_volume_m3 = max(0.0, float(rainfall_mm)) / 1000.0 * float(
        (summary.get("coverage") or {}).get("valid_surface_area_m2", 0.0)
    )
    depression_storage_m3 = pothole_volume
    return {
        "format_version": 1,
        "selected_defect_types": sorted(selected),
        "selected_defect_count": selected_count,
        "quantities": {
            "pothole_area_m2": pothole_area,
            "pothole_volume_m3": pothole_volume,
            "rut_overlay_area_m2": rut_area,
            "bump_grinding_area_m2": bump_area,
        },
        "costs_krw": {
            "pothole": round(pothole_cost),
            "rutting": round(rut_cost),
            "bump": round(bump_cost),
            "mobilization": round(mobilization),
            "total": round(total_cost),
        },
        "score_projection": {
            "current_geometry_score": score,
            "expected_post_maintenance_score": expected_score,
            "note": "Planning estimate only; the recovery model is not calibrated to a pavement standard.",
        },
        "rainfall_screening": {
            "rainfall_mm": float(rainfall_mm),
            "rainfall_volume_over_valid_surface_m3": rainfall_volume_m3,
            "detected_depression_storage_proxy_m3": depression_storage_m3,
            "note": "Screening proxy only. Drain inlets, pipe capacity, runoff, and vertical datum are not modeled.",
        },
        "unit_prices": prices,
    }
