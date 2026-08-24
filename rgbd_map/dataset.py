from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from math import isfinite
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
class FrameChunkSelection:
    """Provenance for a timestamp-based, half-open frame selection."""

    chunk_index: int
    requested_duration_seconds: float
    dataset_first_monotonic_ns: int
    interval_start_monotonic_ns: int
    interval_end_monotonic_ns_exclusive: int
    actual_start_monotonic_ns: int
    actual_end_monotonic_ns: int
    actual_start_wall_time: str
    actual_end_wall_time: str
    actual_duration_seconds: float
    source_frame_start: int
    source_frame_end_inclusive: int
    frame_count: int
    boundary_policy: str = "half_open"
    origin_policy: str = "dataset_first_synchronized_frame"


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

    def _frame_from_row(
        self,
        row: dict[str, str],
        source_index: int,
        output_index: int,
    ) -> FrameRecord | None:
        rgb = row.get("rgb_file") or ""
        depth = row.get("depth_file") or ""
        if not rgb or not depth:
            return None
        confidence = (row.get("confidence_file") or "").strip()
        return FrameRecord(
            output_index=output_index,
            source_index=int(row.get("frame_index") or source_index),
            wall_time=row.get("frame_host_wall_time") or "",
            monotonic_ns=int(row["frame_host_monotonic_ns"]),
            rgb_path=self._resolve_data_path(rgb),
            depth_path=self._resolve_data_path(depth),
            confidence_path=(
                self._resolve_data_path(confidence) if confidence else None
            ),
        )

    @staticmethod
    def _validate_loaded_frames(
        frames: list[FrameRecord],
        *,
        interval_name: str | None = None,
    ) -> None:
        if len(frames) < 2:
            if interval_name is None:
                raise ValueError(
                    "At least two synchronized RGB-depth frames are required"
                )
            raise ValueError(
                f"{interval_name} contains {len(frames)} synchronized RGB-depth "
                "frames; at least two are required"
            )
        missing = [
            str(path)
            for frame in frames
            for path in (frame.rgb_path, frame.depth_path)
            if not path.is_file()
        ]
        if missing:
            preview = "\n".join(missing[:5])
            raise FileNotFoundError(
                f"Referenced RGB/depth files are missing:\n{preview}"
            )

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
                frame = self._frame_from_row(row, source_index, len(frames))
                if frame is not None:
                    frames.append(frame)
        self._validate_loaded_frames(frames)
        return frames

    def load_frame_chunk(
        self,
        chunk_duration_seconds: float,
        chunk_index: int = 0,
    ) -> tuple[list[FrameRecord], FrameChunkSelection]:
        """Load one timestamp window relative to the first valid RGB-D frame.

        Window boundaries are half-open: ``[first + index * duration,
        first + (index + 1) * duration)``. Rows without both RGB and aligned
        depth references do not establish the dataset time origin.
        """

        duration_seconds = float(chunk_duration_seconds)
        if not isfinite(duration_seconds) or duration_seconds <= 0.0:
            raise ValueError("chunk_duration_seconds must be finite and positive")
        if isinstance(chunk_index, bool) or not isinstance(chunk_index, int):
            raise ValueError("chunk_index must be a non-negative integer")
        if chunk_index < 0:
            raise ValueError("chunk_index must be a non-negative integer")
        try:
            duration_ns = int(round(duration_seconds * 1_000_000_000.0))
        except OverflowError as exc:
            raise ValueError("chunk_duration_seconds is too large") from exc
        if duration_ns <= 0:
            raise ValueError(
                "chunk_duration_seconds must span at least one nanosecond"
            )

        frames: list[FrameRecord] = []
        dataset_first_ns: int | None = None
        interval_start_ns: int | None = None
        interval_end_ns: int | None = None
        with (self.root / "timestamps.csv").open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            for source_index, row in enumerate(csv.DictReader(handle)):
                if not (row.get("rgb_file") and row.get("depth_file")):
                    continue
                monotonic_ns = int(row["frame_host_monotonic_ns"])
                if dataset_first_ns is None:
                    dataset_first_ns = monotonic_ns
                    interval_start_ns = (
                        dataset_first_ns + chunk_index * duration_ns
                    )
                    interval_end_ns = interval_start_ns + duration_ns
                assert interval_start_ns is not None
                assert interval_end_ns is not None
                if monotonic_ns >= interval_end_ns:
                    # Recorder timestamps are chronological.  Stop at the first
                    # row outside the half-open window so later chunks are not
                    # even scanned during a one-chunk run.
                    break
                if monotonic_ns < interval_start_ns:
                    continue
                frame = self._frame_from_row(row, source_index, len(frames))
                if frame is not None:
                    frames.append(frame)

        if dataset_first_ns is None:
            raise ValueError("timestamps.csv contains no synchronized RGB-depth frames")
        self._validate_loaded_frames(
            frames,
            interval_name=f"Frame chunk {chunk_index}",
        )
        first = frames[0]
        last = frames[-1]
        assert interval_start_ns is not None
        assert interval_end_ns is not None
        selection = FrameChunkSelection(
            chunk_index=chunk_index,
            requested_duration_seconds=duration_seconds,
            dataset_first_monotonic_ns=dataset_first_ns,
            interval_start_monotonic_ns=interval_start_ns,
            interval_end_monotonic_ns_exclusive=interval_end_ns,
            actual_start_monotonic_ns=first.monotonic_ns,
            actual_end_monotonic_ns=last.monotonic_ns,
            actual_start_wall_time=first.wall_time,
            actual_end_wall_time=last.wall_time,
            actual_duration_seconds=(
                last.monotonic_ns - first.monotonic_ns
            )
            / 1_000_000_000.0,
            source_frame_start=first.source_index,
            source_frame_end_inclusive=last.source_index,
            frame_count=len(frames),
        )
        return frames, selection

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
