from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import spsolve

from .dataset import InterpolatedGps
from .odometry import OdometryResult
from .orientation import (
    apply_heading_correction,
    gps_level_camera_rotation,
    optical_heading_deg,
    wrap_degrees,
)


@dataclass(frozen=True)
class TrajectoryResult:
    positions_enu_m: np.ndarray
    raw_visual_positions_enu_m: np.ndarray
    gps_positions_enu_m: np.ndarray
    rotations_enu_from_camera: np.ndarray
    edge_weights: np.ndarray
    methods: tuple[str, ...]
    metrics: dict


def _orthonormalize(matrix: np.ndarray) -> np.ndarray:
    u, _, vt = np.linalg.svd(matrix)
    result = u @ vt
    if np.linalg.det(result) < 0.0:
        u[:, -1] *= -1.0
        result = u @ vt
    return result


def _quality_weights(fix_quality: np.ndarray, hdop: np.ndarray) -> np.ndarray:
    result = np.full(len(fix_quality), 0.005, dtype=np.float64)
    result[fix_quality == 1] = 0.008
    result[fix_quality == 2] = 0.025
    result[fix_quality == 5] = 0.12
    result[fix_quality == 4] = 0.60
    hdop_values = np.asarray(hdop, dtype=np.float64)
    hdop_scale = np.ones_like(result)
    valid = np.isfinite(hdop_values) & (hdop_values > 0.0)
    hdop_scale[valid] = np.clip((0.8 / hdop_values[valid]) ** 2, 0.25, 4.0)
    return result * hdop_scale


def _solve_position_graph(
    edge_delta: np.ndarray,
    edge_weights: np.ndarray,
    gps_positions: np.ndarray,
    gps_weights: np.ndarray,
    vertical_gps_scale: float,
) -> np.ndarray:
    n = len(gps_positions)
    if n == 1:
        return gps_positions.copy()
    main = gps_weights.copy()
    upper = np.zeros(n - 1, dtype=np.float64)
    rhs = np.zeros((n, 3), dtype=np.float64)
    rhs += gps_weights[:, None] * gps_positions
    for index in range(1, n):
        weight = max(float(edge_weights[index]), 1e-6)
        main[index - 1] += weight
        main[index] += weight
        upper[index - 1] -= weight
        rhs[index - 1] -= weight * edge_delta[index]
        rhs[index] += weight * edge_delta[index]
    main[0] += 5.0
    rhs[0] += 5.0 * gps_positions[0]
    matrix_horizontal = diags((upper, main, upper), offsets=(-1, 0, 1), format="csc")
    result = np.empty_like(gps_positions)
    result[:, 0] = spsolve(matrix_horizontal, rhs[:, 0])
    result[:, 1] = spsolve(matrix_horizontal, rhs[:, 1])

    vertical_weights = gps_weights * float(vertical_gps_scale)
    main_z = vertical_weights.copy()
    upper_z = np.zeros(n - 1, dtype=np.float64)
    rhs_z = vertical_weights * gps_positions[:, 2]
    for index in range(1, n):
        weight = max(float(edge_weights[index]), 1e-6)
        main_z[index - 1] += weight
        main_z[index] += weight
        upper_z[index - 1] -= weight
        rhs_z[index - 1] -= weight * edge_delta[index, 2]
        rhs_z[index] += weight * edge_delta[index, 2]
    main_z[0] += 5.0
    rhs_z[0] += 5.0 * gps_positions[0, 2]
    matrix_vertical = diags((upper_z, main_z, upper_z), offsets=(-1, 0, 1), format="csc")
    result[:, 2] = spsolve(matrix_vertical, rhs_z)
    return result


def _percentiles(values: np.ndarray) -> dict[str, float]:
    if len(values) == 0:
        return {"p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "max": float(np.max(values)),
    }


def _visual_edge_is_gps_consistent(
    visual_delta_enu: np.ndarray,
    gps_delta_enu: np.ndarray,
    speed_m_s: float,
    max_angle_deg: float,
    min_distance_ratio: float,
    max_distance_ratio: float,
    max_vertical_error_m: float,
) -> bool:
    """Reject gross VO jumps using GNSS motion, without using any IMU value."""
    gps_horizontal = float(np.linalg.norm(gps_delta_enu[:2]))
    if speed_m_s < 2.0 or gps_horizontal < 0.5:
        return True
    visual_horizontal = float(np.linalg.norm(visual_delta_enu[:2]))
    if visual_horizontal < 1e-6:
        return False
    ratio = visual_horizontal / gps_horizontal
    if ratio < min_distance_ratio or ratio > max_distance_ratio:
        return False
    cosine = float(
        np.dot(visual_delta_enu[:2], gps_delta_enu[:2])
        / (visual_horizontal * gps_horizontal)
    )
    if cosine < float(np.cos(np.deg2rad(max_angle_deg))):
        return False
    vertical_error = abs(float(visual_delta_enu[2] - gps_delta_enu[2]))
    return vertical_error <= max(max_vertical_error_m, 0.5 * gps_horizontal)


