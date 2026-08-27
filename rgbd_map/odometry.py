from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .dataset import CameraModel
from .depth_quality import DepthQualityPolicy, evaluate_depth_quality


@dataclass(frozen=True)
class FeatureFrame:
    points_px: np.ndarray
    descriptors: np.ndarray | None


@dataclass(frozen=True)
class OdometryResult:
    success: bool
    method: str
    rotation_current_from_previous: np.ndarray
    translation_previous_camera_m: np.ndarray
    matches: int
    inliers: int
    inlier_ratio: float
    translation_norm_m: float
    rotation_deg: float
    reprojection_error_px: float | None
    reason: str

    @classmethod
    def failed(cls, reason: str, matches: int = 0) -> "OdometryResult":
        return cls(
            success=False,
            method="gps_fallback",
            rotation_current_from_previous=np.eye(3, dtype=np.float64),
            translation_previous_camera_m=np.zeros(3, dtype=np.float64),
            matches=matches,
            inliers=0,
            inlier_ratio=0.0,
            translation_norm_m=0.0,
            rotation_deg=0.0,
            reprojection_error_px=None,
            reason=reason,
        )


class SiftRgbdOdometry:
    def __init__(
        self,
        camera: CameraModel,
        image_scale: float = 0.5,
        max_features: int = 3500,
        ratio_test: float = 0.74,
        min_matches: int = 60,
        min_pnp_points: int = 30,
        min_inliers: int = 24,
        min_inlier_ratio: float = 0.20,
        min_depth_m: float = 1.0,
        max_depth_m: float = 30.0,
        feature_top_ratio: float = 0.12,
        feature_bottom_ratio: float = 0.92,
        max_translation_m: float = 15.0,
        min_gps_translation_ratio: float = 0.15,
        max_gps_translation_ratio: float = 3.0,
        max_rotation_deg: float = 25.0,
        depth_quality_policy: DepthQualityPolicy | None = None,
    ):
        if not 0.1 <= image_scale <= 1.0:
            raise ValueError("image_scale must be in [0.1, 1.0]")
        self.camera = camera
        self.image_scale = float(image_scale)
        self.ratio_test = float(ratio_test)
        self.min_matches = int(min_matches)
        self.min_pnp_points = int(min_pnp_points)
        self.min_inliers = int(min_inliers)
        self.min_inlier_ratio = float(min_inlier_ratio)
        self.min_depth_mm = float(min_depth_m) * 1000.0
        self.max_depth_mm = float(max_depth_m) * 1000.0
        self.feature_top_ratio = float(feature_top_ratio)
        self.feature_bottom_ratio = float(feature_bottom_ratio)
        self.max_translation_m = float(max_translation_m)
        self.min_gps_translation_ratio = float(min_gps_translation_ratio)
        self.max_gps_translation_ratio = float(max_gps_translation_ratio)
        self.max_rotation_deg = float(max_rotation_deg)
        self.depth_quality_policy = depth_quality_policy or DepthQualityPolicy(
            min_depth_m=float(min_depth_m),
            max_depth_m=float(max_depth_m),
        )
        self.last_depth_quality_report: dict | None = None
        self.detector = cv2.SIFT_create(nfeatures=int(max_features))
        self.matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)

    def extract(self, rgb_path: Path) -> FeatureFrame:
        gray = cv2.imread(str(rgb_path), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise ValueError(f"Failed to decode RGB image: {rgb_path}")
        if self.image_scale != 1.0:
            gray = cv2.resize(
                gray,
                None,
                fx=self.image_scale,
                fy=self.image_scale,
                interpolation=cv2.INTER_AREA,
            )
        mask = np.zeros_like(gray, dtype=np.uint8)
        y0 = int(round(gray.shape[0] * self.feature_top_ratio))
        y1 = int(round(gray.shape[0] * self.feature_bottom_ratio))
        mask[max(0, y0) : min(gray.shape[0], y1), :] = 255
        keypoints, descriptors = self.detector.detectAndCompute(gray, mask)
        if not keypoints:
            return FeatureFrame(np.empty((0, 2), dtype=np.float32), None)
        points = np.asarray([keypoint.pt for keypoint in keypoints], dtype=np.float32)
        points /= self.image_scale
        return FeatureFrame(points_px=points, descriptors=descriptors)

    def estimate(
        self,
        previous: FeatureFrame,
        current: FeatureFrame,
        previous_depth_path: Path,
        gps_distance_m: float,
        previous_confidence_path: Path | None = None,
    ) -> OdometryResult:
        if previous.descriptors is None or current.descriptors is None:
            return OdometryResult.failed("missing_descriptors")
        if len(previous.descriptors) < 2 or len(current.descriptors) < 2:
            return OdometryResult.failed("too_few_descriptors")

        pairs = self.matcher.knnMatch(previous.descriptors, current.descriptors, k=2)
        good = [first for first, second in pairs if first.distance < self.ratio_test * second.distance]
        if len(good) < self.min_matches:
            return OdometryResult.failed("too_few_ratio_matches", len(good))

        previous_px = np.asarray([previous.points_px[m.queryIdx] for m in good], dtype=np.float32)
        current_px = np.asarray([current.points_px[m.trainIdx] for m in good], dtype=np.float32)
        pnp = self._estimate_pnp(
            previous_px,
            current_px,
            previous_depth_path,
            gps_distance_m,
            previous_confidence_path,
        )
        if pnp.success:
            return pnp

        essential = self._estimate_essential(previous_px, current_px, gps_distance_m)
        if essential.success:
            return essential
        return OdometryResult.failed(
            f"pnp={pnp.reason};essential={essential.reason}", matches=len(good)
        )

    def _estimate_pnp(
        self,
        previous_px: np.ndarray,
        current_px: np.ndarray,
        previous_depth_path: Path,
        gps_distance_m: float,
        confidence_path: Path | None = None,
    ) -> OdometryResult:
        depth = cv2.imread(str(previous_depth_path), cv2.IMREAD_UNCHANGED)
        if depth is None or depth.ndim != 2:
            return OdometryResult.failed("depth_decode_failed", len(previous_px))
        confidence = None
        if confidence_path is not None and confidence_path.is_file():
            confidence = cv2.imread(str(confidence_path), cv2.IMREAD_UNCHANGED)
        quality = evaluate_depth_quality(
            depth,
            self.depth_quality_policy,
            confidence,
        )
        self.last_depth_quality_report = quality.report

        uv = np.rint(previous_px).astype(np.int32)
        inside = (
            (uv[:, 0] >= 0)
            & (uv[:, 0] < depth.shape[1])
            & (uv[:, 1] >= 0)
            & (uv[:, 1] < depth.shape[0])
        )
        depths = np.zeros(len(uv), dtype=np.float64)
        depths[inside] = depth[uv[inside, 1], uv[inside, 0]].astype(np.float64)
        valid = inside.copy()
        valid[inside] &= quality.valid_mask[uv[inside, 1], uv[inside, 0]]
        if int(np.count_nonzero(valid)) < self.min_pnp_points:
            return OdometryResult.failed("too_few_depth_matches", len(previous_px))

        uv_prev = previous_px[valid].astype(np.float64)
        uv_curr = current_px[valid].astype(np.float64)
        z = depths[valid] / 1000.0
        object_points = np.column_stack(
            (
                (uv_prev[:, 0] - self.camera.cx) * z / self.camera.fx,
                (uv_prev[:, 1] - self.camera.cy) * z / self.camera.fy,
                z,
            )
        ).astype(np.float32)

        ok, rvec, tvec, inlier_indices = cv2.solvePnPRansac(
            object_points,
            uv_curr.astype(np.float32),
            self.camera.matrix,
            None,
            iterationsCount=250,
            reprojectionError=2.5,
            confidence=0.999,
            flags=cv2.SOLVEPNP_EPNP,
        )
        if not ok or inlier_indices is None:
            return OdometryResult.failed("solvepnp_failed", len(previous_px))
        inlier_indices = inlier_indices.reshape(-1)
        if len(inlier_indices) >= 6:
            rvec, tvec = cv2.solvePnPRefineLM(
                object_points[inlier_indices],
                uv_curr[inlier_indices].astype(np.float32),
                self.camera.matrix,
                None,
                rvec,
                tvec,
            )
        rotation, _ = cv2.Rodrigues(rvec)
        delta_previous = -rotation.T @ tvec.reshape(3)
        projected, _ = cv2.projectPoints(
            object_points[inlier_indices], rvec, tvec, self.camera.matrix, None
        )
        errors = np.linalg.norm(
            projected.reshape(-1, 2) - uv_curr[inlier_indices], axis=1
        )
        return self._validated_result(
            method="pnp",
            rotation=rotation,
            delta_previous=delta_previous,
            matches=len(previous_px),
            inliers=len(inlier_indices),
            candidate_count=len(object_points),
            gps_distance_m=gps_distance_m,
            reprojection_error_px=float(np.median(errors)) if len(errors) else None,
        )

    def _estimate_essential(
        self,
        previous_px: np.ndarray,
        current_px: np.ndarray,
        gps_distance_m: float,
    ) -> OdometryResult:
        if gps_distance_m < 0.25:
            return OdometryResult.failed("gps_scale_too_small", len(previous_px))
        essential, mask = cv2.findEssentialMat(
            previous_px,
            current_px,
            self.camera.matrix,
            method=cv2.RANSAC,
            prob=0.999,
            threshold=1.5,
        )
        if essential is None:
            return OdometryResult.failed("essential_failed", len(previous_px))
        if essential.shape[0] > 3:
            essential = essential[:3, :3]
        inliers, rotation, translation, pose_mask = cv2.recoverPose(
            essential,
            previous_px,
            current_px,
            self.camera.matrix,
            mask=mask,
        )
        if inliers <= 0:
            return OdometryResult.failed("recover_pose_failed", len(previous_px))
        delta_previous = -rotation.T @ translation.reshape(3)
        norm = float(np.linalg.norm(delta_previous))
        if norm < 1e-9:
            return OdometryResult.failed("essential_zero_translation", len(previous_px))
        delta_previous *= gps_distance_m / norm
        return self._validated_result(
            method="essential_gps_scale",
            rotation=rotation,
            delta_previous=delta_previous,
            matches=len(previous_px),
            inliers=int(inliers),
            candidate_count=len(previous_px),
            gps_distance_m=gps_distance_m,
            reprojection_error_px=None,
        )

    def _validated_result(
        self,
        method: str,
        rotation: np.ndarray,
        delta_previous: np.ndarray,
        matches: int,
        inliers: int,
        candidate_count: int,
        gps_distance_m: float,
        reprojection_error_px: float | None,
    ) -> OdometryResult:
        inlier_ratio = float(inliers / max(candidate_count, 1))
        translation_norm = float(np.linalg.norm(delta_previous))
        cosine = float(np.clip((np.trace(rotation) - 1.0) * 0.5, -1.0, 1.0))
        rotation_deg = float(np.rad2deg(np.arccos(cosine)))
        reason = "ok"
        if inliers < self.min_inliers:
            reason = "too_few_inliers"
        elif inlier_ratio < self.min_inlier_ratio:
            reason = "low_inlier_ratio"
        elif translation_norm > self.max_translation_m:
            reason = "translation_over_absolute_limit"
        elif (
            method == "pnp"
            and gps_distance_m >= 0.75
            and abs(translation_norm - gps_distance_m) > max(1.0, 0.5 * gps_distance_m)
        ):
            # A geometrically clean PnP fit can still have the wrong scale when
            # depth lands on a moving/occluding surface.  Mark it failed here so
            # estimate() can try the essential-matrix direction with GPS scale.
            reason = "translation_inconsistent_vs_gps"
        elif gps_distance_m >= 0.75 and translation_norm < gps_distance_m * self.min_gps_translation_ratio:
            reason = "translation_too_small_vs_gps"
        elif gps_distance_m >= 0.75 and translation_norm > gps_distance_m * self.max_gps_translation_ratio + 1.0:
            reason = "translation_too_large_vs_gps"
        elif rotation_deg > self.max_rotation_deg:
            reason = "rotation_over_limit"
        return OdometryResult(
            success=reason == "ok",
            method=method if reason == "ok" else "gps_fallback",
            rotation_current_from_previous=rotation.astype(np.float64),
            translation_previous_camera_m=delta_previous.astype(np.float64),
            matches=int(matches),
            inliers=int(inliers),
            inlier_ratio=inlier_ratio,
            translation_norm_m=translation_norm,
            rotation_deg=rotation_deg,
            reprojection_error_px=reprojection_error_px,
            reason=reason,
        )
