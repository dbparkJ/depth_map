import json
import struct
from pathlib import Path

import numpy as np
import pytest

from rgbd_map.dataset import FrameRecord, InterpolatedGps
from rgbd_map.exporters import export_mapping, write_browser_points, write_ply
from rgbd_map.geodesy import LocalENU
from rgbd_map.odometry import OdometryResult
from rgbd_map.pointcloud import PointCloudBuildStats, PointCloudResult
from rgbd_map.trajectory import TrajectoryResult


BROWSER_DTYPE = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("r", "u1"),
        ("g", "u1"),
        ("b", "u1"),
        ("a", "u1"),
    ]
)
PLY_DTYPE = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("r", "u1"),
        ("g", "u1"),
        ("b", "u1"),
    ]
)


def make_cloud() -> PointCloudResult:
    points = np.array(
        [
            [-4.0, 0.0, 0.0],
            [-2.0, 1.0, 0.5],
            [0.0, 2.0, 1.0],
            [2.0, 3.0, 1.5],
            [4.0, 4.0, 2.0],
        ],
        dtype=np.float32,
    )
    colors = np.array(
        [
            [10, 20, 30],
            [40, 50, 60],
            [70, 80, 90],
            [100, 110, 120],
            [130, 140, 150],
        ],
        dtype=np.uint8,
    )
    stats = PointCloudBuildStats(
        total_frame_count=2,
        sampled_frame_count=2,
        decoded_frame_count=2,
        candidate_pixel_sample_count=12,
        valid_depth_sample_count=10,
        invalid_depth_sample_count=2,
        discarded_by_per_frame_cap=2,
        points_before_voxel=8,
        unique_voxel_count=5,
        discarded_by_voxel=3,
        points_before_final_cap=5,
        discarded_by_final_cap=0,
    )
    return PointCloudResult(
        points_enu_m=points,
        colors_rgb=colors,
        sampled_frame_count=2,
        decoded_frame_count=2,
        valid_depth_sample_count=10,
        stats=stats,
    )


def make_export_context(tmp_path: Path):
    frames = [
        FrameRecord(
            output_index=index,
            source_index=index,
            wall_time=f"2026-01-01T00:00:0{index}",
            monotonic_ns=index * 1_000_000_000,
            rgb_path=tmp_path / f"rgb_{index}.jpg",
            depth_path=tmp_path / f"depth_{index}.png",
        )
        for index in range(2)
    ]
    gps = InterpolatedGps(
        monotonic_ns=np.array([0, 1_000_000_000], dtype=np.int64),
        latitude_deg=np.array([37.0, 37.00001]),
        longitude_deg=np.array([126.0, 126.00001]),
        altitude_msl_m=np.array([10.0, 10.0]),
        geoid_separation_m=np.array([20.0, 20.0]),
        ellipsoid_height_m=np.array([30.0, 30.0]),
        course_deg=np.array([0.0, 0.0]),
        speed_m_s=np.array([1.0, 1.0]),
        fix_quality=np.array([4, 4], dtype=np.int16),
        fix_quality_name=("RTK fixed", "RTK fixed"),
        hdop=np.array([0.6, 0.6]),
    )
    positions = np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    trajectory = TrajectoryResult(
        positions_enu_m=positions,
        raw_visual_positions_enu_m=positions.copy(),
        gps_positions_enu_m=positions.copy(),
        rotations_enu_from_camera=np.repeat(np.eye(3)[None, :, :], 2, axis=0),
        edge_weights=np.ones(2),
        methods=("gps", "gps"),
        metrics={},
    )
    odometry = [OdometryResult.failed("test") for _ in frames]
    origin = LocalENU(126.0, 37.0, 30.0)
    viewer_source = tmp_path / "viewer_source"
    viewer_source.mkdir()
    return frames, gps, origin, trajectory, odometry, viewer_source


