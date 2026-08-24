from __future__ import annotations

import numpy as np

from rgbd_map.postprocess import (
    PointCloudMetadata,
    PostprocessResult,
    PostprocessStage,
    QualityGuardResult,
    RemovalReason,
    diagnostic_removal_colors,
    primary_removal_reasons,
)
from rgbd_map.postprocess_backends import DependencyInfo
from rgbd_map.postprocess_config import PostprocessConfig, resolve_postprocess_config
from rgbd_map.postprocess_runner import execute_postprocess


_POINTS = np.array(
    [
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.1],
        [2.0, 0.0, 0.2],
        [3.0, 0.0, 0.3],
        [4.0, 0.0, 0.4],
    ],
    dtype=np.float32,
)
_COLORS = np.tile(np.array([[80, 100, 120]], dtype=np.uint8), (len(_POINTS), 1))


def _quality(
    keep_mask: np.ndarray,
    *,
    xy_retention: float,
    corridor_retention: float,
    high_retention: float,
    below_surface_reduction: float | None = 1.0,
    passed: bool = False,
) -> QualityGuardResult:
    clean_count = int(np.count_nonzero(keep_mask))
    removal_ratio = float(1.0 - clean_count / len(keep_mask))
    return QualityGuardResult(
        xy_coverage_retention=xy_retention,
        trajectory_corridor_coverage_retention=corridor_retention,
        high_structure_retention=high_retention,
        below_surface_reduction=below_surface_reduction,
        bright_isolated_reduction=1.0,
        removal_ratio=removal_ratio,
        raw_xy_occupied_cells=10,
        clean_xy_occupied_cells=int(round(10 * xy_retention)),
        raw_corridor_occupied_cells=10,
        clean_corridor_occupied_cells=int(round(10 * corridor_retention)),
        raw_high_structure_point_count=10,
        clean_high_structure_point_count=int(round(10 * high_retention)),
        raw_below_surface_point_count=1,
        clean_below_surface_point_count=0,
        raw_bright_isolated_point_count=1,
        clean_bright_isolated_point_count=0,
        xy_bbox_length_retention=(xy_retention, xy_retention),
        maximum_z_retention=high_retention,
        passed=passed,
        warnings=() if passed else ("synthetic quality guard failure",),
    )


def _result(
    config: PostprocessConfig,
    keep_mask: np.ndarray,
    quality: QualityGuardResult,
) -> PostprocessResult:
    keep = np.asarray(keep_mask, dtype=bool)
    removed = ~keep
    clean_indices = np.flatnonzero(keep).astype(np.int64, copy=False)
    removed_indices = np.flatnonzero(removed).astype(np.int64, copy=False)
    reason_bits = np.zeros(len(_POINTS), dtype=np.uint16)
    reason_bits[removed] = np.uint16(int(RemovalReason.RADIUS_OUTLIER))
    primary = primary_removal_reasons(reason_bits)
    metadata = PointCloudMetadata(
        observation_count=np.full(len(_POINTS), 3, dtype=np.uint32),
        distinct_frame_count=np.full(len(_POINTS), 3, dtype=np.uint32),
        position_std_m=np.zeros(len(_POINTS), dtype=np.float32),
        mean_depth_m=np.full(len(_POINTS), 5.0, dtype=np.float32),
    )
    dependencies = DependencyInfo(
        open3d_available=False,
        open3d_version=None,
        scipy_available=True,
        scipy_version="test",
        pdal_available=False,
        pdal_path=None,
        pdal_version=None,
    )
    report = {
        "format_version": 1,
        "selected_result": f"off_{config.preset.replace('-', '_')}",
        "input": {"raw_point_count": len(_POINTS)},
        "output": {
            "clean_point_count": len(clean_indices),
            "removed_point_count": len(removed_indices),
            "removal_ratio": quality.removal_ratio,
        },
        "reasons": {},
        "quality_guards": quality.to_dict(),
        "timing_seconds": {},
        "dependencies": dependencies.to_dict(),
        "parameters": config.to_dict(),
    }
    return PostprocessResult(
        raw_points_enu_m=_POINTS,
        raw_colors_rgb=_COLORS,
        clean_points_enu_m=_POINTS[clean_indices],
        clean_colors_rgb=_COLORS[clean_indices],
        removed_points_enu_m=_POINTS[removed_indices],
        removed_original_colors_rgb=_COLORS[removed_indices],
        removed_diagnostic_colors_rgb=diagnostic_removal_colors(
            primary[removed_indices]
        ),
        keep_mask=keep,
        removed_mask=removed,
        clean_indices=clean_indices,
        removed_indices=removed_indices,
        removal_reason_bits=reason_bits,
        primary_reason=primary,
        metadata=metadata,
        neighbor_result=None,
        ground_surface=None,
        stages=(
            PostprocessStage(
                "raw", len(_POINTS), len(_POINTS), 0, 0.0
            ),
        ),
        quality=quality,
        config=config,
        neighbor_backend="scipy",
        ground_backend="off",
        dependencies=dependencies,
        report=report,
    )


