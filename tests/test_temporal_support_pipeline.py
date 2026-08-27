from __future__ import annotations

from pathlib import Path

import numpy as np

from rgbd_map.dataset import FrameRecord
from rgbd_map.frame_quality import audit_cloud_frames
from rgbd_map.odometry import OdometryResult
from rgbd_map.pointcloud import (
    _local_coarse_support_run,
    _map_coarse_support,
    _merge_coarse_runs,
    voxel_position_std_upper_bound,
)
from rgbd_map.postprocess import RemovalReason, run_postprocess
from rgbd_map.postprocess_config import resolve_postprocess_config
from rgbd_map.trajectory import TrajectoryResult


def test_fine_voxel_spread_bound_makes_twelve_centimeters_impossible():
    assert np.isclose(voxel_position_std_upper_bound(0.03), np.sqrt(3) * 0.015)
    assert voxel_position_std_upper_bound(0.03) < 0.12


def test_coarse_support_deduplicates_pixels_and_counts_separated_views():
    first_points = np.array([[0.01, 0.01, 5.0], [0.02, 0.01, 5.01]])
    second_points = np.array([[0.03, 0.01, 5.02]])
    first = _local_coarse_support_run(
        first_points, np.array([5.0, 5.01]), 0.15, time_s=0.0, path_m=0.0
    )
    second = _local_coarse_support_run(
        second_points, np.array([5.02]), 0.15, time_s=0.6, path_m=0.4
    )
    merged = _merge_coarse_runs(first, second)

    mapped = _map_coarse_support(
        np.array([[0.02, 0.01, 5.01]]),
        np.array([5.01]),
        merged,
        merged,
        near_voxel_size_m=0.15,
        far_voxel_size_m=0.25,
        far_start_m=20.0,
        min_baseline_m=0.3,
        min_time_separation_s=0.5,
    )

    assert first.frame_counts.tolist() == [1]
    assert first.observation_counts.tolist() == [2]
    assert mapped["support_observation_count"].tolist() == [3]
    assert mapped["support_distinct_frame_count"].tolist() == [2]
    assert mapped["independent_view_count"].tolist() == [2]


def test_dense_far_single_view_curtain_is_removed_but_supported_wall_is_kept():
    curtain = np.column_stack(
        (np.linspace(0.0, 3.0, 80), np.zeros(80), np.full(80, 25.0))
    )
    wall = curtain + np.array([0.0, 1.0, 0.0])
    points = np.vstack((curtain, wall)).astype(np.float32)
    colors = np.full((len(points), 3), 100, dtype=np.uint8)
    metadata = {
        "observation_count": np.ones(len(points), dtype=np.uint32),
        "distinct_frame_count": np.ones(len(points), dtype=np.uint32),
        "position_std_m": np.zeros(len(points), dtype=np.float32),
        "mean_depth_m": np.full(len(points), 25.0, dtype=np.float32),
        "support_observation_count": np.r_[
            np.ones(80), np.full(80, 2)
        ].astype(np.uint32),
        "support_distinct_frame_count": np.r_[
            np.ones(80), np.full(80, 2)
        ].astype(np.uint16),
        "independent_view_count": np.r_[
            np.ones(80), np.full(80, 2)
        ].astype(np.uint16),
        "support_time_span_s": np.r_[np.zeros(80), np.ones(80)].astype(np.float32),
        "support_path_span_m": np.r_[np.zeros(80), np.ones(80)].astype(np.float32),
        "support_position_std_m": np.zeros(len(points), dtype=np.float32),
        "support_depth_std_m": np.zeros(len(points), dtype=np.float32),
        "temporal_test_count": np.zeros(len(points), dtype=np.uint16),
        "temporal_support_count": np.r_[np.zeros(80), np.ones(80)].astype(np.uint16),
        "temporal_contradiction_count": np.zeros(len(points), dtype=np.uint16),
        "far_depth_risk_count": np.ones(len(points), dtype=np.uint32),
        "source_frame_id": np.r_[np.zeros(80), np.full(80, -1)].astype(np.int32),
        "pose_quality_score": np.ones(len(points), dtype=np.float32),
    }
    config = resolve_postprocess_config(
        "road-map-temporal",
        0.03,
        {"map_envelope_mode": "off"},
    )

    result = run_postprocess(
        points,
        colors,
        metadata,
        None,
        config,
        neighbor_backend="scipy",
        ground_backend="off",
    )

    assert not np.any(result.keep_mask[:80])
    assert np.mean(result.keep_mask[80:]) >= 0.95
    assert np.all(
        result.removal_reason_bits[:80] & int(RemovalReason.FAR_DEPTH_UNTRUSTED)
    )


def _odometry(success: bool, method: str = "pnp") -> OdometryResult:
    if not success:
        return OdometryResult.failed("test_failure")
    return OdometryResult(
        True,
        method,
        np.eye(3),
        np.array([1.0, 0.0, 0.0]),
        100,
        80,
        0.8,
        1.0,
        0.0,
        0.5,
        "ok",
    )


def test_pose_audit_interpolates_short_bad_frame_without_rejecting_good_turns():
    frames = [
        FrameRecord(i, i, "", i * 100_000_000, Path("r"), Path("d"))
        for i in range(3)
    ]
    positions = np.array([[0.0, 0.0, 0.0], [1.4, 0.2, 0.0], [2.0, 0.0, 0.0]])
    rotations = np.repeat(np.eye(3)[None], 3, axis=0)
    trajectory = TrajectoryResult(
        positions,
        positions.copy(),
        positions.copy(),
        rotations,
        np.ones(3),
        ("origin", "gps_fallback", "pnp"),
        {},
    )

    audit = audit_cloud_frames(
        frames,
        trajectory,
        [_odometry(False), _odometry(False), _odometry(True)],
        policy="interpolate",
    )

    assert audit.use_for_cloud.tolist() == [True, True, True]
    assert audit.records[1].cloud_pose_action == "interpolated"
    np.testing.assert_allclose(audit.positions_enu_m[1], [1.0, 0.0, 0.0])
