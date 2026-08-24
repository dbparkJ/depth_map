from __future__ import annotations

import numpy as np

from rgbd_map.postprocess import RemovalReason, run_postprocess
from rgbd_map.postprocess_config import resolve_postprocess_config


def _cluster(center_x: float) -> np.ndarray:
    return np.array(
        [
            [center_x + dx, dy, 0.0]
            for dx in (-0.04, 0.0, 0.04)
            for dy in (-0.04, 0.0, 0.04)
        ],
        dtype=np.float64,
    )


def test_brightness_never_removes_supported_white_geometry_by_itself():
    repeated_white = _cluster(0.0)
    single_frame_white_surface = _cluster(0.50)
    points = np.vstack(
        (
            repeated_white,
            single_frame_white_surface,
            [[0.85, 0.0, 0.0]],  # isolated white
            [[0.96, 0.0, 0.0]],  # isolated, but not white
        )
    )
    colors = np.full((len(points), 3), 250, dtype=np.uint8)
    colors[-1] = [30, 80, 120]
    observation_count = np.ones(len(points), dtype=np.int32)
    distinct_frame_count = np.ones(len(points), dtype=np.int32)
    observation_count[: len(repeated_white)] = 3
    distinct_frame_count[: len(repeated_white)] = 3
    metadata = {
        "observation_count": observation_count,
        "distinct_frame_count": distinct_frame_count,
        "position_std_m": np.zeros(len(points)),
        "mean_depth_m": np.full(len(points), 5.0),
    }
    config = resolve_postprocess_config(
        "road-map",
        0.05,
        {
            "radius_outlier_radius_m": 0.13,
            "radius_outlier_min_neighbors": 2,
            "single_frame_min_neighbors": 2,
            "statistical_neighbors": 6,
            "statistical_std_ratio": 100.0,
            "tile_size_m": 2.0,
            "tile_overlap_m": 0.25,
        },
    )

    result = run_postprocess(
        points,
        colors,
        metadata,
        trajectory_enu_m=None,
        config=config,
        neighbor_backend="scipy",
        ground_backend="off",
    )

    assert np.all(result.keep_mask[: len(repeated_white)])
    assert np.all(
        result.keep_mask[
            len(repeated_white) : len(repeated_white) + len(single_frame_white_surface)
        ]
    )
    assert not result.keep_mask[-2]
    assert not result.keep_mask[-1]
    assert result.removal_reason_bits[-2] & int(RemovalReason.BRIGHT_LOW_SUPPORT)
    assert not (
        result.removal_reason_bits[-1] & int(RemovalReason.BRIGHT_LOW_SUPPORT)
    )
    assert result.removal_reason_bits[-1] & int(RemovalReason.RADIUS_OUTLIER)
