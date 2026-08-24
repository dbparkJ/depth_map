(function () {
  "use strict";

  const config = window.RGBD_MAP_CONFIG || {};
  const params = new URLSearchParams(window.location.search);
  const dataBase = params.get("data") || config.dataBase || "../data";
  const state = {
    viewer: null,
    map: null,
    engine: null,
    summary: null,
    trajectory: null,
    cloudLayers: {
      clean: { collections: [], loaded: false, loading: null },
      raw: { collections: [], loaded: false, loading: null },
      removed: { collections: [], loaded: false, loading: null }
    },
    fusedCollection: null,
    gpsCollection: null,
    rawCollection: null,
    residualCollection: null,
    offsets: { east: 0, north: 0, up: 0 },
    pointSize: 2.5,
    dataCenter: [0, 0, 0],
    dataRadius: 1000
  };

  const CLOUD_LAYER_UI = {
    clean: { toggleId: "showCleanCloud", label: "정제 점군" },
    raw: { toggleId: "showRawCloud", label: "원본 점군" },
    removed: { toggleId: "showRemovedCloud", label: "제거된 점" }
  };
  const byId = (id) => document.getElementById(id);
  const setStatus = (message) => { byId("status").textContent = message; };
  const setLoading = (message, visible = true) => {
    byId("loadingText").textContent = message;
    byId("loading").style.display = visible ? "flex" : "none";
  };
  const finiteNumber = (value, fallback = 0) => {
    if (value === null || value === undefined || value === "") return fallback;
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  };

  function cloudPointCounts() {
    const cloud = state.summary.cloud || {};
    const legacyPly = finiteNumber(cloud.ply_point_count ?? cloud.point_count, 0);
    const legacyBrowser = finiteNumber(cloud.browser_point_count ?? cloud.point_count, 0);
    const cleanPly = finiteNumber(cloud.clean_point_count, legacyPly);
    const rawPly = finiteNumber(cloud.raw_point_count, cleanPly);
    const removedPly = finiteNumber(cloud.removed_point_count, Math.max(0, rawPly - cleanPly));
    const removalRatio = finiteNumber(
      cloud.removal_ratio,
      rawPly > 0 ? removedPly / rawPly : 0
    );
    return {
      rawPly,
      cleanPly,
      removedPly,
      removalRatio,
      cleanBrowser: finiteNumber(
        cloud.clean_browser_point_count ?? cloud.web_clean_point_count,
        legacyBrowser
      ),
      rawBrowser: finiteNumber(
        cloud.raw_browser_point_count ?? cloud.web_raw_point_count,
        cloud.raw_browser_binary ? rawPly : 0
      ),
      removedBrowser: finiteNumber(
        cloud.removed_browser_point_count ?? cloud.web_removed_point_count,
        cloud.removed_browser_binary ? removedPly : 0
      )
    };
  }

  function cloudLayerDescriptor(kind) {
    const cloud = state.summary.cloud || {};
    const counts = cloudPointCounts();
    if (kind === "clean") {
      return {
        filename: cloud.clean_browser_binary || cloud.browser_binary || (counts.cleanBrowser ? "points.bin" : null),
        expectedCount: counts.cleanBrowser
      };
    }
    if (kind === "raw") {
      return { filename: cloud.raw_browser_binary || null, expectedCount: counts.rawBrowser };
    }
    return { filename: cloud.removed_browser_binary || null, expectedCount: counts.removedBrowser };
  }

  function allPointCollections() {
    return Object.values(state.cloudLayers).flatMap((layer) => layer.collections);
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.onload = resolve;
      script.onerror = () => reject(new Error("Script load failed: " + src));
      document.head.appendChild(script);
    });
  }

  function loadCss(href) {
    if ([...document.styleSheets].some((sheet) => sheet.href === href)) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
  }

  async function initVWorld(summary) {
    const key = params.get("key") || config.vworldApiKey || "";
    if (!window.vw || !key) throw new Error("VWorld SDK or key is unavailable");
    setLoading("VWorld 3D 지형·건물 로딩 중");
    return new Promise((resolve, reject) => {
      let completed = false;
      const timeout = setTimeout(() => {
        if (!completed) {
          completed = true;
          reject(new Error("VWorld initialization timed out"));
        }
      }, 18000);
      window.vw.ws3dInitCallBack = function () {
        if (completed) return;
        completed = true;
        clearTimeout(timeout);
        try {
          const viewer = window.ws3d.viewer;
          viewer.scene.globe.depthTestAgainstTerrain = true;
          const buildings = state.map.getLayerElement && state.map.getLayerElement("facility_build");
          if (buildings) {
            if (typeof buildings.show === "function") buildings.show();
            else buildings.visible = true;
          }
          resolve(viewer);
        } catch (error) {
          reject(error);
        }
      };
      try {
        if (String(window.vworldIsValid) !== "true") {
          throw new Error(window.vworldErrMsg || "VWorld key/domain authentication failed");
        }
        const origin = summary.origin;
        state.map = new window.vw.Map();
        state.map.setOption({
          mapId: "map",
          initPosition: new window.vw.CameraPosition(
            new window.vw.CoordZ(origin.longitude_deg, origin.latitude_deg, 1800),
            new window.vw.Direction(0, -65, 0)
          ),
          logo: true,
          navigation: true
        });
        state.map.start();
      } catch (error) {
        completed = true;
        clearTimeout(timeout);
        reject(error);
      }
    });
  }

  async function initCesiumFallback() {
    setLoading("대체 Cesium 지도 엔진 로딩 중");
    if (!window.Cesium) {
      const version = "1.144";
      window.CESIUM_BASE_URL = `https://cesium.com/downloads/cesiumjs/releases/${version}/Build/Cesium/`;
      loadCss(`${window.CESIUM_BASE_URL}Widgets/widgets.css`);
      await loadScript(`${window.CESIUM_BASE_URL}Cesium.js`);
    }
    const C = window.Cesium;
    const viewer = new C.Viewer("map", {
      animation: false,
      timeline: false,
      geocoder: false,
      baseLayerPicker: false,
      sceneModePicker: true,
      navigationHelpButton: false,
      infoBox: false,
      selectionIndicator: false,
      imageryProvider: false,
      baseLayer: false,
      terrainProvider: new C.EllipsoidTerrainProvider()
    });
    viewer.imageryLayers.removeAll();
    viewer.imageryLayers.addImageryProvider(new C.OpenStreetMapImageryProvider({
      url: "https://tile.openstreetmap.org/"
    }));
    const key = params.get("key") || config.vworldApiKey || "";
    if (key) {
      const layer = params.get("vworldLayer") || "Satellite";
      const extension = layer === "Satellite" ? "jpeg" : "png";
      viewer.imageryLayers.addImageryProvider(new C.UrlTemplateImageryProvider({
        url: `https://api.vworld.kr/req/wmts/1.0.0/${encodeURIComponent(key)}/${layer}/{z}/{y}/{x}.${extension}`,
        minimumLevel: 0,
        maximumLevel: 19,
        credit: "VWorld"
      }));
    }
    viewer.scene.globe.depthTestAgainstTerrain = true;
    return viewer;
  }

  function localModelMatrix() {
    const C = window.Cesium;
    const origin = state.summary.origin;
    const base = C.Transforms.eastNorthUpToFixedFrame(C.Cartesian3.fromDegrees(
      origin.longitude_deg,
      origin.latitude_deg,
      origin.ellipsoid_height_m
    ));
    const translation = C.Matrix4.fromTranslation(new C.Cartesian3(
      state.offsets.east,
      state.offsets.north,
      state.offsets.up
    ));
    return C.Matrix4.multiply(base, translation, new C.Matrix4());
  }

  function addPolyline(points, color, width) {
    const C = window.Cesium;
    const collection = state.viewer.scene.primitives.add(new C.PolylineCollection());
    collection.modelMatrix = localModelMatrix();
    if (points && points.length) {
      collection.add({
        positions: points.map((point) => new C.Cartesian3(point[0], point[1], point[2])),
        width,
        material: C.Material.fromType("Color", { color })
      });
    }
    return collection;
  }

  function warnHeaderMismatch(filename, field, actual, expected) {
    console.warn(`${filename} header ${field} mismatch: got ${actual}, expected ${expected}`);
  }

  async function loadPointCloudLayer(kind) {
    const descriptor = cloudLayerDescriptor(kind);
    const ui = CLOUD_LAYER_UI[kind];
    if (!descriptor.filename) return false;
    setLoading(`${ui.label} 다운로드 중`);
    const response = await fetch(`${dataBase}/${descriptor.filename}`);
    if (!response.ok) throw new Error(`${descriptor.filename} HTTP ${response.status}`);
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength < 16) {
      console.warn(`${descriptor.filename} header mismatch: file is shorter than 16 bytes`);
      throw new Error(`${descriptor.filename} has an invalid binary header`);
    }

    const view = new DataView(buffer);
    const magic = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3));
    const version = view.getUint32(4, true);
    const headerCount = view.getUint32(8, true);
    const stride = view.getUint32(12, true);
    let structurallyValid = true;
    if (magic !== "RGBD") {
      warnHeaderMismatch(descriptor.filename, "magic", magic, "RGBD");
      structurallyValid = false;
    }
    if (version !== 1) {
      warnHeaderMismatch(descriptor.filename, "version", version, 1);
      structurallyValid = false;
    }
    if (stride !== 16) {
      warnHeaderMismatch(descriptor.filename, "stride", stride, 16);
      structurallyValid = false;
    }
    if (headerCount !== descriptor.expectedCount) {
      warnHeaderMismatch(descriptor.filename, "count", headerCount, descriptor.expectedCount);
    }
    const expectedByteLength = structurallyValid ? 16 + headerCount * stride : null;
    if (expectedByteLength !== null && expectedByteLength !== buffer.byteLength) {
      console.warn(
        `${descriptor.filename} payload size mismatch: got ${buffer.byteLength}, expected ${expectedByteLength}`
      );
      structurallyValid = false;
    }
    if (!structurallyValid) {
      throw new Error(`${descriptor.filename} has an invalid binary structure`);
    }
    const count = headerCount;

    const C = window.Cesium;
    const layer = state.cloudLayers[kind];
    const chunkSize = 60000;
    for (let begin = 0; begin < count; begin += chunkSize) {
      const end = Math.min(begin + chunkSize, count);
      const collection = state.viewer.scene.primitives.add(new C.PointPrimitiveCollection({
        modelMatrix: localModelMatrix(),
        blendOption: C.BlendOption.OPAQUE
      }));
      collection.show = Boolean(byId(ui.toggleId).checked);
      for (let index = begin; index < end; index++) {
        const offset = 16 + index * stride;
        collection.add({
          position: new C.Cartesian3(
            view.getFloat32(offset, true),
            view.getFloat32(offset + 4, true),
            view.getFloat32(offset + 8, true)
          ),
          color: C.Color.fromBytes(
            view.getUint8(offset + 12),
            view.getUint8(offset + 13),
            view.getUint8(offset + 14),
            255
          ),
          pixelSize: state.pointSize
        });
      }
      layer.collections.push(collection);
      setLoading(`${ui.label} 생성 ${end.toLocaleString()} / ${count.toLocaleString()}`);
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    return true;
  }

  async function ensureCloudLayerLoaded(kind) {
    const layer = state.cloudLayers[kind];
    if (layer.loaded) return true;
    if (layer.loading) return layer.loading;
    layer.loading = loadPointCloudLayer(kind)
      .then((loaded) => {
        layer.loaded = loaded;
        return loaded;
      })
      .catch((error) => {
        for (const collection of layer.collections) {
          if (state.viewer) state.viewer.scene.primitives.remove(collection);
        }
        layer.collections = [];
        layer.loaded = false;
        console.warn(`Failed to load ${kind} point cloud`, error);
        const failureMessage = `${CLOUD_LAYER_UI[kind].label} 로드 실패: ${error.message || error}`;
        setStatus(failureMessage);
        byId("loadingText").textContent = failureMessage;
        return false;
      })
      .finally(() => {
        layer.loading = null;
        byId("loading").style.display = "none";
      });
    return layer.loading;
  }

  function setCloudLayerVisibility(kind, visible) {
    for (const collection of state.cloudLayers[kind].collections) collection.show = visible;
    if (state.viewer) state.viewer.scene.requestRender();
  }

  function addTrajectories() {
    const C = window.Cesium;
    state.fusedCollection = addPolyline(state.trajectory.fused, C.Color.fromCssColorString("#36d6ff"), 4);
    state.gpsCollection = addPolyline(state.trajectory.gps, C.Color.fromCssColorString("#ff5570"), 3);
    state.rawCollection = addPolyline(state.trajectory.pre_graph_gps_aided, C.Color.fromCssColorString("#ffb648"), 2);
    state.rawCollection.show = false;
    const residuals = state.viewer.scene.primitives.add(new C.PolylineCollection());
    residuals.modelMatrix = localModelMatrix();
    const material = C.Material.fromType("Color", { color: C.Color.WHITE.withAlpha(0.65) });
    for (const segment of state.trajectory.residuals || []) {
      residuals.add({
        positions: segment.map((point) => new C.Cartesian3(point[0], point[1], point[2])),
        width: 1,
        material
      });
    }
    residuals.show = false;
    state.residualCollection = residuals;
  }

  function updateModelMatrices() {
    const matrix = localModelMatrix();
    for (const collection of allPointCollections()) collection.modelMatrix = matrix;
    for (const collection of [state.fusedCollection, state.gpsCollection, state.rawCollection, state.residualCollection]) {
      if (collection) collection.modelMatrix = matrix;
    }
    state.viewer.scene.requestRender();
  }

  function dataBoundingSphere() {
    const C = window.Cesium;
    const centerLocal = new C.Cartesian3(...state.dataCenter);
    const centerWorld = C.Matrix4.multiplyByPoint(localModelMatrix(), centerLocal, new C.Cartesian3());
    return new C.BoundingSphere(centerWorld, Math.max(100, state.dataRadius));
  }

  function flyToData() {
    const C = window.Cesium;
    const sphere = dataBoundingSphere();
    state.viewer.camera.flyToBoundingSphere(
      sphere,
      { offset: new C.HeadingPitchRange(0, -0.65, Math.max(450, state.dataRadius * 2.2)), duration: 1.4 }
    );
  }

  function viewDataImmediately() {
    const C = window.Cesium;
    const sphere = dataBoundingSphere();
    state.viewer.camera.cancelFlight();
    state.viewer.camera.viewBoundingSphere(
      sphere,
      new C.HeadingPitchRange(0, -0.65, Math.max(450, state.dataRadius * 2.2))
    );
    state.viewer.camera.lookAtTransform(C.Matrix4.IDENTITY);
    state.viewer.scene.requestRender();
  }

  function setupBounds() {
    const cloud = state.summary.cloud || {};
    const bbox = cloud.bbox_enu_min_m && cloud.bbox_enu_max_m
      ? [cloud.bbox_enu_min_m, cloud.bbox_enu_max_m]
      : null;
    const fused = state.trajectory.fused || [];
    const points = bbox || (fused.length ? [
      fused.reduce((accumulator, point) => accumulator.map((value, index) => Math.min(value, point[index])), [Infinity, Infinity, Infinity]),
      fused.reduce((accumulator, point) => accumulator.map((value, index) => Math.max(value, point[index])), [-Infinity, -Infinity, -Infinity])
    ] : [[-50, -50, -10], [50, 50, 10]]);
    state.dataCenter = points[0].map((value, index) => (value + points[1][index]) * 0.5);
    state.dataRadius = Math.hypot(
      points[1][0] - points[0][0],
      points[1][1] - points[0][1],
      points[1][2] - points[0][2]
    ) * 0.5;
  }

  function populateSummary() {
    const metrics = state.summary.trajectory || {};
    const pointCounts = cloudPointCounts();
    byId("frameCount").textContent = finiteNumber(state.summary.frame_count).toLocaleString();
    byId("rawPointCount").textContent = pointCounts.rawPly.toLocaleString();
    byId("cleanPointCount").textContent = pointCounts.cleanPly.toLocaleString();
    byId("removedPointCount").textContent = pointCounts.removedPly.toLocaleString();
    byId("removalRatio").textContent = `${(100 * pointCounts.removalRatio).toFixed(2)}%`;
    byId("webCleanPointCount").textContent = pointCounts.cleanBrowser.toLocaleString();
    byId("voRate").textContent = `${(100 * finiteNumber(metrics.visual_odometry_success_rate)).toFixed(1)}%`;
    const residual = metrics.gps_constraint_residual_horizontal_m || {};
    byId("residualP95").textContent = Number.isFinite(Number(residual.p95))
      ? `${Number(residual.p95).toFixed(2)} m`
      : "-";
  }

  function configureCloudControls() {
    for (const kind of Object.keys(CLOUD_LAYER_UI)) {
      const descriptor = cloudLayerDescriptor(kind);
      const control = byId(CLOUD_LAYER_UI[kind].toggleId);
      control.disabled = !descriptor.filename;
      if (control.disabled) control.checked = false;
    }
  }

  function bindControls() {
    for (const kind of Object.keys(CLOUD_LAYER_UI)) {
      const control = byId(CLOUD_LAYER_UI[kind].toggleId);
      control.addEventListener("change", async (event) => {
        if (kind === "removed") byId("removedLegend").hidden = !event.target.checked;
        if (!event.target.checked) {
          setCloudLayerVisibility(kind, false);
          return;
        }
        const loaded = await ensureCloudLayerLoaded(kind);
        if (!loaded) {
          event.target.checked = false;
          if (kind === "removed") byId("removedLegend").hidden = true;
          return;
        }
        setCloudLayerVisibility(kind, event.target.checked);
      });
    }

    const layerBindings = [
      ["showFused", () => [state.fusedCollection]],
      ["showGps", () => [state.gpsCollection]],
      ["showRaw", () => [state.rawCollection]],
      ["showResiduals", () => [state.residualCollection]]
    ];
    for (const [id, collections] of layerBindings) {
      byId(id).addEventListener("change", (event) => {
        for (const collection of collections()) if (collection) collection.show = event.target.checked;
        state.viewer.scene.requestRender();
      });
    }

    const offsets = [
      ["eastOffset", "eastValue", "east"],
      ["northOffset", "northValue", "north"],
      ["upOffset", "upValue", "up"]
    ];
    for (const [inputId, outputId, key] of offsets) {
      byId(inputId).addEventListener("input", (event) => {
        state.offsets[key] = Number(event.target.value);
        byId(outputId).textContent = `${state.offsets[key].toFixed(1)} m`;
        updateModelMatrices();
      });
    }
    byId("pointSize").addEventListener("change", (event) => {
      state.pointSize = Number(event.target.value);
      byId("pointSizeValue").textContent = `${state.pointSize.toFixed(1)} px`;
      for (const collection of allPointCollections()) {
        for (let index = 0; index < collection.length; index++) collection.get(index).pixelSize = state.pointSize;
      }
      state.viewer.scene.requestRender();
    });
    byId("resetOffsets").addEventListener("click", () => {
      state.offsets = { east: 0, north: 0, up: 0 };
      for (const [inputId, outputId] of offsets) {
        byId(inputId).value = 0;
        byId(outputId).textContent = "0.0 m";
      }
      updateModelMatrices();
    });
    byId("flyToData").addEventListener("click", flyToData);
  }

  async function boot() {
    try {
      setLoading("매핑 결과 읽는 중");
      const [summaryResponse, trajectoryResponse] = await Promise.all([
        fetch(`${dataBase}/summary.json`),
        fetch(`${dataBase}/trajectory.json`)
      ]);
      if (!summaryResponse.ok || !trajectoryResponse.ok) throw new Error("Mapping output files are unavailable");
      state.summary = await summaryResponse.json();
      state.trajectory = await trajectoryResponse.json();
      populateSummary();
      setupBounds();
      configureCloudControls();

      const forceCesium = params.get("engine") === "cesium" || config.preferredEngine === "cesium";
      if (forceCesium) {
        state.viewer = await initCesiumFallback();
        state.engine = "Cesium 타원체 폴백";
      } else {
        try {
          state.viewer = await initVWorld(state.summary);
          state.engine = "VWorld 3D";
        } catch (vworldError) {
          console.warn("VWorld unavailable; reloading a clean Cesium fallback", vworldError);
          const fallbackUrl = new URL(window.location.href);
          fallbackUrl.searchParams.set("engine", "cesium");
          window.location.replace(fallbackUrl.toString());
          return;
        }
      }
      byId("engineBadge").textContent = state.engine;
      addTrajectories();
      bindControls();
      const cleanDescriptor = cloudLayerDescriptor("clean");
      const cleanLoaded = await ensureCloudLayerLoaded("clean");
      if (state.engine === "VWorld 3D") {
        // VWorld can issue late home-camera commands after its SDK callback.
        viewDataImmediately();
        setTimeout(viewDataImmediately, 1200);
        setTimeout(viewDataImmediately, 3500);
      } else {
        flyToData();
      }
      state.viewer.scene.requestRender();
      const terrainNote = state.engine === "VWorld 3D" ? "3D 지형" : "지형고 없음";
      if (cleanLoaded || !cleanDescriptor.filename) {
        setStatus(`${state.engine} · ${terrainNote} · IMU 사용 안 함 · 로컬 ENU 기준`);
        setLoading("완료", false);
      } else {
        byId("loading").style.display = "none";
      }
    } catch (error) {
      console.error(error);
      byId("engineBadge").textContent = "오류";
      setStatus(error.message || String(error));
      setLoading("불러오기 실패", true);
    }
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
