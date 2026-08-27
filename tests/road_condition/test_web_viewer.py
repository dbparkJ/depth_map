from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_web_viewer_has_progressive_route_and_accessibility_contract() -> None:
    html = (ROOT / "services/road_condition_web/index.html").read_text(encoding="utf-8")
    script = (ROOT / "services/road_condition_web/app.js").read_text(encoding="utf-8")
    dockerfile = (ROOT / "services/road_condition_web/Dockerfile").read_text(
        encoding="utf-8"
    )
    for element_id in (
        "jobSelect",
        "routePaths",
        "tileSelect",
        "viewMode",
        "mapAdapter",
        "surfaceCanvas",
        "perspectiveCanvas",
        "mapCanvas",
        "minimumConfidence",
        "compareJob",
        "renderRecovery",
        "reviewControls",
        "reviewActor",
        "reviewAction",
        "reviewReason",
        "submitReview",
    ):
        assert f'id="{element_id}"' in html
    assert 'tabindex="0"' in html
    assert "viewer_core.js" in html
    assert "route-datasets/manifest" in script
    assert "route-datasets/tile" in script
    assert "webglcontextlost" in script
    assert "전체 PLY 미로딩" in script
    assert "/reviews/" in script
    assert "expected_version" in script
    assert "raw prediction 보존" in script
    assert "COPY services/road_condition_web/viewer_core.js" in dockerfile


def test_map_adapters_require_explicit_runtime_configuration() -> None:
    viewer = (ROOT / "services/road_condition_web/viewer_core.js").read_text(
        encoding="utf-8"
    )
    assert "local_enu" in viewer
    assert "vworld" in viewer
    assert "cesium" in viewer
    assert "WGS84" in viewer
    assert "fallback" in viewer
