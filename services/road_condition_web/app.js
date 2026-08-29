(() => {
  "use strict";

  const viewerCore = window.RoadConditionViewerCore;
  if (!viewerCore) throw new Error("viewer_core.js is required");
  const runtimeConfig = window.ROAD_CONDITION_CONFIG || {};
  const pageParams = new URLSearchParams(window.location.search);

  const state = {
    jobId: null,
    summary: null,
    surface: null,
    defects: [],
    geojson: null,
    enuGeojson: null,
    segments: [],
    reviews: null,
    selectedDefect: null,
    pollingTimer: null,
    canvasGeometry: null,
    completedJobs: [],
    routeManifests: [],
    evidenceManifests: {},
    routeTiles: [],
    routeTileIndex: -1,
    evidence: null,
    evidenceTile: null,
    sourceMode: "job",
    vworld: {
      map: null,
      viewer: null,
      dataSource: null,
      pointCollection: null,
      loading: null,
      renderToken: 0,
      renderedTileKey: null,
      selectedDefectId: null,
      selectionListenerInstalled: false
    }
  };

  const $ = (id) => document.getElementById(id);
  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const format = (value, digits = 1) => number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  const verticalDistance = (valueM) => {
    const metres = number(valueM);
    return Math.abs(metres) >= 1 ? `${format(metres, 2)} m` : `${format(metres * 100, 1)} cm`;
  };
  const query = (values) => new URLSearchParams(values).toString();
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
  const apiBinary = async (path) => {
    const response = await fetch(path, {
      headers: { Accept: "application/vnd.road-condition.rcev" }
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try { detail = (await response.json()).detail || detail; } catch (_) { /* no-op */ }
      throw new Error(detail);
    }
    return response.arrayBuffer();
  };

  function leadDefect(defects) {
    const severity = { high: 3, medium: 2, low: 1 };
    return [...(defects || [])].sort((left, right) =>
      number(severity[right.severity]) - number(severity[left.severity])
      || number(right.confidence) - number(left.confidence)
      || number(left.chainage_m) - number(right.chainage_m)
    )[0] || null;
  }

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

  function visibilityOptions() {
    return {
      types: {
        pothole: $("showPotholes").checked,
        rutting: $("showRutting").checked,
        bump: $("showBumps").checked
      },
      showAdvanced: $("showAdvanced").checked,
      showLowConfidence: $("showLowConfidence").checked,
      minimumConfidence: number($("minimumConfidence").value)
    };
  }

  function visibleDefects() {
    return state.defects.filter((defect) => viewerCore.defectVisible(defect, visibilityOptions()));
  }

  async function refreshJobs() {
    try {
      const payload = await api("/api/v1/jobs?limit=12");
      const root = $("jobList");
      root.innerHTML = "";
      state.completedJobs = payload.jobs.filter((job) => job.state === "completed");
      for (const selectId of ["jobSelect", "compareJob"]) {
        const select = $(selectId);
        const firstLabel = selectId === "jobSelect" ? "작업을 선택하세요" : "비교 작업 선택";
        select.innerHTML = `<option value="">${firstLabel}</option>`;
        for (const job of state.completedJobs) {
          const option = document.createElement("option");
          option.value = job.job_id;
          const source = job.request?.source_type === "mapping_bundle" ? "mapping" : job.request?.synthetic_profile || "synthetic";
          option.textContent = `${new Date(job.created_at).toLocaleString("ko-KR")} · ${source} · ${job.job_id.slice(0, 8)}`;
          select.appendChild(option);
        }
      }
      $("openJobButton").disabled = !$("jobSelect").value;
      $("compareButton").disabled = !$("compareJob").value;
      if (!payload.jobs.length) {
        root.innerHTML = '<span class="muted">작업 없음</span>';
        return [];
      }
      for (const job of payload.jobs) {
        const item = document.createElement("div");
        item.className = "job-item";
        item.tabIndex = 0;
        item.setAttribute("role", "button");
        const source = job.request?.source_type === "mapping_bundle" ? "mapping" : job.request?.synthetic_profile || "synthetic";
        item.innerHTML = `<strong>${job.job_id.slice(0, 10)} · ${source}</strong><small>${job.state}</small><small>${new Date(job.created_at).toLocaleString("ko-KR")}</small><small>${Math.round(number(job.progress) * 100)}%</small>`;
        item.addEventListener("click", () => openJob(job.job_id));
        item.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") openJob(job.job_id);
        });
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
          pothole_min_depth_m: number($("potholeDepth").value, 3.5) / 100,
          pothole_min_area_m2: number($("potholeArea").value, 0.035),
          rut_min_depth_m: number($("rutDepth").value, 2.0) / 100,
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
    state.sourceMode = "job";
    state.routeTileIndex = -1;
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
    const [summary, surface, defects, geojson, enuGeojson, segments, reviews] = await Promise.all([
      api(`/api/v1/jobs/${jobId}/summary`),
      api(`/api/v1/jobs/${jobId}/surface`),
      api(`/api/v1/jobs/${jobId}/defects`),
      api(`/api/v1/jobs/${jobId}/defects.local.geojson`),
      api(`/api/v1/jobs/${jobId}/defects.enu.geojson`),
      api(`/api/v1/jobs/${jobId}/segments`),
      api(`/api/v1/jobs/${jobId}/reviews`)
    ]);
    Object.assign(state, {
      summary,
      surface,
      defects,
      geojson,
      enuGeojson,
      segments,
      reviews,
      selectedDefect: leadDefect(defects),
      evidence: null,
      evidenceTile: null
    });
    $("emptyState").hidden = true;
    $("resultView").hidden = false;
    $("scenarioButton").disabled = false;
    $("reportLink").classList.remove("disabled");
    $("reportLink").href = `/api/v1/jobs/${jobId}/report`;
    $("loadedTileLabel").textContent = `job ${jobId.slice(0, 8)}`;
    $("evidenceStatus").textContent = "경량 현장 점군 없음 · 분석 표면과 후보 경계로 표시";
    $("jobProgress").hidden = true;
    renderSummary();
    renderDefectTable();
    renderSegmentTable();
    renderCurrentView();
    renderDefectDetail();
    setApiStatus("분석 완료", "ok");
  }

  async function loadRouteDatasets() {
    try {
      const paths = viewerCore.parseRoutePaths($("routePaths").value);
      if (!paths.length) throw new Error("Route 결과 상대 경로를 하나 이상 입력하세요.");
      $("loadRouteButton").disabled = true;
      $("routeStatus").textContent = "manifest 로딩 중";
      const manifests = await Promise.all(paths.map((path) =>
        api(`/api/v1/route-datasets/manifest?${query({ path })}`)
      ));
      const evidenceEntries = await Promise.all(manifests.map(async (manifest) => {
        if (!manifest.viewer_contract?.point_evidence?.available) {
          return [manifest.workspace_relative_path, null];
        }
        try {
          const evidenceManifest = await api(
            `/api/v1/route-datasets/evidence/manifest?${query({ path: manifest.workspace_relative_path })}`
          );
          return [manifest.workspace_relative_path, evidenceManifest];
        } catch (_) {
          return [manifest.workspace_relative_path, null];
        }
      }));
      state.routeManifests = manifests;
      state.evidenceManifests = Object.fromEntries(evidenceEntries);
      state.routeTiles = viewerCore.buildTileSequence(manifests);
      state.sourceMode = "route";
      state.jobId = null;
      const selector = $("tileSelect");
      selector.innerHTML = "";
      state.routeTiles.forEach((tile, index) => {
        const option = document.createElement("option");
        option.value = String(index);
        const range = Number.isFinite(tile.coreStartM) ? ` · ${format(tile.coreStartM, 0)}–${format(tile.coreEndM, 0)}m` : "";
        option.textContent = `${tile.path} · ${tile.tileId}${range} · ${tile.state}`;
        selector.appendChild(option);
      });
      selector.disabled = !state.routeTiles.length;
      const completed = state.routeTiles.filter((tile) => tile.state === "completed").length;
      const evidenceCount = state.routeTiles.filter((tile) => tile.evidence?.state === "completed").length;
      $("routeStatus").innerHTML = `<strong>${paths.length}개 청크 · ${state.routeTiles.length}개 타일</strong><p>완료 ${completed} · 실패/미완료 ${state.routeTiles.length - completed}</p><p class="muted">경량 현장 점군 ${evidenceCount}개 타일 · 선택 타일만 전송 · 전체 PLY 미전송</p>`;
      const firstCompleted = state.routeTiles.findIndex((tile) => tile.state === "completed");
      if (firstCompleted < 0) throw new Error("열 수 있는 완료 tile이 없습니다.");
      await loadRouteTile(firstCompleted);
    } catch (error) {
      $("routeStatus").textContent = error.message;
      setApiStatus(`Route 오류: ${error.message}`, "error");
    } finally {
      $("loadRouteButton").disabled = false;
    }
  }

  async function loadRouteTile(index) {
    const tile = state.routeTiles[index];
    if (!tile) return;
    state.routeTileIndex = index;
    $("tileSelect").value = String(index);
    $("previousTile").disabled = index <= 0;
    $("nextTile").disabled = index >= state.routeTiles.length - 1;
    if (tile.state !== "completed") {
      $("routeStatus").textContent = `${tile.path} / ${tile.tileId}: ${tile.state} — 완료 산출물 없음`;
      return;
    }
    setApiStatus(`${tile.tileId} 로딩 중`, "pending");
    const endpoint = "/api/v1/route-datasets/tile";
    const artifact = (name) => api(`${endpoint}?${query({ path: tile.path, tile_id: tile.tileId, artifact: name })}`);
    const evidenceManifest = state.evidenceManifests[tile.path];
    const evidenceTile = (evidenceManifest?.tiles || []).find((item) => item.tile_id === tile.tileId) || null;
    const evidenceRequest = evidenceTile?.state === "completed"
      ? apiBinary(`/api/v1/route-datasets/evidence/tile?${query({ path: tile.path, tile_id: tile.tileId })}`)
        .then((payload) => viewerCore.parseRcev(payload))
        .catch((error) => ({ error: error.message }))
      : Promise.resolve(null);
    const [summary, surface, defects, geojson, enuGeojson, segments, evidence] = await Promise.all([
      artifact("summary"), artifact("surface"), artifact("defects"),
      artifact("defects_local_geojson"), artifact("defects_enu_geojson"), artifact("segments"),
      evidenceRequest
    ]);
    Object.assign(state, {
      summary,
      surface,
      defects,
      geojson,
      enuGeojson,
      segments,
      reviews: null,
      selectedDefect: leadDefect(defects),
      evidence: evidence?.error ? null : evidence,
      evidenceTile
    });
    state.vworld.renderedTileKey = null;
    state.vworld.selectedDefectId = null;
    $("emptyState").hidden = true;
    $("resultView").hidden = false;
    $("scenarioButton").disabled = true;
    $("reportLink").classList.add("disabled");
    $("reportLink").href = "#";
    $("loadedTileLabel").textContent = `${index + 1}/${state.routeTiles.length} · ${tile.tileId}`;
    $("evidenceStatus").textContent = evidence?.error
      ? `현장 점군 로딩 실패: ${evidence.error}`
      : evidence
        ? `실제 수집 포인트 ${evidence.count.toLocaleString("ko-KR")}점 · 손상 마스크 ${number(evidenceTile?.masked_point_count).toLocaleString("ko-KR")}점`
        : "이 타일에는 경량 현장 점군이 없습니다.";
    renderSummary();
    renderDefectTable();
    renderSegmentTable();
    renderCurrentView();
    renderDefectDetail();
    setApiStatus("Route tile 완료", "ok");
  }

  function renderSummary() {
    const results = state.summary.results || {};
    const scores = state.summary.scores || {};
    const coverage = state.summary.coverage || {};
    const quality = state.summary.quality || {};
    const source = state.summary.source || {};
    const minimumCoverage = number(state.summary.parameters?.detection?.minimum_valid_coverage_ratio, 0.5);
    const coverageRatio = number(coverage.valid_coverage_ratio);
    const supportedCoverageRatio = number(coverage.supported_coverage_ratio, coverageRatio);
    const excludedCells = number(quality.plausibility_excluded_cell_count);
    const excludedArea = number(coverage.plausibility_excluded_area_m2);
    const lowCoverage = coverageRatio < minimumCoverage;
    const manualReview = quality.manual_review_required === true;
    const roiFallback = quality.roi_applied === false;
    const reasons = [];
    if (lowCoverage) reasons.push(`표면 커버리지 ${format(coverageRatio * 100, 1)}%로 내부 최소 기준 ${format(minimumCoverage * 100, 1)}% 미달`);
    if (manualReview) reasons.push(`카메라 보정 상태 ${quality.calibration_status || "unknown"} · 수동 검수 필수`);
    if (roiFallback) reasons.push("도로 ROI 없음 · trajectory corridor 임시 사용");
    if (quality.multiview_filter_applied === true) reasons.push(`독립 시점 ${number(quality.minimum_independent_view_count, 2)}개 미만 점 ${number(quality.multiview_excluded_point_count).toLocaleString("ko-KR")}개 제외`);
    if (source.type === "mapping_bundle" && quality.multiview_evidence_available === false) reasons.push("다중 시점 evidence 없음 · 일시 물체 필터 미적용");
    if (excludedCells > 0) reasons.push(`비현실 잔차 셀 ${excludedCells.toLocaleString("ko-KR")}개를 결함 판정에서 제외`);
    const ready = !lowCoverage && !manualReview;
    $("qualityBanner").className = `quality-banner ${ready ? "ready" : "hold"}`;
    $("qualityVerdict").textContent = ready ? "검수 가능한 결과" : "자동 판정 보류";
    $("qualityBadge").textContent = ready ? "검수 가능" : "재검토 필요";
    $("qualityBadge").className = `status-pill ${ready ? "ok" : "error"}`;
    const sourcePath = String(source.workspace_relative_path || "").split("/").filter(Boolean);
    const sourceName = source.type === "mapping_bundle"
      ? `depth_map · ${sourcePath[sourcePath.length - 1] || source.tile_id || "mapping bundle"}`
      : `합성 도로 · ${source.profile || "profile"}`;
    $("qualitySource").textContent = `현재 결과: ${sourceName} · 구간 ${format(coverage.chainage_start_m, 1)}–${format(coverage.chainage_end_m, 1)} m`;
    $("qualityReasons").textContent = reasons.length
      ? `${reasons.join(" · ")}. 아래 후보는 현장/RGB 대조 전까지 확정 결함이 아닙니다.`
      : "기본 품질 기준을 통과했습니다. 후보별 현장 검수를 계속 진행하세요.";
    $("geometryScore").textContent = format(scores.geometry_score, 1);
    $("geometryGrade").textContent = `${ready ? "" : "판정 보류 · 참고 "}${scores.grade || "-"} 등급`;
    $("defectCandidateCount").textContent = number(results.defect_count).toLocaleString("ko-KR");
    $("candidateBreakdown").textContent = `포트홀 ${number(results.pothole_count)} · 러팅 ${number(results.rutting_count)} · 범프 ${number(results.bump_count)}`;
    $("maxPotholeDepth").textContent = verticalDistance(results.max_pothole_depth_m);
    $("maxRutDepth").textContent = verticalDistance(results.max_rut_depth_m);
    $("coverageRatio").textContent = `${format(coverageRatio * 100, 1)}%`;
    $("coverageGuide").textContent = `원시 지지 ${format(supportedCoverageRatio * 100, 1)}% · 내부 기준 ${format(minimumCoverage * 100, 0)}% ${lowCoverage ? "미달" : "이상"}`;
    $("excludedSurface").textContent = excludedCells.toLocaleString("ko-KR");
    $("excludedSurfaceGuide").textContent = excludedCells > 0
      ? `${format(excludedArea, 2)} ㎡ · 낮음 ${number(quality.plausibility_excluded_low_cell_count)} / 높음 ${number(quality.plausibility_excluded_high_cell_count)}`
      : "제외 셀 없음";
    $("coverageCard").classList.toggle("danger", lowCoverage);
    $("scoreCard").classList.toggle("muted-card", !ready);
    $("analyzedPoints").textContent = number(quality.analyzed_point_count).toLocaleString("ko-KR");
    $("samplingInfo").textContent = quality.multiview_filter_applied
      ? `다중 시점 ${number(quality.multiview_input_point_count).toLocaleString("ko-KR")}점 → ${number(quality.multiview_retained_point_count).toLocaleString("ko-KR")}점`
      : quality.point_sampling_applied
        ? `원본 ${number(quality.original_point_count).toLocaleString("ko-KR")}점에서 표본 추출`
        : "전체 입력 사용";
    $("locationSummary").innerHTML = `<strong>${format(coverage.chainage_start_m, 0)}–${format(coverage.chainage_end_m, 0)} m 구간</strong><span>${sourceName}</span>`;
    $("findingSummary").innerHTML = `<strong>손상 후보 ${number(results.defect_count).toLocaleString("ko-KR")}건</strong><span>포트홀 ${number(results.pothole_count)} · 러팅 ${number(results.rutting_count)} · 범프 ${number(results.bump_count)}</span>`;
    $("actionSummary").innerHTML = ready
      ? "<strong>후보별 현장 확인</strong><span>지도 마스크와 측정 단면을 대조한 뒤 보수 범위를 실측하세요.</span>"
      : "<strong>자동 확정하지 않음</strong><span>데이터 품질을 보완하고 현장·RGB 대조 후 보수 여부를 결정하세요.</span>";
  }

  function defectPrimaryMetric(defect) {
    const m = defect.metrics || {};
    if (defect.defect_type === "pothole") return `${verticalDistance(m.max_depth_m)} · ${format(m.area_m2, 2)} ㎡`;
    if (defect.defect_type === "rutting") return `${verticalDistance(m.max_depth_m)} · ${format(m.length_m, 1)} m`;
    if (defect.defect_type === "manhole_step_candidate" || defect.defect_type === "step_anomaly") return `${verticalDistance(m.step_height_m)} · edge ${format(m.edge_length_m, 1)} m`;
    if (defect.defect_type === "ponding_screening_proxy") return `${verticalDistance(m.potential_retention_depth_m)} · ${format(m.potential_retention_area_m2, 2)} ㎡`;
    return `${verticalDistance(m.max_height_m)} · ${format(m.area_m2, 2)} ㎡`;
  }

  function defectName(type) {
    return ({ pothole: "포트홀", rutting: "러팅", bump: "범프", manhole_step_candidate: "맨홀 단차 후보", step_anomaly: "단차 후보", ponding_screening_proxy: "물고임 screening" })[type] || type;
  }

  function severityName(value) {
    return ({ high: "우선 확인", medium: "계획 확인", low: "관찰" })[value] || value || "확인 필요";
  }

  function defectAction(defect) {
    if (defect.severity === "high") return "통행 안전을 먼저 확인하고 보수 경계·깊이를 현장에서 실측하세요.";
    if (defect.severity === "medium") return "다음 현장 점검 대상에 포함하고 확대 여부를 기록하세요.";
    return "정기 점검 목록에 등록하고 다음 조사와 변화량을 비교하세요.";
  }

  function renderDefectTable() {
    const body = $("defectTable").querySelector("tbody");
    body.innerHTML = "";
    const displayed = visibleDefects();
    $("defectCountLabel").textContent = `${displayed.length}/${state.defects.length}개`;
    for (const defect of displayed) {
      const row = document.createElement("tr");
      row.dataset.defectId = defect.defect_id;
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.classList.toggle("selected", defect.defect_id === state.selectedDefect?.defect_id);
      const reviewState = state.reviews?.defects?.[defect.defect_id]?.state || (state.sourceMode === "job" ? "pending" : "N/A");
      row.innerHTML = `<td>${defect.defect_id}</td><td>${defectName(defect.defect_type)}</td><td>${defect.lane_id || defect.road_zone || "unknown"}</td><td>${format(defect.chainage_m, 1)} m</td><td class="severity-${defect.severity}">${defect.severity}</td><td>${defectPrimaryMetric(defect)}</td><td>${format(number(defect.confidence) * 100, 0)}%</td><td>${reviewState}</td>`;
      row.addEventListener("click", () => selectDefect(defect.defect_id));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") selectDefect(defect.defect_id);
      });
      body.appendChild(row);
    }
  }

  function renderSegmentTable() {
    const body = $("segmentTable").querySelector("tbody");
    body.innerHTML = "";
    for (const segment of state.segments) {
      const rut = Math.max(number(segment.max_left_rut_depth_m), number(segment.max_right_rut_depth_m));
      const row = document.createElement("tr");
      row.innerHTML = `<td>${format(segment.chainage_start_m, 0)}–${format(segment.chainage_end_m, 0)} m</td><td>${segment.lane_id || "전체"}</td><td>${format(number(segment.valid_coverage_ratio) * 100, 0)}%</td><td>${segment.pothole_count} / ${verticalDistance(segment.max_pothole_depth_m)}</td><td>${verticalDistance(rut)}</td><td>${format(segment.geometry_score, 1)}</td><td>${segment.grade}</td>`;
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

  function layerVisible(properties) {
    return viewerCore.defectVisible(properties, visibilityOptions());
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

    const gradeColors = { A: "#42d392", B: "#7ce3ae", C: "#ffd166", D: "#ff9f43", E: "#ff6b6b" };
    for (const segment of state.segments) {
      const startX = xOf(Math.max(sMin, number(segment.chainage_start_m, sMin)));
      const endX = xOf(Math.min(sMax, number(segment.chainage_end_m, sMax)));
      ctx.fillStyle = gradeColors[segment.grade] || "#6f879b";
      ctx.fillRect(startX, margin.top, Math.max(2, endX - startX), 7);
      if ($("showLowCoverage").checked && number(segment.valid_coverage_ratio, 1) < 0.5) {
        ctx.fillStyle = "rgba(255,107,107,.20)";
        ctx.fillRect(startX, margin.top, Math.max(2, endX - startX), plotHeight);
      }
    }

    const fills = { pothole: "rgba(55,143,255,.42)", rutting: "rgba(248,197,65,.32)", bump: "rgba(255,91,91,.38)", manhole_step_candidate: "rgba(201,122,255,.38)", step_anomaly: "rgba(201,122,255,.38)", ponding_screening_proxy: "rgba(60,210,220,.34)" };
    const strokes = { pothole: "#65afff", rutting: "#ffd166", bump: "#ff7979", manhole_step_candidate: "#d89cff", step_anomaly: "#d89cff", ponding_screening_proxy: "#51d3df" };
    for (const feature of state.geojson.features || []) {
      const type = feature.properties.defect_type;
      if (!layerVisible(feature.properties)) continue;
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

  function prepareCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(600, Math.floor(rect.width * ratio));
    canvas.height = Math.max(360, Math.floor(rect.height * ratio));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const width = canvas.width / ratio;
    const height = canvas.height / ratio;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#050c15";
    ctx.fillRect(0, 0, width, height);
    return { ctx, width, height };
  }

  function renderPerspective() {
    if (!state.surface) return;
    const { ctx, width, height } = prepareCanvas($("perspectiveCanvas"));
    const s = state.surface.s_values_m;
    const t = state.surface.t_values_m;
    const residual = state.surface.residual_mm;
    const sStep = Math.max(1, Math.ceil(s.length / 120));
    const tStep = Math.max(1, Math.ceil(t.length / 44));
    const exaggeration = number($("exaggeration").value, 1);
    for (let i = 0; i < s.length - sStep; i += sStep) {
      for (let j = 0; j < t.length - tStep; j += tStep) {
        const sr0 = i / Math.max(1, s.length - 1), sr1 = (i + sStep) / Math.max(1, s.length - 1);
        const tr0 = j / Math.max(1, t.length - 1), tr1 = (j + tStep) / Math.max(1, t.length - 1);
        const values = [residual[i][j], residual[i + sStep][j], residual[i + sStep][j + tStep], residual[i][j + tStep]];
        const points = [
          viewerCore.perspectivePoint(sr0, tr0, values[0], width, height, exaggeration),
          viewerCore.perspectivePoint(sr1, tr0, values[1], width, height, exaggeration),
          viewerCore.perspectivePoint(sr1, tr1, values[2], width, height, exaggeration),
          viewerCore.perspectivePoint(sr0, tr1, values[3], width, height, exaggeration)
        ];
        const finite = values.filter((value) => value !== null && Number.isFinite(value));
        const mean = finite.length ? finite.reduce((sum, value) => sum + value, 0) / finite.length : null;
        ctx.beginPath();
        points.forEach((point, index) => index ? ctx.lineTo(point[0], point[1]) : ctx.moveTo(point[0], point[1]));
        ctx.closePath();
        ctx.fillStyle = residualColor(mean, exaggeration);
        ctx.fill();
      }
    }
    const sMin = s[0], sMax = s[s.length - 1], tMin = t[0], tMax = t[t.length - 1];
    for (const defect of visibleDefects()) {
      const sr = (defect.chainage_m - sMin) / Math.max(1e-9, sMax - sMin);
      const tr = (defect.lateral_offset_m - tMin) / Math.max(1e-9, tMax - tMin);
      const point = viewerCore.perspectivePoint(sr, tr, 0, width, height, exaggeration);
      ctx.beginPath(); ctx.arc(point[0], point[1], defect === state.selectedDefect ? 7 : 4, 0, Math.PI * 2);
      ctx.fillStyle = defect === state.selectedDefect ? "#fff" : "#ffd166"; ctx.fill();
    }
    ctx.fillStyle = "#9bb4c7"; ctx.font = "12px system-ui";
    ctx.fillText(`preview grid only · Z ${exaggeration}× · 전체 PLY 미로딩`, 18, 24);
  }

  function renderMap() {
    const { ctx, width, height } = prepareCanvas($("mapCanvas"));
    const adapter = viewerCore.mapAdapterStatus($("mapAdapter").value, runtimeConfig, state.enuGeojson?.origin);
    $("adapterNotice").textContent = `${adapter.label}: ${adapter.message}`;
    const features = (state.enuGeojson?.features || []).filter((feature) => layerVisible(feature.properties));
    const polygonPoints = features.flatMap((feature) => feature.geometry?.coordinates?.[0] || []);
    const evidence = state.evidence;
    if (!polygonPoints.length && !evidence?.count) {
      ctx.fillStyle = "#8fa9bf"; ctx.font = "14px system-ui"; ctx.fillText("표시 가능한 local ENU evidence가 없습니다.", 24, 42);
      return;
    }
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const point of polygonPoints) {
      minX = Math.min(minX, number(point[0])); maxX = Math.max(maxX, number(point[0]));
      minY = Math.min(minY, number(point[1])); maxY = Math.max(maxY, number(point[1]));
    }
    for (let index = 0; index < number(evidence?.count); index += 1) {
      const x = evidence.positions[index * 3], y = evidence.positions[index * 3 + 1];
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
    const margin = 42;
    const xOf = (value) => margin + (value - minX) / Math.max(1e-9, maxX - minX) * (width - 2 * margin);
    const yOf = (value) => height - margin - (value - minY) / Math.max(1e-9, maxY - minY) * (height - 2 * margin);
    ctx.strokeStyle = "#29415a"; ctx.lineWidth = 1;
    for (let index = 0; index <= 8; index++) {
      const x = margin + index * (width - 2 * margin) / 8;
      const y = margin + index * (height - 2 * margin) / 8;
      ctx.beginPath(); ctx.moveTo(x, margin); ctx.lineTo(x, height - margin); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(margin, y); ctx.lineTo(width - margin, y); ctx.stroke();
    }
    if (evidence?.count) {
      const contextStep = Math.max(1, Math.ceil(evidence.count / 30000));
      ctx.globalAlpha = 0.72;
      for (let index = 0; index < evidence.count; index += contextStep) {
        if (evidence.defectClasses[index] !== 0) continue;
        const colorOffset = index * 3;
        ctx.fillStyle = `rgb(${evidence.colors[colorOffset]},${evidence.colors[colorOffset + 1]},${evidence.colors[colorOffset + 2]})`;
        ctx.fillRect(xOf(evidence.positions[colorOffset]), yOf(evidence.positions[colorOffset + 1]), 1.7, 1.7);
      }
      ctx.globalAlpha = 1;
      const maskColors = { 1: "#f04452", 2: "#ff9f1c", 3: "#bc6ff1", 4: "#00c2d7" };
      for (let index = 0; index < evidence.count; index += 1) {
        const defectClass = evidence.defectClasses[index];
        if (!defectClass) continue;
        const defectId = viewerCore.evidenceDefectId(state.evidenceTile, evidence.defectIndices[index]);
        const defect = state.defects.find((item) => item.defect_id === defectId);
        if (defect && !viewerCore.defectVisible(defect, visibilityOptions())) continue;
        const positionOffset = index * 3;
        const selected = defectId && defectId === state.selectedDefect?.defect_id;
        ctx.beginPath();
        ctx.arc(xOf(evidence.positions[positionOffset]), yOf(evidence.positions[positionOffset + 1]), selected ? 3.5 : 2.3, 0, Math.PI * 2);
        ctx.fillStyle = selected ? "#ffffff" : (maskColors[defectClass] || "#f04452");
        ctx.fill();
      }
    }
    const polygonFills = { pothole: "rgba(240,68,82,.42)", rutting: "rgba(255,159,28,.38)", bump: "rgba(188,111,241,.38)" };
    const polygonStrokes = { pothole: "#ff6673", rutting: "#ffc166", bump: "#cf8cff" };
    for (const feature of features) {
      const ring = feature.geometry.coordinates[0] || [];
      ctx.beginPath();
      ring.forEach((point, index) => index ? ctx.lineTo(xOf(point[0]), yOf(point[1])) : ctx.moveTo(xOf(point[0]), yOf(point[1])));
      ctx.closePath();
      ctx.fillStyle = feature.id === state.selectedDefect?.defect_id ? "rgba(255,255,255,.50)" : (polygonFills[feature.properties.defect_type] || "rgba(0,194,215,.35)");
      ctx.strokeStyle = feature.id === state.selectedDefect?.defect_id ? "#ffffff" : (polygonStrokes[feature.properties.defect_type] || "#00c2d7");
      ctx.lineWidth = feature.id === state.selectedDefect?.defect_id ? 3 : 2; ctx.fill(); ctx.stroke();
    }
    ctx.fillStyle = "#9bb4c7"; ctx.font = "12px system-ui";
    ctx.fillText(`${adapter.label} · 실제 점 ${number(evidence?.count).toLocaleString("ko-KR")}개 · 밝은 색은 손상 마스크`, 18, 24);
  }

  function prepareEvidenceWgs84() {
    const evidence = state.evidence;
    const origin = state.enuGeojson?.origin;
    if (!evidence?.count || !origin) return null;
    if (evidence.wgs84Positions) return evidence;
    const wgs84Positions = new Float64Array(evidence.count * 3);
    const heightStats = new Map();
    let allUp = 0;
    for (let index = 0; index < evidence.count; index += 1) {
      const offset = index * 3;
      const enu = [evidence.positions[offset], evidence.positions[offset + 1], evidence.positions[offset + 2]];
      const converted = viewerCore.enuToWgs84(enu, origin);
      wgs84Positions.set(converted, offset);
      allUp += enu[2];
      const defectId = viewerCore.evidenceDefectId(state.evidenceTile, evidence.defectIndices[index]);
      if (defectId && evidence.defectClasses[index]) {
        const stats = heightStats.get(defectId) || { sum: 0, count: 0 };
        stats.sum += enu[2]; stats.count += 1; heightStats.set(defectId, stats);
      }
    }
    evidence.wgs84Positions = wgs84Positions;
    evidence.meanUpM = allUp / Math.max(1, evidence.count);
    evidence.defectUpM = new Map([...heightStats].map(([key, value]) => [key, value.sum / value.count]));
    return evidence;
  }

  function cesiumDefectColor(C, type, selected) {
    if (selected) return C.Color.WHITE;
    if (type === "pothole") return C.Color.fromBytes(240, 68, 82, 255);
    if (type === "rutting") return C.Color.fromBytes(255, 159, 28, 255);
    if (type === "bump") return C.Color.fromBytes(188, 111, 241, 255);
    return C.Color.fromBytes(0, 194, 215, 255);
  }

  async function ensureVWorldMap() {
    if (state.vworld.viewer) return state.vworld.viewer;
    if (state.vworld.loading) return state.vworld.loading;
    const adapter = viewerCore.mapAdapterStatus("vworld", runtimeConfig, state.enuGeojson?.origin);
    if (!adapter.ready) throw new Error(adapter.message);
    state.vworld.loading = (async () => {
      if (!window.vw) throw new Error("VWorld SDK runtime is unavailable");
      if (String(window.vworldIsValid) !== "true") {
        throw new Error(window.vworldErrMsg || "VWorld key/domain authentication failed");
      }
      const origin = state.enuGeojson.origin;
      return new Promise((resolve, reject) => {
        let finished = false;
        let poll = null;
        let timeout = null;
        const cleanup = () => {
          if (poll !== null) window.clearInterval(poll);
          if (timeout !== null) window.clearTimeout(timeout);
        };
        const resolveViewer = () => {
          if (finished || !window.ws3d?.viewer) return;
          finished = true;
          cleanup();
          state.vworld.viewer = window.ws3d.viewer;
          resolve(state.vworld.viewer);
        };
        timeout = window.setTimeout(() => {
          if (finished) return;
          finished = true;
          cleanup();
          reject(new Error("VWorld initialization timed out"));
        }, 18000);
        poll = window.setInterval(resolveViewer, 100);
        window.vw.ws3dInitCallBack = resolveViewer;
        try {
          state.vworld.map = new window.vw.Map();
          state.vworld.map.setOption({
            mapId: "vworldMap",
            initPosition: new window.vw.CameraPosition(
              new window.vw.CoordZ(origin.longitude_deg, origin.latitude_deg, 350),
              new window.vw.Direction(0, -75, 0)
            ),
            logo: true,
            navigation: true
          });
          state.vworld.map.start();
          resolveViewer();
        } catch (error) {
          finished = true;
          cleanup();
          reject(error);
        }
      });
    })();
    try {
      return await state.vworld.loading;
    } catch (error) {
      state.vworld.loading = null;
      throw error;
    }
  }

  async function renderVWorld() {
    const renderToken = ++state.vworld.renderToken;
    const adapter = viewerCore.mapAdapterStatus("vworld", runtimeConfig, state.enuGeojson?.origin);
    $("adapterNotice").textContent = `${adapter.label}: ${adapter.message}`;
    if (!adapter.ready) {
      $("vworldMap").hidden = true;
      $("mapCanvas").hidden = false;
      renderMap();
      return;
    }
    try {
      const viewer = await ensureVWorldMap();
      if (renderToken !== state.vworld.renderToken || $("viewMode").value !== "map" || $("mapAdapter").value !== "vworld") return;
      const C = window.Cesium;
      if (!C) throw new Error("VWorld Cesium runtime is unavailable");
      if (!state.vworld.dataSource) {
        state.vworld.dataSource = new C.CustomDataSource("road-condition-defects");
        await viewer.dataSources.add(state.vworld.dataSource);
      }
      if (!state.vworld.selectionListenerInstalled && viewer.selectedEntityChanged?.addEventListener) {
        viewer.selectedEntityChanged.addEventListener((entity) => {
          const defectId = entity?.id;
          if (defectId && state.defects.some((item) => item.defect_id === defectId)) selectDefect(defectId);
        });
        state.vworld.selectionListenerInstalled = true;
      }
      const entities = state.vworld.dataSource.entities;
      entities.removeAll();
      if (!state.vworld.pointCollection) {
        state.vworld.pointCollection = viewer.scene.primitives.add(new C.PointPrimitiveCollection());
      } else {
        state.vworld.pointCollection.removeAll();
      }
      const evidence = prepareEvidenceWgs84();
      const visualLiftM = 0.15;
      let renderedPoints = 0;
      if (evidence) {
        const contextStep = Math.max(1, Math.ceil(evidence.count / 35000));
        for (let index = 0; index < evidence.count; index += 1) {
          const defectClass = evidence.defectClasses[index];
          if (!defectClass && index % contextStep !== 0) continue;
          const defectId = viewerCore.evidenceDefectId(state.evidenceTile, evidence.defectIndices[index]);
          const defect = state.defects.find((item) => item.defect_id === defectId);
          if (defect && !viewerCore.defectVisible(defect, visibilityOptions())) continue;
          const selected = defectId && defectId === state.selectedDefect?.defect_id;
          const offset = index * 3;
          const color = defectClass
            ? cesiumDefectColor(C, defect?.defect_type, selected)
            : C.Color.fromBytes(
              Math.max(35, evidence.colors[offset]),
              Math.max(35, evidence.colors[offset + 1]),
              Math.max(35, evidence.colors[offset + 2]),
              190
            );
          state.vworld.pointCollection.add({
            position: C.Cartesian3.fromDegrees(
              evidence.wgs84Positions[offset],
              evidence.wgs84Positions[offset + 1],
              evidence.wgs84Positions[offset + 2] + visualLiftM
            ),
            color,
            pixelSize: selected ? 7 : defectClass ? 5 : 2,
            outlineColor: selected ? C.Color.BLACK : color,
            outlineWidth: selected ? 1.5 : 0,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
            id: defectId || undefined
          });
          renderedPoints += 1;
        }
      }
      const defaultUpM = evidence?.meanUpM || 0;
      for (const feature of state.enuGeojson?.features || []) {
        if (!layerVisible(feature.properties)) continue;
        const ring = feature.geometry?.coordinates?.[0] || [];
        if (ring.length < 3) continue;
        const selected = feature.id === state.selectedDefect?.defect_id;
        const upM = evidence?.defectUpM?.get(feature.id) ?? defaultUpM;
        const cartesianRing = ring.map((point) => {
          const converted = viewerCore.enuToWgs84([point[0], point[1], upM + visualLiftM], state.enuGeojson.origin);
          return C.Cartesian3.fromDegrees(converted[0], converted[1], converted[2]);
        });
        const color = cesiumDefectColor(C, feature.properties.defect_type, selected);
        entities.add({
          id: feature.id,
          name: `${defectName(feature.properties.defect_type)} · ${feature.id}`,
          properties: feature.properties,
          polygon: {
            hierarchy: new C.PolygonHierarchy(cartesianRing),
            perPositionHeight: true,
            material: color.withAlpha(selected ? 0.66 : 0.40),
            outline: true,
            outlineColor: color
          }
        });
      }
      const currentTile = state.routeTiles[state.routeTileIndex];
      const tileKey = currentTile ? `${currentTile.path}/${currentTile.tileId}` : `job/${state.jobId}`;
      const tileChanged = state.vworld.renderedTileKey !== tileKey;
      const selectedChanged = state.vworld.selectedDefectId !== state.selectedDefect?.defect_id;
      if (evidence && (tileChanged || selectedChanged)) {
        let longitude, latitude, height;
        if (!tileChanged && state.selectedDefect) {
          const selectedIndices = [];
          for (let index = 0; index < evidence.count; index += 1) {
            if (viewerCore.evidenceDefectId(state.evidenceTile, evidence.defectIndices[index]) === state.selectedDefect.defect_id) selectedIndices.push(index);
          }
          const targets = selectedIndices.length ? selectedIndices : [...Array(evidence.count).keys()];
          longitude = targets.reduce((sum, index) => sum + evidence.wgs84Positions[index * 3], 0) / targets.length;
          latitude = targets.reduce((sum, index) => sum + evidence.wgs84Positions[index * 3 + 1], 0) / targets.length;
          height = targets.reduce((sum, index) => sum + evidence.wgs84Positions[index * 3 + 2], 0) / targets.length;
        } else {
          longitude = 0; latitude = 0; height = 0;
          for (let index = 0; index < evidence.count; index += 1) {
            longitude += evidence.wgs84Positions[index * 3];
            latitude += evidence.wgs84Positions[index * 3 + 1];
            height += evidence.wgs84Positions[index * 3 + 2];
          }
          longitude /= evidence.count; latitude /= evidence.count; height /= evidence.count;
        }
        viewer.camera.flyTo({
          destination: C.Cartesian3.fromDegrees(longitude, latitude, height + (tileChanged ? 28 : 13)),
          orientation: { heading: 0, pitch: C.Math.toRadians(tileChanged ? -88 : -78), roll: 0 },
          duration: 0.7
        });
      } else if (!evidence && entities.values.length && tileChanged) {
        await viewer.flyTo(state.vworld.dataSource, { duration: 0.8 });
      }
      state.vworld.renderedTileKey = tileKey;
      state.vworld.selectedDefectId = state.selectedDefect?.defect_id || null;
      $("adapterNotice").textContent = `VWorld 연결됨 · 실제 포인트 ${renderedPoints.toLocaleString("ko-KR")}점 · 손상 마스크 ${entities.values.length}건 · 지형 가림 방지 표시 적용`;
    } catch (error) {
      $("vworldMap").hidden = true;
      $("mapCanvas").hidden = false;
      renderMap();
      $("adapterNotice").textContent = `VWorld 실패: ${error.message} · local ENU fallback`;
    }
  }

  function renderCurrentView() {
    const mode = $("viewMode").value;
    $("surfaceCanvas").hidden = mode !== "plan";
    $("perspectiveCanvas").hidden = mode !== "perspective";
    const useVWorld = mode === "map" && $("mapAdapter").value === "vworld";
    $("mapCanvas").hidden = mode !== "map" || useVWorld;
    $("vworldMap").hidden = !useVWorld;
    $("mapAdapter").disabled = mode !== "map";
    $("adapterNotice").textContent = mode === "map" ? viewerCore.mapAdapterStatus($("mapAdapter").value).message : "";
    if (mode === "plan") renderSurface();
    else if (mode === "perspective") renderPerspective();
    else if (useVWorld) renderVWorld();
    else renderMap();
    $("axisNote").textContent = mode === "plan"
      ? "가로: 진행거리 s(m) · 세로: 횡방향 t(m) · 색상: 기준면 대비 높이 잔차(cm) · 상단 색 띠: 구간 등급"
      : mode === "perspective"
        ? "잔차 preview grid의 경량 3D evidence · 전체 PLY를 브라우저로 보내지 않음"
      : useVWorld
          ? "VWorld 지도 위 실제 수집 점군 · 빨강 포트홀 · 주황 러팅 · 보라 범프 · 마스크는 현장 검수 전 후보"
          : "Local ENU 실제 수집 점군 · 외부 지도 실패 시 오프라인 대체 화면";
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
    const feature = (state.geojson.features || []).find((candidate) => layerVisible(candidate.properties) && pointInPolygon([s, t], candidate.geometry.coordinates[0] || []));
    if (feature) selectDefect(feature.id);
  }

  function selectDefect(defectId) {
    state.selectedDefect = state.defects.find((item) => item.defect_id === defectId) || null;
    document.querySelectorAll("#defectTable tbody tr").forEach((row) => row.classList.toggle("selected", row.dataset.defectId === defectId));
    renderCurrentView();
    renderDefectDetail();
  }

  function renderDefectDetail() {
    const defect = state.selectedDefect;
    if (!defect) {
      $("selectedBadge").textContent = "미선택";
      $("defectDetail").innerHTML = '<span class="muted">지도 또는 아래 표에서 후보를 선택하세요.</span>';
      $("profileChart").innerHTML = "";
      $("longitudinalChart").innerHTML = "";
      $("reviewControls").hidden = true;
      return;
    }
    $("selectedBadge").textContent = `${defectName(defect.defect_type)} · ${severityName(defect.severity)}`;
    const flags = (defect.quality_flags || []).length ? defect.quality_flags.join(", ") : "없음";
    const review = state.reviews?.defects?.[defect.defect_id];
    $("defectDetail").innerHTML = `<div class="plain-finding"><span>무엇이 보였나</span><strong>${defectName(defect.defect_type)} 형태 · ${defectPrimaryMetric(defect)}</strong></div><div class="plain-finding"><span>어디인가</span><strong>조사 시작점에서 ${format(defect.chainage_m, 1)} m · 도로 중심 기준 ${format(defect.lateral_offset_m, 1)} m</strong></div><div class="plain-finding action"><span>다음 조치</span><strong>${defectAction(defect)}</strong></div><details class="technical-note"><summary>측정 상세 보기</summary><p>ID ${defect.defect_id} · 차로/구역 ${defect.lane_id || defect.road_zone || "unknown"}</p><p>후보 내부 신뢰도 ${format(number(defect.confidence) * 100, 0)}% · 품질 플래그 ${flags}</p><p>RGB 사진 증거 N/A · 연결된 frame evidence 없음</p><p>검수 ${review?.state || "N/A"}${review ? ` · version ${review.version}` : ""}</p></details>`;
    $("reviewControls").hidden = state.sourceMode !== "job" || !review;
    $("reviewSeverity").value = review?.current_annotation?.severity || defect.severity || "low";
    $("reviewStatus").textContent = review ? `raw prediction 보존 · 현재 version ${review.version}` : "";
    renderProfile(defect.chainage_m, defect.lateral_offset_m);
  }

  async function submitReview() {
    const defect = state.selectedDefect;
    const review = defect && state.reviews?.defects?.[defect.defect_id];
    if (!state.jobId || state.sourceMode !== "job" || !defect || !review) return;
    const actor = $("reviewActor").value.trim();
    const reason = $("reviewReason").value.trim();
    if (!actor || !reason) {
      $("reviewStatus").textContent = "검수자와 사유를 입력하세요.";
      return;
    }
    const action = $("reviewAction").value;
    const payload = { actor, action, reason, expected_version: review.version };
    if (action === "modified") {
      payload.after = { ...review.current_annotation, severity: $("reviewSeverity").value };
    }
    try {
      $("submitReview").disabled = true;
      const result = await api(`/api/v1/jobs/${state.jobId}/reviews/${encodeURIComponent(defect.defect_id)}`, {
        method: "POST",
        body: JSON.stringify(payload)
      });
      state.reviews.defects[defect.defect_id] = result.record;
      state.reviews.events.push(result.event);
      $("reviewReason").value = "";
      renderDefectTable();
      renderDefectDetail();
      $("reviewStatus").textContent = `${result.event.action} 저장 · version ${result.record.version}`;
    } catch (error) {
      $("reviewStatus").textContent = `저장 실패: ${error.message}`;
    } finally {
      $("submitReview").disabled = false;
    }
  }

  function renderProfile(chainage, lateralOffset) {
    const svg = $("profileChart");
    svg.innerHTML = "";
    const s = state.surface.s_values_m;
    let rowIndex = 0;
    let best = Infinity;
    s.forEach((value, index) => { const distance = Math.abs(value - chainage); if (distance < best) { best = distance; rowIndex = index; } });
    const t = state.surface.t_values_m;
    const residual = state.surface.residual_mm[rowIndex].map((value) => value === null ? null : value / 10);
    const width = 640, height = 220, margin = { left: 48, right: 14, top: 18, bottom: 34 };
    const finite = residual.filter((value) => value !== null && Number.isFinite(value));
    const maxAbs = Math.max(4, ...finite.map((value) => Math.abs(value)));
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
    label.setAttribute("x", margin.left); label.setAttribute("y", height - 9); label.setAttribute("fill", "#91a9bd"); label.setAttribute("font-size", "11"); label.textContent = `횡단면 @ s=${format(s[rowIndex], 1)} m · 높이 잔차(cm)`; svg.appendChild(label);

    const longitudinalSvg = $("longitudinalChart");
    longitudinalSvg.innerHTML = "";
    let columnIndex = 0;
    let lateralBest = Infinity;
    t.forEach((value, index) => { const distance = Math.abs(value - lateralOffset); if (distance < lateralBest) { lateralBest = distance; columnIndex = index; } });
    const longitudinal = state.surface.residual_mm.map((row) => row[columnIndex] === null ? null : row[columnIndex] / 10);
    const longitudinalFinite = longitudinal.filter((value) => value !== null && Number.isFinite(value));
    const longitudinalMax = Math.max(4, ...longitudinalFinite.map((value) => Math.abs(value)));
    const lx = (value) => margin.left + (value - s[0]) / Math.max(1e-9, s[s.length - 1] - s[0]) * (width - margin.left - margin.right);
    const ly = (value) => margin.top + (longitudinalMax - value) / (2 * longitudinalMax) * (height - margin.top - margin.bottom);
    const longAxis = document.createElementNS(ns, "path");
    longAxis.setAttribute("d", `M${margin.left},${ly(0)} H${width - margin.right} M${margin.left},${margin.top} V${height - margin.bottom}`);
    longAxis.setAttribute("stroke", "#5e7890"); longAxis.setAttribute("fill", "none"); longitudinalSvg.appendChild(longAxis);
    const longitudinalPoints = [];
    longitudinal.forEach((value, index) => { if (value !== null && Number.isFinite(value)) longitudinalPoints.push(`${lx(s[index])},${ly(value)}`); });
    const longLine = document.createElementNS(ns, "polyline");
    longLine.setAttribute("points", longitudinalPoints.join(" ")); longLine.setAttribute("fill", "none"); longLine.setAttribute("stroke", "#7ce3ae"); longLine.setAttribute("stroke-width", "2"); longitudinalSvg.appendChild(longLine);
    const longLabel = document.createElementNS(ns, "text");
    longLabel.setAttribute("x", margin.left); longLabel.setAttribute("y", height - 9); longLabel.setAttribute("fill", "#91a9bd"); longLabel.setAttribute("font-size", "11"); longLabel.textContent = `종단면 @ t=${format(t[columnIndex], 1)} m · 높이 잔차(cm)`; longitudinalSvg.appendChild(longLabel);
  }

  function cycleDefect(direction) {
    const displayed = visibleDefects();
    if (!displayed.length) return;
    let index = displayed.findIndex((item) => item.defect_id === state.selectedDefect?.defect_id);
    index = (index + direction + displayed.length) % displayed.length;
    selectDefect(displayed[index].defect_id);
  }

  async function compareJob() {
    const jobId = $("compareJob").value;
    if (!jobId || !state.summary) return;
    try {
      const comparison = await api(`/api/v1/jobs/${jobId}/summary`);
      const currentScore = number(state.summary.scores?.geometry_score);
      const comparisonScore = number(comparison.scores?.geometry_score);
      const currentDefects = number(state.summary.results?.defect_count);
      const comparisonDefects = number(comparison.results?.defect_count);
      const currentCoverage = number(state.summary.coverage?.valid_coverage_ratio);
      const comparisonCoverage = number(comparison.coverage?.valid_coverage_ratio);
      $("comparisonResult").innerHTML = `<strong>점수 Δ ${format(comparisonScore - currentScore, 1)}</strong><p>현재 ${format(currentScore, 1)} → 비교 ${format(comparisonScore, 1)}</p><p>결함 Δ ${comparisonDefects - currentDefects} · 커버리지 Δ ${format((comparisonCoverage - currentCoverage) * 100, 1)}pp</p><p class="muted">서로 다른 조사 정합 여부는 자동 판정하지 않습니다.</p>`;
    } catch (error) {
      $("comparisonResult").textContent = error.message;
    }
  }

  function handleViewerKey(event) {
    if (event.key.toLowerCase() === "n") cycleDefect(1);
    if (event.key.toLowerCase() === "p") cycleDefect(-1);
  }

  function installContextRecovery(canvas) {
    canvas.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
      $("renderRecovery").hidden = false;
    });
    canvas.addEventListener("webglcontextrestored", () => {
      $("renderRecovery").hidden = true;
      renderCurrentView();
    });
  }

  async function calculateScenario() {
    if (!state.jobId) return;
    try {
      const budget = number($("maintenanceBudget").value, 5000000);
      const result = await api(`/api/v1/jobs/${state.jobId}/scenarios/v2`, {
        method: "POST",
        body: JSON.stringify({
          include_types: ["pothole", "rutting", "bump"],
          budget_krw: budget,
          comparison_budgets_krw: [Math.round(budget * 0.5), Math.round(budget * 1.5)],
          goal: "risk_screening_priority"
        })
      });
      const screening = result.budget_screening;
      $("scenarioResult").innerHTML = `<span>알려진 비용 합계</span><strong>${number(screening.priced_total_krw).toLocaleString("ko-KR")}원</strong><p>선정 ${screening.selected_count}건 · 보류 ${screening.deferred_count}건</p><p>현재 ${format(result.score_projection.current_internal_geometry_score, 1)}점 → planning estimate ${format(result.score_projection.post_treatment_internal_score_planning_estimate, 1)}점</p><p class="muted">전체 비용 N/A · 열화율 N/A · 결정적 위험 screening이며 최적화/실제 예측이 아닙니다.</p>`;
    } catch (error) {
      $("scenarioResult").textContent = error.message;
    }
  }

  async function boot() {
    try {
      await api("/api/v1/health");
      setApiStatus("API 정상", "ok");
      const jobs = await refreshJobs();
      const requestedRoutes = pageParams.getAll("route").map((value) => value.trim()).filter(Boolean);
      if (requestedRoutes.length) {
        $("routePaths").value = requestedRoutes.join("\n");
        await loadRouteDatasets();
        return;
      }
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
  $("submitReview").addEventListener("click", submitReview);
  $("reviewAction").addEventListener("change", (event) => {
    $("reviewSeverityLabel").hidden = event.target.value !== "modified";
  });
  $("surfaceCanvas").addEventListener("click", selectFromCanvas);
  ["showResidual", "showRoi", "showPotholes", "showRutting", "showBumps", "showAdvanced", "showLowConfidence", "showLowCoverage"].forEach((id) => $(id).addEventListener("change", () => {
    renderDefectTable(); renderCurrentView();
  }));
  $("minimumConfidence").addEventListener("input", (event) => {
    $("confidenceValue").textContent = `${Math.round(number(event.target.value) * 100)}%`;
    renderDefectTable(); renderCurrentView();
  });
  $("exaggeration").addEventListener("change", renderCurrentView);
  $("viewMode").addEventListener("change", renderCurrentView);
  $("mapAdapter").addEventListener("change", renderCurrentView);
  $("loadRouteButton").addEventListener("click", loadRouteDatasets);
  $("tileSelect").addEventListener("change", (event) => loadRouteTile(number(event.target.value)).catch((error) => setApiStatus(error.message, "error")));
  $("previousTile").addEventListener("click", () => loadRouteTile(state.routeTileIndex - 1).catch((error) => setApiStatus(error.message, "error")));
  $("nextTile").addEventListener("click", () => loadRouteTile(state.routeTileIndex + 1).catch((error) => setApiStatus(error.message, "error")));
  $("jobSelect").addEventListener("change", (event) => { $("openJobButton").disabled = !event.target.value; });
  $("openJobButton").addEventListener("click", () => openJob($("jobSelect").value));
  $("compareJob").addEventListener("change", (event) => { $("compareButton").disabled = !event.target.value; });
  $("compareButton").addEventListener("click", compareJob);
  $("reloadViewer").addEventListener("click", () => window.location.reload());
  [$("surfaceCanvas"), $("perspectiveCanvas"), $("mapCanvas")].forEach((canvas) => {
    canvas.addEventListener("keydown", handleViewerKey);
    installContextRecovery(canvas);
  });
  window.addEventListener("resize", () => { if (state.surface) renderCurrentView(); });

  const requestedView = pageParams.get("view");
  const requestedAdapter = pageParams.get("adapter");
  if (["plan", "perspective", "map"].includes(requestedView)) $("viewMode").value = requestedView;
  if (["local_enu", "vworld", "cesium"].includes(requestedAdapter)) $("mapAdapter").value = requestedAdapter;

  boot();
})();
