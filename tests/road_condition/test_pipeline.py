from __future__ import annotations

import json

import pytest

from road_condition_core.config import AnalysisConfig
from road_condition_core.pipeline import analyze_points, write_analysis_products
from road_condition_core.synthetic import generate_synthetic_scene


def test_mixed_scene_detects_geometry_defects() -> None:
    scene = generate_synthetic_scene(
        "mixed",
        length_m=60.0,
        resolution_m=0.10,
        observations_per_cell=4,
        seed=7,
    )
    products = analyze_points(
        scene.points_enu_m,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        source={"type": "synthetic", "profile": "mixed"},
        source_origin=scene.source_origin,
    )
    results = products.summary["results"]
    assert results["pothole_count"] >= 2
    assert results["max_pothole_depth_m"] >= 0.08
    assert results["rutting_count"] >= 1
    assert results["max_rut_depth_m"] >= 0.025
    assert results["bump_count"] >= 1
    assert products.summary["coverage"]["valid_coverage_ratio"] >= 0.95
    assert products.summary["scores"]["geometry_score"] < 100.0


def test_flat_scene_has_no_threshold_exceeding_defect() -> None:
    scene = generate_synthetic_scene(
        "flat",
        length_m=24.0,
        resolution_m=0.15,
        observations_per_cell=4,
        seed=11,
    )
    config = AnalysisConfig.from_overrides(
        {
            "surface": {
                "grid_size_m": 0.15,
                "reference_min_cells": 80,
            }
        }
    )
    products = analyze_points(
        scene.points_enu_m,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        config=config,
        source={"type": "synthetic", "profile": "flat"},
    )
    assert products.summary["results"]["pothole_count"] == 0
    assert products.summary["results"]["bump_count"] == 0
    assert products.summary["results"]["max_rut_depth_m"] < 0.02


def test_result_contract_is_json_serializable(tmp_path) -> None:
    scene = generate_synthetic_scene(
        "potholes",
        length_m=24.0,
        resolution_m=0.15,
        observations_per_cell=4,
        seed=3,
    )
    config = AnalysisConfig.from_overrides(
        {
            "surface": {
                "grid_size_m": 0.15,
                "reference_min_cells": 80,
            }
        }
    )
    products = analyze_points(
        scene.points_enu_m,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        config=config,
        source={"type": "synthetic", "profile": "potholes"},
        source_origin=scene.source_origin,
    )
    artifacts = write_analysis_products(tmp_path, products)
    assert set(artifacts) >= {
        "summary",
        "defects",
        "defects_local_geojson",
        "defects_enu_geojson",
        "surface_preview",
        "report_html",
    }
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    geojson = json.loads(
        (tmp_path / "defects.local.geojson").read_text(encoding="utf-8")
    )
    assert summary["format_version"] == 1
    assert geojson["type"] == "FeatureCollection"
    assert (tmp_path / "surface.npz").is_file()
    assert (tmp_path / "report.html").is_file()


def test_unknown_config_key_is_rejected() -> None:
    try:
        AnalysisConfig.from_overrides({"surface": {"not_a_parameter": 1}})
    except ValueError as exc:
        assert "unknown surface config keys" in str(exc)
    else:
        raise AssertionError("unknown config key was accepted")


def test_implausible_surface_cells_are_excluded_and_accounted() -> None:
    scene = generate_synthetic_scene(
        "flat",
        length_m=24.0,
        resolution_m=0.15,
        observations_per_cell=4,
        seed=23,
    )
    points = scene.points_enu_m.copy()
    high = (
        (points[:, 0] >= 8.0)
        & (points[:, 0] < 9.0)
        & (points[:, 1] >= 0.4)
        & (points[:, 1] < 1.4)
    )
    low = (
        (points[:, 0] >= 15.0)
        & (points[:, 0] < 16.0)
        & (points[:, 1] >= -1.4)
        & (points[:, 1] < -0.4)
    )
    points[high, 2] += 0.65
    points[low, 2] -= 0.65

    products = analyze_points(
        points,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        config=AnalysisConfig.from_overrides(
            {"surface": {"grid_size_m": 0.15, "reference_min_cells": 80}}
        ),
        source={"type": "synthetic", "profile": "flat_with_nonroad_cells"},
    )

    quality = products.summary["quality"]
    coverage = products.summary["coverage"]
    assert quality["plausibility_excluded_high_cell_count"] > 0
    assert quality["plausibility_excluded_low_cell_count"] > 0
    assert quality["supported_surface_cell_count"] == (
        quality["usable_surface_cell_count"]
        + quality["plausibility_excluded_cell_count"]
    )
    assert coverage["supported_coverage_ratio"] > coverage["valid_coverage_ratio"]
    assert products.summary["results"]["max_bump_height_m"] < 0.25
    assert products.summary["results"]["max_pothole_depth_m"] < 0.30


@pytest.mark.parametrize(
    ("name", "value"),
    (("plausibility_residual_min_m", 0.0), ("plausibility_residual_max_m", 0.0)),
)
def test_plausibility_residual_range_is_validated(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=name):
        AnalysisConfig.from_overrides({"surface": {name: value}})
