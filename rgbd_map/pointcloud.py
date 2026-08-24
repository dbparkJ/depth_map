from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import cv2
import numpy as np

from .dataset import CameraModel, FrameRecord
from .trajectory import TrajectoryResult


@dataclass(frozen=True)
class PointCloudResult:
    points_enu_m: np.ndarray
    colors_rgb: np.ndarray
    sampled_frame_count: int
    decoded_frame_count: int
    valid_depth_sample_count: int


def build_point_cloud(
    frames: list[FrameRecord],
    camera: CameraModel,
    trajectory: TrajectoryResult,
    frame_stride: int = 10,
    pixel_stride: int = 10,
    voxel_size_m: float = 0.25,
    max_points: int = 300_000,
    min_depth_m: float = 1.0,
    max_depth_m: float = 30.0,
    roi_top_ratio: float = 0.15,
    roi_bottom_ratio: float = 0.90,
    camera_offset_right_m: float = 0.0,
    camera_offset_down_m: float = 0.0,
    camera_offset_forward_m: float = 0.0,
    progress_every: int = 25,
) -> PointCloudResult:
    if frame_stride <= 0 or pixel_stride <= 0:
        raise ValueError("frame_stride and pixel_stride must be positive")
    if voxel_size_m <= 0.0 or max_points <= 0:
        raise ValueError("voxel_size_m and max_points must be positive")
    selected = list(range(0, len(frames), frame_stride))
    if selected[-1] != len(frames) - 1:
        selected.append(len(frames) - 1)
    per_frame_cap = max(100, int(ceil(max_points * 1.35 / len(selected))))
    min_depth_mm = float(min_depth_m) * 1000.0
    max_depth_mm = float(max_depth_m) * 1000.0
    camera_offset = np.array(
        [camera_offset_right_m, camera_offset_down_m, camera_offset_forward_m],
        dtype=np.float64,
    )

    y0 = max(0, int(round(camera.height * roi_top_ratio)))
    y1 = min(camera.height, int(round(camera.height * roi_bottom_ratio)))
    yy, xx = np.mgrid[y0:y1:pixel_stride, 0:camera.width:pixel_stride]
    sample_u = xx.reshape(-1)
    sample_v = yy.reshape(-1)
    voxel_map: dict[tuple[int, int, int], tuple[np.ndarray, np.ndarray]] = {}
    decoded = 0
    valid_depth_samples = 0

    for sequence_index, frame_index in enumerate(selected):
        frame = frames[frame_index]
        depth = cv2.imread(str(frame.depth_path), cv2.IMREAD_UNCHANGED)
        color_bgr = cv2.imread(str(frame.rgb_path), cv2.IMREAD_COLOR)
        if depth is None or color_bgr is None:
            continue
        if depth.shape[:2] != (camera.height, camera.width):
            raise ValueError(f"Unexpected depth size at {frame.depth_path}: {depth.shape}")
        if color_bgr.shape[:2] != (camera.height, camera.width):
            raise ValueError(f"Unexpected RGB size at {frame.rgb_path}: {color_bgr.shape}")
        decoded += 1
        d = depth[sample_v, sample_u].astype(np.float64)
        valid = (d >= min_depth_mm) & (d <= max_depth_mm) & (d != 65535.0)
        if not np.any(valid):
            continue
        u = sample_u[valid].astype(np.float64)
        v = sample_v[valid].astype(np.float64)
        z = d[valid] / 1000.0
        points_camera = np.column_stack(
            (
                (u - camera.cx) * z / camera.fx,
                (v - camera.cy) * z / camera.fy,
                z,
            )
        )
        colors = color_bgr[sample_v[valid], sample_u[valid], ::-1].astype(np.uint8)
        valid_depth_samples += len(points_camera)
        if len(points_camera) > per_frame_cap:
            keep = np.linspace(0, len(points_camera) - 1, per_frame_cap, dtype=np.int64)
            points_camera = points_camera[keep]
            colors = colors[keep]

        rotation = trajectory.rotations_enu_from_camera[frame_index]
        camera_position = trajectory.positions_enu_m[frame_index] + rotation @ camera_offset
        points_enu = points_camera @ rotation.T + camera_position
        voxel_keys = np.floor(points_enu / voxel_size_m).astype(np.int64)
        _, unique_indices = np.unique(voxel_keys, axis=0, return_index=True)
        for local_index in unique_indices:
            key_array = voxel_keys[local_index]
            key = (int(key_array[0]), int(key_array[1]), int(key_array[2]))
            if key not in voxel_map:
                voxel_map[key] = (points_enu[local_index], colors[local_index])

        if progress_every and (
            (sequence_index + 1) % progress_every == 0 or sequence_index + 1 == len(selected)
        ):
            print(
                f"[cloud] {sequence_index + 1}/{len(selected)} frames, "
                f"{len(voxel_map):,} voxels",
                flush=True,
            )

    if not voxel_map:
        raise RuntimeError("No valid depth points survived point-cloud filtering")
    points = np.asarray([value[0] for value in voxel_map.values()], dtype=np.float64)
    colors = np.asarray([value[1] for value in voxel_map.values()], dtype=np.uint8)
    if len(points) > max_points:
        keep = np.linspace(0, len(points) - 1, max_points, dtype=np.int64)
        points = points[keep]
        colors = colors[keep]
    return PointCloudResult(
        points_enu_m=points,
        colors_rgb=colors,
        sampled_frame_count=len(selected),
        decoded_frame_count=decoded,
        valid_depth_sample_count=valid_depth_samples,
    )

