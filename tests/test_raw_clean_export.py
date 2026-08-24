import struct
from pathlib import Path

import numpy as np

from rgbd_map.exporters import read_ply, write_ply
from rgbd_map.geodesy import LocalENU
from rgbd_map.postprocess_io import (
    atomic_savez_compressed,
    load_raw_cloud_bundle,
    validate_browser_binary,
    write_processed_cloud_products,
)


def test_raw_clean_removed_outputs_and_bin_headers_are_consistent(tmp_path: Path):
    data_dir = tmp_path / "data"
    raw_points = np.array(
        [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0], [4, 0, 0]],
        dtype=np.float32,
    )
    raw_colors = np.array(
        [[10, 20, 30], [40, 50, 60], [70, 80, 90], [100, 110, 120], [130, 140, 150]],
        dtype=np.uint8,
    )
    keep = np.array([True, True, False, True, False])
    removed_reason = np.array([8, 32], dtype=np.uint16)
    diagnostic_colors = np.array([[250, 225, 45], [245, 55, 65]], dtype=np.uint8)

    summary = write_processed_cloud_products(
        data_dir,
        raw_points=raw_points,
        raw_colors=raw_colors,
        clean_points=raw_points[keep],
        clean_colors=raw_colors[keep],
        removed_points=raw_points[~keep],
        removed_diagnostic_colors=diagnostic_colors,
        removed_original_colors=raw_colors[~keep],
        removal_reason_bits=removed_reason,
        primary_reason=removed_reason,
        origin=LocalENU(126.0, 37.0, 30.0),
        postprocess_preset="road-map",
        browser_max_points=3,
    )

    assert summary["raw_point_count"] == 5
    assert summary["clean_point_count"] == 3
    assert summary["removed_point_count"] == 2
    assert summary["point_count"] == summary["ply_point_count"] == 3
    assert summary["removal_ratio"] == 0.4
    assert (data_dir / "cloud_enu.ply").read_bytes() == (
        data_dir / "cloud_clean_enu.ply"
    ).read_bytes()
    assert (data_dir / "points.bin").read_bytes() == (
        data_dir / "points_clean.bin"
    ).read_bytes()

    for filename, count_field in (
        ("points_raw.bin", "raw_browser_point_count"),
        ("points_clean.bin", "clean_browser_point_count"),
        ("points_removed.bin", "removed_browser_point_count"),
        ("points.bin", "browser_point_count"),
    ):
        result = validate_browser_binary(data_dir / filename, summary[count_field])
        assert result["magic"] == "RGBD"
        assert (data_dir / filename).stat().st_size == 16 + result["count"] * 16

    with np.load(data_dir / "removed_points_metadata.npz", allow_pickle=False) as saved:
        np.testing.assert_array_equal(saved["points_xyz"], raw_points[~keep])
        np.testing.assert_array_equal(saved["original_rgb"], raw_colors[~keep])
        np.testing.assert_array_equal(saved["removal_reason_bits"], removed_reason)


def test_numeric_npz_writer_is_atomic_and_rejects_object_arrays(tmp_path: Path):
    path = tmp_path / "metadata.npz"
    atomic_savez_compressed(path, count=np.array([1, 2], dtype=np.uint32))
    with np.load(path, allow_pickle=False) as saved:
        np.testing.assert_array_equal(saved["count"], [1, 2])
    assert not (tmp_path / "metadata.tmp.npz").exists()

    try:
        atomic_savez_compressed(
            path, unsafe=np.array([{"not": "numeric"}], dtype=object)
        )
    except ValueError as exc:
        assert "object dtype" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("object NPZ array was accepted")


def test_raw_metadata_scalar_reports_array_name_and_expected_shape(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    write_ply(
        data_dir / "cloud_raw_enu.ply",
        np.array([[1.0, 2.0, 3.0]], dtype=np.float32),
        np.array([[10, 20, 30]], dtype=np.uint8),
        LocalENU(126.0, 37.0, 30.0),
    )
    atomic_savez_compressed(
        data_dir / "cloud_raw_metadata.npz",
        observation_count=np.array(1, dtype=np.uint32),
        distinct_frame_count=np.ones(1, dtype=np.uint32),
        position_std_m=np.zeros(1, dtype=np.float32),
        mean_depth_m=np.ones(1, dtype=np.float32),
    )
    (data_dir / "trajectory.json").write_text(
        '{"fused": [[0.0, 0.0, 0.0]]}', encoding="utf-8"
    )
    (data_dir / "summary.json").write_text(
        '{"origin": {"longitude_deg": 126.0, "latitude_deg": 37.0, '
        '"ellipsoid_height_m": 30.0}}',
        encoding="utf-8",
    )

    try:
        load_raw_cloud_bundle(tmp_path)
    except ValueError as exc:
        message = str(exc)
        assert "'observation_count'" in message
        assert "shape (1,)" in message
        assert "got ()" in message
    else:  # pragma: no cover - assertion guard
        raise AssertionError("scalar raw metadata array was accepted")


def test_browser_header_layout_is_little_endian(tmp_path: Path):
    path = tmp_path / "points.bin"
    path.write_bytes(struct.pack("<4sIII", b"RGBD", 1, 0, 16))
    assert validate_browser_binary(path, 0)["count"] == 0


def test_empty_binary_ply_round_trip(tmp_path: Path):
    path = tmp_path / "empty.ply"
    write_ply(
        path,
        np.empty((0, 3), dtype=np.float32),
        np.empty((0, 3), dtype=np.uint8),
        LocalENU(126.0, 37.0, 30.0),
        comments={"pointcloud_stage": "removed"},
    )
    points, colors, comments = read_ply(path)
    assert points.shape == colors.shape == (0, 3)
    assert comments["pointcloud_stage"] == "removed"
