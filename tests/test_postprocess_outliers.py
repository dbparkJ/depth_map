from __future__ import annotations

import numpy as np

from rgbd_map.postprocess import RemovalReason, run_postprocess
from rgbd_map.postprocess_backends import make_pdal_pipeline, tiled_neighbor_filter
from rgbd_map.postprocess_config import resolve_postprocess_config


def _boundary_cluster_with_isolated_point() -> np.ndarray:
    cluster = np.array(
        [
            [x, y, 0.0]
            for x in (0.92, 0.96, 1.00, 1.04, 1.08)
            for y in (-0.04, 0.0, 0.04)
        ],
        dtype=np.float64,
    )
    return np.vstack((cluster, [[1.80, 0.0, 0.0]]))


def _config():
    return resolve_postprocess_config(
        "road-map",
        0.05,
        {
            "radius_outlier_radius_m": 0.16,
            "radius_outlier_min_neighbors": 3,
            "single_frame_min_neighbors": 3,
            "statistical_neighbors": 6,
            "statistical_std_ratio": 100.0,
            "tile_size_m": 1.0,
            "tile_overlap_m": 0.30,
        },
    )


def test_tiled_radius_filter_handles_boundary_and_decides_each_core_once():
    points = _boundary_cluster_with_isolated_point()
    result = tiled_neighbor_filter(
        points,
        _config(),
        backend="scipy",
        mean_depth_m=np.full(len(points), 5.0),
    )

    assert np.all(result.core_evaluation_count == 1)
    assert not np.any(result.radius_outlier_mask[:-1])
    assert result.radius_outlier_mask[-1]
    # The cluster crosses both the x=1 and y=0 core boundaries, so the XY
    # partition contains four occupied core tiles. Overlap must still preserve it.
    assert result.tile_count == 4


def test_statistical_pass_removes_isolated_point_after_loose_radius_pass():
    cluster = np.array(
        [
            [x, y, 0.0]
            for x in (0.00, 0.04, 0.08, 0.12, 0.16)
            for y in (0.00, 0.04, 0.08, 0.12)
        ],
        dtype=np.float64,
    )
    points = np.vstack((cluster, [[1.50, 0.0, 0.0]]))
    colors = np.tile(np.array([[80, 100, 120]], dtype=np.uint8), (len(points), 1))
    metadata = {
        "observation_count": np.full(len(points), 3),
        "distinct_frame_count": np.full(len(points), 3),
        "position_std_m": np.zeros(len(points)),
        "mean_depth_m": np.full(len(points), 5.0),
    }
    config = resolve_postprocess_config(
        "road-map",
        0.05,
        {
            # Every point has radius support; only the KNN mean-distance pass
            # should classify the final point as an outlier.
            "radius_outlier_radius_m": 2.0,
            "radius_outlier_min_neighbors": 1,
            "single_frame_min_neighbors": 1,
            "statistical_neighbors": 6,
            "statistical_std_ratio": 1.0,
            "tile_size_m": 10.0,
            "tile_overlap_m": 3.0,
        },
    )

    result = run_postprocess(
        points,
        colors,
        metadata,
        trajectory_enu_m=None,
        config=config,
        neighbor_backend="scipy",
        ground_backend="off",
    )

    stages = {stage.stage: stage for stage in result.stages}
    assert stages["radius_outlier"].input_count == len(points)
    assert stages["radius_outlier"].output_count == len(points)
    assert stages["radius_outlier"].removed_count == 0
    assert stages["statistical_outlier"].input_count == len(points)
    assert stages["statistical_outlier"].output_count == len(points) - 1
    assert stages["statistical_outlier"].removed_count == 1
    np.testing.assert_array_equal(result.removed_indices, [len(points) - 1])
    assert not (
        result.removal_reason_bits[-1] & int(RemovalReason.RADIUS_OUTLIER)
    )
    assert result.removal_reason_bits[-1] & int(
        RemovalReason.STATISTICAL_OUTLIER
    )


