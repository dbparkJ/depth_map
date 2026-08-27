from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from dataclasses import replace

import pytest

from road_condition_core.pipeline import analyze_points, write_analysis_products
from road_condition_core.config import AnalysisConfig
from road_condition_core.report_v2 import generate_report_bundle, render_report_pdf
from road_condition_core.synthetic import generate_synthetic_scene


def _result(tmp_path):
    scene = generate_synthetic_scene(
        "mixed",
        length_m=24.0,
        resolution_m=0.20,
        observations_per_cell=4,
        seed=47,
    )
    base = AnalysisConfig()
    config = replace(
        base,
        surface=replace(
            base.surface,
            grid_size_m=0.20,
            reference_min_cells=40,
        ),
    )
    products = analyze_points(
        scene.points_enu_m,
        scene.colors_rgb,
        scene.trajectory_enu_m,
        source={"type": "synthetic", "profile": "report-v2"},
        source_origin=scene.source_origin,
        config=config,
    )
    write_analysis_products(tmp_path, products)
    return products


def test_report_v2_matches_json_and_regenerates_deterministically(tmp_path) -> None:
    products = _result(tmp_path)
    report = tmp_path / "report"
    manifest = json.loads((report / "report_manifest.json").read_text(encoding="utf-8"))
    html = (report / "report.html").read_text(encoding="utf-8")
    assert manifest["format_version"] == 2
    assert manifest["defect_count"] == len(products.defects)
    assert manifest["dataset_id"] is None
    assert manifest["mapping_commit_sha"] is None
    assert "공식 PCI/IRI" in html
    assert "mapping_commit=N/A" in html
    score_match = re.search(
        r'data-json-path="scores\.geometry_score">([^<]+)', html
    )
    assert score_match
    assert float(score_match.group(1).replace(",", "")) == pytest.approx(
        products.summary["scores"]["geometry_score"], abs=0.051
    )

    with (report / "summary.csv").open(encoding="utf-8-sig", newline="") as stream:
        summary_rows = {row["metric"]: row for row in csv.DictReader(stream)}
    assert float(summary_rows["internal_geometry_score"]["value"]) == pytest.approx(
        products.summary["scores"]["geometry_score"]
    )
    assert summary_rows["dataset_id"]["value"] == "N/A"
    with (report / "defects.csv").open(encoding="utf-8-sig", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == len(products.defects)

    assert (report / "figures" / "residual_overview.png").is_file()
    for evidence in manifest["evidence"]:
        directory = report / "evidence" / evidence["evidence_directory"]
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        assert "rgb_original.jpg" in metadata["missing"]
        assert "rgb_overlay.jpg" in metadata["missing"]
        assert (directory / "residual_top.png").is_file()
        assert (directory / "longitudinal_profile.svg").is_file()
        assert (directory / "transverse_profile.svg").is_file()

    first_html = (report / "report.html").read_bytes()
    first_manifest = (report / "report_manifest.json").read_bytes()
    generate_report_bundle(tmp_path, report)
    assert (report / "report.html").read_bytes() == first_html
    assert (report / "report_manifest.json").read_bytes() == first_manifest


def test_report_v2_missing_surface_evidence_is_nonfatal(tmp_path) -> None:
    result = tmp_path / "minimal"
    result.mkdir()
    (result / "summary.json").write_text(
        json.dumps(
            {
                "algorithm_version": "fixture",
                "parameters": {},
                "source": {},
                "scores": {"geometry_score": None, "grade": None},
                "results": {},
                "coverage": {},
                "quality": {},
                "limitations": [],
            }
        ),
        encoding="utf-8",
    )
    (result / "defects.json").write_text(
        json.dumps(
            [
                {
                    "defect_id": "manual-review",
                    "defect_type": "pothole",
                    "confidence": 0.2,
                    "chainage_m": 1.0,
                    "lateral_offset_m": 0.0,
                    "metrics": {"max_depth_m": 0.04},
                    "quality_flags": ["manual_review_required"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (result / "segments.json").write_text("[]", encoding="utf-8")
    manifest = generate_report_bundle(result)
    report = result / "report"
    assert manifest["missing_global_evidence"] == ["surface_preview.json"]
    assert manifest["low_confidence_defect_count"] == 1
    assert (report / "report.html").is_file()
    metadata = json.loads(
        (report / "evidence" / "manual-review" / "metadata.json").read_text(
            encoding="utf-8"
        )
    )
    assert "residual_top.png" in metadata["missing"]
    assert "N/A" in (report / "report.html").read_text(encoding="utf-8")


def test_report_v2_confines_untrusted_defect_evidence_id(tmp_path) -> None:
    result = tmp_path / "malicious-id"
    result.mkdir()
    (result / "summary.json").write_text(
        json.dumps(
            {
                "algorithm_version": "fixture",
                "parameters": {},
                "source": {},
                "scores": {},
                "results": {},
                "coverage": {},
                "quality": {},
                "limitations": [],
            }
        ),
        encoding="utf-8",
    )
    (result / "defects.json").write_text(
        json.dumps([{"defect_id": "../escape", "confidence": None}]),
        encoding="utf-8",
    )
    (result / "segments.json").write_text("[]", encoding="utf-8")

    manifest = generate_report_bundle(result)

    evidence_dir = manifest["evidence"][0]["evidence_directory"]
    assert evidence_dir.startswith("defect-")
    assert (result / "report" / "evidence" / evidence_dir / "metadata.json").is_file()
    assert not (result / "report" / "escape").exists()


def test_optional_pdf_contains_json_score(tmp_path) -> None:
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    pdftotext = shutil.which("pdftotext")
    if chrome is None or pdftotext is None:
        pytest.skip("Chrome and pdftotext are required for PDF consistency smoke")
    products = _result(tmp_path)
    pdf = render_report_pdf(tmp_path / "report", chrome)
    assert pdf.stat().st_size > 10_000
    extracted = subprocess.run(
        [pdftotext, str(pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    expected = f"{products.summary['scores']['geometry_score']:.1f}"
    assert expected in extracted
    manifest = json.loads(
        (tmp_path / "report" / "report_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["outputs"]["pdf"] == "report.pdf"
