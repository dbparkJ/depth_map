from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


MODEL_MANIFEST_FORMAT_VERSION = 1
HOLDOUT_PROTOCOL_ID = "road-condition-crack-holdout-v1"
APPROVAL_STATES = {"not_approved", "approved", "rejected"}
REQUIRED_HOLDOUT_METRICS = {
    "pixel_precision",
    "pixel_recall",
    "pixel_f1",
    "instance_recall",
    "mean_absolute_length_error_m",
    "false_positive_per_100m",
    "wet_pixel_f1",
    "shadow_pixel_f1",
}
HIGHER_IS_BETTER = REQUIRED_HOLDOUT_METRICS - {
    "mean_absolute_length_error_m",
    "false_positive_per_100m",
}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_workspace_path(workspace_root: str | Path, relative_path: str) -> Path:
    root = Path(workspace_root).expanduser().resolve()
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError("path must be relative to /workspace")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes /workspace") from exc
    return resolved


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == []


def _sha256_error(value: Any) -> bool:
    return (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    )


def validate_model_manifest(
    payload: Mapping[str, Any],
    *,
    require_approved: bool = True,
) -> list[str]:
    errors: list[str] = []
    if payload.get("format_version") != MODEL_MANIFEST_FORMAT_VERSION:
        errors.append("format_version must be 1")

    dataset = payload.get("dataset")
    if not isinstance(dataset, Mapping):
        errors.append("dataset must be an object")
        dataset = {}
    for field in (
        "dataset_id",
        "split_manifest_sha256",
        "label_format",
        "classes",
        "minimum_detection_width_mm",
        "rgb_resolution_px",
        "ground_sampling_distance_mm_per_px",
        "conditions",
    ):
        if _missing(dataset.get(field)):
            errors.append(f"dataset.{field} is required")
    resolution = dataset.get("rgb_resolution_px")
    if resolution is not None and (
        not isinstance(resolution, list)
        or len(resolution) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in resolution
        )
    ):
        errors.append("dataset.rgb_resolution_px must be [width, height] positive integers")
    classes = dataset.get("classes")
    if classes is not None and (
        not isinstance(classes, list)
        or not classes
        or any(not isinstance(value, str) or not value for value in classes)
        or len(set(classes)) != len(classes)
    ):
        errors.append("dataset.classes must be a unique non-empty string list")
    for field in ("minimum_detection_width_mm", "ground_sampling_distance_mm_per_px"):
        value = dataset.get(field)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0.0
        ):
            errors.append(f"dataset.{field} must be finite and positive")
    conditions = dataset.get("conditions")
    if isinstance(conditions, Mapping):
        for name in ("wet", "shadow", "night"):
            if not isinstance(conditions.get(name), bool):
                errors.append(f"dataset.conditions.{name} must be boolean")
    if dataset.get("split_manifest_sha256") is not None and _sha256_error(
        dataset.get("split_manifest_sha256")
    ):
        errors.append("dataset.split_manifest_sha256 must be a lowercase SHA-256 hex digest")

    environment = payload.get("environment")
    if not isinstance(environment, Mapping):
        errors.append("environment must be an object")
        environment = {}
    for field in (
        "training_gpu",
        "inference_gpu",
        "framework",
        "framework_version",
    ):
        if _missing(environment.get(field)):
            errors.append(f"environment.{field} is required")

    model = payload.get("model")
    if not isinstance(model, Mapping):
        errors.append("model must be an object")
        model = {}
    for field in (
        "name",
        "version",
        "weights_path",
        "weights_sha256",
        "runtime",
        "input_resolution_px",
    ):
        if _missing(model.get(field)):
            errors.append(f"model.{field} is required")
    checksum = model.get("weights_sha256")
    if checksum is not None and _sha256_error(checksum):
        errors.append("model.weights_sha256 must be a lowercase SHA-256 hex digest")
    input_resolution = model.get("input_resolution_px")
    if input_resolution is not None and (
        not isinstance(input_resolution, list)
        or len(input_resolution) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in input_resolution
        )
    ):
        errors.append("model.input_resolution_px must be [width, height] positive integers")

    evaluation = payload.get("holdout_evaluation")
    if not isinstance(evaluation, Mapping):
        errors.append("holdout_evaluation must be an object")
        evaluation = {}
    if evaluation.get("protocol_id") != HOLDOUT_PROTOCOL_ID:
        errors.append(f"holdout_evaluation.protocol_id must be {HOLDOUT_PROTOCOL_ID}")
    metrics = evaluation.get("metrics")
    if not isinstance(metrics, Mapping):
        errors.append("holdout_evaluation.metrics must be an object")
        metrics = {}
    missing_metrics = sorted(REQUIRED_HOLDOUT_METRICS - set(metrics))
    if missing_metrics:
        errors.append("holdout metrics missing: " + ", ".join(missing_metrics))
    for name in sorted(REQUIRED_HOLDOUT_METRICS & set(metrics)):
        value = metrics.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            errors.append(f"holdout_evaluation.metrics.{name} must be finite numeric")
        elif name in {
            "pixel_precision",
            "pixel_recall",
            "pixel_f1",
            "instance_recall",
            "wet_pixel_f1",
            "shadow_pixel_f1",
        } and not 0.0 <= float(value) <= 1.0:
            errors.append(f"holdout_evaluation.metrics.{name} must be between 0 and 1")
        elif name in {
            "mean_absolute_length_error_m",
            "false_positive_per_100m",
        } and float(value) < 0.0:
            errors.append(f"holdout_evaluation.metrics.{name} cannot be negative")

    approval = payload.get("approval")
    if not isinstance(approval, Mapping):
        errors.append("approval must be an object")
        approval = {}
    state = approval.get("state")
    if state not in APPROVAL_STATES:
        errors.append("approval.state must be not_approved, approved, or rejected")
    if require_approved and state != "approved":
        errors.append("model is not explicitly approved for inference")
    if state == "approved":
        for field in ("approved_by", "approved_at", "holdout_protocol_sha256"):
            if _missing(approval.get(field)):
                errors.append(f"approval.{field} is required for approved models")
        if _sha256_error(approval.get("holdout_protocol_sha256")):
            errors.append("approval.holdout_protocol_sha256 must be a lowercase SHA-256 hex digest")
        thresholds = evaluation.get("acceptance_thresholds")
        if not isinstance(thresholds, Mapping) or set(thresholds) != REQUIRED_HOLDOUT_METRICS:
            errors.append("approved models require thresholds for every holdout metric")
        else:
            for name in sorted(REQUIRED_HOLDOUT_METRICS):
                threshold = thresholds[name]
                expected_operator = ">=" if name in HIGHER_IS_BETTER else "<="
                if not isinstance(threshold, Mapping):
                    errors.append(f"acceptance threshold {name} must be an object")
                    continue
                value = threshold.get("value")
                if threshold.get("operator") != expected_operator:
                    errors.append(
                        f"acceptance threshold {name} operator must be {expected_operator}"
                    )
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    errors.append(f"acceptance threshold {name} value must be finite numeric")
                    continue
                metric_value = metrics.get(name)
                if not isinstance(metric_value, (int, float)):
                    continue
                passed = (
                    float(metric_value) >= float(value)
                    if expected_operator == ">="
                    else float(metric_value) <= float(value)
                )
                if not passed:
                    errors.append(f"holdout metric {name} does not pass its threshold")
    return errors


