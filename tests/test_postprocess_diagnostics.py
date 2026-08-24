import json
from pathlib import Path

import cv2
import numpy as np
import pytest

import rgbd_map.diagnostics as diagnostics
from rgbd_map.diagnostics import write_postprocess_diagnostics
from rgbd_map.postprocess import (
    RemovalReason,
    diagnostic_removal_colors,
    primary_removal_reasons,
)


def test_diagnostics_write_comparisons_tiles_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "_DIAGNOSTIC_CHUNK_POINTS", 2)
    raw = np.array(
        [
            [0.0, 0.0, 0.0],
            [0.2, 0.1, 0.0],
            [1.1, 0.1, 0.2],
            [20.2, 0.0, -0.6],
            [20.3, 0.2, 1.5],
        ],
        dtype=np.float32,
    )
    clean = raw[[0, 1, 2, 4]]
    removed = raw[[3]]
    raw_colors = np.array(
        [[40, 70, 100], [60, 90, 120], [80, 110, 140], [255, 255, 255], [20, 180, 60]],
        dtype=np.uint8,
    )
    report = {
        "selected_result": "local_road_map",
        "output": {"removal_ratio": 0.2},
        "quality_guards": {
            "xy_coverage_retention": 0.9,
            "trajectory_corridor_coverage_retention": 0.95,
            "high_structure_retention": 1.0,
            "below_surface_reduction": 0.8,
            "bright_isolated_reduction": "not_evaluable",
            "passed": True,
            "warnings": [],
        },
    }

    paths = write_postprocess_diagnostics(
        tmp_path,
        raw,
        clean,
        removed,
        np.array([32], dtype=np.uint16),
        raw_colors=raw_colors,
        clean_colors=raw_colors[[0, 1, 2, 4]],
        removed_colors=raw_colors[[3]],
        trajectory_points=np.array([[0.0, 0.0, 1.5], [20.0, 0.0, 1.5]]),
        reason_names={32: "below_local_surface"},
        report=report,
        tile_size_m=20.0,
        image_size=(192, 160),
    )

    assert set(paths) == {
        "top_before_after",
        "side_before_after",
        "removed_reason_top",
        "representative_tiles",
        "run_summary",
    }
    assert all(path.is_file() and path.stat().st_size > 0 for path in paths.values())

    top = cv2.imread(str(paths["top_before_after"]))
    side = cv2.imread(str(paths["side_before_after"]))
    reasons = cv2.imread(str(paths["removed_reason_top"]))
    assert top.shape == (160, 192 * 3, 3)
    assert side.shape == top.shape
    assert reasons.shape == (160, 192 * 2, 3)

    tiles = json.loads(paths["representative_tiles"].read_text(encoding="utf-8"))
    assert tiles["format_version"] == 1
    assert tiles["tile_size_m"] == 20.0
    assert tiles["tile_count"] == 2
    assert tiles["representative_tiles"][0]["removed_point_count"] == 1
    assert tiles["representative_tiles"][0]["raw_point_count"] == 2

    summary = paths["run_summary"].read_text(encoding="utf-8")
    assert "raw_point_count: 5" in summary
    assert "clean_point_count: 4" in summary
    assert "removed_point_count: 1" in summary
    assert "removal_ratio: 20.00%" in summary
    assert "quality_guards_passed: True" in summary


def test_diagnostics_are_safe_for_empty_and_nonfinite_points(tmp_path):
    empty = np.empty((0, 3), dtype=np.float32)
    paths = write_postprocess_diagnostics(
        tmp_path / "empty",
        np.array([[np.nan, 0.0, 0.0]], dtype=np.float32),
        empty,
        empty,
        np.empty(0, dtype=np.uint16),
        image_size=(128, 128),
    )

    for key in ("top_before_after", "side_before_after", "removed_reason_top"):
        image = cv2.imread(str(paths[key]))
        assert image is not None
        assert image.shape[0] == 128
    tiles = json.loads(paths["representative_tiles"].read_text(encoding="utf-8"))
    assert tiles["tile_count"] == 0
    assert tiles["representative_tiles"] == []
    assert "clean_point_count: 0" in paths["run_summary"].read_text(encoding="utf-8")


def test_diagnostics_validate_aligned_metadata(tmp_path):
    point = np.zeros((1, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="removed_reason_bits"):
        write_postprocess_diagnostics(
            tmp_path,
            point,
            point,
            point,
            np.zeros(2, dtype=np.uint16),
        )

    with pytest.raises(ValueError, match="raw_colors"):
        write_postprocess_diagnostics(
            tmp_path,
            point,
            point,
            point,
            np.zeros(1, dtype=np.uint16),
            raw_colors=np.zeros((2, 3), dtype=np.uint8),
        )


def test_reason_priority_and_colors_are_consumed_in_bounded_chunks(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(diagnostics, "_DIAGNOSTIC_CHUNK_POINTS", 2)
    points = np.column_stack(
        (
            np.arange(5, dtype=np.float32),
            np.zeros(5, dtype=np.float32),
            np.zeros(5, dtype=np.float32),
        )
    )
    bits = np.array(
        [
            int(RemovalReason.RADIUS_OUTLIER | RemovalReason.BELOW_LOCAL_SURFACE),
            int(RemovalReason.STATISTICAL_OUTLIER),
            int(RemovalReason.HIGH_POSITION_SPREAD),
            int(RemovalReason.BRIGHT_LOW_SUPPORT),
            int(RemovalReason.LOW_MULTI_FRAME_SUPPORT),
        ],
        dtype=np.uint16,
    )
    expected_primary = primary_removal_reasons(bits)
    expected_colors = diagnostic_removal_colors(expected_primary)
    primary_chunk_sizes = []
    color_chunks = []
    original_primary = diagnostics.primary_removal_reasons
    original_colors = diagnostics.diagnostic_removal_colors

    def tracked_primary(chunk):
        primary_chunk_sizes.append(len(chunk))
        return original_primary(chunk)

    def tracked_colors(chunk):
        color_chunks.append(original_colors(chunk))
        return original_colors(chunk)

    monkeypatch.setattr(diagnostics, "primary_removal_reasons", tracked_primary)
    monkeypatch.setattr(diagnostics, "diagnostic_removal_colors", tracked_colors)
    write_postprocess_diagnostics(
        tmp_path,
        points,
        np.empty((0, 3), dtype=np.float32),
        points,
        bits,
        image_size=(128, 128),
    )

    assert primary_chunk_sizes
    assert max(primary_chunk_sizes) <= 2
    assert color_chunks
    np.testing.assert_array_equal(np.concatenate(color_chunks[:3]), expected_colors)


def test_viewer_hard_rejects_structural_bin_errors_but_soft_warns_count():
    source = (Path(__file__).parents[1] / "viewer" / "app.js").read_text(
        encoding="utf-8"
    )
    count_warning = source.index('if (headerCount !== descriptor.expectedCount)')
    structural_rejection = source.index('if (!structurallyValid)')

    assert count_warning < structural_rejection
    assert 'throw new Error(`${descriptor.filename} has an invalid binary header`)' in source
    assert 'throw new Error(`${descriptor.filename} has an invalid binary structure`)' in source
    assert "const count = headerCount;" in source
    assert "layer.loaded = false;" in source
    assert "if (cleanLoaded || !cleanDescriptor.filename)" in source
