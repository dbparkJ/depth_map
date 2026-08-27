from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

import numpy as np
import cv2
from scipy.spatial.transform import Rotation, Slerp

from .dataset import FrameRecord
from .odometry import OdometryResult
from .trajectory import TrajectoryResult


PoseCloudPolicy = Literal["keep", "skip", "interpolate"]


@dataclass(frozen=True)
class FrameQualityRecord:
    frame_index: int
    source_frame_index: int
    monotonic_ns: int
    pose_method: str
    odometry_method: str
    edge_dt_s: float
    matches: int
    inliers: int
    inlier_ratio: float
    reprojection_error_px: float | None
    translation_m: float
    rotation_deg: float
    linear_speed_m_s: float
    angular_speed_deg_s: float
    quality_score: float
    nominal_quality: bool
    use_for_cloud: bool
    cloud_pose_action: str
    exclusion_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FrameAuditResult:
    records: tuple[FrameQualityRecord, ...]
    use_for_cloud: np.ndarray
    positions_enu_m: np.ndarray
    rotations_enu_from_camera: np.ndarray
    quality_scores: np.ndarray
    metrics: dict[str, Any]


def _robust_upper(values: np.ndarray, *, minimum: float) -> float:
    finite = values[np.isfinite(values)]
    if len(finite) < 4:
        return float(minimum)
    median = float(np.median(finite))
    mad = float(np.median(np.abs(finite - median)))
    return max(float(minimum), median + 8.0 * max(mad, 1e-6))