def test_postprocess_reports_accounting_and_reason_bits():
    points = _boundary_cluster_with_isolated_point()
    colors = np.tile(np.array([[80, 100, 120]], dtype=np.uint8), (len(points), 1))
    metadata = {
        "observation_count": np.full(len(points), 3),
        "distinct_frame_count": np.full(len(points), 3),
        "position_std_m": np.zeros(len(points)),
        "mean_depth_m": np.full(len(points), 5.0),
    }

    result = run_postprocess(
        points,
        colors,
        metadata,
        trajectory_enu_m=None,
        config=_config(),
        neighbor_backend="scipy",
        ground_backend="off",
    )

    assert len(points) == len(result.clean_indices) + len(result.removed_indices)
    assert len(result.removed_indices) == 1
    assert result.removed_indices[0] == len(points) - 1
    assert result.removal_reason_bits[-1] & int(RemovalReason.RADIUS_OUTLIER)
    assert result.report["output"]["clean_point_count"] == len(points) - 1
    assert result.report["output"]["removed_point_count"] == 1
    for stage in result.stages:
        assert stage.input_count == stage.output_count + stage.removed_count
    stages = {stage.stage: stage for stage in result.stages}
    assert stages["radius_outlier"].seconds > 0.0
    assert stages["statistical_outlier"].seconds > 0.0
    assert stages["radius_outlier"].seconds == result.neighbor_result.radius_seconds
    assert (
        stages["statistical_outlier"].seconds
        == result.neighbor_result.statistical_seconds
    )
    assert result.report["timing_seconds"]["radius_outlier"] > 0.0
    assert result.report["timing_seconds"]["statistical_outlier"] > 0.0


def test_quality_guard_reports_clean_empty_trajectory_segment():
    points = np.array(
        [
            [0.1, 0.0, 0.0],
            [0.2, 0.0, 0.0],
            [10.1, 0.0, 0.0],
            [10.2, 0.0, 0.0],
            [20.1, 0.0, 0.0],
            [20.2, 0.0, 0.0],
        ],
        dtype=np.float64,
    )
    trajectory_x = np.array([0.0, 10.0, 20.0, 30.0])
    trajectory = np.column_stack(
        (trajectory_x, np.zeros_like(trajectory_x), np.ones_like(trajectory_x))
    )
    colors = np.tile(np.array([[80, 100, 120]], dtype=np.uint8), (len(points), 1))
    position_std = np.zeros(len(points), dtype=np.float32)
    position_std[2:4] = 1.0
    metadata = {
        "observation_count": np.full(len(points), 3),
        "distinct_frame_count": np.full(len(points), 3),
        "position_std_m": position_std,
        "mean_depth_m": np.full(len(points), 5.0),
    }
    config = resolve_postprocess_config(
        "road-map",
        0.05,
        {
            "radius_outlier_min_neighbors": 100,
            "single_frame_min_neighbors": 100,
            "statistical_std_ratio": 100.0,
            "tile_size_m": 100.0,
        },
    )

    result = run_postprocess(
        points,
        colors,
        metadata,
        trajectory_enu_m=trajectory,
        config=config,
        neighbor_backend="scipy",
        ground_backend="off",
    )

    quality = result.quality
    assert quality.raw_supported_trajectory_segment_count == 3
    assert quality.clean_supported_trajectory_segment_count == 2
    assert quality.empty_clean_trajectory_segment_count == 1
    assert quality.empty_clean_trajectory_segment_indices == (1,)
    assert quality.trajectory_segment_coverage_retention == 2 / 3
    assert any("no clean points" in warning for warning in quality.warnings)
    assert result.report["quality_guards"][
        "empty_clean_trajectory_segment_indices"
    ] == [1]


def test_pdal_smrf_skips_elm_noise_classification(tmp_path):
    pipeline = make_pdal_pipeline(
        tmp_path / "raw.ply",
        tmp_path / "clean.ply",
        _config(),
    )

    smrf_stage = next(
        stage for stage in pipeline["pipeline"] if stage["type"] == "filters.smrf"
    )
    assert smrf_stage["where"] == "!(Classification == 7)"
