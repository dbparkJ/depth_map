from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from rgbd_map.dataset import CameraModel, FrameRecord
from rgbd_map.pointcloud import build_point_cloud
from rgbd_map.trajectory import TrajectoryResult


def _trajectory(frame_count: int) -> TrajectoryResult:
    positions = np.zeros((frame_count, 3), dtype=np.float64)
    return TrajectoryResult(
        positions_enu_m=positions,
        raw_visual_positions_enu_m=positions.copy(),
        gps_positions_enu_m=positions.copy(),
        rotations_enu_from_camera=np.repeat(
            np.eye(3, dtype=np.float64)[None, :, :], frame_count, axis=0
        ),
        edge_weights=np.ones(frame_count, dtype=np.float64),
        methods=tuple("test" for _ in range(frame_count)),
        metrics={},
    )


def _write_frame(
    root: Path, index: int, depth: np.ndarray, confidence: np.ndarray | None = None
) -> FrameRecord:
    depth_path = root / f"depth-{index}.png"
    rgb_path = root / f"rgb-{index}.png"
    confidence_path = (
        root / f"confidence-{index}.png" if confidence is not None else None
    )
    assert cv2.imwrite(str(depth_path), np.asarray(depth, dtype=np.uint16))
    rgb = np.full((*depth.shape, 3), 40 + index, dtype=np.uint8)
    assert cv2.imwrite(str(rgb_path), rgb)
    if confidence_path is not None:
        assert cv2.imwrite(str(confidence_path), np.asarray(confidence, dtype=np.uint8))
    return FrameRecord(index, index, "", index, rgb_path, depth_path, confidence_path)


def _build(frames: list[FrameRecord], camera: CameraModel, **overrides):
    options = {
        "frame_stride": 1,
        "pixel_stride": 1,
        "voxel_size_m": 0.5,
        "max_points": 100,
        "per_frame_max_points": 0,
        "min_depth_m": 0.1,
        "max_depth_m": 2.0,
        "roi_top_ratio": 0.0,
        "roi_bottom_ratio": 1.0,
        "progress_every": 0,
    }
    options.update(overrides)
    return build_point_cloud(frames, camera, _trajectory(len(frames)), **options)


def test_voxel_metadata_counts_distinct_frames_and_xyz_spread(tmp_path):
    frames = [
        _write_frame(
            tmp_path,
            0,
            np.array([[1_000, 1_000]], dtype=np.uint16),
            confidence=np.array([[255, 255]], dtype=np.uint8),
        ),
        _write_frame(tmp_path, 1, np.array([[1_080, 0]], dtype=np.uint16)),
    ]
    camera = CameraModel(2, 1, 1_000.0, 1_000.0, 0.0, 0.0)

    result = _build(frames, camera)

    assert len(result.points_enu_m) == 1
    np.testing.assert_array_equal(result.observation_count, [3])
    np.testing.assert_array_equal(result.distinct_frame_count, [2])
    np.testing.assert_allclose(result.mean_depth_m, [3.08 / 3.0], rtol=1e-6)
    np.testing.assert_allclose(result.depth_min_m, [1.0])
    np.testing.assert_allclose(result.depth_max_m, [1.08])
    np.testing.assert_array_equal(result.depth_edge_pass_count, [3])
    np.testing.assert_array_equal(result.source_voxel_key, [[0, 0, 2]])

    source_points = np.array(
        [[0.0, 0.0, 1.0], [0.001, 0.0, 1.0], [0.0, 0.0, 1.08]],
        dtype=np.float64,
    )
    expected_std = np.sqrt(np.var(source_points, axis=0).sum())
    np.testing.assert_allclose(result.position_std_m, [expected_std], rtol=1e-5)
    assert result.stats is not None
    assert result.stats.confidence_map_available
    assert not result.stats.confidence_filter_applied


def test_final_cap_slices_all_metadata_and_keeps_voxel_key_order(tmp_path):
    frame = _write_frame(
        tmp_path,
        0,
        np.array([[1_000, 1_000, 1_000, 1_000]], dtype=np.uint16),
    )
    camera = CameraModel(4, 1, 1.0, 1.0, 0.0, 0.0)

    result = _build([frame], camera, max_points=2)

    metadata = (
        result.observation_count,
        result.distinct_frame_count,
        result.position_std_m,
        result.mean_depth_m,
        result.depth_min_m,
        result.depth_max_m,
        result.depth_edge_pass_count,
        result.source_voxel_key,
    )
    assert all(values is not None and len(values) == 2 for values in metadata)
    assert result.source_voxel_key is not None
    order = np.lexsort(result.source_voxel_key[:, ::-1].T)
    np.testing.assert_array_equal(order, np.arange(2))
    np.testing.assert_array_equal(
        np.floor(result.points_enu_m / 0.5).astype(np.int64),
        result.source_voxel_key,
    )
