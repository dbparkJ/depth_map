from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from rgbd_map.dataset import CameraModel, FrameRecord
from rgbd_map.pointcloud import (
    build_point_cloud,
    select_frame_indices,
    spatially_sample_indices,
    voxel_average_points,
)
from rgbd_map.trajectory import TrajectoryResult


def _trajectory(
    count: int,
    positions: np.ndarray | None = None,
    rotations: np.ndarray | None = None,
) -> TrajectoryResult:
    if positions is None:
        positions = np.zeros((count, 3), dtype=np.float64)
    if rotations is None:
        rotations = np.repeat(np.eye(3, dtype=np.float64)[None, :, :], count, axis=0)
    return TrajectoryResult(
        positions_enu_m=np.asarray(positions, dtype=np.float64),
        raw_visual_positions_enu_m=np.asarray(positions, dtype=np.float64).copy(),
        gps_positions_enu_m=np.asarray(positions, dtype=np.float64).copy(),
        rotations_enu_from_camera=np.asarray(rotations, dtype=np.float64),
        edge_weights=np.ones(count, dtype=np.float64),
        methods=tuple("test" for _ in range(count)),
        metrics={},
    )


def _dummy_frames(count: int, timestamps_ns: list[int] | None = None) -> list[FrameRecord]:
    if timestamps_ns is None:
        timestamps_ns = [index * 100_000_000 for index in range(count)]
    return [
        FrameRecord(
            output_index=index,
            source_index=index,
            wall_time="",
            monotonic_ns=timestamps_ns[index],
            rgb_path=Path(f"rgb-{index}.png"),
            depth_path=Path(f"depth-{index}.png"),
        )
        for index in range(count)
    ]


def _write_sequence(
    tmp_path: Path,
    depths: list[np.ndarray],
    colors_rgb: list[np.ndarray] | None = None,
    timestamps_ns: list[int] | None = None,
) -> tuple[list[FrameRecord], CameraModel, TrajectoryResult]:
    height, width = depths[0].shape
    if colors_rgb is None:
        colors_rgb = []
        for index in range(len(depths)):
            color = np.empty((height, width, 3), dtype=np.uint8)
            color[..., 0] = 20 + index
            color[..., 1] = 40 + index
            color[..., 2] = 60 + index
            colors_rgb.append(color)
    if timestamps_ns is None:
        timestamps_ns = [index * 100_000_000 for index in range(len(depths))]

    frames: list[FrameRecord] = []
    for index, (depth, color_rgb) in enumerate(zip(depths, colors_rgb, strict=True)):
        depth_path = tmp_path / f"depth-{index}.png"
        rgb_path = tmp_path / f"rgb-{index}.png"
        assert cv2.imwrite(str(depth_path), np.asarray(depth, dtype=np.uint16))
        color_bgr = np.ascontiguousarray(np.asarray(color_rgb, dtype=np.uint8)[..., ::-1])
        assert cv2.imwrite(str(rgb_path), color_bgr)
        frames.append(
            FrameRecord(
                output_index=index,
                source_index=index,
                wall_time="",
                monotonic_ns=timestamps_ns[index],
                rgb_path=rgb_path,
                depth_path=depth_path,
            )
        )
    camera = CameraModel(
        width=width,
        height=height,
        fx=10.0,
        fy=10.0,
        cx=0.0,
        cy=0.0,
    )
    return frames, camera, _trajectory(len(frames))


