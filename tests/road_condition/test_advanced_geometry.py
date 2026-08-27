from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from road_condition_core.advanced_geometry import (
    crossfall_profile,
    detect_ponding_screening,
    detect_step_manhole_candidates,
    longitudinal_profile,
)
from road_condition_core.config import (
    AdvancedGeometryConfig,
    AnalysisConfig,
    DetectionConfig,
)
from road_condition_core.models import SurfaceGrid
from road_condition_core.pipeline import analyze_points, write_analysis_products
from road_condition_core.synthetic import generate_synthetic_scene


def _grid(
    observed: np.ndarray,
    reference: np.ndarray,
    *,
    resolution_m: float = 0.10,
) -> SurfaceGrid:
    rows, columns = observed.shape
    s_values = np.arange(rows, dtype=np.float64) * resolution_m
    t_values = (
        np.arange(columns, dtype=np.float64) - 0.5 * (columns - 1)
    ) * resolution_m
    trajectory = np.column_stack(
        (s_values, np.zeros(rows), np.full(rows, 1.5, dtype=np.float64))
    )
    return SurfaceGrid(
        s_values_m=s_values,
        t_values_m=t_values,
        observed_local_up_m=observed.astype(np.float32),
        reference_local_up_m=reference.astype(np.float32),
        residual_m=(observed - reference).astype(np.float32),
        point_count=np.full((rows, columns), 6, dtype=np.uint16),
        position_std_m=np.full((rows, columns), 0.005, dtype=np.float32),
        valid_mask=np.ones((rows, columns), dtype=bool),
        trajectory_enu_m=trajectory,
        trajectory_cumulative_m=s_values,
    )


@pytest.mark.parametrize(
    "flag",
    [
        "step_manhole_enabled",
        "crossfall_enabled",
        "longitudinal_enabled",
        "ponding_screening_enabled",
    ],
)
def test_advanced_detector_flags_are_independent_and_opt_in(flag: str) -> None:
    config = AnalysisConfig.from_overrides(
        {"advanced_geometry": {flag: True}}
    ).advanced_geometry
    for candidate in (
        "step_manhole_enabled",
        "crossfall_enabled",
        "longitudinal_enabled",
        "ponding_screening_enabled",
    ):
        assert getattr(config, candidate) is (candidate == flag)


def test_step_manhole_candidate_reports_height_slope_and_edge() -> None:
    shape = (70, 50)
    reference = np.zeros(shape, dtype=np.float64)
    s, t = np.meshgrid(
        np.arange(shape[0]) * 0.10,
        (np.arange(shape[1]) - 24.5) * 0.10,
        indexing="ij",
    )
    cover = ((s - 3.0) / 0.45) ** 2 + (t / 0.45) ** 2 <= 1.0
    observed = reference.copy()
    observed[cover] += 0.032
    candidates = detect_step_manhole_candidates(
        _grid(observed, reference),
        AdvancedGeometryConfig(step_manhole_enabled=True),
    )
    assert candidates
    candidate = max(candidates, key=lambda item: item.metrics["step_height_m"])
    assert candidate.defect_type == "manhole_step_candidate"
    assert candidate.metrics["step_height_m"] >= 0.025
    assert candidate.metrics["approach_slope_percent"] >= 8.0
    assert candidate.metrics["edge_length_m"] >= 0.30
    assert candidate.metrics["forward_approach_step_height_m"] >= 0.025
    assert candidate.metrics["reverse_approach_step_height_m"] >= 0.025
    assert "asset_or_rgb_confirmation_required" in candidate.quality_flags


def test_crossfall_and_longitudinal_profiles_recover_known_plane() -> None:
    rows, columns = 80, 60
    s, t = np.meshgrid(
        np.arange(rows) * 0.10,
        (np.arange(columns) - 29.5) * 0.10,
        indexing="ij",
    )
    plane = 0.010 * s + 0.020 * t
    grid = _grid(plane, plane)
    crossfall = crossfall_profile(grid)
    longitudinal = longitudinal_profile(grid, DetectionConfig())
    assert crossfall["profiles"]["road"]["median_percent"] == pytest.approx(
        2.0, abs=0.02
    )
    assert longitudinal["slope"]["median_percent"] == pytest.approx(
        1.0, abs=0.02
    )
    assert longitudinal["roughness_name_guard"].endswith("not_standardized_IRI")

    crowned = -0.015 * t * t + 0.010 * s
    crown_profile = crossfall_profile(_grid(crowned, crowned))
    assert crown_profile["profiles"]["road"][
        "median_crown_offset_m"
    ] == pytest.approx(0.0, abs=0.11)


