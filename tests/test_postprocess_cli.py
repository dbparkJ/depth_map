from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from rgbd_map.exporters import read_ply, write_ply
from rgbd_map.geodesy import LocalENU
from rgbd_map.postprocess_cli import build_parser, run
from rgbd_map.postprocess_io import atomic_savez_compressed, validate_browser_binary


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_existing_output(tmp_path: Path) -> Path:
    output = tmp_path / "mapping"
    data = output / "data"
    data.mkdir(parents=True)
    cluster = np.array(
        [
            [x, y, 0.0]
            for x in (0.00, 0.05, 0.10, 0.15, 0.20)
            for y in (0.00, 0.05, 0.10)
        ],
        dtype=np.float32,
    )
    # The isolated point shares a populated core tile with the cluster so the
    # documented small-tile preservation rule does not exempt it.
    points = np.vstack((cluster, [[0.8, 0.8, -0.5]])).astype(np.float32)
    colors = np.tile(np.array([[80, 100, 120]], dtype=np.uint8), (len(points), 1))
    write_ply(
        data / "cloud_raw_enu.ply",
        points,
        colors,
        LocalENU(126.0, 37.0, 30.0),
        comments={"pointcloud_stage": "raw"},
    )
    atomic_savez_compressed(
        data / "cloud_raw_metadata.npz",
        observation_count=np.full(len(points), 3, dtype=np.uint32),
        distinct_frame_count=np.full(len(points), 3, dtype=np.uint32),
        position_std_m=np.zeros(len(points), dtype=np.float32),
        mean_depth_m=np.full(len(points), 5.0, dtype=np.float32),
    )
    (data / "trajectory.json").write_text(
        json.dumps({"fused": [[0.0, 0.0, 1.5], [0.2, 0.0, 1.5]]}),
        encoding="utf-8",
    )
    (data / "trajectory.csv").write_text("sentinel trajectory\n", encoding="utf-8")
    (data / "odometry.csv").write_text("sentinel odometry\n", encoding="utf-8")
    summary = {
        "format_version": 1,
        "origin": {
            "longitude_deg": 126.0,
            "latitude_deg": 37.0,
            "ellipsoid_height_m": 30.0,
        },
        "cloud": {"point_count": len(points), "ply_point_count": len(points)},
        "parameters": {
            "voxel_size_m": 0.05,
            "browser_max_points": 50,
            "resolved_cloud_config": {
                "voxel_size_m": 0.05,
                "browser_max_points": 50,
            },
        },
    }
    (data / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return output


def _args(output: Path, preset: str):
    return build_parser().parse_args(
        [
            "--output",
            str(output),
            "--postprocess-preset",
            preset,
            "--neighbor-backend",
            "scipy",
            "--ground-backend",
            "off",
            "--no-auto-postprocess-fallback",
            "--postprocess-tile-size-m",
            "1.0",
            "--postprocess-tile-overlap-m",
            "0.30",
        ]
    )


def test_postprocess_only_rewrites_clean_products_not_trajectory_or_raw(tmp_path: Path):
    output = _make_existing_output(tmp_path)
    data = output / "data"
    protected = [
        data / "trajectory.json",
        data / "trajectory.csv",
        data / "odometry.csv",
        data / "cloud_raw_enu.ply",
        data / "cloud_raw_metadata.npz",
    ]
    before = {path.name: (_digest(path), path.stat().st_mtime_ns) for path in protected}

    first = run(_args(output, "road-map"))
    raw_count = first["cloud"]["raw_point_count"]
    clean_count = first["cloud"]["clean_point_count"]
    removed_count = first["cloud"]["removed_point_count"]
    assert raw_count == clean_count + removed_count
    assert removed_count == 1
    assert first["cloud"]["point_count"] == first["cloud"]["ply_point_count"]
    assert first["cloud"]["postprocess_preset"] == "road-map"
    assert first["cloud"]["neighbor_backend"] == "scipy"
    for name in (
        "cloud_clean_enu.ply",
        "cloud_removed_enu.ply",
        "cloud_enu.ply",
        "removed_points_metadata.npz",
        "points_raw.bin",
        "points_clean.bin",
        "points_removed.bin",
        "points.bin",
        "postprocess_report.json",
        "postprocess_stages.json",
        "postprocess_parameters.json",
    ):
        assert (data / name).is_file(), name
    for name in (
        "top_before_after.png",
        "side_before_after.png",
        "removed_reason_top.png",
        "representative_tiles.json",
        "run_summary.txt",
    ):
        assert (output / "diagnostics" / name).is_file(), name

    clean_points, _colors, _comments = read_ply(data / "cloud_clean_enu.ply")
    assert len(clean_points) == clean_count
    for filename, field in (
        ("points_raw.bin", "raw_browser_point_count"),
        ("points_clean.bin", "clean_browser_point_count"),
        ("points_removed.bin", "removed_browser_point_count"),
        ("points.bin", "browser_point_count"),
    ):
        validate_browser_binary(data / filename, first["cloud"][field])

    report = json.loads((data / "postprocess_report.json").read_text())
    assert report["input"]["voxel_size_m"] == 0.05
    assert report["output"]["clean_point_count"] == clean_count
    assert all(
        value >= 0
        for counts in report["reasons"].values()
        for value in counts.values()
    )

    second = run(_args(output, "conservative"))
    assert second["cloud"]["postprocess_preset"] == "conservative"
    assert "resolved_postprocess_config" not in second["parameters"]
    assert second["parameters"]["last_postprocess_only_config"]["preset"] == "conservative"
    second_report = json.loads((data / "postprocess_report.json").read_text())
    assert second_report["input"]["raw_build_depth_edge"] == {
        "status": "not_recorded_in_existing_summary"
    }
    after = {path.name: (_digest(path), path.stat().st_mtime_ns) for path in protected}
    assert after == before


def test_postprocess_only_can_write_capped_debug_stage_artifacts(tmp_path: Path):
    output = _make_existing_output(tmp_path)
    data = output / "data"
    protected = [
        data / "trajectory.json",
        data / "trajectory.csv",
        data / "odometry.csv",
        data / "cloud_raw_enu.ply",
        data / "cloud_raw_metadata.npz",
    ]
    before = {path.name: (_digest(path), path.stat().st_mtime_ns) for path in protected}
    args = _args(output, "road-map")
    args.write_debug_stages = True
    args.debug_stage_max_points = 5
    args.write_postprocess_diagnostics = False

    summary = run(args)

    index_path = data / "debug_stages" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert summary["cloud"]["debug_stages"] == "debug_stages/index.json"
    assert summary["cloud"]["debug_stage_max_points"] == 5
    assert index["debug_stage_max_points"] == 5
    assert index["accounting"]["delta_union_equals_final_removed"] is True
    assert len(index["stages"]) == 8
    for record in index["stages"]:
        stage = json.loads(
            (output / record["stage_json"]).read_text(encoding="utf-8")
        )
        assert stage["survivors"]["sample_point_count"] <= 5
        assert stage["removed_this_stage"]["sample_point_count"] <= 5
        for relative in stage["diagnostics"].values():
            assert (output / relative).is_file()

    after = {path.name: (_digest(path), path.stat().st_mtime_ns) for path in protected}
    assert after == before
