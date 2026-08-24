from __future__ import annotations

import numpy as np


def wrap_degrees(value: float | np.ndarray) -> float | np.ndarray:
    return (value + 180.0) % 360.0 - 180.0


def rotation_x(angle_rad: float) -> np.ndarray:
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]], dtype=np.float64)


def rotation_y(angle_rad: float) -> np.ndarray:
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]], dtype=np.float64)


def rotation_z(angle_rad: float) -> np.ndarray:
    c, s = np.cos(angle_rad), np.sin(angle_rad)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)


def gps_level_camera_rotation(
    course_deg: float,
    mount_roll_deg: float = 0.0,
    mount_pitch_deg: float = 0.0,
    mount_yaw_deg: float = 0.0,
) -> np.ndarray:
    """Return R_ENU_CAM for OpenCV camera axes (+right, +down, +forward).

    Mount angles follow vehicle/camera language: yaw is positive to the right,
    pitch is positive down, and roll is positive clockwise in the image.
    """
    heading = np.deg2rad(course_deg)
    right = np.array([np.cos(heading), -np.sin(heading), 0.0], dtype=np.float64)
    down = np.array([0.0, 0.0, -1.0], dtype=np.float64)
    forward = np.array([np.sin(heading), np.cos(heading), 0.0], dtype=np.float64)
    base = np.column_stack((right, down, forward))
    mount = (
        rotation_y(np.deg2rad(mount_yaw_deg))
        @ rotation_x(-np.deg2rad(mount_pitch_deg))
        @ rotation_z(np.deg2rad(mount_roll_deg))
    )
    return base @ mount


def optical_heading_deg(rotation_enu_from_camera: np.ndarray) -> float:
    forward = rotation_enu_from_camera[:, 2]
    return float(np.rad2deg(np.arctan2(forward[0], forward[1])) % 360.0)


def apply_heading_correction(rotation_enu_from_camera: np.ndarray, correction_deg: float) -> np.ndarray:
    # GPS heading grows clockwise from north, whereas a positive mathematical
    # Z rotation is counter-clockwise in ENU.  The signs are therefore opposite.
    return rotation_z(-np.deg2rad(correction_deg)) @ rotation_enu_from_camera
