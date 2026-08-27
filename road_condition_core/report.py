from __future__ import annotations

import html
from typing import Any


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{number:,.{digits}f}{suffix}"


def render_html_report(summary: dict[str, Any], defects: list[dict[str, Any]], segments: list[dict[str, Any]]) -> str:
    source = summary.get("source") or {}
    coverage = summary.get("coverage") or {}
    results = summary.get("results") or {}
    scores = summary.get("scores") or {}
    quality = summary.get("quality") or {}

    defect_rows = []
    for defect in defects:
        metrics = defect.get("metrics") or {}
        if defect.get("defect_type") == "pothole":
            primary = f"depth {_fmt(metrics.get('max_depth_m'), 3, ' m')}, area {_fmt(metrics.get('area_m2'), 2, ' m²')}"
        elif defect.get("defect_type") == "rutting":
            primary = f"depth {_fmt(metrics.get('max_depth_m'), 3, ' m')}, length {_fmt(metrics.get('length_m'), 1, ' m')}"
        else:
            primary = f"height {_fmt(metrics.get('max_height_m'), 3, ' m')}, area {_fmt(metrics.get('area_m2'), 2, ' m²')}"
        defect_rows.append(
            "<tr>"
            f"<td>{html.escape(str(defect.get('defect_id', '')))}</td>"
            f"<td>{html.escape(str(defect.get('defect_type', '')))}</td>"
            f"<td>{html.escape(str(defect.get('severity', '')))}</td>"
            f"<td>{_fmt(defect.get('chainage_m'), 1, ' m')}</td>"
            f"<td>{html.escape(primary)}</td>"
            f"<td>{_fmt(100 * float(defect.get('confidence', 0)), 0, '%')}</td>"
            "</tr>"
        )

    segment_rows = []
    for segment in segments:
        segment_rows.append(
            "<tr>"
            f"<td>{html.escape(str(segment.get('segment_id', '')))}</td>"
            f"<td>{_fmt(segment.get('chainage_start_m'), 1)}–{_fmt(segment.get('chainage_end_m'), 1)} m</td>"
            f"<td>{_fmt(100 * float(segment.get('valid_coverage_ratio', 0)), 1, '%')}</td>"
            f"<td>{int(segment.get('pothole_count', 0))}</td>"
            f"<td>{_fmt(1000 * float(segment.get('max_pothole_depth_m', 0)), 0, ' mm')}</td>"
            f"<td>{_fmt(1000 * max(float(segment.get('max_left_rut_depth_m', 0)), float(segment.get('max_right_rut_depth_m', 0))), 0, ' mm')}</td>"
            f"<td>{_fmt(segment.get('geometry_score'), 1)}</td>"
            f"<td>{html.escape(str(segment.get('grade', '-')))}</td>"
            "</tr>"
        )

    limitations = "".join(
        f"<li>{html.escape(str(item))}</li>" for item in summary.get("limitations", [])
    )
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>도로 상태 자동 분석 리포트</title>
<style>
body{{font-family:Arial,'Noto Sans KR',sans-serif;margin:0;background:#f4f6f8;color:#1d2733}}
main{{max-width:1180px;margin:0 auto;padding:32px}}
header{{background:#102a43;color:white;padding:28px;border-radius:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:18px 0}}
.card{{background:white;border:1px solid #d9e2ec;border-radius:12px;padding:16px}}
.card strong{{display:block;font-size:24px;margin-top:8px}}
table{{width:100%;border-collapse:collapse;background:white}}
th,td{{border-bottom:1px solid #d9e2ec;padding:10px;text-align:left;font-size:13px}}
th{{background:#eaf0f5}}
section{{margin-top:28px}}
.badge{{display:inline-block;background:#d9e2ec;border-radius:999px;padding:4px 9px;margin-right:6px}}
.notice{{background:#fff7d6;border-left:5px solid #f0b429;padding:14px}}
small{{color:#627d98}}
</style>
</head>
<body><main>
<header>
  <small>ROAD CONDITION GEOMETRY MVP</small>
  <h1>도로 상태 자동 분석 리포트</h1>
  <p>소스: {html.escape(str(source.get('type', '-')))} / 알고리즘: {html.escape(str(summary.get('algorithm_version', '-')))}</p>
</header>
<div class="grid">
  <div class="card">형상 점수<strong>{_fmt(scores.get('geometry_score'), 1)}</strong><span class="badge">{html.escape(str(scores.get('grade', '-')))}</span></div>
  <div class="card">포트홀<strong>{int(results.get('pothole_count', 0))}</strong><small>총 {_fmt(results.get('pothole_area_m2'), 2, ' m²')}</small></div>
  <div class="card">최대 포트홀 깊이<strong>{_fmt(1000 * float(results.get('max_pothole_depth_m', 0)), 0, ' mm')}</strong></div>
  <div class="card">최대 러팅 깊이<strong>{_fmt(1000 * float(results.get('max_rut_depth_m', 0)), 0, ' mm')}</strong></div>
  <div class="card">유효 표면 커버리지<strong>{_fmt(100 * float(coverage.get('valid_coverage_ratio', 0)), 1, '%')}</strong></div>
  <div class="card">분석 점 수<strong>{int(quality.get('analyzed_point_count', 0)):,}</strong></div>
</div>
<section><h2>구간별 결과</h2><table>
<thead><tr><th>구간</th><th>체인리지</th><th>커버리지</th><th>포트홀</th><th>최대 깊이</th><th>최대 러팅</th><th>점수</th><th>등급</th></tr></thead>
<tbody>{''.join(segment_rows)}</tbody></table></section>
<section><h2>결함 상세</h2><table>
<thead><tr><th>ID</th><th>종류</th><th>심각도</th><th>체인리지</th><th>주요 측정값</th><th>신뢰도</th></tr></thead>
<tbody>{''.join(defect_rows) if defect_rows else '<tr><td colspan="6">검출된 결함이 없습니다.</td></tr>'}</tbody></table></section>
<section class="notice"><h2>해석 제한</h2><ul>{limitations}</ul></section>
</main></body></html>"""
