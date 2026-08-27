from __future__ import annotations

import json

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
