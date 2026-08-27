from pathlib import Path

import numpy as np
import pytest

from rgbd_map.dataset import FrameRecord
from rgbd_map.pointcloud import limit_stationary_frame_indices


def _frames(count: int) -> list[FrameRecord]:
    return [
        FrameRecord(
            output_index=index,
            source_index=index,
            wall_time=f"2026-01-01T00:00:{index:02d}",
            monotonic_ns=index * 1_000_000_000,
            rgb_path=Path(f"rgb_{index}.jpg"),
            depth_path=Path(f"depth_{index}.png"),
        )
        for index in range(count)
    ]


def test_sustained_stationary_run_is_uniformly_capped():
    frames = _frames(10)
    speeds = np.array([1.0, 0.1, 0.1, 0.1, 0.1, 1.0, 0.1, 0.1, 1.0, 1.0])
    result = limit_stationary_frame_indices(
        frames,
        list(range(10)),
        speeds,
        speed_threshold_m_s=0.3,
        min_duration_s=2.0,
        max_frames_per_run=2,
    )

    assert result.selected_indices == (0, 1, 4, 5, 6, 7, 8, 9)
    assert result.run_count == 1
    assert result.candidate_frame_count == 4
    assert result.retained_frame_count == 2
    assert result.skipped_frame_count == 2
    np.testing.assert_array_equal(np.flatnonzero(result.skipped_mask), [2, 3])
    np.testing.assert_array_equal(
        np.flatnonzero(result.limited_stationary_mask), [1, 2, 3, 4]
    )
    # The one-second low-speed interval is below min_duration_s and is untouched.
    assert result.stationary_mask[6] and result.stationary_mask[7]
    assert not result.limited_stationary_mask[6]


def test_cap_applies_only_to_already_selected_cloud_candidates():
    result = limit_stationary_frame_indices(
        _frames(8),
        [0, 2, 4, 6, 7],
        np.zeros(8),
        speed_threshold_m_s=0.3,
        min_duration_s=2.0,
        max_frames_per_run=3,
    )
    assert result.selected_indices == (0, 4, 7)
    assert result.candidate_frame_count == 5
    assert result.skipped_frame_count == 2


@pytest.mark.parametrize(
    ("speeds", "kwargs", "message"),
    [
        (np.zeros(3), {"speed_threshold_m_s": -0.1}, "non-negative"),
        (np.zeros(2), {}, "align with frames"),
        (np.zeros(3), {"min_duration_s": 0.0}, "positive"),
        (np.zeros(3), {"max_frames_per_run": 0}, "positive"),
    ],
)
def test_stationary_selection_rejects_invalid_inputs(speeds, kwargs, message):
    options = {
        "speed_threshold_m_s": 0.3,
        "min_duration_s": 2.0,
        "max_frames_per_run": 5,
        **kwargs,
    }
    with pytest.raises(ValueError, match=message):
        limit_stationary_frame_indices(_frames(3), [0, 1, 2], speeds, **options)