def test_execute_postprocess_attempts_at_most_one_fallback(monkeypatch):
    requested = resolve_postprocess_config("road-map", 0.05)
    calls: list[str] = []

    def fake_run_postprocess(*args, **kwargs):
        config = args[4]
        calls.append(config.preset)
        if len(calls) > 2:
            raise AssertionError("execute_postprocess attempted more than one fallback")
        if config.preset == "road-map":
            keep = np.array([True, False, False, False, False])
            return _result(
                config,
                keep,
                _quality(
                    keep,
                    xy_retention=0.50,
                    corridor_retention=0.50,
                    high_retention=0.50,
                ),
            )
        assert config.preset == "conservative"
        keep = np.array([True, True, False, False, False])
        return _result(
            config,
            keep,
            _quality(
                keep,
                xy_retention=0.70,
                corridor_retention=0.70,
                high_retention=0.70,
            ),
        )

    monkeypatch.setattr(
        "rgbd_map.postprocess_runner.run_postprocess", fake_run_postprocess
    )
    execution = execute_postprocess(
        _POINTS,
        _COLORS,
        None,
        None,
        requested,
        voxel_size_m=0.05,
        neighbor_backend="scipy",
        ground_backend="off",
    )

    assert calls == ["road-map", "conservative"]
    assert len(execution.attempts) == 2
    assert execution.fallback_preset == "conservative"
    assert execution.fallback_selected is True
    assert execution.selected.config.preset == "conservative"
    assert execution.selected.report["selection"]["maximum_attempt_count"] == 2
    assert len(execution.selected.report["selection"]["attempts"]) == 2


def test_execute_postprocess_materializes_first_result_when_fallback_is_worse(
    monkeypatch,
):
    requested = resolve_postprocess_config("road-map", 0.05)
    first_keep = np.array([True, True, False, True, False])
    first_reason_bits = np.zeros(len(_POINTS), dtype=np.uint16)
    first_reason_bits[~first_keep] = np.uint16(int(RemovalReason.RADIUS_OUTLIER))
    calls: list[str] = []

    def fake_run_postprocess(*args, **kwargs):
        config = args[4]
        calls.append(config.preset)
        if config.preset == "road-map":
            return _result(
                config,
                first_keep,
                _quality(
                    first_keep,
                    xy_retention=0.95,
                    corridor_retention=0.95,
                    high_retention=0.90,
                ),
            )
        assert config.preset == "conservative"
        fallback_keep = np.array([True, False, False, False, False])
        return _result(
            config,
            fallback_keep,
            _quality(
                fallback_keep,
                xy_retention=0.40,
                corridor_retention=0.40,
                high_retention=0.40,
            ),
        )

    monkeypatch.setattr(
        "rgbd_map.postprocess_runner.run_postprocess", fake_run_postprocess
    )
    execution = execute_postprocess(
        _POINTS,
        _COLORS,
        None,
        None,
        requested,
        voxel_size_m=0.05,
        neighbor_backend="scipy",
        ground_backend="off",
    )

    assert calls == ["road-map", "conservative"]
    assert execution.fallback_selected is False
    selected = execution.selected
    assert selected.config.preset == "road-map"
    np.testing.assert_array_equal(selected.keep_mask, first_keep)
    np.testing.assert_array_equal(selected.removal_reason_bits, first_reason_bits)
    np.testing.assert_array_equal(selected.clean_indices, [0, 1, 3])
    np.testing.assert_array_equal(selected.removed_indices, [2, 4])
    np.testing.assert_array_equal(selected.clean_points_enu_m, _POINTS[[0, 1, 3]])
    np.testing.assert_array_equal(selected.removed_points_enu_m, _POINTS[[2, 4]])
    assert selected.neighbor_result is None
    assert selected.ground_surface is None


