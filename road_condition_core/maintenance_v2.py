from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


_CATALOG_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DEFECT_TYPES = ("pothole", "rutting", "bump")
_METHOD_BY_TYPE = {
    "pothole": "pothole_patch",
    "rutting": "rut_overlay",
    "bump": "bump_grinding",
}
_SEVERITY_WEIGHT = {"low": 1.0, "medium": 2.0, "high": 3.0}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite_nonnegative(value: Any, field: str, *, allow_null: bool = False) -> float | None:
    if value is None and allow_null:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a non-negative number")
    numeric = float(value)
    if numeric < 0 or numeric == float("inf") or numeric != numeric:
        raise ValueError(f"{field} must be a finite non-negative number")
    return numeric


@dataclass(frozen=True)
class MaintenanceCatalog:
    catalog_id: str
    version: str
    source_document: str | None
    effective_date: str | None
    approval_status: str
    currency: str
    price_basis: str
    goal: str
    methods: dict[str, dict[str, Any]]
    mobilization_krw: float
    mobilization_status: str
    unpriced_components: dict[str, None]
    recommendation_rules: dict[str, Any]
    deterioration: dict[str, Any]
    catalog_sha256: str

    def contract(self) -> dict[str, Any]:
        return {
            "catalog_id": self.catalog_id,
            "catalog_version": self.version,
            "catalog_sha256": self.catalog_sha256,
            "source_document": self.source_document,
            "effective_date": self.effective_date,
            "approval_status": self.approval_status,
            "currency": self.currency,
            "price_basis": self.price_basis,
        }


def _validate_catalog(payload: Mapping[str, Any], expected_id: str) -> None:
    required = {
        "catalog_id",
        "version",
        "source_document",
        "effective_date",
        "approval_status",
        "currency",
        "price_basis",
        "goal",
        "methods",
        "mobilization",
        "unpriced_components",
        "recommendation_rules",
        "deterioration",
    }
    missing = required - set(payload)
    unknown = set(payload) - required
    if missing:
        raise ValueError("maintenance catalog fields missing: " + ", ".join(sorted(missing)))
    if unknown:
        raise ValueError("unknown maintenance catalog fields: " + ", ".join(sorted(unknown)))
    if payload["catalog_id"] != expected_id:
        raise ValueError("catalog_id does not match filename")
    if payload["approval_status"] not in {"experimental", "approved_internal", "retired"}:
        raise ValueError("unsupported maintenance catalog approval_status")
    if payload["approval_status"] == "approved_internal" and (
        not payload["source_document"] or not payload["effective_date"]
    ):
        raise ValueError("approved catalog requires source_document and effective_date")
    for name in ("version", "currency", "price_basis"):
        if not isinstance(payload[name], str) or not payload[name]:
            raise ValueError(f"{name} is required")
    if payload["goal"] != "risk_screening_priority":
        raise ValueError("only risk_screening_priority is supported")
    methods = payload["methods"]
    if not isinstance(methods, Mapping) or set(methods) != set(_METHOD_BY_TYPE.values()):
        raise ValueError("catalog must define exactly the supported maintenance methods")
    for defect_type, method_name in _METHOD_BY_TYPE.items():
        method = methods[method_name]
        if not isinstance(method, Mapping) or method.get("applies_to") != defect_type:
            raise ValueError(f"invalid method contract: {method_name}")
        if method.get("quantity_unit") != "m2":
            raise ValueError(f"{method_name}.quantity_unit must be m2")
        _finite_nonnegative(method.get("minimum_quantity"), f"{method_name}.minimum_quantity")
        _finite_nonnegative(method.get("area_krw_per_unit"), f"{method_name}.area_krw_per_unit")
        _finite_nonnegative(
            method.get("secondary_volume_krw_per_m3"),
            f"{method_name}.secondary_volume_krw_per_m3",
            allow_null=True,
        )
        expected_rule = "max_area_or_volume" if defect_type == "pothole" else "area"
        if method.get("pricing_rule") != expected_rule:
            raise ValueError(f"unsupported pricing rule for {method_name}")
    mobilization = payload["mobilization"]
    if not isinstance(mobilization, Mapping):
        raise ValueError("mobilization must be an object")
    _finite_nonnegative(mobilization.get("krw_per_scenario"), "mobilization.krw_per_scenario")
    unpriced = payload["unpriced_components"]
    expected_unpriced = {"traffic_control", "equipment_relocation", "waste_disposal"}
    if not isinstance(unpriced, Mapping) or set(unpriced) != expected_unpriced:
        raise ValueError("unpriced_components must list traffic control, relocation, and waste")
    if any(value is not None for value in unpriced.values()):
        raise ValueError("unprovided cost components must be null, never zero")
    deterioration = payload["deterioration"]
    if not isinstance(deterioration, Mapping) or deterioration.get("annual_rate") is not None:
        raise ValueError("annual deterioration rate requires repeated survey evidence")
    if deterioration.get("status") != "N/A_no_repeated_survey":
        raise ValueError("missing deterioration rate must be marked N/A")


