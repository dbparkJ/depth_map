from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Mapping

import yaml

from .config import ScoreConfig


_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_APPROVAL_STATES = {"experimental", "approved_internal", "validated_standard", "retired"}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ScoringProfile:
    profile_id: str
    version: str
    source_document: str | None
    effective_date: str | None
    distress_types: tuple[str, ...]
    severity_thresholds: dict[str, Any]
    density_calculation: dict[str, Any]
    weights_deduct_rules: dict[str, Any]
    missing_metric_policy: str
    approval_status: str
    standard_naming_allowed: bool
    segment_length_m: float
    lane_evaluation: str
    automatic_approval_confidence_threshold: float | None
    profile_sha256: str

    @property
    def score_config(self) -> ScoreConfig:
        values = self.weights_deduct_rules["score_config"]
        return ScoreConfig(**values)

    def summary_contract(self, *, custom_override_applied: bool) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_version": self.version,
            "profile_sha256": self.profile_sha256,
            "source_document": self.source_document,
            "effective_date": self.effective_date,
            "approval_status": self.approval_status,
            "standard_naming_allowed": self.standard_naming_allowed,
            "missing_metric_policy": self.missing_metric_policy,
            "segment_length_m": self.segment_length_m,
            "lane_evaluation": self.lane_evaluation,
            "automatic_approval_confidence_threshold": (
                self.automatic_approval_confidence_threshold
            ),
            "custom_override_applied": custom_override_applied,
        }


def _validate_payload(payload: Mapping[str, Any], expected_id: str) -> None:
    required = {
        "profile_id",
        "version",
        "source_document",
        "effective_date",
        "distress_types",
        "severity_thresholds",
        "density_calculation",
        "weights_deduct_rules",
        "missing_metric_policy",
        "approval_status",
        "standard_naming_allowed",
        "segment_length_m",
        "lane_evaluation",
        "automatic_approval_confidence_threshold",
    }
    missing = required - set(payload)
    unknown = set(payload) - required
    if missing:
        raise ValueError("scoring profile fields missing: " + ", ".join(sorted(missing)))
    if unknown:
        raise ValueError("unknown scoring profile fields: " + ", ".join(sorted(unknown)))
    if payload["profile_id"] != expected_id:
        raise ValueError("profile_id does not match filename")
    if not isinstance(payload["version"], str) or not payload["version"]:
        raise ValueError("profile version is required")
    if (
        not isinstance(payload["distress_types"], list)
        or not payload["distress_types"]
        or any(not isinstance(value, str) or not value for value in payload["distress_types"])
    ):
        raise ValueError("distress_types must be a non-empty string list")
    for field_name in ("severity_thresholds", "density_calculation", "weights_deduct_rules"):
        if not isinstance(payload[field_name], Mapping):
            raise ValueError(f"{field_name} must be an object")
    score_payload = payload["weights_deduct_rules"].get("score_config")
    if not isinstance(score_payload, Mapping):
        raise ValueError("weights_deduct_rules.score_config must be an object")
    expected_score_fields = {field.name for field in fields(ScoreConfig)}
    if set(score_payload) != expected_score_fields:
        raise ValueError("score_config must define exactly the AnalysisConfig score fields")
    ScoreConfig(**score_payload).validate()
    if payload["missing_metric_policy"] not in {"N/A_and_manual_review", "reject_profile"}:
        raise ValueError("unsupported missing_metric_policy")
    approval = payload["approval_status"]
    if approval not in _APPROVAL_STATES:
        raise ValueError("unsupported approval_status")
    if not isinstance(payload["standard_naming_allowed"], bool):
        raise ValueError("standard_naming_allowed must be boolean")
    if payload["standard_naming_allowed"] and approval != "validated_standard":
        raise ValueError("standard naming requires validated_standard approval")
    if approval == "validated_standard" and (
        not payload["source_document"] or not payload["effective_date"]
    ):
        raise ValueError("validated standards require source_document and effective_date")
    if (
        isinstance(payload["segment_length_m"], bool)
        or not isinstance(payload["segment_length_m"], (int, float))
        or float(payload["segment_length_m"]) <= 0
    ):
        raise ValueError("segment_length_m must be positive")
    if payload["lane_evaluation"] not in {"when_roi_available", "disabled", "required"}:
        raise ValueError("unsupported lane_evaluation")
    threshold = payload["automatic_approval_confidence_threshold"]
    if threshold is not None and (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not 0.0 <= float(threshold) <= 1.0
    ):
        raise ValueError("automatic approval confidence threshold must be null or [0, 1]")


def load_scoring_profile(root: str | Path, profile_id: str) -> ScoringProfile:
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ValueError("invalid scoring profile id")
    directory = Path(root).expanduser().resolve()
    path = (directory / f"{profile_id}.yaml").resolve()
    try:
        path.relative_to(directory)
    except ValueError as exc:
        raise ValueError("scoring profile escapes profile root") from exc
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("scoring profile must be a YAML object")
    _validate_payload(payload, profile_id)
    return ScoringProfile(
        profile_id=profile_id,
        version=payload["version"],
        source_document=payload["source_document"],
        effective_date=payload["effective_date"],
        distress_types=tuple(payload["distress_types"]),
        severity_thresholds=copy.deepcopy(payload["severity_thresholds"]),
        density_calculation=copy.deepcopy(payload["density_calculation"]),
        weights_deduct_rules=copy.deepcopy(payload["weights_deduct_rules"]),
        missing_metric_policy=payload["missing_metric_policy"],
        approval_status=payload["approval_status"],
        standard_naming_allowed=payload["standard_naming_allowed"],
        segment_length_m=float(payload["segment_length_m"]),
        lane_evaluation=payload["lane_evaluation"],
        automatic_approval_confidence_threshold=(
            None
            if payload["automatic_approval_confidence_threshold"] is None
            else float(payload["automatic_approval_confidence_threshold"])
        ),
        profile_sha256=_canonical_hash(payload),
    )


def merge_profile_config(
    profile: ScoringProfile,
    request_overrides: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    merged: dict[str, Any] = {
        "score": asdict(profile.score_config),
        "detection": {"segment_length_m": profile.segment_length_m},
    }
    custom = False
    for section, values in (request_overrides or {}).items():
        if isinstance(values, Mapping) and isinstance(merged.get(section), dict):
            merged[section].update(copy.deepcopy(dict(values)))
            if section == "score" or (
                section == "detection" and "segment_length_m" in values
            ):
                custom = True
        else:
            merged[section] = copy.deepcopy(values)
    return merged, custom
