from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

import rgbd_map.debug_stages as debug_stages
from rgbd_map.debug_stages import write_debug_stage_artifacts
from rgbd_map.exporters import read_ply
from rgbd_map.geodesy import LocalENU
from rgbd_map.postprocess import PostprocessStage, RemovalReason
from rgbd_map.postprocess_args import resolve_output_options
from rgbd_map.postprocess_cli import build_parser
from rgbd_map.postprocess_config import resolve_postprocess_config
from rgbd_map.postprocess_io import validate_browser_binary


def _synthetic_stage_state():
    points = np.array(
        [
            [0.0, 0.0, 0.0],
            [np.nan, 0.0, 0.0],
            [2.0, 0.0, 0.2],
            [3.0, 0.0, 0.0],
            [4.0, 0.0, 0.1],
            [5.0, 0.0, -0.8],
            [6.0, 0.0, 0.0],
            [7.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    colors = np.column_stack(
        (
            np.arange(20, 100, 10, dtype=np.uint8),
            np.arange(30, 110, 10, dtype=np.uint8),
            np.arange(40, 120, 10, dtype=np.uint8),
        )
    )
    reasons = np.array(
        [
            0,
            int(RemovalReason.NON_FINITE),
            int(RemovalReason.HIGH_POSITION_SPREAD),
            int(RemovalReason.RADIUS_OUTLIER),
            int(RemovalReason.STATISTICAL_OUTLIER),
            int(RemovalReason.BELOW_LOCAL_SURFACE),
            int(RemovalReason.RADIUS_OUTLIER | RemovalReason.LOW_MULTI_FRAME_SUPPORT),
            int(RemovalReason.STATISTICAL_OUTLIER | RemovalReason.BRIGHT_LOW_SUPPORT),
        ],
        dtype=np.uint16,
    )
    stages = (
        PostprocessStage("raw", 8, 8, 0, 0.0),
        PostprocessStage("non_finite", 8, 7, 1, 0.01),
        PostprocessStage("high_position_spread", 7, 6, 1, 0.02),
        PostprocessStage("radius_outlier", 6, 4, 2, 0.03),
        PostprocessStage("statistical_outlier", 4, 2, 2, 0.04),
        PostprocessStage("local_surface", 2, 1, 1, 0.05),
        PostprocessStage("low_support_bright_combined", 1, 1, 0, 0.06),
    )
    return points, colors, reasons, reasons == 0, stages


def test_debug_stages_write_capped_products_fixed_views_and_accounting(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(debug_stages, "_MASK_SCAN_CHUNK_POINTS", 2)
    points, colors, reasons, keep, stages = _synthetic_stage_state()
    paths = write_debug_stage_artifacts(
        tmp_path,
        raw_points=points,
        raw_colors=colors,
        removal_reason_bits=reasons,
        keep_mask=keep,
        stages=stages,
        origin=LocalENU(126.0, 37.0, 30.0),
        trajectory_points=np.array(
            [[0.0, 0.0, 1.5], [7.0, 0.0, 1.5]], dtype=np.float32
        ),
        max_points=2,
        selected_preset="conservative",
        neighbor_backend="open3d",
        ground_backend="local",
        report={
            "ground_surface": {
                "valid_surface_cell_count": 3,
                "below_surface_removed_count": 1,
            }
        },
        image_size=(128, 128),
    )

    index_path = paths["debug_stage_index"]
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["debug_stage_max_points"] == 2
    assert index["raw_point_count"] == 8
    assert index["clean_point_count"] == 1
    assert index["removed_point_count"] == 7
    assert index["accounting"] == {
        "postprocess_stage_counts_match": True,
        "removal_deltas_disjoint": True,
        "removal_delta_sum": 7,
        "final_removed_point_count": 7,
        "delta_union_equals_final_removed": True,
        "last_stage_equals_final_clean": True,
    }
    assert [record["stage"] for record in index["stages"]] == [
        "raw",
        "non_finite",
        "high_position_spread",
        "radius_outlier",
        "statistical_outlier",
        "local_surface",
        "low_support_bright_combined",
        "clean",
    ]

    delta_sum = 0
    for record in index["stages"]:
        payload = json.loads(
            (tmp_path / record["stage_json"]).read_text(encoding="utf-8")
        )
        assert payload["projection"] == index["projection"]
        assert payload["survivors"]["sample_point_count"] <= 2
        assert payload["removed_this_stage"]["sample_point_count"] <= 2
        delta_sum += payload["removed_this_stage_count"]
        for section in ("survivors", "removed_this_stage"):
            product = payload[section]
            ply_points, ply_colors, comments = read_ply(tmp_path / product["ply"])
            assert len(ply_points) == len(ply_colors) == product["sample_point_count"]
            assert comments["sample_selection"] == index["sample_selection"]
            validate_browser_binary(
                tmp_path / product["browser_binary"],
                product["browser_point_count"],
            )
        for image_relative in payload["diagnostics"].values():
            image = cv2.imread(str(tmp_path / image_relative))
            assert image is not None
            assert image.shape == (128, 128 * 3, 3)

    assert delta_sum == 7
    local_record = next(
        record for record in index["stages"] if record["stage"] == "local_surface"
    )
    local_payload = json.loads(
        (tmp_path / local_record["stage_json"]).read_text(encoding="utf-8")
    )
    assert local_payload["local_surface"]["removed_below_surface_count"] == 1
    assert (
        local_payload["local_surface"]["ground_surface_stats"]
        ["valid_surface_cell_count"]
        == 3
    )
    assert "supported road-local surface" in local_payload["local_surface"]["purpose"]


def test_debug_stage_accounting_mismatch_fails_before_writing(tmp_path: Path):
    points, colors, reasons, keep, stages = _synthetic_stage_state()
    bad_stages = list(stages)
    bad_stages[3] = replace(bad_stages[3], output_count=5, removed_count=1)
    with pytest.raises(RuntimeError, match="accounting mismatch for radius_outlier"):
        write_debug_stage_artifacts(
            tmp_path / "bad",
            raw_points=points,
            raw_colors=colors,
            removal_reason_bits=reasons,
            keep_mask=keep,
            stages=bad_stages,
            origin=LocalENU(126.0, 37.0, 30.0),
            trajectory_points=None,
            max_points=2,
            selected_preset="road-map",
            neighbor_backend="scipy",
            ground_backend="local",
        )
    assert not (tmp_path / "bad" / "data" / "debug_stages").exists()


def test_debug_stage_cli_options_default_off_and_require_positive_cap():
    parser = build_parser()
    default_args = parser.parse_args(["--output", "/tmp/existing-map"])
    config = resolve_postprocess_config("road-map", 0.05, default_args)
    default_options = resolve_output_options(default_args, config)
    assert default_options.write_debug_stages is False
    assert default_options.debug_stage_max_points == 500_000

    enabled_args = parser.parse_args(
        [
            "--output",
            "/tmp/existing-map",
            "--write-debug-stages",
            "--debug-stage-max-points",
            "17",
        ]
    )
    enabled_options = resolve_output_options(enabled_args, config)
    assert enabled_options.write_debug_stages is True
    assert enabled_options.debug_stage_max_points == 17

    invalid_args = parser.parse_args(
        [
            "--output",
            "/tmp/existing-map",
            "--debug-stage-max-points",
            "0",
        ]
    )
    with pytest.raises(ValueError, match="debug-stage-max-points"):
        resolve_output_options(invalid_args, config)
