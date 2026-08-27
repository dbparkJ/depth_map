from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Sequence

import numpy as np
from PIL import Image, ImageDraw


REPORT_FORMAT_VERSION = 2


def _json(path: Path, *, required: bool = True) -> Any:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
    )


def _config_hash(summary: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        summary.get("parameters", {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _source_hash(input_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in ("summary.json", "segments.json", "defects.json"):
        path = input_dir / name
        digest.update(name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _na(value: Any) -> Any:
    return "N/A" if value is None or value == "" else value


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))
    if not math.isfinite(number):
        return "N/A"
    return f"{number:,.{digits}f}{suffix}"


def _percent(value: Any, digits: int = 1) -> str:
    if value is None:
        return "N/A"
    try:
        return _fmt(100.0 * float(value), digits, "%")
    except (TypeError, ValueError):
        return "N/A"


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)
    temporary.replace(path)


def _summary_rows(summary: Mapping[str, Any], config_hash: str) -> list[list[Any]]:
    source = summary.get("source") or {}
    results = summary.get("results") or {}
    scores = summary.get("scores") or {}
    coverage = summary.get("coverage") or {}
    quality = summary.get("quality") or {}
    profile = summary.get("scoring_profile") or {}
    return [
        ["algorithm_version", _na(summary.get("algorithm_version")), ""],
        ["config_sha256", config_hash, ""],
        ["dataset_id", _na(source.get("dataset_id")), ""],
        ["mapping_commit_sha", _na(source.get("mapping_commit_sha")), ""],
        ["scoring_profile_id", _na(profile.get("profile_id")), ""],
        ["scoring_profile_version", _na(profile.get("profile_version")), ""],
        ["scoring_profile_sha256", _na(profile.get("profile_sha256")), ""],
        ["scoring_profile_approval", _na(profile.get("approval_status")), ""],
        ["internal_geometry_score", _na(scores.get("geometry_score")), "point"],
        ["grade", _na(scores.get("grade")), ""],
        ["pothole_count", _na(results.get("pothole_count")), "count"],
        ["pothole_area_m2", _na(results.get("pothole_area_m2")), "m2"],
        ["max_pothole_depth_m", _na(results.get("max_pothole_depth_m")), "m"],
        ["max_rut_depth_m", _na(results.get("max_rut_depth_m")), "m"],
        ["roughness_proxy_m", _na(results.get("roughness_proxy_m")), "m"],
        ["valid_coverage_ratio", _na(coverage.get("valid_coverage_ratio")), "ratio"],
        ["analyzed_point_count", _na(quality.get("analyzed_point_count")), "count"],
    ]


def _defect_rows(defects: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
    return [
        [
            item.get("defect_id"),
            item.get("defect_type"),
            item.get("lane_id") or item.get("road_zone") or "unknown",
            item.get("severity"),
            item.get("confidence"),
            item.get("chainage_m"),
            item.get("lateral_offset_m"),
            json.dumps(item.get("metrics") or {}, sort_keys=True, ensure_ascii=False),
            ";".join(item.get("quality_flags") or []),
            item.get("source"),
        ]
        for item in defects
    ]


def _segment_rows(segments: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
    columns = [
        "segment_id",
        "lane_id",
        "road_zone",
        "chainage_start_m",
        "chainage_end_m",
        "valid_coverage_ratio",
        "pothole_count",
        "pothole_area_m2",
        "pothole_volume_m3",
        "max_pothole_depth_m",
        "max_left_rut_depth_m",
        "max_right_rut_depth_m",
        "bump_count",
        "roughness_proxy_m",
        "geometry_score",
        "grade",
    ]
    return [[item.get(column) for column in columns] for item in segments]


def _surface_image(
    preview: Mapping[str, Any],
    defects: Sequence[Mapping[str, Any]],
) -> tuple[Image.Image, dict[str, Any]]:
    residual = np.asarray(
        [
            [np.nan if value is None else float(value) for value in row]
            for row in preview["residual_mm"]
        ],
        dtype=np.float64,
    )
    s_values = np.asarray(preview["s_values_m"], dtype=np.float64)
    t_values = np.asarray(preview["t_values_m"], dtype=np.float64)
    width, height = 1200, 480
    normalized = np.clip(residual / 120.0, -1.0, 1.0)
    colors = np.empty((*residual.shape, 3), dtype=np.uint8)
    invalid = ~np.isfinite(normalized)
    depression = np.isfinite(normalized) & (normalized < 0)
    rise = np.isfinite(normalized) & ~depression
    amount = -normalized[depression]
    colors[depression, 0] = np.clip(92 - 52 * amount, 0, 255).astype(np.uint8)
    colors[depression, 1] = np.clip(125 - 50 * amount, 0, 255).astype(np.uint8)
    colors[depression, 2] = np.clip(150 + 90 * amount, 0, 255).astype(np.uint8)
    amount = normalized[rise]
    colors[rise, 0] = np.clip(120 + 120 * amount, 0, 255).astype(np.uint8)
    colors[rise, 1] = np.clip(130 - 55 * amount, 0, 255).astype(np.uint8)
    colors[rise, 2] = np.clip(135 - 65 * amount, 0, 255).astype(np.uint8)
    colors[invalid] = (18, 30, 42)
    image = Image.fromarray(
        np.flip(np.swapaxes(colors, 0, 1), axis=0),
        mode="RGB",
    ).resize((width, height), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(image)
    s_min, s_max = float(s_values[0]), float(s_values[-1])
    t_min, t_max = float(t_values[0]), float(t_values[-1])

    def point(st: Sequence[float]) -> tuple[float, float]:
        return (
            (float(st[0]) - s_min) / max(s_max - s_min, 1e-9) * (width - 1),
            (t_max - float(st[1])) / max(t_max - t_min, 1e-9) * (height - 1),
        )

    for defect in defects:
        polygon = defect.get("local_polygon_st_m") or []
        if len(polygon) >= 2:
            ring = [point(value) for value in polygon]
            draw.line([*ring, ring[0]], fill=(255, 225, 92), width=3)
    return image, {
        "width": width,
        "height": height,
        "s_min": s_min,
        "s_max": s_max,
        "t_min": t_min,
        "t_max": t_max,
    }


def _safe_evidence_id(defect_id: Any) -> str:
    value = str(defect_id or "unknown")
    if value and all(character.isalnum() or character in "-_" for character in value):
        return value
    return "defect-" + hashlib.sha256(value.encode()).hexdigest()[:12]


def _low_confidence(defect: Mapping[str, Any]) -> bool:
    try:
        confidence = float(defect.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return confidence < 0.5 or "manual_review_required" in (
        defect.get("quality_flags") or []
    )


def _profile_svg(
    coordinates: np.ndarray,
    values: np.ndarray,
    title: str,
) -> str:
    valid = np.isfinite(values)
    if np.count_nonzero(valid) < 2:
        return """<svg xmlns="http://www.w3.org/2000/svg" width="640" height="220"><text x="20" y="40">N/A — profile evidence unavailable</text></svg>"""
    x_values = coordinates[valid]
    y_values = values[valid]
    maximum = max(40.0, float(np.max(np.abs(y_values))))
    x = 48 + (x_values - x_values[0]) / max(float(x_values[-1] - x_values[0]), 1e-9) * 570
    y = 18 + (maximum - y_values) / (2 * maximum) * 168
    points = " ".join(f"{left:.2f},{top:.2f}" for left, top in zip(x, y, strict=True))
    escaped = html.escape(title)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="220" viewBox="0 0 640 220">
<rect width="640" height="220" fill="#f5f8fa"/><path d="M48 102 H620 M48 18 V186" stroke="#8393a1" fill="none"/>
<polyline points="{points}" fill="none" stroke="#087f8c" stroke-width="2"/>
<text x="48" y="210" font-family="Noto Sans CJK KR, sans-serif" font-size="12">{escaped}</text></svg>"""


def _make_evidence(
    output: Path,
    defects: Sequence[Mapping[str, Any]],
    preview: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    missing_global: list[str] = []
    overview = None
    geometry = None
    residual = None
    s_values = t_values = None
    if preview is not None:
        try:
            overview, geometry = _surface_image(preview, defects)
            (output / "figures").mkdir(parents=True, exist_ok=True)
            overview.save(output / "figures" / "residual_overview.png", format="PNG")
            s_values = np.asarray(preview["s_values_m"], dtype=np.float64)
            t_values = np.asarray(preview["t_values_m"], dtype=np.float64)
            residual = np.asarray(
                [[np.nan if value is None else value for value in row] for row in preview["residual_mm"]],
                dtype=np.float64,
            )
        except Exception as exc:  # noqa: BLE001 - evidence omission is non-fatal
            missing_global.append(f"residual_overview: {type(exc).__name__}: {exc}")
    else:
        missing_global.append("surface_preview.json")
    for defect in defects:
        defect_id = str(defect.get("defect_id") or "unknown")
        safe_id = _safe_evidence_id(defect_id)
        directory = output / "evidence" / safe_id
        directory.mkdir(parents=True, exist_ok=True)
        missing = ["rgb_original.jpg", "rgb_overlay.jpg"]
        produced: list[str] = []
        if overview is not None and geometry is not None and residual is not None:
            s = float(defect.get("chainage_m", 0.0))
            t = float(defect.get("lateral_offset_m", 0.0))
            x = int((s - geometry["s_min"]) / max(geometry["s_max"] - geometry["s_min"], 1e-9) * (geometry["width"] - 1))
            y = int((geometry["t_max"] - t) / max(geometry["t_max"] - geometry["t_min"], 1e-9) * (geometry["height"] - 1))
            left = max(0, x - 180)
            top = max(0, y - 120)
            right = min(geometry["width"], left + 360)
            bottom = min(geometry["height"], top + 240)
            overview.crop((left, top, right, bottom)).save(directory / "residual_top.png", format="PNG")
            produced.append("residual_top.png")
            row = int(np.argmin(np.abs(s_values - s)))
            column = int(np.argmin(np.abs(t_values - t)))
            _atomic_text(directory / "transverse_profile.svg", _profile_svg(t_values, residual[row], f"횡단면 s={s_values[row]:.2f}m · residual mm"))
            _atomic_text(directory / "longitudinal_profile.svg", _profile_svg(s_values, residual[:, column], f"종단면 t={t_values[column]:.2f}m · residual mm"))
            produced.extend(["transverse_profile.svg", "longitudinal_profile.svg"])
        else:
            missing.extend(["residual_top.png", "transverse_profile.svg", "longitudinal_profile.svg"])
        metadata = {
            "format_version": 1,
            "defect_id": defect_id,
            "evidence_directory": safe_id,
            "produced": produced,
            "missing": missing,
            "missing_policy": "N/A; report generation continues",
        }
        _atomic_json(directory / "metadata.json", metadata)
        records.append(metadata)
    return records, missing_global


def _defect_primary(defect: Mapping[str, Any]) -> str:
    metrics = defect.get("metrics") or {}
    defect_type = defect.get("defect_type")
    if defect_type in {"pothole", "rutting"}:
        return f"depth {_fmt(metrics.get('max_depth_m'), 3, ' m')}"
    if defect_type == "bump":
        return f"height {_fmt(metrics.get('max_height_m'), 3, ' m')}"
    if defect_type in {"manhole_step_candidate", "step_anomaly"}:
        return f"step {_fmt(metrics.get('step_height_m'), 3, ' m')}"
    if defect_type == "ponding_screening_proxy":
        return f"screening depth {_fmt(metrics.get('potential_retention_depth_m'), 3, ' m')}"
    return "N/A"


def _render_html(
    summary: Mapping[str, Any],
    defects: Sequence[Mapping[str, Any]],
    segments: Sequence[Mapping[str, Any]],
    *,
    config_hash: str,
    source_hash: str,
    evidence: Sequence[Mapping[str, Any]],
    missing_global: Sequence[str],
) -> str:
    source = summary.get("source") or {}
    scores = summary.get("scores") or {}
    results = summary.get("results") or {}
    coverage = summary.get("coverage") or {}
    profile = summary.get("scoring_profile") or {}
    low_confidence = [item for item in defects if _low_confidence(item)]
    segment_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('segment_id', '')))}</td>"
        f"<td>{_fmt(item.get('chainage_start_m'), 1)}–{_fmt(item.get('chainage_end_m'), 1)}m</td>"
        f"<td>{_percent(item.get('valid_coverage_ratio'), 1)}</td>"
        f"<td>{_fmt(item.get('geometry_score'), 1)}</td>"
        f"<td>{html.escape(str(item.get('grade', 'N/A')))}</td></tr>"
        for item in segments
    )
    defect_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('defect_id', '')))}</td>"
        f"<td>{html.escape(str(item.get('defect_type', '')))}</td>"
        f"<td>{_fmt(item.get('chainage_m'), 2, 'm')}</td>"
        f"<td>{html.escape(_defect_primary(item))}</td>"
        f"<td>{_percent(item.get('confidence'), 0)}</td></tr>"
        for item in defects
    )
    evidence_cards = "".join(
        f"<article><h3>{html.escape(str(item['defect_id']))}</h3>"
        f"<img src=\"evidence/{html.escape(str(item['evidence_directory']))}/residual_top.png\" alt=\"residual evidence\" onerror=\"this.replaceWith(document.createTextNode('N/A — residual evidence missing'))\">"
        f"<p>RGB original/overlay: {'N/A' if 'rgb_original.jpg' in item['missing'] else 'available'}</p>"
        f"<a href=\"evidence/{html.escape(str(item['evidence_directory']))}/metadata.json\">metadata.json</a></article>"
        for item in evidence
    )
    limitations = "".join(f"<li>{html.escape(str(value))}</li>" for value in summary.get("limitations", []))
    missing = "".join(f"<li>{html.escape(str(value))}</li>" for value in missing_global) or "<li>없음</li>"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>도로 상태 내부 evidence 리포트 v2</title><style>
@page{{size:A4;margin:14mm}}*{{box-sizing:border-box}}body{{font-family:'Noto Sans CJK KR','Noto Sans KR',sans-serif;color:#182733;margin:0;background:#eef3f6}}
main{{max-width:1180px;margin:auto;background:white;padding:28px}}header{{background:#102a43;color:white;padding:26px;border-radius:12px}}.warning{{background:#fff3cd;border-left:5px solid #e3a008;padding:13px;margin:16px 0}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.card{{border:1px solid #cbd8e2;border-radius:8px;padding:12px}}.card strong{{display:block;font-size:24px}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid #d8e2e9;text-align:left;font-size:12px}}th{{background:#edf3f6}}section{{margin-top:24px}}
.overview{{width:100%;border:1px solid #cbd8e2}}.evidence{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}.evidence article{{border:1px solid #cbd8e2;padding:10px}}.evidence img{{width:100%;max-height:220px;object-fit:contain}}
.meta{{font-family:monospace;font-size:11px;word-break:break-all}}@media print{{body{{background:white}}main{{padding:0}}a{{color:#182733}}}}</style></head><body><main>
<header><small>INTERNAL ROAD GEOMETRY EVIDENCE · FORMAT V2</small><h1>도로 상태 내부 evidence 리포트</h1><p>공식 PCI/IRI 또는 침수 예측이 아닙니다.</p></header>
<div class="warning"><strong>내부 형상 점수 / roughness proxy</strong><br>공식 검증 전에는 행정·법적 성능 지표로 사용할 수 없습니다.</div>
<section class="grid"><div class="card">내부 형상 점수<strong data-json-path="scores.geometry_score">{_fmt(scores.get('geometry_score'), 1)}</strong></div>
<div class="card">등급<strong>{html.escape(str(_na(scores.get('grade'))))}</strong></div><div class="card">포트홀<strong data-json-path="results.pothole_count">{_fmt(results.get('pothole_count'), 0)}</strong></div>
<div class="card">커버리지<strong data-json-path="coverage.valid_coverage_ratio">{_percent(coverage.get('valid_coverage_ratio'), 1)}</strong></div></section>
<section><h2>추적 정보</h2><div class="meta">algorithm={html.escape(str(_na(summary.get('algorithm_version'))))}<br>config_sha256={config_hash}<br>mapping_commit={html.escape(str(_na(source.get('mapping_commit_sha'))))}<br>dataset_id={html.escape(str(_na(source.get('dataset_id'))))}<br>scoring_profile={html.escape(str(_na(profile.get('profile_id'))))}@{html.escape(str(_na(profile.get('profile_version'))))}<br>scoring_profile_approval={html.escape(str(_na(profile.get('approval_status'))))}<br>scoring_profile_sha256={html.escape(str(_na(profile.get('profile_sha256'))))}<br>source_json_sha256={source_hash}</div></section>
<section><h2>Residual overview</h2><img class="overview" src="figures/residual_overview.png" alt="residual overview" onerror="this.replaceWith(document.createTextNode('N/A — residual overview missing'))"></section>
<section><h2>구간</h2><table><thead><tr><th>ID</th><th>체인리지</th><th>커버리지</th><th>점수</th><th>등급</th></tr></thead><tbody>{segment_rows or '<tr><td colspan="5">N/A</td></tr>'}</tbody></table></section>
<section><h2>결함</h2><table><thead><tr><th>ID</th><th>종류</th><th>체인리지</th><th>metric</th><th>신뢰도</th></tr></thead><tbody>{defect_rows or '<tr><td colspan="5">검출 결함 없음</td></tr>'}</tbody></table></section>
<section><h2>낮은 신뢰도 / 재수집 검토</h2><p>{len(low_confidence)}개</p><ul>{''.join(f'<li>{html.escape(str(item.get("defect_id")))}</li>' for item in low_confidence) or '<li>없음</li>'}</ul></section>
<section><h2>결함별 evidence</h2><div class="evidence">{evidence_cards or '<p>결함 evidence 없음</p>'}</div></section>
<section><h2>누락 evidence</h2><ul>{missing}</ul></section><section><h2>제한</h2><ul>{limitations}</ul></section>
</main></body></html>"""


def generate_report_bundle(
    result_dir: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(result_dir).expanduser().resolve()
    output = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else source / "report"
    )
    summary = _json(source / "summary.json")
    defects = _json(source / "defects.json")
    segments = _json(source / "segments.json")
    preview = _json(source / "surface_preview.json", required=False)
    if not isinstance(summary, dict) or not isinstance(defects, list) or not isinstance(segments, list):
        raise ValueError("report inputs have invalid JSON types")
    output.mkdir(parents=True, exist_ok=True)
    config_hash = _config_hash(summary)
    source_hash = _source_hash(source)
    _write_csv(output / "summary.csv", ["metric", "value", "unit"], _summary_rows(summary, config_hash))
    _write_csv(
        output / "defects.csv",
        ["defect_id", "defect_type", "lane_or_zone", "severity", "confidence", "chainage_m", "lateral_offset_m", "metrics_json", "quality_flags", "source"],
        _defect_rows(defects),
    )
    segment_columns = ["segment_id", "lane_id", "road_zone", "chainage_start_m", "chainage_end_m", "valid_coverage_ratio", "pothole_count", "pothole_area_m2", "pothole_volume_m3", "max_pothole_depth_m", "max_left_rut_depth_m", "max_right_rut_depth_m", "bump_count", "roughness_proxy_m", "geometry_score", "grade"]
    _write_csv(output / "segments.csv", segment_columns, _segment_rows(segments))
    evidence, missing_global = _make_evidence(output, defects, preview)
    report_html = _render_html(
        summary,
        defects,
        segments,
        config_hash=config_hash,
        source_hash=source_hash,
        evidence=evidence,
        missing_global=missing_global,
    )
    _atomic_text(output / "report.html", report_html)
    manifest = {
        "format_version": REPORT_FORMAT_VERSION,
        "report_profile": "internal_korean_geometry_evidence_v2",
        "source_result_contract": "summary.json + segments.json + defects.json",
        "algorithm_version": summary.get("algorithm_version"),
        "config_sha256": config_hash,
        "source_json_sha256": source_hash,
        "dataset_id": (summary.get("source") or {}).get("dataset_id"),
        "mapping_commit_sha": (summary.get("source") or {}).get("mapping_commit_sha"),
        "scoring_profile": summary.get("scoring_profile"),
        "defect_count": len(defects),
        "segment_count": len(segments),
        "low_confidence_defect_count": sum(_low_confidence(item) for item in defects),
        "evidence": evidence,
        "missing_global_evidence": missing_global,
        "outputs": {
            "html": "report.html",
            "summary_csv": "summary.csv",
            "segments_csv": "segments.csv",
            "defects_csv": "defects.csv",
            "residual_overview": (
                "figures/residual_overview.png"
                if (output / "figures" / "residual_overview.png").is_file()
                else None
            ),
            "pdf": None,
        },
        "naming_guards": [
            "internal geometry score, not certified PCI",
            "roughness proxy, not standardized IRI",
            "ponding screening proxy, not flooding prediction",
        ],
    }
    _atomic_json(output / "report_manifest.json", manifest)
    return manifest


def render_report_pdf(
    report_dir: str | Path,
    executable: str = "chromium",
) -> Path:
    output = Path(report_dir).expanduser().resolve()
    html_path = output / "report.html"
    if not html_path.is_file():
        raise FileNotFoundError(html_path)
    resolved_executable = shutil.which(executable)
    if resolved_executable is None:
        raise FileNotFoundError(f"PDF renderer executable not found: {executable}")
    pdf_path = output / "report.pdf"
    with TemporaryDirectory(prefix="road-condition-report-chrome-") as profile:
        subprocess.run(
            [
                resolved_executable,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-crash-reporter",
                "--disable-breakpad",
                "--allow-file-access-from-files",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={pdf_path}",
                html_path.as_uri(),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            env={**os.environ, "HOME": profile},
        )
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError("PDF renderer did not create report.pdf")
    manifest_path = output / "report_manifest.json"
    manifest = _json(manifest_path)
    manifest["outputs"]["pdf"] = "report.pdf"
    _atomic_json(manifest_path, manifest)
    return pdf_path
