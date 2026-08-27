(function attachViewerCore(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RoadConditionViewerCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function buildViewerCore() {
  "use strict";

  function parseRoutePaths(value) {
    const paths = String(value || "").split(/[\n,]+/).map((item) => item.trim()).filter(Boolean);
    const unique = [...new Set(paths)];
    for (const path of unique) {
      if (path.startsWith("/") || path.split("/").includes("..")) {
        throw new Error("route 결과 경로는 /workspace 아래 상대 경로여야 합니다.");
      }
    }
    return unique;
  }

  function buildTileSequence(manifests) {
    const sequence = [];
    for (const manifest of manifests || []) {
      for (const tile of manifest.tiles || []) {
        sequence.push({
          datasetId: manifest.dataset_id,
          path: manifest.workspace_relative_path,
          tileId: tile.tile_id,
          state: tile.state,
          coreStartM: Number(tile.core_start_m),
          coreEndM: Number(tile.core_end_m)
        });
      }
    }
    return sequence;
  }

  function defectVisible(defect, options) {
    const resolved = options || {};
    const type = defect.defect_type;
    if (resolved.types && resolved.types[type] === false) return false;
    const isAdvanced = !["pothole", "rutting", "bump"].includes(type);
    if (isAdvanced && resolved.showAdvanced === false) return false;
    if (Number(defect.confidence || 0) < Number(resolved.minimumConfidence || 0)) return false;
    const lowConfidence = (defect.quality_flags || []).some((flag) =>
      ["manual_review_required", "low_point_support", "high_position_spread"].includes(flag)
    );
    if (lowConfidence && resolved.showLowConfidence === false) return false;
    return true;
  }

  function mapAdapterStatus(name) {
    const adapters = {
      local_enu: { ready: true, label: "Local ENU", message: "오프라인 local ENU evidence renderer" },
      vworld: { ready: false, label: "VWorld adapter", message: "VWorld API key와 WGS84 변환 설정 후 basemap을 연결합니다. 현재 local ENU로 fallback합니다." },
      cesium: { ready: false, label: "Cesium adapter", message: "Cesium runtime/token과 WGS84 변환 설정 후 3D globe를 연결합니다. 현재 local ENU로 fallback합니다." }
    };
    return adapters[name] || adapters.local_enu;
  }

  function perspectivePoint(sRatio, tRatio, residualMm, width, height, exaggeration) {
    const x = width * (0.10 + 0.72 * sRatio + 0.14 * tRatio);
    const y = height * (0.84 - 0.54 * sRatio + 0.13 * tRatio)
      - Number(residualMm || 0) * Number(exaggeration || 1) * 0.65;
    return [x, y];
  }

  return { parseRoutePaths, buildTileSequence, defectVisible, mapAdapterStatus, perspectivePoint };
});