def _rotation_z(degrees: float) -> np.ndarray:
    angle = np.deg2rad(degrees)
    cosine = np.cos(angle)
    sine = np.sin(angle)
    return np.array(
        [[cosine, -sine, 0.0], [sine, cosine, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def test_stride_selection_includes_last_frame_once():
    frames = _dummy_frames(6)
    trajectory = _trajectory(6)

    assert select_frame_indices(frames, trajectory, frame_stride=1) == [0, 1, 2, 3, 4, 5]
    assert select_frame_indices(frames, trajectory, frame_stride=2) == [0, 2, 4, 5]
    assert select_frame_indices(_dummy_frames(5), _trajectory(5), frame_stride=2) == [
        0,
        2,
        4,
    ]


def test_keyframe_thresholds_replace_stride_and_use_or_semantics():
    timestamps = [0, 100_000_000, 200_000_000, 300_000_000, 1_000_000_000, 1_100_000_000]
    frames = _dummy_frames(6, timestamps)
    positions = np.array(
        [
            [0.0, 0.0, 0.0],
            [0.1, 0.0, 0.0],
            [0.3, 0.0, 0.0],
            [0.31, 0.0, 0.0],
            [0.32, 0.0, 0.0],
            [0.33, 0.0, 0.0],
        ]
    )
    rotations = np.stack(
        (
            _rotation_z(0.0),
            _rotation_z(0.0),
            _rotation_z(0.0),
            _rotation_z(12.0),
            _rotation_z(12.0),
            _rotation_z(12.0),
        )
    )

    selected = select_frame_indices(
        frames,
        _trajectory(6, positions, rotations),
        frame_stride=100,
        keyframe_distance_m=0.25,
        keyframe_angle_deg=10.0,
        keyframe_max_dt_s=0.5,
    )

    assert selected == [0, 2, 3, 4, 5]


def test_voxel_average_uses_xyz_rgb_means_and_floor_for_negative_coordinates():
    points = np.array(
        [
            [0.01, 0.01, 0.01],
            [0.03, 0.03, 0.03],
            [-0.01, -0.01, -0.01],
            [-0.03, -0.03, -0.03],
        ],
        dtype=np.float64,
    )
    colors = np.array(
        [
            [100, 120, 140],
            [200, 220, 240],
            [10, 30, 50],
            [30, 50, 70],
        ],
        dtype=np.uint8,
    )

    averaged_points, averaged_colors = voxel_average_points(points, colors, 0.1)

    np.testing.assert_allclose(
        averaged_points,
        [[-0.02, -0.02, -0.02], [0.02, 0.02, 0.02]],
        atol=1e-7,
    )
    np.testing.assert_array_equal(
        averaged_colors,
        [[20, 40, 60], [150, 170, 190]],
    )


def test_voxel_average_merges_sums_across_frame_runs(tmp_path):
    depths = [np.array([[1000]], dtype=np.uint16) for _ in range(2)]
    colors = [
        np.array([[[100, 120, 140]]], dtype=np.uint8),
        np.array([[[200, 220, 240]]], dtype=np.uint8),
    ]
    frames, camera, trajectory = _write_sequence(tmp_path, depths, colors)

    result = build_point_cloud(
        frames,
        camera,
        trajectory,
        frame_stride=1,
        pixel_stride=1,
        voxel_size_m=0.1,
        max_points=100,
        per_frame_max_points=0,
        min_depth_m=0.1,
        max_depth_m=2.0,
        roi_top_ratio=0.0,
        roi_bottom_ratio=1.0,
        progress_every=0,
    )

    np.testing.assert_allclose(result.points_enu_m, [[0.0, 0.0, 1.0]])
    np.testing.assert_array_equal(result.colors_rgb, [[150, 170, 190]])
    assert result.stats is not None
    assert result.stats.points_before_voxel == 2
    assert result.stats.unique_voxel_count == 1
    assert result.stats.discarded_by_voxel == 1


def test_roi_pixel_and_depth_filter_stats(tmp_path):
    depth = np.array(
        [
            [1000, 1000, 1000, 1000],
            [0, 65535, 500, 1000],
            [2000, 3000, 3001, 1500],
            [1000, 1000, 1000, 1000],
        ],
        dtype=np.uint16,
    )
    frames, camera, trajectory = _write_sequence(tmp_path, [depth])

    result = build_point_cloud(
        frames,
        camera,
        trajectory,
        frame_stride=1,
        pixel_stride=1,
        voxel_size_m=0.001,
        max_points=100,
        per_frame_max_points=0,
        min_depth_m=1.0,
        max_depth_m=3.0,
        roi_top_ratio=0.25,
        roi_bottom_ratio=0.75,
        progress_every=0,
    )

    assert result.stats is not None
    assert result.stats.candidate_pixel_sample_count == 8
    assert result.stats.valid_depth_sample_count == 4
    assert result.stats.invalid_depth_sample_count == 4
    assert result.stats.points_before_voxel == 4
    assert len(result.points_enu_m) == 4


def test_per_frame_cap_zero_and_explicit_limit_update_stats(tmp_path):
    depth = np.full((10, 10), 1000, dtype=np.uint16)
    frames, camera, trajectory = _write_sequence(tmp_path, [depth])
    common = dict(
        frame_stride=1,
        pixel_stride=1,
        voxel_size_m=0.001,
        max_points=1000,
        min_depth_m=0.1,
        max_depth_m=2.0,
        roi_top_ratio=0.0,
        roi_bottom_ratio=1.0,
        progress_every=0,
    )

    uncapped = build_point_cloud(
        frames, camera, trajectory, per_frame_max_points=0, **common
    )
    capped = build_point_cloud(
        frames, camera, trajectory, per_frame_max_points=25, **common
    )

    assert uncapped.stats is not None
    assert capped.stats is not None
    assert uncapped.stats.valid_depth_sample_count == 100
    assert uncapped.stats.points_before_voxel == 100
    assert uncapped.stats.discarded_by_per_frame_cap == 0
    assert len(uncapped.points_enu_m) == 100
    assert capped.stats.valid_depth_sample_count == 100
    assert capped.stats.points_before_voxel == 25
    assert capped.stats.discarded_by_per_frame_cap == 75
    assert len(capped.points_enu_m) == 25


def test_spatial_sampler_is_exact_unique_deterministic_and_covers_coarse_cells():
    points = np.array(
        [[x, y, z] for x in range(4) for y in range(4) for z in range(4)],
        dtype=np.float64,
    )

    first = spatially_sample_indices(points, 10)
    second = spatially_sample_indices(points, 10)

    np.testing.assert_array_equal(first, second)
    assert first.dtype == np.int64
    assert len(first) == len(np.unique(first)) == 10
    occupied_octants = {
        tuple((points[index] >= 2.0).astype(np.int8)) for index in first
    }
    assert len(occupied_octants) == 8


def test_final_cap_stats_and_spatial_selection_are_deterministic(tmp_path):
    depth = np.full((10, 10), 1000, dtype=np.uint16)
    frames, camera, trajectory = _write_sequence(tmp_path, [depth])
    options = dict(
        frame_stride=1,
        pixel_stride=1,
        voxel_size_m=0.001,
        max_points=10,
        per_frame_max_points=0,
        min_depth_m=0.1,
        max_depth_m=2.0,
        roi_top_ratio=0.0,
        roi_bottom_ratio=1.0,
        progress_every=0,
    )

    first = build_point_cloud(frames, camera, trajectory, **options)
    second = build_point_cloud(frames, camera, trajectory, **options)

    np.testing.assert_array_equal(first.points_enu_m, second.points_enu_m)
    assert first.stats is not None
    assert first.stats.points_before_final_cap == 100
    assert first.stats.discarded_by_final_cap == 90
    assert len(first.points_enu_m) == 10


def test_dense_settings_do_not_reduce_synthetic_point_count(tmp_path):
    depth = np.full((8, 8), 1000, dtype=np.uint16)
    frames, camera, trajectory = _write_sequence(tmp_path, [depth])
    common = dict(
        frame_stride=1,
        max_points=1000,
        per_frame_max_points=0,
        min_depth_m=0.1,
        max_depth_m=2.0,
        progress_every=0,
    )
    preview = build_point_cloud(
        frames,
        camera,
        trajectory,
        pixel_stride=4,
        voxel_size_m=0.25,
        roi_top_ratio=0.15,
        roi_bottom_ratio=0.90,
        **common,
    )
    balanced = build_point_cloud(
        frames,
        camera,
        trajectory,
        pixel_stride=2,
        voxel_size_m=0.10,
        roi_top_ratio=0.10,
        roi_bottom_ratio=0.98,
        **common,
    )
    dense = build_point_cloud(
        frames,
        camera,
        trajectory,
        pixel_stride=1,
        voxel_size_m=0.05,
        roi_top_ratio=0.05,
        roi_bottom_ratio=0.98,
        **common,
    )

    assert len(balanced.points_enu_m) >= len(preview.points_enu_m)
    assert len(dense.points_enu_m) >= len(balanced.points_enu_m)


@pytest.mark.parametrize(
    "override",
    [
        {"frame_stride": 0},
        {"pixel_stride": 0},
        {"voxel_size_m": 0.0},
        {"max_points": 0},
        {"per_frame_max_points": -1},
        {"min_depth_m": 0.0},
        {"min_depth_m": 2.0, "max_depth_m": 2.0},
        {"roi_top_ratio": -0.1},
        {"roi_top_ratio": 0.5, "roi_bottom_ratio": 0.5},
        {"roi_bottom_ratio": 1.1},
        {"keyframe_distance_m": 0.0},
        {"keyframe_angle_deg": 181.0},
        {"keyframe_max_dt_s": -1.0},
    ],
)
def test_build_point_cloud_validates_density_inputs(tmp_path, override):
    frames, camera, trajectory = _write_sequence(
        tmp_path, [np.array([[1000]], dtype=np.uint16)]
    )
    options = dict(
        frame_stride=1,
        pixel_stride=1,
        voxel_size_m=0.1,
        max_points=10,
        per_frame_max_points=0,
        min_depth_m=0.1,
        max_depth_m=2.0,
        roi_top_ratio=0.0,
        roi_bottom_ratio=1.0,
        progress_every=0,
    )
    options.update(override)

    with pytest.raises(ValueError):
        build_point_cloud(frames, camera, trajectory, **options)
