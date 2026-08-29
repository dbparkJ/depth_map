from __future__ import annotations

from pathlib import Path

import pytest

from road_condition_core.maintenance_v2 import (
    calculate_maintenance_scenario_v2,
    load_maintenance_catalog,
)


CATALOG_ROOT = Path(__file__).parents[2] / "maintenance_catalogs"


def _defects() -> list[dict]:
    return [
        {
            "defect_id": "p-high",
            "defect_type": "pothole",
            "severity": "high",
            "metrics": {"area_m2": 0.10, "volume_m3": 0.002, "max_depth_m": 0.11},
        },
        {
            "defect_id": "r-low",
            "defect_type": "rutting",
            "severity": "low",
            "metrics": {"area_m2": 2.0, "max_depth_m": 0.02},
        },
    ]


def test_catalog_is_versioned_experimental_and_path_guarded() -> None:
    catalog = load_maintenance_catalog(CATALOG_ROOT, "internal-planning-v1")
    assert catalog.version == "1.0.0"
    assert catalog.approval_status == "experimental"
    assert catalog.source_document is None
    assert len(catalog.catalog_sha256) == 64
    assert all(value is None for value in catalog.unpriced_components.values())
    with pytest.raises(ValueError, match="invalid maintenance catalog id"):
        load_maintenance_catalog(CATALOG_ROOT, "../outside")


def test_scenario_applies_minimum_quantity_and_preserves_unknown_costs() -> None:
    catalog = load_maintenance_catalog(CATALOG_ROOT, "internal-planning-v1")
    result = calculate_maintenance_scenario_v2(
        {"scores": {"geometry_score": 60.0}},
        _defects(),
        catalog=catalog,
    )
    pothole = next(item for item in result["recommendations"] if item["defect_id"] == "p-high")
    assert pothole["measured_area_m2"] == 0.10
    assert pothole["billable_area_m2"] == 0.25
    assert pothole["minimum_quantity_status"] == "experimental_default"
    assert pothole["full_cost_krw"] is None
    costs = result["budget_screening"]
    assert costs["priced_total_krw"] > 0
    assert costs["full_total_krw"] is None
    assert costs["cost_completeness"] == "partial_known_components_only"
    assert costs["unpriced_components"] == [
        "equipment_relocation",
        "traffic_control",
        "waste_disposal",
    ]
    assert result["deterioration"]["annual_rate"] is None
    assert result["deterioration"]["status"] == "N/A_no_repeated_survey"
    assert result["score_projection"]["status"] == (
        "uncalibrated_planning_estimate_not_prediction"
    )


def test_budget_screening_is_deterministic_and_never_exceeds_known_budget() -> None:
    catalog = load_maintenance_catalog(CATALOG_ROOT, "internal-planning-v1")
    result = calculate_maintenance_scenario_v2(
        {"scores": {"geometry_score": 60.0}},
        _defects(),
        catalog=catalog,
        budget_krw=550_000,
        comparison_budgets_krw=[500_000, 700_000],
    )
    screening = result["budget_screening"]
    assert screening["selected_defect_ids"] == ["p-high"]
    assert screening["deferred_defect_ids"] == ["r-low"]
    assert screening["priced_total_krw"] <= screening["budget_krw"]
    assert screening["method"] == "deterministic_greedy_risk_screening_not_optimization"
    assert [item["selected_count"] for item in result["scenario_comparison"]] == [0, 2]


def test_empty_include_types_selects_no_work() -> None:
    catalog = load_maintenance_catalog(CATALOG_ROOT, "internal-planning-v1")
    result = calculate_maintenance_scenario_v2(
        {"scores": {"geometry_score": 60.0}},
        _defects(),
        catalog=catalog,
        include_types=set(),
    )
    assert result["recommendations"] == []
    assert result["budget_screening"]["priced_total_krw"] == 0


def test_2026_official_reference_catalog_prices_only_known_components() -> None:
    catalog = load_maintenance_catalog(CATALOG_ROOT, "kr-molit-2026h2-reference")
    assert catalog.version == "2026.2.0"
    assert catalog.approval_status == "official_reference_experimental_assembly"
    assert len(catalog.references) == 2
    assert all(
        item["unit_rate_use"] == "prohibited_mixed_scope_total"
        for item in catalog.public_project_context
    )
    result = calculate_maintenance_scenario_v2(
        {"scores": {"geometry_score": 60.0}},
        [
            *_defects(),
            {
                "defect_id": "b-unpriced",
                "defect_type": "bump",
                "severity": "medium",
                "metrics": {"area_m2": 0.3, "max_height_m": 0.06},
            },
        ],
        catalog=catalog,
    )
    pothole = next(
        item for item in result["recommendations"] if item["defect_id"] == "p-high"
    )
    assert pothole["priced_cost_krw"] == 876
    assert pothole["full_cost_krw"] is None
    assert pothole["source_item_codes"] == [
        "DC221.10000",
        "LC211.00000",
        "LC401.00750",
    ]
    screening = result["budget_screening"]
    assert screening["unpriced_candidate_count"] == 1
    assert screening["unpriced_defect_ids"] == ["b-unpriced"]
    assert "asphalt_mixture_material" in screening["unpriced_components"]
    report = result["budget_report"]
    assert report["price_date"] == "2026-05-08"
    assert report["amount_range_krw"]["priced_known_components_lower_bound"] == (
        screening["priced_total_krw"]
    )
    assert report["amount_range_krw"]["full_project_estimate"] is None
    assert len(report["public_project_context"]) == 2
    assert len(report["procurement_checklist"]) >= 5
