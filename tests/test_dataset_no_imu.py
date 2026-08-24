import csv
import json
from pathlib import Path

from rgbd_map.dataset import RgbdGpsDataset


def write_csv(path: Path, fieldnames, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_dataset_ignores_broken_imu_files(tmp_path):
    metadata = {
        "camera_model": {
            "intrinsics": [[100.0, 0.0, 50.0], [0.0, 100.0, 40.0], [0.0, 0.0, 1.0]],
            "width": 100,
            "height": 80,
        },
        "image_size": {"width": 100, "height": 80},
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (tmp_path / "imu.csv").write_text("this,is,intentionally,invalid\n", encoding="utf-8")
    (tmp_path / "external_imu.csv").write_bytes(b"\x00broken")
    (tmp_path / "rgb").mkdir()
    (tmp_path / "depth_mm").mkdir()
    for index in range(2):
        (tmp_path / "rgb" / f"{index}.jpg").write_bytes(b"placeholder")
        (tmp_path / "depth_mm" / f"{index}.png").write_bytes(b"placeholder")
    write_csv(
        tmp_path / "timestamps.csv",
        [
            "frame_index",
            "frame_host_wall_time",
            "frame_host_monotonic_ns",
            "rgb_file",
            "depth_file",
        ],
        [
            {
                "frame_index": 0,
                "frame_host_wall_time": "2026-01-01T00:00:00.000",
                "frame_host_monotonic_ns": 1_100_000_000,
                "rgb_file": "rgb/0.jpg",
                "depth_file": "depth_mm/0.png",
            },
            {
                "frame_index": 1,
                "frame_host_wall_time": "2026-01-01T00:00:00.100",
                "frame_host_monotonic_ns": 1_200_000_000,
                "rgb_file": "rgb/1.jpg",
                "depth_file": "depth_mm/1.png",
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
    write_csv(
        tmp_path / "gps.csv",
        gps_fields,
        [
            {
                "nmea_type": "GGA",
                "measurement_host_monotonic_ns": 1_000_000_000,
                "latitude_deg": 37.0,
                "longitude_deg": 126.0,
                "altitude_m": 10.0,
                "geoid_separation_m": 20.0,
                "course_deg": 350.0,
                "speed_knots": 10.0,
                "fix_quality": 4,
                "fix_quality_name": "RTK fixed",
                "hdop": 0.6,
            },
            {
                "nmea_type": "GGA",
                "measurement_host_monotonic_ns": 1_300_000_000,
                "latitude_deg": 37.0003,
                "longitude_deg": 126.0003,
                "altitude_m": 11.0,
                "geoid_separation_m": 20.0,
                "course_deg": 10.0,
                "speed_knots": 11.0,
                "fix_quality": 4,
                "fix_quality_name": "RTK fixed",
                "hdop": 0.6,
            },
        ],
    )
    dataset = RgbdGpsDataset(tmp_path)
    frames = dataset.load_frames()
    gps = dataset.interpolate_gps(frames)
    assert len(frames) == 2
    assert 350.0 < gps.course_deg[0] < 360.0
    assert 0.0 < gps.course_deg[1] < 10.0
    assert gps.ellipsoid_height_m[0] > 30.0

