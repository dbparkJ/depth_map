from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RIMMS_REQUEST_CONTRACT_VERSION = "road-condition-rimms-request-v1"
RIMMS_RESULT_CONTRACT_VERSION = "road-condition-rimms-result-v1"


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["synthetic", "mapping_bundle"] = "synthetic"
    synthetic_profile: Literal["flat", "potholes", "rutting", "mixed"] = "mixed"
    mapping_output_path: str | None = None
    road_roi_path: str | None = None
    point_cloud_stage: Literal["raw", "clean", "removed"] = "raw"
    scoring_profile_id: str = Field(
        default="internal-geometry-mvp-v1",
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$",
    )
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> "CreateJobRequest":
        if self.source_type == "mapping_bundle" and not self.mapping_output_path:
            raise ValueError("mapping_output_path is required for mapping_bundle")
        if self.source_type == "synthetic" and self.mapping_output_path:
            raise ValueError("mapping_output_path is only valid for mapping_bundle")
        if self.source_type == "synthetic" and self.road_roi_path:
            raise ValueError("road_roi_path is only valid for mapping_bundle")
        return self


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_types: list[Literal["pothole", "rutting", "bump"]] = Field(
        default_factory=lambda: ["pothole", "rutting", "bump"]
    )
    unit_prices: dict[str, float] = Field(default_factory=dict)
    rainfall_mm: float = Field(default=30.0, ge=0.0, le=1000.0)


class ScenarioV2Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_id: str = Field(
        default="kr-molit-2026h2-reference",
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$",
    )
    include_types: list[Literal["pothole", "rutting", "bump"]] = Field(
        default_factory=lambda: ["pothole", "rutting", "bump"]
    )
    budget_krw: float | None = Field(default=None, ge=0.0)
    comparison_budgets_krw: list[float] = Field(default_factory=list, max_length=10)
    goal: Literal["risk_screening_priority"] = "risk_screening_priority"

    @model_validator(mode="after")
    def validate_comparison_budgets(self) -> "ScenarioV2Request":
        if any(value < 0 for value in self.comparison_budgets_krw):
            raise ValueError("comparison budgets must be non-negative")
        return self


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1, max_length=128)
    action: Literal[
        "accepted",
        "modified",
        "rejected",
        "needs_recollection",
    ]
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=0)
    after: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_after(self) -> "ReviewRequest":
        if self.action == "modified" and self.after is None:
            raise ValueError("modified review requires after")
        if self.action != "modified" and self.after is not None:
            raise ValueError("after is only valid for modified review")
        return self


class RimmsJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(min_length=1, max_length=64)
    expected_result_contract_version: str = Field(min_length=1, max_length=64)
    external_job_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    survey_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    route_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    lane_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )
    mapping_bundle_uri: str = Field(min_length=1, max_length=2048)
    raw_dataset_uri: str = Field(min_length=1, max_length=2048)
    road_roi_uri: str | None = Field(default=None, min_length=1, max_length=2048)
    config_profile_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$"
    )
    callback_url: str | None = Field(default=None, max_length=2048)

    @field_validator("mapping_bundle_uri", "raw_dataset_uri", "road_roi_uri")
    @classmethod
    def validate_reference_uri(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"s3", "gs", "az", "https"}:
            raise ValueError("URI scheme must be one of s3, gs, az, https")
        if not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("URI must have a host/bucket and no userinfo")
        if parsed.query or parsed.fragment:
            raise ValueError("URI query and fragment are not stored; use credential-free object URIs")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> "RimmsJobRequest":
        if self.contract_version != RIMMS_REQUEST_CONTRACT_VERSION:
            raise ValueError(
                "unsupported contract_version; expected "
                f"{RIMMS_REQUEST_CONTRACT_VERSION}"
            )
        if self.expected_result_contract_version != RIMMS_RESULT_CONTRACT_VERSION:
            raise ValueError(
                "unsupported expected_result_contract_version; expected "
                f"{RIMMS_RESULT_CONTRACT_VERSION}"
            )
        if self.callback_url is not None:
            raise ValueError("callback mode is disabled; omit callback_url and use polling")
        return self
