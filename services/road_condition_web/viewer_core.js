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

  function mapAdapterStatus(name, runtimeConfig, origin) {
    const config = runtimeConfig || {};
    const hasOrigin = Boolean(
      origin
      && Number.isFinite(Number(origin.longitude_deg))
      && Number.isFinite(Number(origin.latitude_deg))
    );
    const hasVWorldKey = Boolean(String(config.vworldApiKey || "").trim());
    const hasVWorldDomain = Boolean(String(config.vworldDomain || "").trim());
    const vworldReady = hasVWorldKey && hasVWorldDomain && hasOrigin;
    const vworldMissing = [
      !hasVWorldKey ? "API key" : null,
      !hasVWorldDomain ? "domain" : null,
      !hasOrigin ? "ENU origin" : null
    ].filter(Boolean).join(", ");
    const adapters = {
      local_enu: { ready: true, label: "Local ENU", message: "오프라인 local ENU evidence renderer" },
      vworld: {
        ready: vworldReady,
        label: "VWorld",
        message: vworldReady
          ? "VWorld 인증 설정과 ENU→WGS84 변환 준비 완료"
          : `VWorld 준비 안 됨 (${vworldMissing}); local ENU로 fallback합니다.`
      },
      cesium: { ready: false, label: "Cesium adapter", message: "Cesium runtime/token과 WGS84 변환 설정 후 3D globe를 연결합니다. 현재 local ENU로 fallback합니다." }
    };
    return adapters[name] || adapters.local_enu;
  }

  function enuToWgs84(point, origin) {
    if (!origin) throw new Error("ENU origin is required for WGS84 conversion");
    const east = Number(point[0]);
    const north = Number(point[1]);
    const up = Number(point[2] || 0);
    const longitude = Number(origin.longitude_deg);
    const latitude = Number(origin.latitude_deg);
    const height = Number(origin.ellipsoid_height_m || 0);
    if (![east, north, up, longitude, latitude, height].every(Number.isFinite)) {
      throw new Error("ENU point and origin must contain finite numbers");
    }
    const a = 6378137.0;
    const inverseFlattening = 298.257223563;
    const flattening = 1 / inverseFlattening;
    const eccentricitySquared = flattening * (2 - flattening);
    const radians = Math.PI / 180;
    const lon0 = longitude * radians;
    const lat0 = latitude * radians;
    const sinLon = Math.sin(lon0), cosLon = Math.cos(lon0);
    const sinLat = Math.sin(lat0), cosLat = Math.cos(lat0);
    const primeVertical = a / Math.sqrt(1 - eccentricitySquared * sinLat * sinLat);
    const x0 = (primeVertical + height) * cosLat * cosLon;
    const y0 = (primeVertical + height) * cosLat * sinLon;
    const z0 = (primeVertical * (1 - eccentricitySquared) + height) * sinLat;
    const x = x0 - sinLon * east - sinLat * cosLon * north + cosLat * cosLon * up;
    const y = y0 + cosLon * east - sinLat * sinLon * north + cosLat * sinLon * up;
    const z = z0 + cosLat * north + sinLat * up;
    const lon = Math.atan2(y, x);
    const horizontal = Math.hypot(x, y);
    let lat = Math.atan2(z, horizontal * (1 - eccentricitySquared));
    let recoveredHeight = 0;
    for (let iteration = 0; iteration < 8; iteration += 1) {
      const sinRecovered = Math.sin(lat);
      const n = a / Math.sqrt(1 - eccentricitySquared * sinRecovered * sinRecovered);
      recoveredHeight = horizontal / Math.max(Math.cos(lat), 1e-12) - n;
      lat = Math.atan2(
        z,
        horizontal * (1 - eccentricitySquared * n / (n + recoveredHeight))
      );
    }
    return [lon / radians, lat / radians, recoveredHeight];
  }

  function enuFeatureCollectionToWgs84(collection) {
    const source = collection || {};
    if (!source.origin) throw new Error("ENU GeoJSON origin is unavailable");
    const features = (source.features || []).map((feature) => ({
      ...feature,
      geometry: {
        ...feature.geometry,
        coordinates: (feature.geometry?.coordinates || []).map((ring) =>
          ring.map((point) => enuToWgs84(point, source.origin).slice(0, 2))
        )
      }
    }));
    return {
      ...source,
      name: "road_condition_defects_wgs84",
      coordinate_system: "EPSG:4326",
      features
    };
  }

  function perspectivePoint(sRatio, tRatio, residualMm, width, height, exaggeration) {
    const x = width * (0.10 + 0.72 * sRatio + 0.14 * tRatio);
    const y = height * (0.84 - 0.54 * sRatio + 0.13 * tRatio)
      - Number(residualMm || 0) * Number(exaggeration || 1) * 0.65;
    return [x, y];
  }

  return { parseRoutePaths, buildTileSequence, defectVisible, mapAdapterStatus, enuToWgs84, enuFeatureCollectionToWgs84, perspectivePoint };
});