def load_maintenance_catalog(root: str | Path, catalog_id: str) -> MaintenanceCatalog:
    if not _CATALOG_ID.fullmatch(catalog_id):
        raise ValueError("invalid maintenance catalog id")
    directory = Path(root).expanduser().resolve()
    path = (directory / f"{catalog_id}.yaml").resolve()
    try:
        path.relative_to(directory)
    except ValueError as exc:
        raise ValueError("maintenance catalog escapes catalog root") from exc
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("maintenance catalog must be a YAML object")
    _validate_catalog(payload, catalog_id)
    return MaintenanceCatalog(
        catalog_id=catalog_id,
        version=payload["version"],
        source_document=payload["source_document"],
        effective_date=payload["effective_date"],
        approval_status=payload["approval_status"],
        currency=payload["currency"],
        price_basis=payload["price_basis"],
        goal=payload["goal"],
        methods=copy.deepcopy(payload["methods"]),
        mobilization_krw=float(payload["mobilization"]["krw_per_scenario"]),
        mobilization_status=str(payload["mobilization"]["status"]),
        unpriced_components=copy.deepcopy(payload["unpriced_components"]),
        recommendation_rules=copy.deepcopy(payload["recommendation_rules"]),
        deterioration=copy.deepcopy(payload["deterioration"]),
        catalog_sha256=_canonical_hash(payload),
    )


def _candidate(defect: Mapping[str, Any], catalog: MaintenanceCatalog) -> dict[str, Any] | None:
    defect_type = str(defect.get("defect_type", ""))
    method_name = _METHOD_BY_TYPE.get(defect_type)
    if method_name is None:
        return None
    method = catalog.methods[method_name]
    metrics = defect.get("metrics") if isinstance(defect.get("metrics"), Mapping) else {}
    measured_area = max(0.0, float(metrics.get("area_m2", 0.0)))
    billable_area = max(measured_area, float(method["minimum_quantity"]))
    area_cost = billable_area * float(method["area_krw_per_unit"])
    measured_volume = max(0.0, float(metrics.get("volume_m3", 0.0)))
    volume_rate = method.get("secondary_volume_krw_per_m3")
    volume_cost = measured_volume * float(volume_rate) if volume_rate is not None else None
    priced_cost = max(area_cost, volume_cost or 0.0) if volume_cost is not None else area_cost
    severity = str(defect.get("severity", "low")).lower()
    severity_weight = _SEVERITY_WEIGHT.get(severity, 1.0)
    depth = max(
        0.0,
        float(metrics.get("max_depth_m", metrics.get("max_height_m", 0.0))),
    )
    priority = severity_weight * 1000.0 + min(depth, 1.0) * 100.0 + min(measured_area, 10.0)
    return {
        "defect_id": str(defect.get("defect_id", "")),
        "defect_type": defect_type,
        "severity": severity,
        "recommended_method": method_name,
        "method_label": method["label"],
        "measured_area_m2": measured_area,
        "billable_area_m2": billable_area,
        "minimum_quantity_m2": float(method["minimum_quantity"]),
        "minimum_quantity_status": method["minimum_quantity_status"],
        "measured_volume_m3": measured_volume if defect_type == "pothole" else None,
        "priced_cost_krw": round(priced_cost),
        "full_cost_krw": None,
        "priority_proxy": round(priority, 6),
        "priority_status": "experimental_risk_screening_proxy",
    }


