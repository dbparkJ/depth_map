from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import yaml


REQUIRED_DATASETS = {
    "calibration",
    "development",
    "holdout",
    "adverse_weather_shadow",
    "flat_negative",
    "severe_pothole",
    "rutting",
    "intersection",
    "stationary_pose_poor",
}
REQUIRED_METRICS = {
    "pothole_instance_precision",
    "pothole_instance_recall",
    "max_depth_mae_m",
    "area_error_m2",
    "volume_error_m3",
    "rut_depth_mae_m",
    "flat_false_positive_per_100m",
    "crack_holdout_metrics",
    "valid_coverage_ratio",
    "low_confidence_recall",
    "job_success_rate",
    "wall_time_s",
    "peak_rss_mb",
    "web_first_meaningful_render_s",
    "report_consistency",
}
REQUIRED_PROHIBITIONS = {
    "holdout_not_synthetic_only",
    "official_naming_restrictions",
    "full_dataset_no_oom",
    "report_json_consistency",
    "boundary_defect_deduplication",
    "calibration_known_before_auto_confirmation",
    "docker_quickstart",
}
REQUIRED_ARTIFACTS = {
    "changelog",
    "release_manifest",
    "docker_image_tags",
    "sbom",
    "migration_notes",
    "benchmark_report",
    "known_limitations",
    "operator_runbook",
    "rollback_runbook",
}


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_release_readiness(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("release readiness manifest must be a YAML object")
    required = {
        "format_version",
        "target_release",
        "evaluation_date",
        "release_status",
        "dataset_policy",
        "metrics",
        "release_prohibitions",
        "release_artifacts",
        "approval",
    }
    missing = required - set(payload)
    unknown = set(payload) - required
    if missing:
        raise ValueError("release readiness fields missing: " + ", ".join(sorted(missing)))
    if unknown:
        raise ValueError("unknown release readiness fields: " + ", ".join(sorted(unknown)))
    if payload["format_version"] != 1:
        raise ValueError("unsupported release readiness format_version")
    return payload


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_release_readiness(
    payload: Mapping[str, Any],
    *,
    artifact_root: str | Path | None = None,
) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []

    def block(code: str, path: str, message: str) -> None:
        blockers.append({"code": code, "path": path, "message": message})

    datasets = _object(_object(payload["dataset_policy"], "dataset_policy").get("subsets"), "dataset_policy.subsets")
    if set(datasets) != REQUIRED_DATASETS:
        block("DATASET_SCHEMA", "dataset_policy.subsets", "required release subsets must be defined exactly")
    for name in sorted(REQUIRED_DATASETS):
        entry = _object(datasets.get(name, {}), f"dataset_policy.subsets.{name}")
        if not entry.get("dataset_id") or not entry.get("evidence_uri"):
            block("DATASET_MISSING", f"dataset_policy.subsets.{name}", "dataset ID and evidence are required")
    if payload["dataset_policy"].get("parameter_tuning_and_final_evaluation_separated") is not True:
        block("DATASET_LEAKAGE_GUARD", "dataset_policy.parameter_tuning_and_final_evaluation_separated", "tuning/final evaluation separation must be confirmed")

    metrics = _object(payload["metrics"], "metrics")
    if set(metrics) != REQUIRED_METRICS:
        block("METRIC_SCHEMA", "metrics", "required release metrics must be defined exactly")
    for name in sorted(REQUIRED_METRICS):
        entry = _object(metrics.get(name, {}), f"metrics.{name}")
        value = entry.get("value")
        numeric_or_pass = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        ) or value == "pass"
        if (
            not numeric_or_pass
            or entry.get("acceptance_status") != "pass"
            or not entry.get("evidence_uri")
        ):
            block(
                "METRIC_MISSING",
                f"metrics.{name}",
                "finite value/pass, acceptance_status pass, and evidence are required",
            )

    prohibitions = _object(payload["release_prohibitions"], "release_prohibitions")
    if set(prohibitions) != REQUIRED_PROHIBITIONS:
        block("PROHIBITION_SCHEMA", "release_prohibitions", "all release prohibitions must be defined exactly")
    for name in sorted(REQUIRED_PROHIBITIONS):
        entry = _object(prohibitions.get(name, {}), f"release_prohibitions.{name}")
        if entry.get("status") != "pass" or not entry.get("evidence_uri"):
            block("RELEASE_PROHIBITION", f"release_prohibitions.{name}", "status pass and evidence are required")

    artifacts = _object(payload["release_artifacts"], "release_artifacts")
    if set(artifacts) != REQUIRED_ARTIFACTS:
        block("ARTIFACT_SCHEMA", "release_artifacts", "all release artifacts must be defined exactly")
    for name in sorted(REQUIRED_ARTIFACTS):
        entry = _object(artifacts.get(name, {}), f"release_artifacts.{name}")
        if entry.get("available") is not True or not entry.get("path") or not entry.get("sha256"):
            block("ARTIFACT_MISSING", f"release_artifacts.{name}", "available path and SHA-256 are required")
            continue
        if artifact_root is not None:
            root = Path(artifact_root).expanduser().resolve()
            artifact = (root / str(entry["path"])).resolve()
            try:
                artifact.relative_to(root)
            except ValueError:
                block(
                    "ARTIFACT_INVALID",
                    f"release_artifacts.{name}",
                    "artifact path escapes artifact root",
                )
                continue
            if not artifact.is_file():
                block(
                    "ARTIFACT_INVALID",
                    f"release_artifacts.{name}",
                    "artifact file does not exist",
                )
            elif _file_sha256(artifact) != entry["sha256"]:
                block(
                    "ARTIFACT_INVALID",
                    f"release_artifacts.{name}",
                    "artifact SHA-256 mismatch",
                )

    approval = _object(payload["approval"], "approval")
    if approval.get("status") != "approved" or not approval.get("approver") or not approval.get("approved_at"):
        block("APPROVAL_MISSING", "approval", "named approval and timestamp are required")
    if payload.get("release_status") != "ready":
        block("STATUS_NOT_READY", "release_status", "manifest must explicitly request ready status")

    blockers.sort(key=lambda item: (item["path"], item["code"]))
    return {
        "format_version": 1,
        "target_release": payload.get("target_release"),
        "manifest_sha256": _canonical_hash(payload),
        "status": "READY" if not blockers else "BLOCKED",
        "release_allowed": not blockers,
        "blocker_count": len(blockers),
        "blockers": blockers,
    }
