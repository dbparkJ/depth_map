import numpy as np

from rgbd_map.geodesy import LocalENU
from rgbd_map.orientation import (
    apply_heading_correction,
    gps_level_camera_rotation,
    optical_heading_deg,
    wrap_degrees,
)


def test_local_enu_roundtrip():
    frame = LocalENU(126.75, 37.87, 40.0)
    source = np.array(
        [
            [126.75, 37.87, 40.0],
            [126.7502, 37.8701, 43.5],
            [126.7498, 37.8699, 35.2],
        ]
    )
    enu = frame.geodetic_to_enu(source[:, 0], source[:, 1], source[:, 2])
    restored = frame.enu_to_geodetic(enu)
    np.testing.assert_allclose(restored, source, atol=1e-7)


def test_gps_level_camera_axes_and_heading():
    north = gps_level_camera_rotation(0.0)
    np.testing.assert_allclose(north[:, 0], [1.0, 0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(north[:, 1], [0.0, 0.0, -1.0], atol=1e-12)
    np.testing.assert_allclose(north[:, 2], [0.0, 1.0, 0.0], atol=1e-12)
    assert abs(optical_heading_deg(gps_level_camera_rotation(123.4)) - 123.4) < 1e-9


def test_heading_correction_uses_clockwise_gps_convention():
    corrected = apply_heading_correction(gps_level_camera_rotation(300.0), 8.0)
    error = float(wrap_degrees(optical_heading_deg(corrected) - 308.0))
    assert abs(error) < 1e-10


def test_mount_angles_follow_camera_axis_conventions():
    yaw_right = gps_level_camera_rotation(0.0, mount_yaw_deg=10.0)
    assert abs(optical_heading_deg(yaw_right) - 10.0) < 1e-10

    pitch_down = gps_level_camera_rotation(0.0, mount_pitch_deg=10.0)
    assert pitch_down[2, 2] < 0.0
    assert abs(optical_heading_deg(pitch_down)) < 1e-10

    roll_clockwise = gps_level_camera_rotation(0.0, mount_roll_deg=10.0)
    np.testing.assert_allclose(roll_clockwise[:, 2], [0.0, 1.0, 0.0], atol=1e-12)
    assert roll_clockwise[2, 0] < 0.0