def verify_model_bundle(
    workspace_root: str | Path,
    manifest_relative_path: str,
    *,
    require_approved: bool = True,
    expected_holdout_protocol_sha256: str | None = None,
) -> dict[str, Any]:
    manifest_path = resolve_workspace_path(workspace_root, manifest_relative_path)
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("model manifest must be a JSON object")
    errors = validate_model_manifest(payload, require_approved=require_approved)
    if errors:
        raise ValueError("; ".join(errors))
    if (
        expected_holdout_protocol_sha256 is not None
        and payload["approval"].get("holdout_protocol_sha256") is not None
        and payload["approval"].get("holdout_protocol_sha256")
        != expected_holdout_protocol_sha256
    ):
        raise ValueError("holdout protocol SHA-256 mismatch")
    weights_path = resolve_workspace_path(
        workspace_root,
        str(payload["model"]["weights_path"]),
    )
    if not weights_path.is_file():
        raise FileNotFoundError(weights_path)
    actual = file_sha256(weights_path)
    expected = payload["model"]["weights_sha256"]
    if actual != expected:
        raise ValueError("model weights SHA-256 mismatch")
    return {
        "manifest": payload,
        "manifest_sha256": canonical_sha256(payload),
        "weights_sha256": actual,
        "weights_path": str(weights_path.relative_to(Path(workspace_root).resolve())),
        "approved_for_inference": payload["approval"]["state"] == "approved",
    }


def attach_prediction_audit(defect: Mapping[str, Any]) -> dict[str, Any]:
    original = copy.deepcopy(dict(defect))
    record = copy.deepcopy(original)
    record["original_prediction"] = original
    record["original_prediction_sha256"] = canonical_sha256(original)
    record["review"] = {"state": "unreviewed", "revisions": []}
    return record


def append_review_revision(
    record: Mapping[str, Any],
    *,
    actor: str,
    timestamp: str,
    action: str,
    patch: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    if not actor or not timestamp or not reason:
        raise ValueError("actor, timestamp, and reason are required")
    if action not in {"correct", "approve", "reject"}:
        raise ValueError("unsupported review action")
    updated = copy.deepcopy(dict(record))
    original = updated.get("original_prediction")
    original_hash = updated.get("original_prediction_sha256")
    if not isinstance(original, Mapping) or canonical_sha256(original) != original_hash:
        raise ValueError("original prediction audit hash mismatch")
    review = updated.setdefault("review", {"state": "unreviewed", "revisions": []})
    revisions = review.setdefault("revisions", [])
    revisions.append(
        {
            "revision": len(revisions) + 1,
            "actor": actor,
            "timestamp": timestamp,
            "action": action,
            "patch": copy.deepcopy(dict(patch)),
            "reason": reason,
        }
    )
    review["state"] = {
        "correct": "corrected",
        "approve": "approved",
        "reject": "rejected",
    }[action]
    review["current_annotation"] = copy.deepcopy(dict(patch))
    return updated
