import csv
import json
from pathlib import Path

import pytest

from rgbd_map.cli import build_parser, run, validate_frame_selection_args
from rgbd_map.dataset import RgbdGpsDataset


def _write_csv(path: Path, fieldnames, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _make_dataset(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    metadata = {
        "camera_model": {
            "intrinsics": [
                [100.0, 0.0, 50.0],
                [0.0, 100.0, 40.0],
                [0.0, 0.0, 1.0],
            ],
            "width": 100,
            "height": 80,
        },
        "image_size": {"width": 100, "height": 80},
    }
    (root / "metadata.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    (root / "rgb").mkdir()
    (root / "depth_mm").mkdir()
    for source_index in range(10, 15):
        (root / "rgb" / f"{source_index}.jpg").write_bytes(b"rgb")
        (root / "depth_mm" / f"{source_index}.png").write_bytes(b"depth")

    timestamp_fields = [
        "frame_index",
        "frame_host_wall_time",
        "frame_host_monotonic_ns",
        "rgb_file",
        "depth_file",
    ]
    _write_csv(
        root / "timestamps.csv",
        timestamp_fields,
        [
            # An incomplete row must not establish the chunk time origin.
            {
                "frame_index": 0,
                "frame_host_wall_time": "2026-01-01T00:00:01.000",
                "frame_host_monotonic_ns": 1_000_000_000,
                "rgb_file": "rgb/10.jpg",
                "depth_file": "",
            },
            {
                "frame_index": 10,
                "frame_host_wall_time": "2026-01-01T00:00:10.000",
                "frame_host_monotonic_ns": 10_000_000_000,
                "rgb_file": "rgb/10.jpg",
                "depth_file": "depth_mm/10.png",
            },
            {
                "frame_index": 11,
                "frame_host_wall_time": "2026-01-01T00:00:20.000",
                "frame_host_monotonic_ns": 20_000_000_000,
                "rgb_file": "rgb/11.jpg",
                "depth_file": "depth_mm/11.png",
            },
            {
                "frame_index": 12,
                "frame_host_wall_time": "2026-01-01T00:05:09.999",
                "frame_host_monotonic_ns": 309_999_999_999,
                "rgb_file": "rgb/12.jpg",
                "depth_file": "depth_mm/12.png",
            },
            # Exactly on the upper boundary: excluded from chunk 0, included in 1.
            {
                "frame_index": 13,
                "frame_host_wall_time": "2026-01-01T00:05:10.000",
                "frame_host_monotonic_ns": 310_000_000_000,
                "rgb_file": "rgb/13.jpg",
                "depth_file": "depth_mm/13.png",
            },
            {
                "frame_index": 14,
                "frame_host_wall_time": "2026-01-01T00:05:15.000",
                "frame_host_monotonic_ns": 315_000_000_000,
                "rgb_file": "rgb/14.jpg",
                "depth_file": "depth_mm/14.png",
            },
        ],
    )

    gps_fields = [
        "nmea_type",
        "measurement_host_monotonic_ns",
        "latitude_deg",
        "longitude_deg",
        "altitude_m",
        "geoid_separation_m",
        "course_deg",
        "speed_knots",
        "fix_quality",
        "fix_quality_name",
        "hdop",
    ]
    _write_csv(
        root / "gps.csv",
        gps_fields,
        [
            {
                "nmea_type": "GGA",
                "measurement_host_monotonic_ns": 5_000_000_000,
                "latitude_deg": 37.0,
                "longitude_deg": 126.0,
                "altitude_m": 10.0,
                "geoid_separation_m": 20.0,
                "course_deg": 0.0,
                "speed_knots": 5.0,
                "fix_quality": 4,
                "fix_quality_name": "RTK fixed",
                "hdop": 0.5,
            },
            {
                "nmea_type": "GGA",
                "measurement_host_monotonic_ns": 620_000_000_000,
                "latitude_deg": 37.001,
                "longitude_deg": 126.001,
                "altitude_m": 11.0,
                "geoid_separation_m": 20.0,
                "course_deg": 10.0,
                "speed_knots": 5.0,
                "fix_quality": 4,
                "fix_quality_name": "RTK fixed",
                "hdop": 0.5,
            },
        ],
    )
    return root


def _parse_args(dataset: Path, output: Path, *options: str):
    return build_parser().parse_args(
        [str(dataset), "--output", str(output), *options]
    )


def test_timestamp_chunks_use_first_valid_frame_and_half_open_bounds(tmp_path):
    dataset = RgbdGpsDataset(_make_dataset(tmp_path))

    first, first_selection = dataset.load_frame_chunk(300.0, 0)
    second, second_selection = dataset.load_frame_chunk(300.0, 1)

    assert [frame.source_index for frame in first] == [10, 11, 12]
    assert [frame.output_index for frame in first] == [0, 1, 2]
    assert [frame.source_index for frame in second] == [13, 14]
    assert first_selection.dataset_first_monotonic_ns == 10_000_000_000
    assert first_selection.interval_start_monotonic_ns == 10_000_000_000
    assert (
        first_selection.interval_end_monotonic_ns_exclusive
        == 310_000_000_000
    )
    assert first_selection.actual_start_wall_time == "2026-01-01T00:00:10.000"
    assert first_selection.actual_end_wall_time == "2026-01-01T00:05:09.999"
    assert first_selection.actual_duration_seconds == pytest.approx(299.999999999)
    assert first_selection.source_frame_start == 10
    assert first_selection.source_frame_end_inclusive == 12
    assert first_selection.frame_count == 3
    assert first_selection.boundary_policy == "half_open"
    assert (
        first_selection.origin_policy == "dataset_first_synchronized_frame"
    )
    assert second_selection.interval_start_monotonic_ns == 310_000_000_000


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (("--chunk-duration-seconds", "0"), "finite and positive"),
        (("--chunk-duration-seconds", "nan"), "finite and positive"),
        (
            ("--chunk-duration-seconds", "300", "--chunk-index", "-1"),
            "non-negative integer",
        ),
        (("--chunk-index", "1"), "requires --chunk-duration-seconds"),
        (
            ("--chunk-duration-seconds", "300", "--start-frame", "1"),
            "cannot be combined with --start-frame",
        ),
        (
            ("--chunk-duration-seconds", "300", "--max-frames", "10"),
            "cannot be combined with --max-frames",
        ),
    ],
)
def test_chunk_cli_rejects_invalid_or_ambiguous_selection(
    tmp_path, options, message
):
    args = _parse_args(tmp_path, tmp_path / "output", *options)
    with pytest.raises(ValueError, match=message):
        validate_frame_selection_args(args)


def test_chunk_duration_defaults_to_first_chunk(tmp_path):
    args = _parse_args(
        tmp_path,
        tmp_path / "output",
        "--chunk-duration-seconds",
        "300",
    )
    validate_frame_selection_args(args)
    assert args.chunk_index is None


def test_run_records_timestamp_chunk_provenance_in_summary(tmp_path):
    dataset = _make_dataset(tmp_path / "dataset")
    output = tmp_path / "output"
    args = _parse_args(
        dataset,
        output,
        "--pose-mode",
        "gps",
        "--trajectory-only",
        "--chunk-duration-seconds",
        "300",
        "--chunk-index",
        "1",
    )

    summary = run(args)

    assert summary["frame_count"] == 2
    assert summary["parameters"]["chunk_duration_seconds"] == 300.0
    assert summary["parameters"]["chunk_index"] == 1
    chunk = summary["parameters"]["frame_chunk"]
    assert chunk["chunk_index"] == 1
    assert chunk["requested_duration_seconds"] == 300.0
    assert chunk["actual_start_wall_time"] == "2026-01-01T00:05:10.000"
    assert chunk["actual_end_wall_time"] == "2026-01-01T00:05:15.000"
    assert chunk["actual_duration_seconds"] == 5.0
    assert chunk["source_frame_start"] == 13
    assert chunk["source_frame_end_inclusive"] == 14
    assert chunk["frame_count"] == 2
    assert chunk["boundary_policy"] == "half_open"
    assert chunk["origin_policy"] == "dataset_first_synchronized_frame"
    expected_origin_latitude = 37.0 + (5.0 / 615.0) * 0.001
    assert summary["origin"]["latitude_deg"] == pytest.approx(
        expected_origin_latitude
    )
    assert summary["origin"]["latitude_deg"] != pytest.approx(
        37.0 + (305.0 / 615.0) * 0.001
    )


def test_legacy_frame_selection_keeps_selected_first_frame_origin(tmp_path):
    dataset = _make_dataset(tmp_path / "dataset")
    args = _parse_args(
        dataset,
        tmp_path / "output",
        "--pose-mode",
        "gps",
        "--trajectory-only",
        "--start-frame",
        "3",
        "--max-frames",
        "2",
    )

    summary = run(args)

    selected_offset_seconds = (309_999_999_999 - 5_000_000_000) / 1e9
    expected_origin_latitude = 37.0 + (
        selected_offset_seconds / 615.0
    ) * 0.001
    assert summary["origin"]["latitude_deg"] == pytest.approx(
        expected_origin_latitude
    )
    assert summary["parameters"]["frame_chunk"] is None