def test_execute_postprocess_keeps_passing_first_over_lower_penalty_failed_fallback(
    monkeypatch,
):
    requested = resolve_postprocess_config("road-map", 0.05)
    calls: list[str] = []

    def fake_run_postprocess(*args, **kwargs):
        config = args[4]
        calls.append(config.preset)
        if config.preset == "road-map":
            keep = np.ones(len(_POINTS), dtype=bool)
            return _result(
                config,
                keep,
                _quality(
                    keep,
                    xy_retention=1.0,
                    corridor_retention=1.0,
                    high_retention=1.0,
                    below_surface_reduction=None,
                    passed=True,
                ),
            )
        assert config.preset == "aggressive"
        keep = np.array([True, True, True, True, False])
        return _result(
            config,
            keep,
            _quality(
                keep,
                xy_retention=0.89,
                corridor_retention=1.0,
                high_retention=1.0,
                passed=False,
            ),
        )

    monkeypatch.setattr(
        "rgbd_map.postprocess_runner.run_postprocess", fake_run_postprocess
    )
    execution = execute_postprocess(
        _POINTS,
        _COLORS,
        None,
        None,
        requested,
        voxel_size_m=0.05,
        neighbor_backend="scipy",
        ground_backend="off",
    )

    assert calls == ["road-map", "aggressive"]
    assert execution.attempts[0].quality_guards_passed is True
    assert execution.attempts[1].quality_guards_passed is False
    assert execution.attempts[1].quality_penalty < execution.attempts[0].quality_penalty
    assert execution.fallback_selected is False
    assert execution.selected.config.preset == "road-map"


def test_execute_postprocess_selects_passing_fallback_over_lower_penalty_failed_first(
    monkeypatch,
):
    requested = resolve_postprocess_config("road-map", 0.05)
    calls: list[str] = []

    def fake_run_postprocess(*args, **kwargs):
        config = args[4]
        calls.append(config.preset)
        if config.preset == "road-map":
            keep = np.array([True, True, True, True, False])
            return _result(
                config,
                keep,
                _quality(
                    keep,
                    xy_retention=0.89,
                    corridor_retention=1.0,
                    high_retention=1.0,
                    passed=False,
                ),
            )
        assert config.preset == "conservative"
        keep = np.ones(len(_POINTS), dtype=bool)
        return _result(
            config,
            keep,
            _quality(
                keep,
                xy_retention=1.0,
                corridor_retention=1.0,
                high_retention=1.0,
                passed=True,
            ),
        )

    monkeypatch.setattr(
        "rgbd_map.postprocess_runner.run_postprocess", fake_run_postprocess
    )
    execution = execute_postprocess(
        _POINTS,
        _COLORS,
        None,
        None,
        requested,
        voxel_size_m=0.05,
        neighbor_backend="scipy",
        ground_backend="off",
    )

    assert calls == ["road-map", "conservative"]
    assert execution.attempts[0].quality_guards_passed is False
    assert execution.attempts[1].quality_guards_passed is True
    assert execution.attempts[0].quality_penalty < execution.attempts[1].quality_penalty
    assert execution.fallback_selected is True
    assert execution.selected.config.preset == "conservative"
