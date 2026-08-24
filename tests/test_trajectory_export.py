import struct
from dataclasses import replace

import numpy as np

from rgbd_map.dataset import InterpolatedGps
from rgbd_map.exporters import write_browser_points
from rgbd_map.odometry import OdometryResult
from rgbd_map.pointcloud import PointCloudResult
from rgbd_map.trajectory import build_trajectory


def make_gps(count=5):
    return InterpolatedGps(
        monotonic_ns=np.arange(count, dtype=np.int64),
        latitude_deg=np.full(count, 37.0),
        longitude_deg=np.full(count, 126.0),
        altitude_msl_m=np.full(count, 10.0),
        geoid_separation_m=np.full(count, 20.0),
        ellipsoid_height_m=np.full(count, 30.0),
        course_deg=np.zeros(count),
        speed_m_s=np.full(count, 10.0),
        fix_quality=np.full(count, 4, dtype=np.int16),
        fix_quality_name=tuple("RTK fixed" for _ in range(count)),
        hdop=np.full(count, 0.6),
    )


def test_gps_pose_mode_equals_gps_positions():
    gps = make_gps()
    positions = np.column_stack((np.zeros(5), np.arange(5, dtype=float), np.zeros(5)))
    odometry = [OdometryResult.failed("test") for _ in range(5)]
    result = build_trajectory(gps, positions, odometry, pose_mode="gps")
    np.testing.assert_allclose(result.positions_enu_m, positions)
    assert result.metrics["visual_odometry_success_rate"] == 0.0


def test_hybrid_rejects_visual_motion_opposite_to_gps():
    gps = make_gps(count=2)
    positions = np.array([[0.0, 0.0, 0.0], [0.0, 2.0, 0.0]])
    opposite = OdometryResult(
        success=True,
        method="pnp",
        rotation_current_from_previous=np.eye(3),
        translation_previous_camera_m=np.array([0.0, 0.0, -2.0]),
        matches=100,
        inliers=80,
        inlier_ratio=0.8,
        translation_norm_m=2.0,
        rotation_deg=0.0,
        reprojection_error_px=0.5,
        reason="ok",
    )
    result = build_trajectory(
        gps,
        positions,
        [OdometryResult.failed("origin"), opposite],
        pose_mode="hybrid",
    )
    assert result.methods[1] == "gps_vector_rejected"
    np.testing.assert_allclose(result.raw_visual_positions_enu_m, positions)


def test_hybrid_removes_rotating_camera_gnss_lever_arm_from_visual_edge():
    gps = replace(
        make_gps(count=2),
        course_deg=np.array([0.0, 90.0]),
        speed_m_s=np.zeros(2),
    )
    antenna_positions = np.zeros((2, 3), dtype=np.float64)
    rotation_0 = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])
    rotation_1 = np.array([[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]])
    offset_camera = np.array([0.0, 0.0, 1.0])
    camera_delta_world = rotation_1 @ offset_camera - rotation_0 @ offset_camera
    estimate = OdometryResult(
        success=True,
        method="pnp",
        rotation_current_from_previous=rotation_1.T @ rotation_0,
        translation_previous_camera_m=rotation_0.T @ camera_delta_world,
        matches=100,
        inliers=80,
        inlier_ratio=0.8,
        translation_norm_m=float(np.linalg.norm(camera_delta_world)),
        rotation_deg=90.0,
        reprojection_error_px=0.5,
        reason="ok",
    )
    result = build_trajectory(
        gps,
        antenna_positions,
        [OdometryResult.failed("origin"), estimate],
        pose_mode="hybrid",
        level_correction_gain=0.0,
        camera_offset_forward_m=1.0,
    )

    np.testing.assert_allclose(result.raw_visual_positions_enu_m, antenna_positions, atol=1e-12)


def test_browser_point_binary_layout(tmp_path):
    cloud = PointCloudResult(
        points_enu_m=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
        colors_rgb=np.array([[10, 20, 30], [40, 50, 60]], dtype=np.uint8),
        sampled_frame_count=1,
        decoded_frame_count=1,
        valid_depth_sample_count=2,
    )
    path = tmp_path / "points.bin"
    write_browser_points(path, cloud)
    payload = path.read_bytes()
    magic, version, count, stride = struct.unpack("<4sIII", payload[:16])
    assert (magic, version, count, stride) == (b"RGBD", 1, 2, 16)
    assert len(payload) == 16 + 2 * 16