def audit_cloud_frames(
    frames: Sequence[FrameRecord],
    trajectory: TrajectoryResult,
    odometry: Sequence[OdometryResult],
    *,
    policy: PoseCloudPolicy = "keep",
    max_edge_dt_s: float = 0.25,
    min_inliers: int = 24,
    min_inlier_ratio: float = 0.20,
    max_reprojection_error_px: float = 2.5,
) -> FrameAuditResult:
    """Score frames for dense-cloud use without altering exported trajectory."""

    if policy not in {"keep", "skip", "interpolate"}:
        raise ValueError("pose cloud policy must be keep, skip, or interpolate")
    count = len(frames)
    if count == 0 or len(odometry) != count:
        raise ValueError("frames and odometry must be non-empty and aligned")
    positions = np.asarray(trajectory.positions_enu_m, dtype=np.float64)
    rotations = np.asarray(trajectory.rotations_enu_from_camera, dtype=np.float64)
    if positions.shape != (count, 3) or rotations.shape != (count, 3, 3):
        raise ValueError("trajectory arrays do not align with frames")
    timestamps = np.asarray([frame.monotonic_ns for frame in frames], dtype=np.int64)
    dt = np.zeros(count, dtype=np.float64)
    dt[1:] = np.diff(timestamps).astype(np.float64) / 1e9
    translation = np.zeros(count, dtype=np.float64)
    translation[1:] = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    linear_speed = np.zeros(count, dtype=np.float64)
    np.divide(translation, dt, out=linear_speed, where=dt > 0.0)
    angular = np.zeros(count, dtype=np.float64)
    for index in range(1, count):
        relative = rotations[index - 1].T @ rotations[index]
        cosine = float(np.clip((np.trace(relative) - 1.0) * 0.5, -1.0, 1.0))
        angular[index] = float(np.rad2deg(np.arccos(cosine)))
    angular_speed = np.zeros(count, dtype=np.float64)
    np.divide(angular, dt, out=angular_speed, where=dt > 0.0)
    speed_limit = _robust_upper(linear_speed[1:], minimum=45.0)
    angular_limit = _robust_upper(angular_speed[1:], minimum=120.0)

    nominal = np.ones(count, dtype=bool)
    reasons: list[list[str]] = [[] for _ in range(count)]
    scores = np.ones(count, dtype=np.float64)
    fallback_methods = {"gps_fallback", "time_gap_fallback", "gps_vector_rejected"}
    for index in range(1, count):
        estimate = odometry[index]
        pose_method = trajectory.methods[index]
        if not np.isfinite(dt[index]) or dt[index] <= 0.0 or dt[index] > max_edge_dt_s:
            reasons[index].append("timestamp_gap")
            scores[index] -= 0.45
        if pose_method in fallback_methods:
            reasons[index].append(pose_method)
            scores[index] -= 0.40
        if pose_method == "pnp" or estimate.method == "pnp":
            if estimate.inliers < min_inliers:
                reasons[index].append("low_inliers")
                scores[index] -= 0.25
            if estimate.inlier_ratio < min_inlier_ratio:
                reasons[index].append("low_inlier_ratio")
                scores[index] -= 0.25
            if (
                estimate.reprojection_error_px is not None
                and estimate.reprojection_error_px > max_reprojection_error_px
            ):
                reasons[index].append("high_reprojection_error")
                scores[index] -= 0.25
        # Motion outliers are a corroborating signal. They never reject an otherwise
        # sound PnP/essential edge, which preserves legitimate sharp turns.
        motion_outlier = (
            linear_speed[index] > speed_limit
            or angular_speed[index] > angular_limit
        )
        if motion_outlier:
            reasons[index].append("motion_mad_outlier")
            scores[index] -= 0.15
        nominal[index] = not reasons[index] or (
            reasons[index] == ["motion_mad_outlier"]
            and pose_method not in fallback_methods
        )
    np.clip(scores, 0.0, 1.0, out=scores)

    cloud_positions = positions.copy()
    cloud_rotations = rotations.copy()
    use = nominal.copy() if policy != "keep" else np.ones(count, dtype=bool)
    actions = ["original" if nominal[index] else "kept_by_policy" for index in range(count)]
    if policy == "skip":
        actions = ["original" if nominal[index] else "skipped" for index in range(count)]
    elif policy == "interpolate":
        good_indices = np.flatnonzero(nominal)
        for index in np.flatnonzero(~nominal):
            previous = good_indices[good_indices < index]
            following = good_indices[good_indices > index]
            if not len(previous) or not len(following):
                use[index] = False
                actions[index] = "skipped_no_bracket"
                reasons[index].append("interpolation_unavailable")
                continue
            left = int(previous[-1])
            right = int(following[0])
            span_s = float(timestamps[right] - timestamps[left]) / 1e9
            if span_s <= 0.0 or span_s > 2.5 * max_edge_dt_s:
                use[index] = False
                actions[index] = "skipped_long_bracket"
                reasons[index].append("interpolation_span_too_large")
                continue
            alpha = float(timestamps[index] - timestamps[left]) / float(
                timestamps[right] - timestamps[left]
            )
            cloud_positions[index] = (
                (1.0 - alpha) * positions[left] + alpha * positions[right]
            )
            key_times = np.array([0.0, 1.0], dtype=np.float64)
            slerp = Slerp(
                key_times,
                Rotation.from_matrix(np.stack((rotations[left], rotations[right]))),
            )
            cloud_rotations[index] = slerp([alpha]).as_matrix()[0]
            use[index] = True
            actions[index] = "interpolated"

    records: list[FrameQualityRecord] = []
    for index, frame in enumerate(frames):
        estimate = odometry[index]
        records.append(
            FrameQualityRecord(
                frame_index=index,
                source_frame_index=int(frame.source_index),
                monotonic_ns=int(frame.monotonic_ns),
                pose_method=str(trajectory.methods[index]),
                odometry_method=str(estimate.method),
                edge_dt_s=float(dt[index]),
                matches=int(estimate.matches),
                inliers=int(estimate.inliers),
                inlier_ratio=float(estimate.inlier_ratio),
                reprojection_error_px=(
                    None
                    if estimate.reprojection_error_px is None
                    else float(estimate.reprojection_error_px)
                ),
                translation_m=float(translation[index]),
                rotation_deg=float(angular[index]),
                linear_speed_m_s=float(linear_speed[index]),
                angular_speed_deg_s=float(angular_speed[index]),
                quality_score=float(scores[index]),
                nominal_quality=bool(nominal[index]),
                use_for_cloud=bool(use[index]),
                cloud_pose_action=actions[index],
                exclusion_reason=";".join(reasons[index]) or "ok",
            )
        )
    action_counts = {
        action: int(sum(record.cloud_pose_action == action for record in records))
        for action in sorted({record.cloud_pose_action for record in records})
    }
    return FrameAuditResult(
        records=tuple(records),
        use_for_cloud=use,
        positions_enu_m=cloud_positions,
        rotations_enu_from_camera=cloud_rotations,
        quality_scores=scores.astype(np.float32),
        metrics={
            "policy": policy,
            "frame_count": count,
            "nominal_quality_count": int(np.count_nonzero(nominal)),
            "cloud_use_count": int(np.count_nonzero(use)),
            "action_counts": action_counts,
            "resolved_linear_speed_outlier_m_s": speed_limit,
            "resolved_angular_speed_outlier_deg_s": angular_limit,
            "thresholds": {
                "max_edge_dt_s": float(max_edge_dt_s),
                "min_inliers": int(min_inliers),
                "min_inlier_ratio": float(min_inlier_ratio),
                "max_reprojection_error_px": float(max_reprojection_error_px),
            },
        },
    )


