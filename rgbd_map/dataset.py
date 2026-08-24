from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class CameraModel:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float

    @property
    def matrix(self) -> np.ndarray:
        return np.array(
            [[self.fx, 0.0, self.cx], [0.0, self.fy, self.cy], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )


@dataclass(frozen=True)
class FrameRecord:
    output_index: int
    source_index: int
    wall_time: str
    monotonic_ns: int
    rgb_path: Path
    depth_path: Path
    confidence_path: Path | None = None


@dataclass(frozen=True)
class InterpolatedGps:
    monotonic_ns: np.ndarray
    latitude_deg: np.ndarray
    longitude_deg: np.ndarray
    altitude_msl_m: np.ndarray
    geoid_separation_m: np.ndarray
    ellipsoid_height_m: np.ndarray
    course_deg: np.ndarray
    speed_m_s: np.ndarray
    fix_quality: np.ndarray
    fix_quality_name: tuple[str, ...]
    hdop: np.ndarray


class RgbdGpsDataset:
    """Loads only RGB, aligned depth, metadata, timestamps and GNSS.

    Deliberately does not open imu.csv, imu_events.csv, or external_imu.csv.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"Dataset directory not found: {self.root}")
        self.metadata = self._load_json("metadata.json")
        self.camera = self._camera_from_metadata(self.metadata)

    def _load_json(self, name: str) -> dict:
        path = self.root / name
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _camera_from_metadata(metadata: dict) -> CameraModel:
        camera = metadata.get("camera_model") or {}
        image_size = metadata.get("image_size") or {}
        intrinsics = camera.get("intrinsics")
        if not intrinsics or len(intrinsics) != 3:
            raise ValueError("metadata.json does not contain camera_model.intrinsics")
        width = int(image_size.get("width") or camera.get("width") or 0)
        height = int(image_size.get("height") or camera.get("height") or 0)
        if width <= 0 or height <= 0:
            raise ValueError("metadata.json does not contain a valid image size")
        return CameraModel(
            width=width,
            height=height,
            fx=float(intrinsics[0][0]),
            fy=float(intrinsics[1][1]),
            cx=float(intrinsics[0][2]),
            cy=float(intrinsics[1][2]),
        )

    def _resolve_data_path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Path escapes dataset root: {relative}") from exc
        return path

    def load_frames(
        self,
        start_frame: int = 0,
        max_frames: int | None = None,
    ) -> list[FrameRecord]:
        if start_frame < 0:
            raise ValueError("start_frame must be non-negative")
        frames: list[FrameRecord] = []
        with (self.root / "timestamps.csv").open("r", encoding="utf-8", newline="") as handle:
            for source_index, row in enumerate(csv.DictReader(handle)):
                if source_index < start_frame:
                    continue
                if max_frames is not None and len(frames) >= max_frames:
                    break
                rgb = row.get("rgb_file") or ""
                depth = row.get("depth_file") or ""
                confidence = (row.get("confidence_file") or "").strip()
                if not rgb or not depth:
                    continue
                frames.append(
                    FrameRecord(
                        output_index=len(frames),
                        source_index=int(row.get("frame_index") or source_index),
                        wall_time=row.get("frame_host_wall_time") or "",
                        monotonic_ns=int(row["frame_host_monotonic_ns"]),
                        rgb_path=self._resolve_data_path(rgb),
                        depth_path=self._resolve_data_path(depth),
                        confidence_path=(
                            self._resolve_data_path(confidence) if confidence else None
                        ),
                    )
                )
        if len(frames) < 2:
            raise ValueError("At least two synchronized RGB-depth frames are required")
        missing = [str(p) for frame in frames for p in (frame.rgb_path, frame.depth_path) if not p.is_file()]
        if missing:
            preview = "\n".join(missing[:5])
            raise FileNotFoundError(f"Referenced RGB/depth files are missing:\n{preview}")
        return frames

    def interpolate_gps(self, frames: list[FrameRecord]) -> InterpolatedGps:
        rows: list[dict[str, str]] = []
        with (self.root / "gps.csv").open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("nmea_type") != "GGA":
                    continue
                if not row.get("measurement_host_monotonic_ns"):
                    continue
                if not row.get("latitude_deg") or not row.get("longitude_deg"):
                    continue
                rows.append(row)
        if len(rows) < 2:
            raise ValueError("gps.csv does not contain at least two usable GGA epochs")

        gps_t = np.asarray([int(row["measurement_host_monotonic_ns"]) for row in rows], dtype=np.int64)
        order = np.argsort(gps_t, kind="stable")
        gps_t = gps_t[order]
        rows = [rows[int(index)] for index in order]
        frame_t = np.asarray([frame.monotonic_ns for frame in frames], dtype=np.int64)
        if frame_t[0] < gps_t[0] or frame_t[-1] > gps_t[-1]:
            raise ValueError("Selected frame interval is not fully covered by gps.csv")

        def numeric(field: str, default: float = 0.0) -> np.ndarray:
            return np.asarray(
                [float(row[field]) if row.get(field) not in (None, "") else default for row in rows],
                dtype=np.float64,
            )

        def interp(values: np.ndarray) -> np.ndarray:
            return np.interp(frame_t.astype(np.float64), gps_t.astype(np.float64), values)

        lat = interp(numeric("latitude_deg"))
        lon = interp(numeric("longitude_deg"))
        altitude = interp(numeric("altitude_m"))
        geoid = interp(numeric("geoid_separation_m"))
        speed_m_s = interp(numeric("speed_knots") * 0.5144444444444445)

        course_rad = np.unwrap(np.deg2rad(numeric("course_deg")))
        course_deg = np.rad2deg(interp(course_rad)) % 360.0

        nearest_right = np.searchsorted(gps_t, frame_t, side="left")
        nearest_right = np.clip(nearest_right, 0, len(gps_t) - 1)
        nearest_left = np.clip(nearest_right - 1, 0, len(gps_t) - 1)
        choose_left = np.abs(frame_t - gps_t[nearest_left]) <= np.abs(gps_t[nearest_right] - frame_t)
        nearest = np.where(choose_left, nearest_left, nearest_right)

        fix = np.asarray(
            [int(rows[int(index)].get("fix_quality") or 0) for index in nearest], dtype=np.int16
        )
        names = tuple(rows[int(index)].get("fix_quality_name") or "unknown" for index in nearest)
        hdop = np.asarray(
            [float(rows[int(index)].get("hdop") or "nan") for index in nearest], dtype=np.float64
        )
        return InterpolatedGps(
            monotonic_ns=frame_t,
            latitude_deg=lat,
            longitude_deg=lon,
            altitude_msl_m=altitude,
            geoid_separation_m=geoid,
            ellipsoid_height_m=altitude + geoid,
            course_deg=course_deg,
            speed_m_s=speed_m_s,
            fix_quality=fix,
            fix_quality_name=names,
            hdop=hdop,
        )