def build_trajectory(
    gps: InterpolatedGps,
    gps_positions_enu_m: np.ndarray,
    odometry: list[OdometryResult],
    pose_mode: str = "hybrid",
    mount_roll_deg: float = 0.0,
    mount_pitch_deg: float = 0.0,
    mount_yaw_deg: float = 0.0,
    course_correction_gain: float = 0.08,
    level_correction_gain: float = 0.01,
    gps_weight_scale: float = 1.0,
    vertical_gps_scale: float = 0.10,
    max_visual_gps_angle_deg: float = 45.0,
    min_visual_gps_distance_ratio: float = 0.25,
    max_visual_gps_distance_ratio: float = 2.5,
    max_visual_vertical_error_m: float = 1.5,
    max_visual_edge_dt_s: float = 0.25,
    camera_offset_right_m: float = 0.0,
    camera_offset_down_m: float = 0.0,
    camera_offset_forward_m: float = 0.0,
) -> TrajectoryResult:
    n = len(gps_positions_enu_m)
    if len(odometry) != n:
        raise ValueError("odometry must contain one entry per frame")
    if pose_mode not in {"hybrid", "gps"}:
        raise ValueError("pose_mode must be 'hybrid' or 'gps'")

    rotations = np.empty((n, 3, 3), dtype=np.float64)
    raw_positions = np.empty((n, 3), dtype=np.float64)
    edge_delta = np.zeros((n, 3), dtype=np.float64)
    edge_weights = np.zeros(n, dtype=np.float64)
    methods = ["origin"]
    camera_offset = np.array(
        [camera_offset_right_m, camera_offset_down_m, camera_offset_forward_m],
        dtype=np.float64,
    )
    rotations[0] = gps_level_camera_rotation(
        gps.course_deg[0], mount_roll_deg, mount_pitch_deg, mount_yaw_deg
    )
    raw_positions[0] = gps_positions_enu_m[0]
    edge_weights[0] = 1.0

    if pose_mode == "gps":
        for index in range(1, n):
            rotations[index] = gps_level_camera_rotation(
                gps.course_deg[index], mount_roll_deg, mount_pitch_deg, mount_yaw_deg
            )
            edge_delta[index] = gps_positions_enu_m[index] - gps_positions_enu_m[index - 1]
            raw_positions[index] = gps_positions_enu_m[index]
            edge_weights[index] = 0.05
            methods.append("gps_course")
        positions = gps_positions_enu_m.copy()
    else:
        for index in range(1, n):
            estimate = odometry[index]
            edge_dt_s = float(gps.monotonic_ns[index] - gps.monotonic_ns[index - 1]) / 1e9
            gps_delta = gps_positions_enu_m[index] - gps_positions_enu_m[index - 1]
            if estimate.success and edge_dt_s <= max_visual_edge_dt_s:
                candidate = rotations[index - 1] @ estimate.rotation_current_from_previous.T
                level = gps_level_camera_rotation(
                    gps.course_deg[index], mount_roll_deg, mount_pitch_deg, mount_yaw_deg
                )
                heading_target = optical_heading_deg(level)
                heading_error = float(
                    wrap_degrees(heading_target - optical_heading_deg(candidate))
                )
                heading_gain = course_correction_gain if gps.speed_m_s[index] >= 2.0 else 0.0
                candidate = apply_heading_correction(candidate, heading_gain * heading_error)
                if level_correction_gain > 0.0:
                    # Level roll/pitch without re-injecting noisy low-speed GPS yaw.
                    attitude_level = apply_heading_correction(
                        level,
                        float(wrap_degrees(optical_heading_deg(candidate) - optical_heading_deg(level))),
                    )
                    candidate = _orthonormalize(
                        (1.0 - level_correction_gain) * candidate
                        + level_correction_gain * attitude_level
                    )
                visual_camera_delta = (
                    rotations[index - 1] @ estimate.translation_previous_camera_m
                )
                lever_delta = candidate @ camera_offset - rotations[index - 1] @ camera_offset
                visual_gnss_delta = visual_camera_delta - lever_delta
                if _visual_edge_is_gps_consistent(
                    visual_gnss_delta,
                    gps_delta,
                    float(gps.speed_m_s[index]),
                    max_visual_gps_angle_deg,
                    min_visual_gps_distance_ratio,
                    max_visual_gps_distance_ratio,
                    max_visual_vertical_error_m,
                ):
                    rotations[index] = candidate
                    edge_delta[index] = visual_gnss_delta
                    if estimate.method == "pnp":
                        edge_weights[index] = 1.0 + min(2.0, estimate.inliers / 300.0)
                    else:
                        edge_weights[index] = 0.45
                    methods.append(estimate.method)
                else:
                    rotations[index] = level
                    edge_delta[index] = gps_delta
                    edge_weights[index] = 0.04
                    methods.append("gps_vector_rejected")
            else:
                rotations[index] = gps_level_camera_rotation(
                    gps.course_deg[index], mount_roll_deg, mount_pitch_deg, mount_yaw_deg
                )
                edge_delta[index] = gps_delta
                edge_weights[index] = 0.04
                methods.append("time_gap_fallback" if estimate.success else "gps_fallback")
            raw_positions[index] = raw_positions[index - 1] + edge_delta[index]

        gps_weights = _quality_weights(gps.fix_quality, gps.hdop) * float(gps_weight_scale)
        positions = _solve_position_graph(
            edge_delta,
            edge_weights,
            gps_positions_enu_m,
            gps_weights,
            vertical_gps_scale,
        )

    raw_horizontal = np.linalg.norm(raw_positions[:, :2] - gps_positions_enu_m[:, :2], axis=1)
    fused_horizontal = np.linalg.norm(positions[:, :2] - gps_positions_enu_m[:, :2], axis=1)
    raw_vertical = np.abs(raw_positions[:, 2] - gps_positions_enu_m[:, 2])
    fused_vertical = np.abs(positions[:, 2] - gps_positions_enu_m[:, 2])
    heading_error = np.asarray(
        [
            abs(
                float(
                    wrap_degrees(
                        optical_heading_deg(rotations[index])
                        - optical_heading_deg(
                            gps_level_camera_rotation(
                                gps.course_deg[index],
                                mount_roll_deg,
                                mount_pitch_deg,
                                mount_yaw_deg,
                            )
                        )
                    )
                )
            )
            for index in range(n)
        ],
        dtype=np.float64,
    )
    gps_path_length = float(
        np.linalg.norm(np.diff(gps_positions_enu_m[:, :2], axis=0), axis=1).sum()
    )
    fused_path_length = float(
        np.linalg.norm(np.diff(positions[:, :2], axis=0), axis=1).sum()
    )
    method_counts = Counter(methods[1:])
    visual_success = method_counts.get("pnp", 0) + method_counts.get("essential_gps_scale", 0)
    metrics = {
        "pose_mode": pose_mode,
        "frame_count": n,
        "visual_odometry_success_count": int(visual_success),
        "visual_odometry_success_rate": float(visual_success / max(n - 1, 1)),
        "method_counts": dict(method_counts),
        "visual_gps_gate": {
            "max_angle_deg": float(max_visual_gps_angle_deg),
            "min_distance_ratio": float(min_visual_gps_distance_ratio),
            "max_distance_ratio": float(max_visual_gps_distance_ratio),
            "max_vertical_error_m": float(max_visual_vertical_error_m),
            "max_edge_dt_s": float(max_visual_edge_dt_s),
        },
        "camera_from_gnss_offset_camera_axes_m": camera_offset.tolist(),
        "pre_graph_gps_aided_vs_gps_horizontal_m": _percentiles(raw_horizontal),
        "gps_constraint_residual_horizontal_m": _percentiles(fused_horizontal),
        "pre_graph_gps_aided_vs_gps_vertical_m": _percentiles(raw_vertical),
        "gps_constraint_residual_vertical_m": _percentiles(fused_vertical),
        "optical_heading_vs_gps_mount_heading_deg": _percentiles(heading_error),
        "gps_horizontal_path_length_m": gps_path_length,
        "fused_horizontal_path_length_m": fused_path_length,
        "gps_fix_quality_counts": {
            str(key): int(value) for key, value in Counter(gps.fix_quality.tolist()).items()
        },
        "warning": (
            "The reported fused-to-GPS values are optimization constraint residuals, not an "
            "independent absolute-accuracy measurement. The pre-graph path is also GPS-aided by "
            "heading, essential-matrix scale and fallbacks. Inspect the cloud against independent "
            "roads, buildings or surveyed control points."
        ),
    }
    return TrajectoryResult(
        positions_enu_m=positions,
        raw_visual_positions_enu_m=raw_positions,
        gps_positions_enu_m=gps_positions_enu_m,
        rotations_enu_from_camera=rotations,
        edge_weights=edge_weights,
        methods=tuple(methods),
        metrics=metrics,
    )