def test_export_summary_separates_ply_and_browser_counts(tmp_path):
    frames, gps, origin, trajectory, odometry, viewer_source = make_export_context(
        tmp_path
    )
    output = tmp_path / "mapping"
    summary = export_mapping(
        output,
        viewer_source,
        frames,
        gps,
        origin,
        trajectory,
        odometry,
        make_cloud(),
        3,
        {"cloud_preset": "balanced"},
    )

    written_summary = json.loads((output / "data" / "summary.json").read_text())
    assert written_summary == summary
    cloud = summary["cloud"]
    assert cloud["point_count"] == cloud["ply_point_count"] == 5
    assert cloud["browser_point_count"] == 3
    assert cloud["browser_point_count"] <= cloud["ply_point_count"]
    assert cloud["candidate_pixel_sample_count"] == 12
    assert cloud["valid_depth_sample_count"] == 10
    assert cloud["valid_depth_sample_count_before_voxel"] == 10
    assert cloud["discarded_by_per_frame_cap"] == 2
    assert cloud["points_before_voxel"] == 8
    assert cloud["unique_voxel_count"] == 5
    assert cloud["unique_voxel_count_before_final_cap"] == 5
    assert cloud["discarded_by_voxel"] == 3
    assert cloud["discarded_by_final_cap"] == 0

    payload = (output / "data" / "points.bin").read_bytes()
    magic, version, count, stride = struct.unpack("<4sIII", payload[:16])
    assert (magic, version, count, stride) == (b"RGBD", 1, 3, 16)
    assert count == cloud["browser_point_count"]
    assert len(payload) == 16 + count * stride


def test_trajectory_only_summary_has_all_zero_point_counts(tmp_path):
    frames, gps, origin, trajectory, odometry, viewer_source = make_export_context(
        tmp_path
    )
    summary = export_mapping(
        tmp_path / "trajectory_only",
        viewer_source,
        frames,
        gps,
        origin,
        trajectory,
        odometry,
        None,
        3,
        {},
    )
    assert summary["cloud"] == {
        "point_count": 0,
        "ply_point_count": 0,
        "browser_point_count": 0,
        "trajectory_only": True,
    }


def test_browser_writer_uses_spatial_indices_and_preserves_color_pairs(
    tmp_path, monkeypatch
):
    cloud = make_cloud()
    calls = []

    def select_indices(points, max_points):
        calls.append((points, max_points))
        return np.array([4, 2, 0], dtype=np.int64)

    monkeypatch.setattr("rgbd_map.exporters.spatially_sample_indices", select_indices)
    monkeypatch.setattr("rgbd_map.exporters._WRITE_CHUNK_POINTS", 2)
    path = tmp_path / "points.bin"
    assert write_browser_points(path, cloud, 3) == 3
    assert len(calls) == 1
    assert calls[0][0] is cloud.points_enu_m
    assert calls[0][1] == 3

    records = np.frombuffer(path.read_bytes()[16:], dtype=BROWSER_DTYPE)
    np.testing.assert_allclose(records["x"], [4.0, 0.0, -4.0])
    np.testing.assert_array_equal(records["r"], [130, 70, 10])
    np.testing.assert_array_equal(records["a"], [255, 255, 255])


def test_chunked_ply_writer_preserves_every_point(tmp_path, monkeypatch):
    cloud = make_cloud()
    monkeypatch.setattr("rgbd_map.exporters._WRITE_CHUNK_POINTS", 2)
    path = tmp_path / "cloud.ply"
    write_ply(path, cloud, LocalENU(126.0, 37.0, 30.0))

    payload = path.read_bytes()
    body_offset = payload.index(b"end_header\n") + len(b"end_header\n")
    header = payload[:body_offset]
    records = np.frombuffer(payload[body_offset:], dtype=PLY_DTYPE)
    assert b"element vertex 5\n" in header
    assert len(records) == 5
    np.testing.assert_allclose(records["x"], cloud.points_enu_m[:, 0])
    np.testing.assert_array_equal(records["b"], cloud.colors_rgb[:, 2])


def test_browser_writer_rejects_nonpositive_cap(tmp_path):
    with pytest.raises(ValueError, match="max_points must be positive"):
        write_browser_points(tmp_path / "points.bin", make_cloud(), 0)
