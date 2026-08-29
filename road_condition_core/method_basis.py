from __future__ import annotations

from copy import deepcopy
from typing import Any


_METHOD_BASIS: dict[str, Any] = {
    "profile_id": "road-geometry-evidence-v1",
    "profile_version": "1.0.0",
    "evidence_status": "research_supported_workflow_not_sensor_validated",
    "standard_naming_allowed": False,
    "threshold_policy": {
        "status": "experimental_until_surveyed_holdout",
        "default_change_requires": [
            "flat-road noise holdout",
            "surveyed pothole depth and area",
            "surveyed left and right rut depth",
            "cross-chunk regression",
        ],
        "note": (
            "Published definitions and severity bands are reference evidence, not "
            "automatic calibration for this RGB-D/GNSS sensor."
        ),
    },
    "workflow_evidence": [
        {
            "rule_id": "surface-isolation-before-distress",
            "implementation_state": "applied",
            "implementation": (
                "trajectory corridor, independent-view filter, robust surface fit, "
                "and residual plausibility guard"
            ),
            "source_ids": ["de-blasiis-2020", "kim-kang-choi-2017"],
        },
        {
            "rule_id": "reference-surface-height-residual",
            "implementation_state": "applied",
            "implementation": "robust local quadratic reference surface and signed residual",
            "source_ids": ["de-blasiis-2020"],
        },
        {
            "rule_id": "separate-negative-and-positive-displacement",
            "implementation_state": "applied",
            "implementation": "pothole and bump candidates are segmented separately",
            "source_ids": ["de-blasiis-2020"],
        },
        {
            "rule_id": "report-pothole-count-area-and-maximum-depth",
            "implementation_state": "applied",
            "implementation": "count, area, maximum/p95/mean depth, and volume",
            "source_ids": ["fhwa-hrt-13-092", "kict-pothole-free-2019"],
        },
        {
            "rule_id": "rut-depth-from-transverse-wheel-path-profile",
            "implementation_state": "applied_experimental",
            "implementation": "left/right wheel-band depth series in local road coordinates",
            "source_ids": ["fhwa-hrt-13-092", "el-issaoui-2021"],
        },
        {
            "rule_id": "visual-confirmation-with-collected-evidence",
            "implementation_state": "applied_for_review_not_classification",
            "implementation": (
                "actual RGB point evidence and geometry mask; no RGB crack model is claimed"
            ),
            "source_ids": ["kim-kang-choi-2017", "fhwa-hrt-13-092"],
        },
    ],
    "sources": [
        {
            "source_id": "fhwa-hrt-13-092",
            "region": "international",
            "source_type": "official_distress_manual",
            "title": (
                "Distress Identification Manual for the Long-Term Pavement "
                "Performance Program, Fifth Revised Edition"
            ),
            "year": 2014,
            "url": (
                "https://www.fhwa.dot.gov/publications/research/infrastructure/"
                "pavements/ltpp/13092/001.cfm"
            ),
            "use": (
                "common definitions; pothole count/area/maximum-depth reporting; "
                "rut depth as a transverse-profile measurement"
            ),
            "limitation": "The manual states that it is not a standard or regulation.",
        },
        {
            "source_id": "de-blasiis-2020",
            "region": "international",
            "source_type": "peer_reviewed_article",
            "title": "Mobile Laser Scanning Data for the Evaluation of Pavement Surface Distress",
            "year": 2020,
            "doi": "10.3390/rs12060942",
            "url": "https://doi.org/10.3390/rs12060942",
            "use": (
                "road-surface extraction, reference-surface residual, separate "
                "positive/negative segmentation, and geometric quantification"
            ),
            "limitation": (
                "The authors warn that processing parameters depend on data and "
                "boundary conditions; their values are not copied into this project."
            ),
        },
        {
            "source_id": "el-issaoui-2021",
            "region": "international",
            "source_type": "peer_reviewed_article",
            "title": "Feasibility of Mobile Laser Scanning towards Operational Accurate Road Rut Depth Measurements",
            "year": 2021,
            "doi": "10.3390/s21041180",
            "url": "https://doi.org/10.3390/s21041180",
            "use": "mobile-laser rut-depth feasibility and surveyed reference comparison",
            "limitation": "Its sensor and reference setup differ from this RGB-D system.",
        },
        {
            "source_id": "kim-kang-choi-2017",
            "region": "domestic",
            "source_type": "peer_reviewed_article",
            "title": "2D LiDAR based 3D Pothole Detection System",
            "year": 2017,
            "doi": "10.9728/dcs.2017.18.5.989",
            "url": "https://doi.org/10.9728/dcs.2017.18.5.989",
            "use": "noise filtering, line/profile change, depth and width measurement, multi-sensor review",
            "limitation": "The acquisition geometry differs from this dense RGB-D point cloud.",
        },
        {
            "source_id": "kict-pothole-free-2019",
            "region": "domestic",
            "source_type": "government_research_report",
            "title": "포트홀 Free 도로포장시스템 개발",
            "year": 2019,
            "url": (
                "https://www.codil.or.kr/filebank/original/RK/OTKCRK190381/"
                "OTKCRK190381.pdf"
            ),
            "use": "domestic pothole depth bands and combined image/point-cloud evidence fields",
            "limitation": "The depth bands remain reference labels until field truth is supplied.",
        },
    ],
    "known_gaps": [
        "No surveyed ground truth exists for the current 26 chunks.",
        "RGB crack, patch, raveling, and bleeding classification is not implemented.",
        "Rutting severity labels are project-specific; measured depth is the primary evidence.",
        "The internal geometry score is not PCI and the roughness proxy is not IRI.",
    ],
}


def method_basis_contract() -> dict[str, Any]:
    """Return a mutation-safe method-evidence contract for analysis artifacts."""

    return deepcopy(_METHOD_BASIS)