def write_pose_frame_quality_csv(
    path: Path,
    audit: FrameAuditResult,
    frame_reports: Sequence[dict[str, Any]] | None = None,
) -> None:
    """Atomically write pose QA plus optional projection/depth summaries."""

    path.parent.mkdir(parents=True, exist_ok=True)
    reports = list(frame_reports or ())
    extra_names = sorted({key for report in reports for key in report})
    fieldnames = list(audit.records[0].to_dict()) + extra_names
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, record in enumerate(audit.records):
            row = record.to_dict()
            if index < len(reports):
                row.update(reports[index])
            writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_frame_quality_diagnostics(
    diagnostics_dir: Path,
    frames: Sequence[FrameRecord],
    audit: FrameAuditResult,
    frame_reports: Sequence[dict[str, Any]] | None = None,
    *,
    montage_frame_count: int = 12,
) -> dict[str, str]:
    """Write a deterministic reject heatmap and highest-risk frame montage."""

    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    reports = list(frame_reports or ())
    count = len(audit.records)
    cell_width = max(2, min(8, 1600 // max(count, 1)))
    heatmap = np.full((72, max(1, count * cell_width), 3), 245, dtype=np.uint8)
    for index, record in enumerate(audit.records):
        if not record.use_for_cloud:
            color = (35, 35, 230)
        elif record.cloud_pose_action == "interpolated":
            color = (20, 180, 245)
        elif record.nominal_quality:
            color = (45, 190, 60)
        else:
            color = (80, 150, 220)
        x0 = index * cell_width
        heatmap[:, x0 : x0 + cell_width] = color
    heatmap_path = diagnostics_dir / "frame_reject_heatmap.png"
    cv2.imwrite(str(heatmap_path), heatmap)

    risk: list[tuple[float, int]] = []
    for index, record in enumerate(audit.records):
        report = reports[index] if index < len(reports) else {}
        removed = float(report.get("depth_quality_removed_count") or 0) + float(
            report.get("temporal_removed_count") or 0
        )
        contribution = max(1.0, float(report.get("projection_candidate_count") or 1))
        score = (1.0 - record.quality_score) * 10.0 + removed / contribution
        risk.append((score, index))
    selected = [
        index
        for _score, index in sorted(risk, key=lambda item: (-item[0], item[1]))[
            : max(1, int(montage_frame_count))
        ]
    ]
    thumbnails: list[np.ndarray] = []
    for index in selected:
        image = cv2.imread(str(frames[index].rgb_path), cv2.IMREAD_COLOR)
        if image is None:
            image = np.zeros((180, 320, 3), dtype=np.uint8)
        else:
            image = cv2.resize(image, (320, 180), interpolation=cv2.INTER_AREA)
        record = audit.records[index]
        label = (
            f"f{index} {record.pose_method} {record.cloud_pose_action} "
            f"q={record.quality_score:.2f}"
        )
        cv2.rectangle(image, (0, 0), (320, 26), (0, 0, 0), thickness=-1)
        cv2.putText(
            image,
            label,
            (6, 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.43,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        thumbnails.append(image)
    columns = 4
    rows = int(np.ceil(len(thumbnails) / columns))
    montage = np.zeros((rows * 180, columns * 320, 3), dtype=np.uint8)
    for index, thumbnail in enumerate(thumbnails):
        row, column = divmod(index, columns)
        montage[row * 180 : (row + 1) * 180, column * 320 : (column + 1) * 320] = thumbnail
    montage_path = diagnostics_dir / "problem_frame_montage.png"
    cv2.imwrite(str(montage_path), montage)
    return {
        "frame_reject_heatmap": str(heatmap_path.name),
        "problem_frame_montage": str(montage_path.name),
    }
