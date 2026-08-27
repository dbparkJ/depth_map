from __future__ import annotations

import numpy as np

from rgbd_map.dataset import CameraModel
from rgbd_map.depth_consistency import TemporalDepthState, classify_projective_depth
from rgbd_map.trajectory_geometry import project_to_trajectory_polyline


def test_projective_temporal_states_distinguish_support_occlusion_and_free_space():
    camera = CameraModel(3, 1, 1.0, 1.0, 1.0, 0.0)
    points = np.array([[-5.0, 0.0, 5.0], [0.0, 0.0, 5.0], [5.0, 0.0, 5.0]])
    observed = np.array([[5000, 3000, 7000]], dtype=np.uint16)

    states = classify_projective_depth(
        points,
        camera,
        np.zeros(3),
        np.eye(3),
        observed,
        absolute_tolerance_m=0.1,
        relative_tolerance_ratio=0.0,
    )

    np.testing.assert_array_equal(
        states,
        [
            int(TemporalDepthState.SUPPORT),
            int(TemporalDepthState.OCCLUDED_OR_UNKNOWN),
            int(TemporalDepthState.FREE_SPACE_CONTRADICTION),
        ],
    )


def test_polyline_projection_handles_curve_and_endpoint_buffer():
    trajectory = np.array(
        [[0.0, 0.0, 1.5], [10.0, 0.0, 1.5], [10.0, 10.0, 2.0]]
    )
    points = np.array(
        [[9.0, 4.0, 2.2], [-2.0, 1.0, 1.0], [10.0, 14.0, 2.0]]
    )

    result = project_to_trajectory_polyline(
        points, trajectory, endpoint_buffer_m=5.0
    )

    assert result.nearest_segment_index[0] == 1
    assert np.isclose(result.cross_track_m[0], 1.0)
    assert np.isclose(result.along_track_m[0], 14.0)
    assert result.in_endpoint_buffer[1]
    assert result.in_endpoint_buffer[2]