def _screen_budget(
    candidates: Sequence[Mapping[str, Any]],
    catalog: MaintenanceCatalog,
    budget_krw: float | None,
) -> dict[str, Any]:
    ordered = sorted(
        candidates,
        key=lambda item: (-float(item["priority_proxy"]), str(item["defect_id"])),
    )
    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    direct = 0.0
    limit = None if budget_krw is None else _finite_nonnegative(budget_krw, "budget_krw")
    for item in ordered:
        candidate_cost = float(item["priced_cost_krw"])
        projected = direct + candidate_cost + catalog.mobilization_krw
        if limit is None or projected <= limit:
            selected.append(dict(item))
            direct += candidate_cost
        else:
            deferred.append(dict(item))
    mobilization = catalog.mobilization_krw if selected else 0.0
    priced_total = round(direct + mobilization)
    return {
        "budget_krw": None if limit is None else round(limit),
        "goal": catalog.goal,
        "method": "deterministic_greedy_risk_screening_not_optimization",
        "selected_count": len(selected),
        "deferred_count": len(deferred),
        "selected_defect_ids": [item["defect_id"] for item in selected],
        "deferred_defect_ids": [item["defect_id"] for item in deferred],
        "priced_direct_cost_krw": round(direct),
        "priced_mobilization_krw": round(mobilization),
        "priced_total_krw": priced_total,
        "full_total_krw": None,
        "unpriced_components": sorted(catalog.unpriced_components),
        "cost_completeness": "partial_known_components_only",
    }


def calculate_maintenance_scenario_v2(
    summary: Mapping[str, Any],
    defects: Sequence[Mapping[str, Any]],
    *,
    catalog: MaintenanceCatalog,
    include_types: set[str] | None = None,
    budget_krw: float | None = None,
    comparison_budgets_krw: Sequence[float] = (),
) -> dict[str, Any]:
    selected_types = set(_DEFECT_TYPES) if include_types is None else include_types
    unknown = selected_types - set(_DEFECT_TYPES)
    if unknown:
        raise ValueError("unsupported defect types: " + ", ".join(sorted(unknown)))
    candidates = [
        candidate
        for defect in defects
        if str(defect.get("defect_type")) in selected_types
        for candidate in [_candidate(defect, catalog)]
        if candidate is not None
    ]
    primary = _screen_budget(candidates, catalog, budget_krw)
    comparisons = [
        _screen_budget(candidates, catalog, float(value))
        for value in comparison_budgets_krw
    ]
    current_score = float((summary.get("scores") or {}).get("geometry_score", 0.0))
    treatment_fraction = (
        primary["selected_count"] / len(candidates) if candidates else 0.0
    )
    planning_score = min(
        100.0,
        current_score + max(0.0, 100.0 - current_score) * 0.90 * treatment_fraction,
    )
    return {
        "format_version": 2,
        "catalog": catalog.contract(),
        "selected_defect_types": sorted(selected_types),
        "recommendations": candidates,
        "budget_screening": primary,
        "scenario_comparison": comparisons,
        "score_projection": {
            "current_internal_geometry_score": current_score,
            "post_treatment_internal_score_planning_estimate": planning_score,
            "status": "uncalibrated_planning_estimate_not_prediction",
            "model": "selected_candidate_fraction_heuristic",
            "note": "This is not an observed outcome or a pavement-standard forecast.",
        },
        "deterioration": {
            "annual_rate": None,
            "projected_score": None,
            "status": catalog.deterioration["status"],
            "note": "Repeated, spatially aligned surveys were not provided.",
        },
        "limitations": [
            "Unit prices and minimum work quantities are experimental planning examples.",
            "Traffic control, equipment relocation, and waste disposal costs are N/A.",
            "Budget selection is deterministic risk screening, not mathematical optimization.",
        ],
    }