def test_ponding_screening_reports_closed_depression_without_capacity() -> None:
    rows, columns = 70, 50
    s, t = np.meshgrid(
        np.arange(rows) * 0.10,
        (np.arange(columns) - 24.5) * 0.10,
        indexing="ij",
    )
    radius = ((s - 3.0) / 0.8) ** 2 + (t / 0.55) ** 2
    observed = np.zeros((rows, columns), dtype=np.float64)
    observed[radius < 1.0] -= 0.06 * (1.0 - radius[radius < 1.0]) ** 2
    candidates = detect_ponding_screening(
        _grid(observed, np.zeros_like(observed)),
        AdvancedGeometryConfig(ponding_screening_enabled=True),
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.defect_type == "ponding_screening_proxy"
    assert candidate.metrics["potential_retention_depth_m"] >= 0.05
    assert candidate.metrics["potential_retention_volume_m3"] > 0.0
    assert "drainage_capacity_not_computed" in candidate.quality_flags


def test_advanced_detector_failure_does_not_change_potholes(monkeypatch) -> None:
    scene = generate_synthetic_scene(
        "potholes",
        length_m=20.0,
        resolution_m=0.20,
        observations_per_cell=4,
        seed=31,
    )
    base = AnalysisConfig()
    base = replace(
        base,
        surface=replace(base.surface, grid_size_m=0.20, reference_min_cells=40),
    )
    baseline = analyze_points(
        scene.points_enu_m,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        config=base,
    )

    def fail_detector(*_args, **_kwargs):
        raise RuntimeError("isolated fixture")

    monkeypatch.setattr(
        "road_condition_core.pipeline.detect_step_manhole_candidates",
        fail_detector,
    )
    enabled = replace(
        base,
        advanced_geometry=replace(
            base.advanced_geometry,
            step_manhole_enabled=True,
        ),
    )
    isolated = analyze_points(
        scene.points_enu_m,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        config=enabled,
    )
    baseline_potholes = [
        item.to_dict() for item in baseline.defects if item.defect_type == "pothole"
    ]
    isolated_potholes = [
        item.to_dict() for item in isolated.defects if item.defect_type == "pothole"
    ]
    assert isolated_potholes == baseline_potholes
    assert isolated.summary["advanced_geometry"]["failure_count"] == 1
    assert isolated.summary["advanced_geometry"]["detectors"]["step_manhole"][
        "state"
    ] == "failed"


def test_all_advanced_detectors_complete_and_serialize(tmp_path) -> None:
    scene = generate_synthetic_scene(
        "potholes",
        length_m=20.0,
        resolution_m=0.20,
        observations_per_cell=4,
        seed=37,
    )
    base = AnalysisConfig()
    config = replace(
        base,
        surface=replace(base.surface, grid_size_m=0.20, reference_min_cells=40),
        advanced_geometry=replace(
            base.advanced_geometry,
            step_manhole_enabled=True,
            crossfall_enabled=True,
            longitudinal_enabled=True,
            ponding_screening_enabled=True,
        ),
    )
    products = analyze_points(
        scene.points_enu_m,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        config=config,
    )
    states = {
        name: payload["state"]
        for name, payload in products.summary["advanced_geometry"][
            "detectors"
        ].items()
    }
    assert states == {
        "step_manhole": "completed",
        "crossfall": "completed",
        "longitudinal": "completed",
        "ponding_screening": "completed",
    }
    longitudinal = products.summary["advanced_geometry"]["detectors"][
        "longitudinal"
    ]["profile"]
    assert longitudinal["slope"]["median_percent"] == pytest.approx(0.30, abs=0.06)
    artifacts = write_analysis_products(tmp_path, products)
    assert artifacts["summary"] == "summary.json"
    assert (tmp_path / "defects.json").is_file()
