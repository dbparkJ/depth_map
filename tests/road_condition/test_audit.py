from __future__ import annotations

from dataclasses import replace

import numpy as np

from road_condition_core.audit import plan_audit_windows, summarize_audit_tile
from road_condition_core.config import AnalysisConfig
from road_condition_core.detectors import (
    bump_plausibility_boundary_guard_stats,
    detect_bumps,
    detect_potholes,
)
from road_condition_core.models import SurfaceGrid
from road_condition_core.pipeline import analyze_points
from road_condition_core.synthetic import generate_synthetic_scene


def test_audit_window_plan_keeps_short_and_stationary_controls() -> None:
    stationary = plan_audit_windows(0.8)
    assert len(stationary) == 1
    assert stationary[0].label == "full"
    assert stationary[0].core_start_m == 0.0
    assert stationary[0].core_end_m == 0.8

    short = plan_audit_windows(31.0)
    assert len(short) == 1
    assert short[0].label == "central"
    assert (short[0].core_start_m, short[0].core_end_m) == (10.5, 20.5)

    normal = plan_audit_windows(100.0)
    assert [item.label for item in normal] == ["q25", "q50", "q75"]
    assert [(item.core_start_m, item.core_end_m) for item in normal] == [
        (20.0, 30.0),
        (45.0, 55.0),
        (70.0, 80.0),
    ]


def test_audit_summary_reports_current_and_literature_comparison() -> None:
    scene = generate_synthetic_scene(
        "potholes", length_m=30.0, resolution_m=0.20, observations_per_cell=5
    )
    base = AnalysisConfig()
    config = replace(
        base,
        surface=replace(base.surface, grid_size_m=0.20, reference_min_cells=40),
    )
    products = analyze_points(
        scene.points_enu_m,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        config=config,
        source={"type": "synthetic", "profile": "audit"},
    )
    literature = replace(
        config.detection,
        pothole_min_depth_m=0.025,
        pothole_min_area_m2=0.020,
    )
    result = summarize_audit_tile(
        products,
        plan_audit_windows(30.0)[0],
        alternative_potholes=detect_potholes(products.surface, literature),
    )
    assert result["quality"]["supported_surface_cell_count"] > 0
    assert result["coverage"]["valid_coverage_ratio"] > 0
    assert result["defects"]["count"] >= 1
    assert isinstance(result["literature_comparison"]["count_delta_vs_current"], int)
    assert result["residual_m"]["supported_quantiles"]["p50"] is not None


def test_bump_fragment_touching_excluded_high_object_is_suppressed() -> None:
    shape = (12, 12)
    residual = np.zeros(shape, dtype=np.float32)
    residual[1:4, 1:4] = 0.10
    residual[5:8, 6:9] = 0.10
    residual[5, 5] = 0.31
    supported = np.ones(shape, dtype=bool)
    excluded_high = np.zeros(shape, dtype=bool)
    excluded_high[5, 5] = True
    valid = supported & ~excluded_high
    grid = SurfaceGrid(
        s_values_m=np.arange(shape[0], dtype=np.float32) * 0.1,
        t_values_m=np.arange(shape[1], dtype=np.float32) * 0.1,
        observed_local_up_m=residual.copy(),
        reference_local_up_m=np.zeros(shape, dtype=np.float32),
        residual_m=residual,
        point_count=np.full(shape, 8, dtype=np.uint32),
        position_std_m=np.full(shape, 0.01, dtype=np.float32),
        valid_mask=valid,
        trajectory_enu_m=np.asarray([[0, 0, 0], [2, 0, 0]], dtype=float),
        trajectory_cumulative_m=np.asarray([0, 2], dtype=float),
        supported_mask=supported,
        plausibility_excluded_low_mask=np.zeros(shape, dtype=bool),
        plausibility_excluded_high_mask=excluded_high,
    )
    config = AnalysisConfig().detection
    stats = bump_plausibility_boundary_guard_stats(grid, config)
    bumps = detect_bumps(grid, config)
    assert stats["removed_component_count"] == 1
    assert stats["removed_candidate_cell_count"] == 9
    assert len(bumps) == 1
    assert bumps[0].chainage_m < 0.3
