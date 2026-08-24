from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from rgbd_map.dataset import CameraModel, FrameRecord, RgbdGpsDataset
from rgbd_map.pointcloud import build_point_cloud, depth_edge_keep_mask
from rgbd_map.trajectory import TrajectoryResult


def _stationary_trajectory(frame_count: int) -> TrajectoryResult:
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


def test_depth_edge_mask_rejects_discontinuities_without_treating_invalid_as_zero():
    depth = np.full((7, 9), 5_000, dtype=np.uint16)
    depth[:, 4:] = 15_000
    depth[3, 4] = 30_000
    depth[3, 1] = 0
    valid = (depth != 0) & (depth != 65_535)

    keep = depth_edge_keep_mask(
        depth,
        valid,
        radius_px=1,
        abs_threshold_m=0.25,
        rel_ratio=0.01,
        min_valid_neighbors=3,
    )

    assert keep.dtype == np.bool_
    assert keep[2, 1]  # A missing neighbor does not become a zero-depth edge.
    assert keep[3, 2]
    assert keep[3, 7]
    assert not keep[3, 1]
    assert not keep[3, 4]  # Median disagreement catches the isolated 30 m value.
    assert not keep[3, 3]  # The real 5 m / 15 m discontinuity is also rejected.


def test_build_point_cloud_records_depth_edge_counts(tmp_path):
    depth = np.full((7, 9), 5_000, dtype=np.uint16)
    depth[:, 4:] = 15_000
    depth[3, 4] = 30_000
    rgb = np.full((7, 9, 3), 80, dtype=np.uint8)
    depth_path = tmp_path / "depth.png"
    rgb_path = tmp_path / "rgb.png"
    assert cv2.imwrite(str(depth_path), depth)
    assert cv2.imwrite(str(rgb_path), rgb)
    frame = FrameRecord(0, 0, "", 0, rgb_path, depth_path)
    camera = CameraModel(9, 7, 100.0, 100.0, 4.0, 3.0)

    result = build_point_cloud(
        [frame],
        camera,
        _stationary_trajectory(1),
        frame_stride=1,
        pixel_stride=1,
        voxel_size_m=0.01,
        max_points=1_000,
        per_frame_max_points=0,
        min_depth_m=1.0,
        max_depth_m=40.0,
        roi_top_ratio=0.0,
        roi_bottom_ratio=1.0,
        progress_every=0,
        depth_edge_filter=True,
        depth_edge_radius_px=1,
        depth_edge_abs_m=0.25,
        depth_edge_rel_ratio=0.01,
        depth_edge_min_valid_neighbors=3,
    )

    assert result.stats is not None
    assert result.stats.depth_edge_rejected_count > 0
    assert (
        result.stats.depth_edge_rejected_count
        + result.stats.depth_edge_retained_count
        == result.stats.valid_depth_sample_count
    )
    assert result.depth_edge_pass_count is not None
    assert int(result.depth_edge_pass_count.sum()) == result.stats.points_before_voxel


def test_disabled_depth_edge_accepts_off_preset_sentinels(tmp_path):
    depth = np.full((3, 3), 1_000, dtype=np.uint16)
    rgb = np.full((3, 3, 3), 80, dtype=np.uint8)
    depth_path = tmp_path / "depth-off.png"
    rgb_path = tmp_path / "rgb-off.png"
    assert cv2.imwrite(str(depth_path), depth)
    assert cv2.imwrite(str(rgb_path), rgb)
    frame = FrameRecord(0, 0, "", 0, rgb_path, depth_path)

    result = build_point_cloud(
        [frame],
        CameraModel(3, 3, 100.0, 100.0, 1.0, 1.0),
        _stationary_trajectory(1),
        frame_stride=1,
        pixel_stride=1,
        voxel_size_m=0.01,
        max_points=100,
        per_frame_max_points=0,
        min_depth_m=0.1,
        max_depth_m=2.0,
        roi_top_ratio=0.0,
        roi_bottom_ratio=1.0,
        progress_every=0,
        depth_edge_filter=False,
        depth_edge_radius_px=1,
        depth_edge_abs_m=0.0,
        depth_edge_rel_ratio=0.0,
        depth_edge_min_valid_neighbors=0,
    )

    assert len(result.points_enu_m) == 9
    assert result.stats is not None
    assert result.stats.depth_edge_rejected_count == 0
    assert result.stats.depth_edge_retained_count == 9


@pytest.mark.parametrize(
    "override",
    [
        {"depth_edge_radius_px": 0},
        {"depth_edge_abs_m": 0.0},
        {"depth_edge_rel_ratio": -0.01},
        {"depth_edge_min_valid_neighbors": 0},
    ],
)
def test_enabled_depth_edge_keeps_strict_validation(tmp_path, override):
    frame = FrameRecord(
        0,
        0,
        "",
        0,
        tmp_path / "unused-rgb.png",
        tmp_path / "unused-depth.png",
    )
    options = {
        "depth_edge_filter": True,
        "depth_edge_radius_px": 1,
        "depth_edge_abs_m": 0.18,
        "depth_edge_rel_ratio": 0.03,
        "depth_edge_min_valid_neighbors": 4,
    }
    options.update(override)

    with pytest.raises(ValueError):
        build_point_cloud(
            [frame],
            CameraModel(3, 3, 100.0, 100.0, 1.0, 1.0),
            _stationary_trajectory(1),
            progress_every=0,
            **options,
        )


def test_timestamps_confidence_file_is_optional_and_missing_file_is_nonfatal(tmp_path):
    metadata = {
        "image_size": {"width": 1, "height": 1},
        "camera_model": {
            "intrinsics": [
                [100.0, 0.0, 0.0],
                [0.0, 100.0, 0.0],
                [0.0, 0.0, 1.0],
            ]
        },
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    for name in ("rgb0.png", "rgb1.png", "depth0.png", "depth1.png"):
        (tmp_path / name).touch()
    (tmp_path / "timestamps.csv").write_text(
        "frame_index,frame_host_monotonic_ns,rgb_file,depth_file,confidence_file\n"
        "0,0,rgb0.png,depth0.png,missing-confidence.png\n"
        "1,1,rgb1.png,depth1.png,\n",
        encoding="utf-8",
    )

    frames = RgbdGpsDataset(tmp_path).load_frames()

    assert frames[0].confidence_path == (tmp_path / "missing-confidence.png").resolve()
    assert frames[1].confidence_path is None
