from __future__ import annotations

import numpy as np

from rgbd_map.registration_quality import compute_adjacent_frame_registration_quality


def test_adjacent_parallel_planes_have_small_point_to_plane_residual():
    xy = np.array([[x, y] for x in np.linspace(0, 1, 12) for y in np.linspace(0, 1, 12)])
    first = np.column_stack((xy, np.full(len(xy), 5.0)))
    second = first + np.array([0.01, 0.01, 0.02])
    points = np.vstack((first, second))
    frames = np.r_[np.zeros(len(first)), np.ones(len(second))].astype(np.int32)
    depths = np.full(len(points), 5.0, dtype=np.float32)

    result = compute_adjacent_frame_registration_quality(points, frames, depths)

    assert result["evaluated_frame_pair_count"] == 1
    assert result["overlap_ratio"] == 1.0
    assert result["point_to_plane_by_depth_m"]["0-10"]["p50_m"] < 0.03
    assert result["frame_pairs"][0]["source_frame"] == 0
    assert result["frame_pairs"][0]["target_frame"] == 1
    assert result["frame_pairs"][0]["point_to_plane_by_depth_m"]["0-10"]["p95_m"] < 0.03
