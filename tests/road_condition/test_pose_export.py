from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from rgbd_map.calibration import (
    build_analysis_source_manifest,
    build_camera_pose_payload,
    write_camera_pose_bundle,
)
from rgbd_map.dataset import CameraModel, FrameRecord
from rgbd_map.exporters import write_ply
from rgbd_map.geodesy import LocalENU
from rgbd_map.trajectory import TrajectoryResult
from road_condition_core.calibration import (
    analyze_flat_surface_noise,
    recommend_thresholds,
    write_calibration_bundle,
)
from road_condition_core.io import load_mapping_bundle


def _trajectory() -> TrajectoryResult:
    positions = np.array([[0.0, 0.0, 1.5], [1.0, 0.0, 1.5]], dtype=np.float64)
    rotations = np.repeat(np.eye(3)[None, :, :], 2, axis=0)
    return TrajectoryResult(
        positions_enu_m=positions,
        raw_visual_positions_enu_m=positions.copy(),
        gps_positions_enu_m=positions.copy(),
        rotations_enu_from_camera=rotations,
        edge_weights=np.ones(2, dtype=np.float64),
        methods=("origin", "gps_course"),
        metrics={},
    )


def _frames(tmp_path: Path) -> list[FrameRecord]:
    return [
        FrameRecord(index, 10 + index, "", 1_000 + index, tmp_path / "rgb", tmp_path / "depth")
        for index in range(2)
    ]


def test_pose_matrix_export_and_optional_bundle_loading(tmp_path: Path) -> None:
    camera = CameraModel(width=640, height=480, fx=500.0, fy=501.0, cx=320.0, cy=240.0)
    payload = build_camera_pose_payload(
        _frames(tmp_path),
        _trajectory(),
        camera,
        pose_quality_score=np.array([1.0, 0.6], dtype=np.float32),
        camera_offset_right_m=0.2,
        camera_offset_down_m=0.1,
        camera_offset_forward_m=0.3,
    )
    assert payload["T_enu_camera"].shape == (2, 4, 4)
    np.testing.assert_allclose(payload["T_enu_camera"][:, :3, 3], [[0.2, 0.1, 1.8], [1.2, 0.1, 1.8]])
    assert len(payload["frame_index"]) == len(payload["T_enu_camera"])
    for matrix in payload["T_enu_camera"]:
        np.testing.assert_allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-9)

    mapping = tmp_path / "mapping"
    data = mapping / "data"
    data.mkdir(parents=True)
    points = np.array(
        [[0.0, 0.0, 0.0], [0.2, 0.0, 0.0], [0.4, 0.0, 0.0]], dtype=np.float32
    )
    colors = np.full((3, 3), 100, dtype=np.uint8)
    write_ply(data / "cloud_raw_enu.ply", points, colors, LocalENU(126.0, 37.0, 30.0))
    (data / "trajectory.json").write_text(
        json.dumps({"fused": [[0.0, 0.0, 1.5], [1.0, 0.0, 1.5]]}),
        encoding="utf-8",
    )
    (data / "summary.json").write_text('{"format_version": 1}', encoding="utf-8")
    manifest = build_analysis_source_manifest(
        dataset_id="fixture",
        mapping_commit_sha="a" * 40,
        camera_model="fixture-camera",
        camera_height_m=None,
        mount_yaw_deg=0.0,
        mount_pitch_deg=0.0,
        mount_roll_deg=0.0,
        camera_offset_right_m=0.0,
        camera_offset_down_m=0.0,
        camera_offset_forward_m=0.0,
        rgb_depth_alignment="aligned_depth_to_rgb",
        calibration_status="unknown",
    )
    write_camera_pose_bundle(data, payload, manifest)
    bundle = load_mapping_bundle(mapping, stage="raw")
    assert bundle.camera_poses is not None
    assert bundle.analysis_capabilities["camera_pose_contract"] is True
    assert bundle.analysis_quality["manual_review_required"] is True
    assert "manual_review_required" in bundle.analysis_quality["quality_flags"]


def test_noise_scaled_recommendation_and_calibration_bundle(tmp_path: Path) -> None:
    rng = np.random.default_rng(13)
    x = rng.uniform(-2.0, 2.0, 20_000)
    y = rng.uniform(-1.0, 1.0, 20_000)
    z = 0.01 * x + 0.003 * x * y + rng.normal(0.0, 0.002, len(x))
    distance = rng.uniform(1.0, 18.0, len(x))
    report = analyze_flat_surface_noise(np.column_stack((x, y, z)), distance)
    assert report["global"]["count"] == len(x)
    assert 0.001 < report["global"]["mad_m"] < 0.003
    recommendation = recommend_thresholds(report, calibration_status="unknown")
    assert recommendation["approval_status"] == "manual_review_required"
    assert recommendation["pothole_min_depth_m"] > report["global"]["rmse_m"]

    artifacts = write_calibration_bundle(
        tmp_path / "calibration",
        {"format_version": 1, "calibration_status": "unknown"},
        noise_report=report,
    )
    assert set(artifacts) == {
        "manifest",
        "flat_surface_noise",
        "pothole_ground_truth",
        "rut_ground_truth",
        "threshold_recommendation",
        "calibration_report",
    }
    assert (tmp_path / "calibration" / "calibration_report.html").is_file()
