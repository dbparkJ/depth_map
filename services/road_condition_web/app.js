(() => {
  "use strict";

  const state = {
    jobId: null,
    summary: null,
    surface: null,
    defects: [],
    geojson: null,
    segments: [],
    selectedDefect: null,
    pollingTimer: null,
    canvasGeometry: null
  };

  const $ = (id) => document.getElementById(id);
  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const format = (value, digits = 1) => number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  const api = async (path, options = {}) => {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try { detail = (await response.json()).detail || detail; } catch (_) { /* no-op */ }
      throw new Error(detail);
    }
    if (response.status === 204) return null;
    return response.json();
  };

  function setApiStatus(text, kind) {
    const element = $("apiStatus");
    element.textContent = text;
    element.className = `status-pill ${kind}`;
  }

  function setProgress(status) {
    $("jobProgress").hidden = false;
    const progress = Math.max(0, Math.min(1, number(status.progress)));
    $("progressBar").style.width = `${progress * 100}%`;
    $("progressText").textContent = `${status.state} · ${status.message || ""}`;
    $("runButton").disabled = status.state === "queued" || status.state === "running";
  }

  async function refreshJobs() {
    try {
      const payload = await api("/api/v1/jobs?limit=12");
      const root = $("jobList");
      root.innerHTML = "";
      if (!payload.jobs.length) {
        root.innerHTML = '<span class="muted">작업 없음</span>';
        return [];
      }
      for (const job of payload.jobs) {
        const item = document.createElement("div");
        item.className = "job-item";
        const source = job.request?.source_type === "mapping_bundle" ? "mapping" : job.request?.synthetic_profile || "synthetic";
        item.innerHTML = `<strong>${job.job_id.slice(0, 10)} · ${source}</strong><small>${job.state}</small><small>${new Date(job.created_at).toLocaleString("ko-KR")}</small><small>${Math.round(number(job.progress) * 100)}%</small>`;
        item.addEventListener("click", () => openJob(job.job_id));
        root.appendChild(item);
      }
      return payload.jobs;
    } catch (error) {
      $("jobList").innerHTML = `<span class="muted">${error.message}</span>`;
      return [];
    }
  }

  function buildRequest() {
    const sourceType = $("sourceType").value;
    const request = {
      source_type: sourceType,
      point_cloud_stage: $("cloudStage").value,
      config: {
        surface: {
          grid_size_m: number($("gridSize").value, 0.1),
          corridor_half_width_m: number($("halfWidth").value, 3.5)
        },
        detection: {
          pothole_min_depth_m: number($("potholeDepth").value, 0.035),
          pothole_min_area_m2: number($("potholeArea").value, 0.035),
          rut_min_depth_m: number($("rutDepth").value, 0.02),
          segment_length_m: number($("segmentLength").value, 20)
        }
      }
    };
    if (sourceType === "synthetic") {
      request.synthetic_profile = $("syntheticProfile").value;
    } else {
      request.mapping_output_path = $("mappingPath").value.trim();
      const roiPath = $("roadRoiPath").value.trim();
      if (roiPath) request.road_roi_path = roiPath;
    }
    return request;
  }

  async function createJob(auto = false) {
    try {
      $("runButton").disabled = true;
      const payload = await api("/api/v1/jobs", {
        method: "POST",
        body: JSON.stringify(auto ? { source_type: "synthetic", synthetic_profile: "mixed" } : buildRequest())
      });
      state.jobId = payload.job_id;
      setProgress(payload);
      pollJob(payload.job_id);
      refreshJobs();
    } catch (error) {
      setApiStatus(`실행 오류: ${error.message}`, "error");
      $("runButton").disabled = false;
    }
  }

  async function openJob(jobId) {
    clearTimeout(state.pollingTimer);
    state.jobId = jobId;
    try {
      const status = await api(`/api/v1/jobs/${jobId}`);
      setProgress(status);
      if (status.state === "completed") await loadResults(jobId);
      else if (status.state === "failed") throw new Error(status.error || "analysis failed");
      else pollJob(jobId);
    } catch (error) {
      setApiStatus(`작업 오류: ${error.message}`, "error");
    }
  }

  async function pollJob(jobId) {
    clearTimeout(state.pollingTimer);
    try {
      const status = await api(`/api/v1/jobs/${jobId}`);
      setProgress(status);
      if (status.state === "completed") {
        $("runButton").disabled = false;
        await loadResults(jobId);
        refreshJobs();
        return;
      }
      if (status.state === "failed") {
        $("runButton").disabled = false;
        throw new Error(status.error || "analysis failed");
      }
      state.pollingTimer = setTimeout(() => pollJob(jobId), 600);
    } catch (error) {
      setApiStatus(`분석 실패: ${error.message}`, "error");
      $("progressText").textContent = error.message;
      $("runButton").disabled = false;
    }
  }

  async function loadResults(jobId) {
    const [summary, surface, defects, geojson, segments] = await Promise.all([
      api(`/api/v1/jobs/${jobId}/summary`),
      api(`/api/v1/jobs/${jobId}/surface`),
      api(`/api/v1/jobs/${jobId}/defects`),
      api(`/api/v1/jobs/${jobId}/defects.local.geojson`),
      api(`/api/v1/jobs/${jobId}/segments`)
    ]);
    Object.assign(state, { summary, surface, defects, geojson, segments, selectedDefect: null });
    $("emptyState").hidden = true;
    $("resultView").hidden = false;
    $("scenarioButton").disabled = false;
    $("reportLink").classList.remove("disabled");
    $("reportLink").href = `/api/v1/jobs/${jobId}/report`;
    $("jobProgress").hidden = true;
    renderSummary();
    renderDefectTable();
    renderSegmentTable();
    renderSurface();
    renderDefectDetail();
    setApiStatus("분석 완료", "ok");
  }

  function renderSummary() {
    const results = state.summary.results || {};
    const scores = state.summary.scores || {};
    const coverage = state.summary.coverage || {};
    const quality = state.summary.quality || {};
    $("geometryScore").textContent = format(scores.geometry_score, 1);
    $("geometryGrade").textContent = `${scores.grade || "-"} 등급`;
    $("potholeCount").textContent = number(results.pothole_count).toLocaleString("ko-KR");
    $("potholeArea").textContent = `총 ${format(results.pothole_area_m2, 2)} ㎡`;
    $("maxPotholeDepth").textContent = `${format(number(results.max_pothole_depth_m) * 1000, 0)} mm`;
    $("maxRutDepth").textContent = `${format(number(results.max_rut_depth_m) * 1000, 0)} mm`;
    $("coverageRatio").textContent = `${format(number(coverage.valid_coverage_ratio) * 100, 1)}%`;
    $("analyzedPoints").textContent = number(quality.analyzed_point_count).toLocaleString("ko-KR");
  }

  function defectPrimaryMetric(defect) {
    const m = defect.metrics || {};
    if (defect.defect_type === "pothole") return `${format(number(m.max_depth_m) * 1000, 0)} mm · ${format(m.area_m2, 2)} ㎡`;
    if (defect.defect_type === "rutting") return `${format(number(m.max_depth_m) * 1000, 0)} mm · ${format(m.length_m, 1)} m`;
    return `${format(number(m.max_height_m) * 1000, 0)} mm · ${format(m.area_m2, 2)} ㎡`;
  }

  function defectName(type) {
    return ({ pothole: "포트홀", rutting: "러팅", bump: "범프" })[type] || type;
  }

  function renderDefectTable() {
    const body = $("defectTable").querySelector("tbody");
    body.innerHTML = "";
    $("defectCountLabel").textContent = `${state.defects.length}개`;
    for (const defect of state.defects) {
      const row = document.createElement("tr");
      row.dataset.defectId = defect.defect_id;
      row.innerHTML = `<td>${defect.defect_id}</td><td>${defectName(defect.defect_type)}</td><td>${defect.lane_id || defect.road_zone || "unknown"}</td><td>${format(defect.chainage_m, 1)} m</td><td class="severity-${defect.severity}">${defect.severity}</td><td>${defectPrimaryMetric(defect)}</td><td>${format(number(defect.confidence) * 100, 0)}%</td>`;
      row.addEventListener("click", () => selectDefect(defect.defect_id));
      body.appendChild(row);
    }
  }

  function renderSegmentTable() {
    const body = $("segmentTable").querySelector("tbody");
    body.innerHTML = "";
    for (const segment of state.segments) {
      const rut = Math.max(number(segment.max_left_rut_depth_m), number(segment.max_right_rut_depth_m));
      const row = document.createElement("tr");
      row.innerHTML = `<td>${format(segment.chainage_start_m, 0)}–${format(segment.chainage_end_m, 0)} m</td><td>${segment.lane_id || "전체"}</td><td>${format(number(segment.valid_coverage_ratio) * 100, 0)}%</td><td>${segment.pothole_count} / ${format(number(segment.max_pothole_depth_m) * 1000, 0)} mm</td><td>${format(rut * 1000, 0)} mm</td><td>${format(segment.geometry_score, 1)}</td><td>${segment.grade}</td>`;
      body.appendChild(row);
    }
  }

  function residualColor(valueMm, exaggeration) {
    if (valueMm === null || !Number.isFinite(valueMm)) return "rgb(12,24,36)";
    const normalized = Math.max(-1, Math.min(1, valueMm * exaggeration / 100));
    if (normalized < 0) {
      const a = -normalized;
      return `rgb(${Math.round(72 - 42 * a)},${Math.round(108 - 52 * a)},${Math.round(132 + 92 * a)})`;
    }
    const a = normalized;
    return `rgb(${Math.round(105 + 125 * a)},${Math.round(118 - 45 * a)},${Math.round(125 - 70 * a)})`;
  }

  function layerVisible(type) {
    if (type === "pothole") return $("showPotholes").checked;
    if (type === "rutting") return $("showRutting").checked;
    if (type === "bump") return $("showBumps").checked;
    return true;
  }

  function renderSurface() {
    if (!state.surface) return;
    const canvas = $("surfaceCanvas");
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(600, Math.floor(rect.width * ratio));
    canvas.height = Math.max(360, Math.floor(rect.height * ratio));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const width = canvas.width / ratio;
    const height = canvas.height / ratio;
    ctx.clearRect(0, 0, width, height);
    const margin = { left: 54, right: 18, top: 16, bottom: 34 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const s = state.surface.s_values_m;
    const t = state.surface.t_values_m;
    const sMin = s[0], sMax = s[s.length - 1];
    const tMin = t[0], tMax = t[t.length - 1];
    const xOf = (value) => margin.left + (value - sMin) / Math.max(1e-9, sMax - sMin) * plotWidth;
    const yOf = (value) => margin.top + (tMax - value) / Math.max(1e-9, tMax - tMin) * plotHeight;
    state.canvasGeometry = { margin, width, height, plotWidth, plotHeight, sMin, sMax, tMin, tMax, xOf, yOf };

    ctx.fillStyle = "#06101a";
    ctx.fillRect(margin.left, margin.top, plotWidth, plotHeight);
    if ($("showResidual").checked) {
      const dx = plotWidth / Math.max(1, s.length - 1);
      const dy = plotHeight / Math.max(1, t.length - 1);
      const exaggeration = number($("exaggeration").value, 1);
      for (let i = 0; i < s.length; i++) {
        const row = state.surface.residual_mm[i];
        for (let j = 0; j < t.length; j++) {
          ctx.fillStyle = residualColor(row[j], exaggeration);
          ctx.fillRect(xOf(s[i]) - dx / 2, yOf(t[j]) - dy / 2, dx + 1, dy + 1);
        }
      }
    }
    if ($("showRoi").checked && state.surface.roi?.applied) {
      const dx = plotWidth / Math.max(1, s.length - 1);
      const dy = plotHeight / Math.max(1, t.length - 1);
      const overlay = { 0: "rgba(115,128,140,.72)", 3: "rgba(245,166,35,.28)", 4: "rgba(255,70,70,.55)" };
      for (let i = 0; i < s.length; i++) {
        for (let j = 0; j < t.length; j++) {
          const color = overlay[state.surface.roi.zone_code[i][j]];
          if (!color) continue;
          ctx.fillStyle = color;
          ctx.fillRect(xOf(s[i]) - dx / 2, yOf(t[j]) - dy / 2, dx + 1, dy + 1);
        }
      }
    }

    const fills = { pothole: "rgba(55,143,255,.42)", rutting: "rgba(248,197,65,.32)", bump: "rgba(255,91,91,.38)" };
    const strokes = { pothole: "#65afff", rutting: "#ffd166", bump: "#ff7979" };
    for (const feature of state.geojson.features || []) {
      const type = feature.properties.defect_type;
      if (!layerVisible(type)) continue;
      const ring = feature.geometry.coordinates[0] || [];
      if (!ring.length) continue;
      ctx.beginPath();
      ring.forEach((point, index) => {
        const x = xOf(point[0]); const y = yOf(point[1]);
        if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.closePath();
      ctx.fillStyle = fills[type] || "rgba(255,255,255,.25)";
      ctx.strokeStyle = feature.id === state.selectedDefect?.defect_id ? "#ffffff" : (strokes[type] || "#fff");
      ctx.lineWidth = feature.id === state.selectedDefect?.defect_id ? 3 : 1.5;
      ctx.fill(); ctx.stroke();
    }

    ctx.strokeStyle = "#6f879b";
    ctx.lineWidth = 1;
    ctx.strokeRect(margin.left, margin.top, plotWidth, plotHeight);
    ctx.fillStyle = "#90a8bc";
    ctx.font = "11px system-ui";
    ctx.textAlign = "center";
    for (let k = 0; k <= 6; k++) {
      const value = sMin + (sMax - sMin) * k / 6;
      const x = xOf(value);
      ctx.fillText(`${value.toFixed(0)} m`, x, height - 10);
    }
    ctx.textAlign = "right";
    for (let k = 0; k <= 4; k++) {
      const value = tMin + (tMax - tMin) * k / 4;
      ctx.fillText(`${value.toFixed(1)}`, margin.left - 7, yOf(value) + 4);
    }
  }

  function pointInPolygon(point, polygon) {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const xi = polygon[i][0], yi = polygon[i][1];
      const xj = polygon[j][0], yj = polygon[j][1];
      const intersects = ((yi > point[1]) !== (yj > point[1])) && (point[0] < (xj - xi) * (point[1] - yi) / ((yj - yi) || 1e-12) + xi);
      if (intersects) inside = !inside;
    }
    return inside;
  }

  function selectFromCanvas(event) {
    if (!state.canvasGeometry || !state.geojson) return;
    const canvas = $("surfaceCanvas");
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const g = state.canvasGeometry;
    const s = g.sMin + (x - g.margin.left) / g.plotWidth * (g.sMax - g.sMin);
    const t = g.tMax - (y - g.margin.top) / g.plotHeight * (g.tMax - g.tMin);
    const feature = (state.geojson.features || []).find((candidate) => layerVisible(candidate.properties.defect_type) && pointInPolygon([s, t], candidate.geometry.coordinates[0] || []));
    if (feature) selectDefect(feature.id);
  }

  function selectDefect(defectId) {
    state.selectedDefect = state.defects.find((item) => item.defect_id === defectId) || null;
    document.querySelectorAll("#defectTable tbody tr").forEach((row) => row.classList.toggle("selected", row.dataset.defectId === defectId));
    renderSurface();
    renderDefectDetail();
  }

  function renderDefectDetail() {
    const defect = state.selectedDefect;
    if (!defect) {
      $("selectedBadge").textContent = "미선택";
      $("defectDetail").innerHTML = '<span class="muted">지도 또는 아래 표에서 결함을 선택하세요.</span>';
      $("profileChart").innerHTML = "";
      return;
    }
    $("selectedBadge").textContent = `${defectName(defect.defect_type)} · ${defect.severity}`;
    const flags = (defect.quality_flags || []).length ? defect.quality_flags.join(", ") : "없음";
    $("defectDetail").innerHTML = `<strong>${defect.defect_id}</strong><p>차로/구역 ${defect.lane_id || defect.road_zone || "unknown"} · 체인리지 ${format(defect.chainage_m, 2)} m · 횡방향 ${format(defect.lateral_offset_m, 2)} m</p><p>측정: ${defectPrimaryMetric(defect)} · 신뢰도 ${format(number(defect.confidence) * 100, 0)}%</p><p class="muted">품질 플래그: ${flags}</p>`;
    renderProfile(defect.chainage_m);
  }

  function renderProfile(chainage) {
    const svg = $("profileChart");
    svg.innerHTML = "";
    const s = state.surface.s_values_m;
    let rowIndex = 0;
    let best = Infinity;
    s.forEach((value, index) => { const distance = Math.abs(value - chainage); if (distance < best) { best = distance; rowIndex = index; } });
    const t = state.surface.t_values_m;
    const residual = state.surface.residual_mm[rowIndex];
    const width = 640, height = 220, margin = { left: 48, right: 14, top: 18, bottom: 34 };
    const finite = residual.filter((value) => value !== null && Number.isFinite(value));
    const maxAbs = Math.max(40, ...finite.map((value) => Math.abs(value)));
    const xOf = (value) => margin.left + (value - t[0]) / Math.max(1e-9, t[t.length - 1] - t[0]) * (width - margin.left - margin.right);
    const yOf = (value) => margin.top + (maxAbs - value) / (2 * maxAbs) * (height - margin.top - margin.bottom);
    const ns = "http://www.w3.org/2000/svg";
    const axis = document.createElementNS(ns, "path");
    axis.setAttribute("d", `M${margin.left},${yOf(0)} H${width - margin.right} M${margin.left},${margin.top} V${height - margin.bottom}`);
    axis.setAttribute("stroke", "#5e7890"); axis.setAttribute("fill", "none"); svg.appendChild(axis);
    const points = [];
    residual.forEach((value, index) => { if (value !== null && Number.isFinite(value)) points.push(`${xOf(t[index])},${yOf(value)}`); });
    const line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", points.join(" ")); line.setAttribute("fill", "none"); line.setAttribute("stroke", "#51d3df"); line.setAttribute("stroke-width", "2"); svg.appendChild(line);
    const label = document.createElementNS(ns, "text");
    label.setAttribute("x", margin.left); label.setAttribute("y", height - 9); label.setAttribute("fill", "#91a9bd"); label.setAttribute("font-size", "11"); label.textContent = `횡단면 @ s=${format(s[rowIndex], 1)} m · residual mm`; svg.appendChild(label);
  }

  async function calculateScenario() {
    if (!state.jobId) return;
    try {
      const result = await api(`/api/v1/jobs/${state.jobId}/scenarios`, {
        method: "POST",
        body: JSON.stringify({
          include_types: ["pothole", "rutting", "bump"],
          rainfall_mm: number($("rainfall").value, 30),
          unit_prices: {
            pothole_patch_krw_per_m2: number($("patchPrice").value, 85000),
            rut_overlay_krw_per_m2: number($("rutPrice").value, 52000)
          }
        })
      });
      $("scenarioResult").innerHTML = `<span>예상 총비용</span><strong>${number(result.costs_krw.total).toLocaleString("ko-KR")}원</strong><p>현재 ${format(result.score_projection.current_geometry_score, 1)}점 → 보수 후 추정 ${format(result.score_projection.expected_post_maintenance_score, 1)}점</p><p class="muted">포트홀 ${format(result.quantities.pothole_area_m2, 2)}㎡ · 러팅 ${format(result.quantities.rut_overlay_area_m2, 2)}㎡ · 저류 프록시 ${format(result.rainfall_screening.detected_depression_storage_proxy_m3, 3)}㎥</p>`;
    } catch (error) {
      $("scenarioResult").textContent = error.message;
    }
  }

  async function boot() {
    try {
      await api("/api/v1/health");
      setApiStatus("API 정상", "ok");
      const jobs = await refreshJobs();
      const completed = jobs.find((job) => job.state === "completed");
      const active = jobs.find((job) => job.state === "queued" || job.state === "running");
      if (completed) await openJob(completed.job_id);
      else if (active) await openJob(active.job_id);
      else await createJob(true);
    } catch (error) {
      setApiStatus(`API 연결 실패: ${error.message}`, "error");
    }
  }

  $("sourceType").addEventListener("change", (event) => {
    const mapping = event.target.value === "mapping_bundle";
    $("mappingFields").hidden = !mapping;
    $("syntheticFields").hidden = mapping;
  });
  $("runButton").addEventListener("click", () => createJob(false));
  $("scenarioButton").addEventListener("click", calculateScenario);
  $("surfaceCanvas").addEventListener("click", selectFromCanvas);
  ["showResidual", "showRoi", "showPotholes", "showRutting", "showBumps"].forEach((id) => $(id).addEventListener("change", renderSurface));
  $("exaggeration").addEventListener("input", (event) => { $("exaggerationValue").textContent = `${event.target.value}×`; renderSurface(); });
  window.addEventListener("resize", () => { if (state.surface) renderSurface(); });

  boot();
})();
