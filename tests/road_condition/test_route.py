from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pyarrow.parquet as pq

from road_condition_core.config import AnalysisConfig
from road_condition_core.route import (
    RouteChunkInput,
    RouteConfig,
    aggregate_chunk_routes,
    aggregate_route_tiles,
    merge_defect_records,
    plan_tiles,
    run_tiled_analysis,
)
from road_condition_core.synthetic import generate_synthetic_scene


def _defect(defect_id: str, chainage: float, lateral: float, depth: float):
    return {
        "defect_id": defect_id,
        "defect_type": "pothole",
        "severity": "medium",
        "confidence": 0.8,
        "chainage_m": chainage,
        "lateral_offset_m": lateral,
        "local_polygon_st_m": [
            [chainage - 0.3, lateral - 0.2],
            [chainage + 0.3, lateral - 0.2],
            [chainage + 0.3, lateral + 0.2],
            [chainage - 0.3, lateral + 0.2],
        ],
        "metrics": {"max_depth_m": depth, "area_m2": 0.24, "volume_m3": 0.01},
        "quality_flags": [],
        "source": "geometry",
        "lane_id": None,
        "road_zone": "corridor_fallback",
    }


def test_tile_plan_has_core_halo_and_last_short_tile() -> None:
    windows = plan_tiles(0.0, 25.0)
    assert [(item.core_start_m, item.core_end_m) for item in windows] == [
        (0.0, 10.0),
        (10.0, 20.0),
        (20.0, 25.0),
    ]
    assert (windows[1].halo_start_m, windows[1].halo_end_m) == (7.0, 23.0)
    assert windows[-1].is_last is True


def test_defect_merge_is_order_independent_and_keeps_nearby_independent_defect() -> None:
    records = [
        _defect("chunk-a", 10.0, 0.0, 0.08),
        _defect("chunk-b", 10.25, 0.08, 0.085),
        _defect("independent", 10.4, 1.8, 0.08),
    ]
    forward = merge_defect_records(records)
    reverse = merge_defect_records(list(reversed(records)))
    assert forward == reverse
    assert len(forward) == 2
    merged = next(item for item in forward if len(item["merged_from"]) == 2)
    assert merged["merged_from"] == ["chunk-a", "chunk-b"]


def test_boundary_pothole_has_one_owner_and_resume_skips_tiles(
    tmp_path, monkeypatch
) -> None:
    scene = generate_synthetic_scene(
        "flat",
        length_m=20.0,
        resolution_m=0.20,
        observations_per_cell=4,
        seed=19,
    )
    points = scene.points_enu_m.copy()
    s = points[:, 0]
    t = points[:, 1]
    radius = ((s - 10.0) / 0.8) ** 2 + (t / 0.55) ** 2
    points[radius < 1.0, 2] -= 0.10 * (1.0 - radius[radius < 1.0]) ** 2
    base = AnalysisConfig()
    config = replace(
        base,
        surface=replace(
            base.surface,
            grid_size_m=0.20,
            reference_min_cells=40,
        ),
    )
    output = tmp_path / "route"
    first = run_tiled_analysis(
        points,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        output,
        analysis_config=config,
        source={"type": "synthetic", "profile": "boundary"},
    )
    assert first["state"] == "completed"
    assert first["tile_count"] == 2
    assert first["defect_count"] == 1
    assert pq.read_table(output / "route_defects.parquet").num_rows == 1
    assert pq.read_table(output / "route_segments.parquet").num_rows >= 1
    for tile in first["tiles"]:
        result_dir = output / "tiles" / tile["tile_id"] / "result"
        assert (result_dir / "defects.parquet").is_file()
        assert (result_dir / "segments.parquet").is_file()

    second = run_tiled_analysis(
        points,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        output,
        analysis_config=config,
        source={"type": "synthetic", "profile": "boundary"},
    )
    assert second["run_stats"]["skipped_completed_tile_count"] == 2
    assert second["defect_count"] == 1

    monkeypatch.setattr("road_condition_core.route.ALGORITHM_VERSION", "test-next-version")
    third = run_tiled_analysis(
        points,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        output,
        analysis_config=config,
        source={"type": "synthetic", "profile": "boundary"},
    )
    assert third["run_stats"]["executed_completed_tile_count"] == 2
    assert third["run_stats"]["skipped_completed_tile_count"] == 0

    merged = aggregate_chunk_routes(
        [
            RouteChunkInput("chunk-a", output, 0.0),
            RouteChunkInput("chunk-b", output, 0.2),
        ],
        tmp_path / "merged-chunks",
    )
    assert merged["state"] == "completed"
    assert merged["chunk_count"] == 2
    assert merged["defect_count"] == 1


def test_failed_tile_keeps_partial_route_outputs(tmp_path) -> None:
    output = tmp_path / "partial"
    complete = output / "tiles" / "tile-000000"
    failed = output / "tiles" / "tile-000001"
    (complete / "result").mkdir(parents=True)
    failed.mkdir(parents=True)
    (complete / "status.json").write_text(
        json.dumps({"tile_id": "tile-000000", "state": "completed"}),
        encoding="utf-8",
    )
    (complete / "result" / "defects.json").write_text(
        json.dumps([_defect("owned", 5.0, 0.0, 0.08)]),
        encoding="utf-8",
    )
    (complete / "result" / "segments.json").write_text(
        json.dumps(
            [
                {
                    "segment_id": "segment-0",
                    "chainage_start_m": 0.0,
                    "chainage_end_m": 10.0,
                    "valid_coverage_ratio": 1.0,
                    "pothole_count": 1,
                    "pothole_area_m2": 0.24,
                    "pothole_volume_m3": 0.01,
                    "max_pothole_depth_m": 0.08,
                    "max_left_rut_depth_m": 0.0,
                    "max_right_rut_depth_m": 0.0,
                    "bump_count": 0,
                    "roughness_proxy_m": 0.005,
                    "geometry_score": 85.0,
                    "grade": "B",
                    "lane_id": None,
                    "road_zone": "corridor_fallback",
                }
            ]
        ),
        encoding="utf-8",
    )
    (failed / "status.json").write_text(
        json.dumps({"tile_id": "tile-000001", "state": "failed", "error": "fixture"}),
        encoding="utf-8",
    )
    manifest = aggregate_route_tiles(output)
    assert manifest["state"] == "partial"
    assert manifest["completed_tile_count"] == 1
    assert manifest["failed_tile_count"] == 1
    assert manifest["defect_count"] == 1
    assert (output / "route_manifest.json").is_file()
