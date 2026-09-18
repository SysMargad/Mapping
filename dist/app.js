(() => {
  "use strict";

  const PROJECT = "Nergui Undur";
  const FORBIDDEN_PROJECTS = ["artsat", "будуун хад", "buduunkhad", "buduun khad"];
  const $ = (selector) => document.querySelector(selector);
  const ui = {
    loading: $("#loading"),
    error: $("#error"),
    errorMessage: $("#error-message"),
    retry: $("#retry"),
    warnings: $("#data-warnings"),
    primaryLabel: $("#summary-label-primary"),
    primaryValue: $("#summary-value-primary"),
    secondaryLabel: $("#summary-label-secondary"),
    secondaryValue: $("#summary-value-secondary"),
    tertiaryLabel: $("#summary-label-tertiary"),
    tertiaryValue: $("#summary-value-tertiary"),
    summaryNote: $("#summary-note"),
    baseLayers: $("#base-layer-list"),
    licenseBrowser: $("#license-browser"),
    licenseBrowserTitle: $("#license-browser-title"),
    licenseCount: $("#license-count"),
    licenseContextList: $("#license-context-list"),
    clearLicenseContext: $("#clear-license-context"),
    projectTrackerPanel: $("#project-tracker-panel"),
    sensors: $("#sensor-list"),
    sensorPanel: $("#sensor-panel"),
    datasetInfo: $("#dataset-info"),
    sourceLinks: $("#source-links"),
    fit: $("#fit-map"),
    refresh: $("#refresh-data"),
  };

  const state = {
    map: null,
    manifest: null,
    registry: null,
    datasets: new Map(),
    activeSensor: "MagArrow",
    activeDatasetId: "magarrow-planned-survey",
    baseLayers: new Map(),
    licenceData: null,
    licenseContextLayers: new Map(),
    licenseContextData: null,
    selectedContextLicense: null,
    hetsuuCadData: null,
    hetsuuCadLayer: null,
    hetsuuCadLabelLayers: [],
    trackerData: null,
    projectControlData: null,
    projectControlLayers: new Map(),
    selectedTrackerProject: null,
    selectedTrackerSensor: null,
    selectedTrackerDate: "all",
    projectFlightData: null,
    projectFlightCoverage: null,
    projectFlightLayers: new Map(),
    licenseBrowserMode: "licence",
    uchastikData: null,
    uchastikFeatureLayers: new Map(),
    selectedUchastikKey: null,
    areaScope: null,
    planLayers: new Map(),
    planVisibility: { boundary: true, main: true, tie: true },
    missionLayers: new Map(),
    selectedMissions: new Set(),
    missionData: null,
    missionPromise: null,
    actualTrackData: null,
    actualCoverage: null,
    actualTrackLayers: new Map(),
    selectedFlightDate: "all",
    planArea: 0,
    mainCoverage: 0,
    warnings: new Set(),
  };

  const sensorConfig = [
    { id: "MagArrow", label: "MagArrow", status: "DATA AVAILABLE", tone: "available" },
    { id: "L3", label: "L3", status: "SURVEY DATA AVAILABLE", tone: "survey" },
    { id: "L2", label: "L2", status: "SOURCE PENDING", tone: "pending" },
    { id: "P1", label: "P1", status: "SOURCE PENDING", tone: "pending" },
    { id: "Medusa", label: "Medusa MS-700", status: "NO FLIGHT DATA", tone: "unavailable" },
  ];

  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const formatArea = (squareMetres) => {
    if (!Number.isFinite(squareMetres)) return "—";
    if (squareMetres >= 1_000_000) {
      return `${(squareMetres / 1_000_000).toLocaleString("mn-MN", { maximumFractionDigits: 2 })} км²`;
    }
    return `${squareMetres.toLocaleString("mn-MN", { maximumFractionDigits: 0 })} м²`;
  };

  const formatLength = (metres) => {
    if (!Number.isFinite(metres)) return "—";
    return metres >= 1000
      ? `${(metres / 1000).toLocaleString("mn-MN", { maximumFractionDigits: 2 })} км`
      : `${metres.toLocaleString("mn-MN", { maximumFractionDigits: 0 })} м`;
  };

  const geometryArea = (geometry) => {
    if (!geometry) return 0;
    const polygons = geometry.type === "Polygon"
      ? [geometry.coordinates]
      : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
    const ringArea = (ring) => {
      if (!ring?.length) return 0;
      const latitude = ring.reduce((sum, point) => sum + point[1], 0) / ring.length;
      const xScale = 111320 * Math.cos(latitude * Math.PI / 180);
      return Math.abs(ring.reduce((sum, point, index) => {
        const next = ring[(index + 1) % ring.length];
        return sum + point[0] * xScale * next[1] * 111320 - next[0] * xScale * point[1] * 111320;
      }, 0)) / 2;
    };
    return polygons.reduce((total, polygon) => (
      total + ringArea(polygon[0]) - polygon.slice(1).reduce((sum, hole) => sum + ringArea(hole), 0)
    ), 0);
  };

  const featureArea = (feature) => Number(feature?.properties?.area_m2) || geometryArea(feature?.geometry);

  const lineLength = (feature) => {
    const geometry = feature?.geometry;
    if (!geometry || !["LineString", "MultiLineString"].includes(geometry.type)) return 0;
    const lines = geometry.type === "MultiLineString" ? geometry.coordinates : [geometry.coordinates];
    return lines.reduce((total, line) => total + line.slice(1).reduce((sum, point, index) => {
      const previous = line[index];
      const x = (point[0] - previous[0]) * 111320 * Math.cos(point[1] * Math.PI / 180);
      const y = (point[1] - previous[1]) * 111320;
      return sum + Math.hypot(x, y);
    }, 0), 0);
  };

  const geometryWithinNergui = (geometry) => {
    let seen = false;
    let valid = true;
    const visit = (value) => {
      if (!valid || !Array.isArray(value)) return;
      if (value.length >= 2 && Number.isFinite(value[0]) && Number.isFinite(value[1])) {
        seen = true;
        valid = value[0] >= 112 && value[0] <= 115 && value[1] >= 47 && value[1] <= 51;
        return;
      }
      value.forEach(visit);
    };
    visit(geometry?.coordinates);
    return seen && valid;
  };

  const popup = (rows) => rows
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([label, value]) => `<span class="popup-label">${escapeHtml(label)}</span><span class="popup-value">${escapeHtml(value)}</span>`)
    .join("");

  const plannedPopup = (feature) => {
    const props = feature.properties || {};
    const main = props.dataType === "planned_main_line";
    const tie = props.dataType === "planned_tie_line";
    const type = main ? "Planned main line" : tie ? "Planned tie line" : "Planned survey geometry";
    return popup([
      ["Sensor", "MagArrow"],
      ["Type", type],
      ["Direction", props.direction || (main ? "E-W" : tie ? "N-S" : "")],
      ["Azimuth", Number.isFinite(Number(props.azimuth_deg)) ? `${props.azimuth_deg}°` : ""],
      ["Spacing", Number.isFinite(Number(props.spacing_m)) ? `${props.spacing_m} m` : ""],
      ["AGL", Number.isFinite(Number(props.planned_sensor_agl_m)) ? `${props.planned_sensor_agl_m} m` : "30 m"],
      ["Speed", Number.isFinite(Number(props.planned_speed_mps)) ? `${props.planned_speed_mps} m/s` : "6 m/s"],
      ["Status", "Planned"],
    ]);
  };

  const missionPopup = (feature) => {
    const props = feature.properties || {};
    return popup([
      ["Sensor", "MagArrow"],
      ["Mission", props.mission_id || props.plan],
      ["Type", "DJI mission plan"],
      ["Date", props.date],
      ["Status", "Planned"],
      ["Actual flown track", "Not represented by this geometry"],
      ["Source", props.sourceFile],
    ]);
  };

  const actualPopup = (feature) => {
    const props = feature.properties || {};
    return popup([
      ["Sensor", "MagArrow"],
      ["Acquisition", props.acquisition],
      ["Date", props.date],
      ["Type", "Actual GNSS trajectory"],
      ["Start", props.startTime],
      ["End", props.endTime],
      ["Source", props.sourceFile],
    ]);
  };

  const basePopup = (feature) => {
    const props = feature.properties || {};
    const label = ["licence_boundary", "licence_context"].includes(props.dataType) ? "Licence boundary"
      : props.dataType === "uchastik_boundary" ? "Uchastik boundary"
      : props.dataType === "block_boundary" ? "Block boundary"
      : props.dataType === "block_label" ? "Block label"
      : "CAD reference geometry";
    return popup([
      ["Category", "Base / Control"],
      ["Type", label],
      ["Name", props.AREANAME_L || props.AREANAME || props.name || props.text_value],
      ["Licence", props.LICENSE],
      ["Block", props.block_num],
      ["CAD layer", props.cadLayer],
      ["Label", props.text_value],
      ["Entity", props.entityHandle],
      ["Source", props.sourceFile || props.source_dwg],
      ["Source CRS", props.sourceCrs || "Unknown / unverified"],
      ["Web CRS", props.displayCrs || "EPSG:4326"],
    ]);
  };

  const ensureMap = () => {
    if (state.map) return state.map;
    const map = L.map("map", { zoomControl: false, preferCanvas: true });
    state.map = map;
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);
    L.control.zoom({ position: "topright" }).addTo(map);
    const panes = {
      licencePane: 430,
      surveyPane: 440,
      uchastikPane: 450,
      plannedPane: 460,
      missionPane: 470,
      actualPane: 480,
      measurementPane: 490,
      labelsPane: 500,
    };
    for (const [name, zIndex] of Object.entries(panes)) {
      const pane = map.createPane(name);
      pane.style.zIndex = String(zIndex);
    }
    map.on("zoomend", syncHetsuuCadLabels);
    map.setView([49.1, 107.5], 8);
    return map;
  };

  const fetchJson = async (url) => {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`${url} → HTTP ${response.status}`);
    return response.json();
  };

  const assertProjectTruth = (payload, label) => {
    const rootProject = payload?.project;
    if (rootProject && rootProject !== PROJECT) throw new Error(`${label}: project=${rootProject}`);
    for (const feature of payload?.features || []) {
      const props = feature.properties || {};
      if (props.project && props.project !== PROJECT) throw new Error(`${label}: feature ${feature.id} belongs to ${props.project}`);
      const provenance = `${props.project || ""} ${props.sourceFile || ""}`.toLowerCase();
      const conflict = FORBIDDEN_PROJECTS.find((token) => provenance.includes(token));
      if (conflict) throw new Error(`${label}: cross-project marker ${conflict}`);
      if (props.dataType === "actual_flight_track") {
        const source = String(props.sourceFile || "").toLowerCase();
        if (/\.(kml|kmz|wpmz|zip)$/.test(source) || props.plannedActual !== "actual") {
          throw new Error(`${label}: a mission-plan source was classified as an actual track`);
        }
      }
    }
  };

  const assertExternalReference = (payload, label, expectedDataType) => {
    if (payload?.scope !== "external_reference" || payload?.project !== "External licence reference") {
      throw new Error(`${label} must be isolated as an external reference dataset`);
    }
    for (const feature of payload.features || []) {
      const props = feature.properties || {};
      if (props.dataType !== expectedDataType || props.contextOnly !== true) {
        throw new Error(`${label} feature ${feature.id} is not marked context-only`);
      }
    }
  };

  const projectFlightPopup = (feature) => {
    const props = feature.properties || {};
    return popup([
      ["Project", props.area || props.project],
      ["Sensor", props.sensor],
      ["Mission", props.mission],
      ["Date", props.date],
      ["Type", props.sourceKind || "Verified actual flight trajectory"],
      ["Start", props.startTime],
      ["Points", props.pointCount],
      ["Coverage model", `${props.coverageSwathWidthM || 50} m swath`],
      ["Source", props.sourceFile],
    ]);
  };

  const assertLicenceContext = (payload) => assertExternalReference(payload, "Licence context", "licence_context");

  const assertProjectOperations = (payload, label, expectedDataType) => {
    if (payload?.scope !== "project_operations" || payload?.project !== "Multi-project operations") {
      throw new Error(`${label} must be isolated as project operations data`);
    }
    if (expectedDataType && payload.dataType !== expectedDataType) {
      throw new Error(`${label}: expected ${expectedDataType}, got ${payload.dataType}`);
    }
    for (const feature of payload.features || []) {
      const props = feature.properties || {};
      if (props.dataType !== expectedDataType || props.contextOnly !== true) {
        throw new Error(`${label} feature ${feature.id} is not isolated correctly`);
      }
    }
  };

  const dataset = (id) => state.datasets.get(id);

  const addWarning = (key, message) => {
    if (state.warnings.has(key)) return;
    state.warnings.add(key);
    const item = document.createElement("div");
    item.className = "warning-item";
    item.textContent = message;
    ui.warnings.appendChild(item);
  };

  const clearWarnings = () => {
    state.warnings.clear();
    ui.warnings.replaceChildren();
  };

  const renderAreaSummary = () => {
    const features = state.areaScope?.features || [];
    const totalArea = features.length ? features.reduce((sum, feature) => sum + featureArea(feature), 0) : NaN;
    const projectSensorCoverage = state.selectedTrackerProject && state.selectedTrackerSensor
      ? state.projectFlightCoverage?.projects?.[state.selectedTrackerProject]?.sensors?.[state.selectedTrackerSensor]
      : null;
    const coverage = projectSensorCoverage?.scopes?.licence || (state.areaScope?.coverageKey
      ? state.actualCoverage?.scopes?.[state.areaScope.coverageKey]
      : null);
    const dateFilter = projectSensorCoverage ? state.selectedTrackerDate : state.selectedFlightDate;
    const selectedDate = dateFilter !== "all" ? dateFilter : null;
    const dailyAreas = Object.values(coverage?.dailyAreaM2 || {}).filter(Number.isFinite);
    const allDaysArea = coverage ? dailyAreas.reduce((sum, area) => sum + area, 0) : NaN;
    const selectedArea = selectedDate ? coverage?.dailyAreaM2?.[selectedDate] : coverage?.totalAreaM2;
    ui.primaryLabel.textContent = "Нийт талбай";
    ui.primaryValue.textContent = formatArea(totalArea);
    ui.secondaryLabel.textContent = "Ниссэн нийт талбай";
    ui.secondaryValue.textContent = formatArea(selectedArea);
    ui.tertiaryLabel.textContent = "Бүх өдрийн нийлбэр";
    ui.tertiaryValue.textContent = formatArea(allDaysArea);
    ui.summaryNote.textContent = coverage
      ? `${state.areaScope.label} · ${state.selectedTrackerSensor || "MagArrow"} · ${selectedDate || "Бүх огноо"} · Баталгаажсан trajectory-д суурилсан 50 м зурвасын тооцоо.${selectedDate ? "" : " Өдрийн нийлбэрт огноо хоорондын давхардал орж болно."}`
      : state.areaScope
        ? `${state.areaScope.label} · Сонгосон sensor/өдөрт баталгаажсан trajectory geometry байхгүй.`
        : "Талбай сонгоход үзүүлэлт шинэчлэгдэнэ.";
  };

  const setAreaScope = (label, features, coverageKey = null) => {
    state.areaScope = {
      label,
      features: (Array.isArray(features) ? features : [features]).filter(Boolean),
      coverageKey,
    };
    renderAreaSummary();
    if (state.actualTrackData) {
      if (state.activeSensor === "MagArrow" && state.selectedFlightDate !== "all"
        && !actualFeaturesInArea().some((feature) => feature.properties?.date === state.selectedFlightDate)) {
        state.selectedFlightDate = "all";
        renderAreaSummary();
      }
      syncActualTrackVisibility();
      if (state.activeSensor === "MagArrow") renderMagArrowPanel();
    }
  };

  const registerBaseControl = (id, label, detail, layer, visible, tone = "base") => {
    if (visible) layer.addTo(state.map);
    const row = document.createElement("label");
    row.className = "toggle-row";
    row.style.order = String({ licence: 1, uchastik: 2, blocks: 3, cad: 4, trackerControl: 5, hetsuuCad: 6, hetsuuCadPoints: 7 }[id] || 99);
    row.innerHTML = `
      <span class="toggle-copy"><span class="mini-symbol ${tone}" aria-hidden="true"></span><span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(detail)}</small></span></span>
      <span class="switch"><input type="checkbox" ${visible ? "checked" : ""} /><span aria-hidden="true"></span></span>`;
    const input = row.querySelector("input");
    state.baseLayers.set(id, { layer, visible, label, input });
    input.addEventListener("change", (event) => {
      state.baseLayers.get(id).visible = event.target.checked;
      if (id === "uchastik") syncUchastikLayerVisibility();
      else if (id === "trackerControl") syncProjectControlVisibility();
      else if (event.target.checked) layer.addTo(state.map);
      else state.map.removeLayer(layer);
    });
    ui.baseLayers.appendChild(row);
  };

  const registerUnavailableBase = (label, error) => {
    const row = document.createElement("div");
    row.className = "toggle-row is-disabled";
    row.innerHTML = `<span class="toggle-copy"><span class="mini-symbol pending"></span><span><strong>${escapeHtml(label)}</strong><small>Уншигдсангүй</small></span></span>`;
    ui.baseLayers.appendChild(row);
    addWarning(`base-${label}`, `${label}: ${error.message}`);
  };

  const loadLicence = async () => {
    const config = dataset("base-licence");
    const data = await fetchJson(config.webAsset);
    assertProjectTruth(data, "Licence");
    state.licenceData = data;
    const layer = L.geoJSON(data, {
      pane: "licencePane",
      style: { color: "#ffd166", weight: 3, fillColor: "#ffd166", fillOpacity: 0.05, dashArray: "9 5" },
      onEachFeature(feature, item) { item.bindPopup(basePopup(feature)); },
    });
    registerBaseControl("licence", "Licence", `${data.features.length} polygon`, layer, true, "licence");
  };

  const clearLicenceContext = () => {
    for (const layer of state.licenseContextLayers.values()) state.map.removeLayer(layer);
    if (state.hetsuuCadLayer) state.map.removeLayer(state.hetsuuCadLayer);
    for (const layer of state.projectControlLayers.values()) state.map.removeLayer(layer);
    for (const layer of state.projectFlightLayers.values()) state.map.removeLayer(layer);
    for (const id of ["hetsuuCad", "hetsuuCadPoints"]) {
      const cadControl = state.baseLayers.get(id);
      if (!cadControl) continue;
      state.map.removeLayer(cadControl.layer);
      cadControl.visible = false;
      if (cadControl.input) cadControl.input.checked = false;
    }
    const trackerControl = state.baseLayers.get("trackerControl");
    if (trackerControl) {
      state.map.removeLayer(trackerControl.layer);
      trackerControl.visible = false;
      if (trackerControl.input) trackerControl.input.checked = false;
    }
    state.selectedTrackerProject = null;
    state.selectedTrackerSensor = null;
    state.selectedTrackerDate = "all";
    ui.projectTrackerPanel.hidden = true;
    ui.projectTrackerPanel.replaceChildren();
    state.selectedContextLicense = null;
    for (const button of ui.licenseContextList.querySelectorAll("button")) button.classList.remove("is-active");
  };

  const clearUchastikMapLayers = () => {
    const aggregate = state.baseLayers.get("uchastik")?.layer;
    if (aggregate) state.map.removeLayer(aggregate);
    for (const layer of state.uchastikFeatureLayers.values()) state.map.removeLayer(layer);
  };

  const syncUchastikLayerVisibility = () => {
    clearUchastikMapLayers();
    const control = state.baseLayers.get("uchastik");
    if (!control?.visible) return;
    if (state.licenseBrowserMode === "uchastik") {
      if (state.selectedUchastikKey === "all") control.layer.addTo(state.map);
      else state.uchastikFeatureLayers.get(state.selectedUchastikKey)?.addTo(state.map);
      return;
    }
    if (!state.selectedContextLicense) control.layer.addTo(state.map);
  };

  const fitSingleLayer = (layer) => {
    const bounds = layer?.getBounds?.();
    if (bounds?.isValid()) state.map.fitBounds(bounds, { padding: [44, 44], maxZoom: 15 });
  };

  const featureLabel = (feature, fallback = "Талбай") => feature?.properties?.AREANAME_L
    || feature?.properties?.AREANAME
    || feature?.properties?.name
    || fallback;

  const isNerguiLicence = (feature) => {
    const value = `${feature?.properties?.AREANAME || ""} ${feature?.properties?.AREANAME_L || ""}`.toLowerCase();
    return value.includes("nergui undur") || value.includes("нэргүй өндөр");
  };

  const trackerProjectForLicence = (feature) => {
    const licence = feature?.properties?.LICENSE;
    return ({
      "XV-022905": "hetsuu-hutul",
      "XV-021395": "artsat",
      "XV-023222": "buduunkhad",
    })[licence] || null;
  };

  const selectUchastik = (key, button) => {
    state.selectedUchastikKey = key;
    for (const item of ui.licenseContextList.querySelectorAll("button")) item.classList.remove("is-active");
    button.classList.add("is-active");
    const control = state.baseLayers.get("uchastik");
    if (control) {
      control.visible = true;
      if (control.input) control.input.checked = true;
    }
    syncUchastikLayerVisibility();
    if (key === "all") {
      setAreaScope("Нэргүй өндөр · Бүх участик", state.uchastikData?.features || [], "uchastik-all");
      fitSingleLayer(control?.layer);
    } else {
      const feature = state.uchastikData?.features.find((item) => String(item.id) === key);
      const layer = state.uchastikFeatureLayers.get(key);
      setAreaScope(`Нэргүй өндөр · ${featureLabel(feature, key)}`, feature, key);
      fitSingleLayer(layer);
    }
    state.activeDatasetId = "base-uchastik";
    renderDatasetInfo();
  };

  const showUchastikBrowser = (licenceFeature) => {
    state.licenseBrowserMode = "uchastik";
    ui.licenseBrowser.classList.add("is-uchastik");
    state.selectedUchastikKey = null;
    syncUchastikLayerVisibility();
    ui.licenseBrowserTitle.textContent = "Участик сонгох";
    ui.licenseCount.textContent = `${state.uchastikData?.features.length || 0} талбай`;
    ui.clearLicenseContext.textContent = "← Лицензийн жагсаалт";
    ui.clearLicenseContext.hidden = false;
    ui.licenseContextList.replaceChildren();
    const features = state.uchastikData?.features || [];
    const entries = [{ key: "all", label: "Бүх участик", detail: `${features.length} талбай` }]
      .concat(features.map((feature) => ({
        key: String(feature.id),
        label: featureLabel(feature, feature.id),
        detail: formatArea(featureArea(feature)),
      })));
    for (const entry of entries) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "license-context-button";
      button.innerHTML = `<strong>${escapeHtml(entry.label)}</strong><small>${escapeHtml(entry.detail)}</small>`;
      button.addEventListener("click", () => selectUchastik(entry.key, button));
      ui.licenseContextList.appendChild(button);
    }
    setAreaScope(featureLabel(licenceFeature, "Нэргүй өндөр"), licenceFeature, "licence");
  };

  const selectLicenceContext = (key, button) => {
    let activeContextDataset = "base-licence-context";
    clearLicenceContext();
    selectSensor("MagArrow");
    state.licenseBrowserMode = "licence";
    state.selectedUchastikKey = null;
    state.selectedContextLicense = key;
    button.classList.add("is-active");
    ui.clearLicenseContext.textContent = "Сонголт арилгах";
    ui.clearLicenseContext.hidden = false;
    syncUchastikLayerVisibility();
    if (key === "all") {
      for (const layer of state.licenseContextLayers.values()) layer.addTo(state.map);
      fitSingleLayer(L.featureGroup([...state.licenseContextLayers.values()]));
      setAreaScope("Бүх лиценз", state.licenseContextData?.features || [], null);
    } else {
      const layer = state.licenseContextLayers.get(key);
      layer?.addTo(state.map);
      const feature = state.licenseContextData?.features.find((item) => String(item.id) === key);
      if (isNerguiLicence(feature)) {
        fitSingleLayer(layer);
        showUchastikBrowser(feature);
      } else {
        const projectKey = trackerProjectForLicence(feature);
        if (projectKey) {
          const trackerLayer = activateTrackerProject(projectKey);
          const fitLayers = [layer, trackerLayer].filter(Boolean);
          if (projectKey === "hetsuu-hutul" && state.hetsuuCadLayer) {
            const cadControl = state.baseLayers.get("hetsuuCad");
            if (cadControl) {
              cadControl.visible = true;
              if (cadControl.input) cadControl.input.checked = true;
            }
            state.hetsuuCadLayer.addTo(state.map);
            syncHetsuuCadLabels();
            fitLayers.push(state.hetsuuCadLayer);
          }
          fitSingleLayer(L.featureGroup(fitLayers));
          activeContextDataset = projectFlightFeatures(projectKey, null, "all").length
            ? "context-project-flight-tracks"
            : "context-project-trackers";
        } else {
          fitSingleLayer(layer);
        }
        setAreaScope(featureLabel(feature, key), feature, null);
      }
    }
    state.activeDatasetId = activeContextDataset;
    renderDatasetInfo();
  };

  const renderLicenceBrowser = () => {
    const data = state.licenseContextData;
    if (!data) return;
    state.licenseBrowserMode = "licence";
    ui.licenseBrowser.classList.remove("is-uchastik");
    state.selectedUchastikKey = null;
    ui.licenseBrowserTitle.textContent = "Лицензийн талбай сонгох";
    ui.licenseCount.textContent = `${data.features.length} талбай`;
    ui.clearLicenseContext.textContent = "Сонголт арилгах";
    ui.clearLicenseContext.hidden = !state.selectedContextLicense;
    ui.licenseContextList.replaceChildren();
    const entries = [{ key: "all", label: "Бүх лиценз", licence: `${data.features.length} талбай`, feature: null }]
      .concat(data.features.map((feature) => ({
        key: String(feature.id),
        label: featureLabel(feature, feature.id),
        licence: feature.properties?.LICENSE || "Licence number unavailable",
        feature,
      })));
    for (const entry of entries) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "license-context-button";
      button.innerHTML = `<strong>${escapeHtml(entry.label)}</strong><small>${escapeHtml(entry.licence)}</small>`;
      button.addEventListener("click", () => selectLicenceContext(entry.key, button));
      ui.licenseContextList.appendChild(button);
    }
  };

  const returnToLicenceBrowser = () => {
    clearLicenceContext();
    selectSensor("MagArrow");
    state.licenseBrowserMode = "licence";
    state.selectedUchastikKey = null;
    syncUchastikLayerVisibility();
    renderLicenceBrowser();
    const feature = state.licenceData?.features?.[0];
    setAreaScope(featureLabel(feature, "Нэргүй өндөр"), feature, "licence");
    state.activeDatasetId = "base-licence";
    renderDatasetInfo();
    fitSingleLayer(state.baseLayers.get("licence")?.layer);
  };

  const loadLicenceContext = async () => {
    const config = dataset("base-licence-context");
    const data = await fetchJson(config.webAsset);
    assertLicenceContext(data);
    state.licenseContextData = data;
    for (const feature of data.features) {
        const layer = L.geoJSON(feature, {
          pane: "licencePane",
          style: { color: "#ffe08a", weight: 3.2, opacity: 1, fillColor: "#ffd166", fillOpacity: 0.12, dashArray: "10 5" },
          onEachFeature(itemFeature, item) { item.bindPopup(basePopup(itemFeature)); },
        });
        state.licenseContextLayers.set(String(feature.id), layer);
    }
    renderLicenceBrowser();
  };

  const hetsuuCadPoint = (feature, latlng) => {
    const props = feature.properties || {};
    if (props.displayRole === "label" && props.text_value) {
      const marker = L.marker(latlng, {
        pane: "labelsPane",
        interactive: true,
        icon: L.divIcon({ className: "map-label cad-map-label", html: `<span>${escapeHtml(props.text_value)}</span>` }),
      });
      marker.setOpacity(state.map.getZoom() >= 12 ? 1 : 0);
      state.hetsuuCadLabelLayers.push(marker);
      return marker;
    }
    return L.circleMarker(latlng, {
      pane: "surveyPane",
      radius: 2,
      color: "#9eb1c3",
      weight: 1,
      fillColor: "#9eb1c3",
      fillOpacity: 0.45,
    });
  };

  const hetsuuCadReferencePoint = (feature, latlng) => {
    const elevation = feature.properties?.displayRole === "elevation";
    return L.circleMarker(latlng, {
      pane: "surveyPane",
      radius: elevation ? 1.8 : 2.4,
      color: elevation ? "#aab7c4" : "#56d7ff",
      weight: 1,
      fillColor: elevation ? "#aab7c4" : "#56d7ff",
      fillOpacity: elevation ? 0.42 : 0.58,
    });
  };

  const hetsuuCadPopup = (feature) => {
    const props = feature.properties || {};
    const roleLabels = {
      boundary: "Талбайн хүрээ",
      block: "Участикийн хүрээ",
      section: "Хэсгийн хүрээ",
      polygon: "CAD polygon",
      control_point: "CAD control point",
      elevation: "Өндрийн тэмдэглэгээ",
      label: "CAD нэршил",
    };
    return popup([
      ["Төрөл", roleLabels[props.displayRole] || "CAD reference"],
      ["Нэр/утга", props.text_value || ""],
      ["CAD layer", props.cadLayer || ""],
      ["Статус", "Reference geometry · нислэгийн trajectory биш"],
      ["Source", props.sourceFile || "Hetsuu hutul.DWG"],
    ]);
  };

  const syncHetsuuCadLabels = () => {
    const visible = state.map?.getZoom() >= 12;
    for (const marker of state.hetsuuCadLabelLayers) {
      marker.setOpacity(visible ? 1 : 0);
      const element = marker.getElement?.();
      if (element) element.style.pointerEvents = visible ? "auto" : "none";
    }
  };

  const loadHetsuuCad = async () => {
    const config = dataset("context-hetsuu-hutul-dwg");
    const data = await fetchJson(config.webAsset);
    assertExternalReference(data, "Hetsuu hutul DWG", "cad_reference_geometry");
    state.hetsuuCadData = data;
    state.hetsuuCadLabelLayers = [];
    const outlineFeatures = data.features.filter((feature) => feature.geometry?.type !== "Point");
    const labelFeatures = data.features.filter((feature) => feature.properties?.displayRole === "label");
    const pointFeatures = data.features.filter((feature) => ["control_point", "elevation"].includes(feature.properties?.displayRole));
    const layer = L.geoJSON({ type: "FeatureCollection", features: [...outlineFeatures, ...labelFeatures] }, {
      pane: "surveyPane",
      style(feature) {
        const role = feature.properties?.displayRole;
        if (role === "boundary") return { color: "#ffca5c", weight: 3.4, opacity: 1, fillOpacity: 0 };
        if (role === "block") return { color: "#ff8fb8", weight: 2, opacity: 0.96, fillOpacity: 0 };
        if (role === "section") return { color: "#71f6c1", weight: 1.9, opacity: 0.92, fillOpacity: 0 };
        return { color: "#56d7ff", weight: 1.6, opacity: 0.88, fillOpacity: 0 };
      },
      pointToLayer: hetsuuCadPoint,
      onEachFeature(feature, item) { item.bindPopup(hetsuuCadPopup(feature)); },
    });
    const pointLayer = L.geoJSON({ type: "FeatureCollection", features: pointFeatures }, {
      pane: "surveyPane",
      pointToLayer: hetsuuCadReferencePoint,
      onEachFeature(feature, item) { item.bindPopup(hetsuuCadPopup(feature)); },
    });
    state.hetsuuCadLayer = layer;
    registerBaseControl(
      "hetsuuCad",
      "Хэцүү хөтөл · CAD хүрээ",
      `${outlineFeatures.length} хүрээ · ${labelFeatures.length} нэр · trajectory биш`,
      layer,
      false,
      "hetsuu",
    );
    registerBaseControl(
      "hetsuuCadPoints",
      "Хэцүү хөтөл · CAD цэг",
      `${pointFeatures.length} control/elevation point · trajectory биш`,
      pointLayer,
      false,
      "pending",
    );
  };

  const trackerPoint = (feature, latlng) => {
    const base = feature.properties?.controlType === "base";
    return L.circleMarker(latlng, {
      pane: "surveyPane",
      radius: base ? 5.5 : 3.5,
      color: base ? "#ffca5c" : "#56d7ff",
      weight: base ? 2 : 1.4,
      fillColor: base ? "#ffca5c" : "#56d7ff",
      fillOpacity: base ? 0.68 : 0.55,
    });
  };

  const trackerPointPopup = (feature) => {
    const props = feature.properties || {};
    return popup([
      ["Project", props.area],
      ["Type", props.controlType === "base" ? "Base station" : "GCP"],
      ["Name", props.name],
      ["Elevation", Number.isFinite(Number(props.elevationM)) ? `${props.elevationM} m` : ""],
      ["UTM", `${props.sourceCrs || ""} · E ${props.easting || ""} · N ${props.northing || ""}`],
      ["Dates", (props.dates || []).join(", ")],
      ["Observations", props.observationCount],
      ["Source", props.sourceFile],
    ]);
  };

  const syncProjectControlVisibility = () => {
    const control = state.baseLayers.get("trackerControl");
    if (!control) return;
    state.map.removeLayer(control.layer);
    for (const layer of state.projectControlLayers.values()) state.map.removeLayer(layer);
    if (!control.visible) return;
    if (state.selectedTrackerProject) state.projectControlLayers.get(state.selectedTrackerProject)?.addTo(state.map);
    else control.layer.addTo(state.map);
  };

  const formatCount = (value) => Number(value || 0).toLocaleString("mn-MN");

  const projectFlightFeatures = (projectKey = state.selectedTrackerProject, sensor = state.selectedTrackerSensor, date = state.selectedTrackerDate) => (
    (state.projectFlightData?.features || []).filter((feature) => {
      const props = feature.properties || {};
      return (!projectKey || props.projectKey === projectKey)
        && (!sensor || props.sensor === sensor)
        && (!date || date === "all" || props.date === date);
    })
  );

  const selectedProjectFlightLayers = () => projectFlightFeatures()
    .map((feature) => state.projectFlightLayers.get(feature.id))
    .filter(Boolean);

  const trackerDateCadReferences = (
    projectKey = state.selectedTrackerProject,
    sensor = state.selectedTrackerSensor,
    date = state.selectedTrackerDate,
  ) => {
    if (projectKey !== "hetsuu-hutul" || !date || date === "all") return [];
    const blockLabels = new Set((state.trackerData?.records || [])
      .filter((record) => record.projectKey === projectKey && record.sensor === sensor && record.date === date)
      .flatMap((record) => String(record.mission || "").match(/XT-B\d+/gi) || [])
      .map((label) => label.toUpperCase()));
    if (!blockLabels.size) return [];
    return (state.hetsuuCadData?.features || []).filter((feature) => (
      blockLabels.has(String(feature.properties?.text_value || "").toUpperCase())
    ));
  };

  const fitTrackerDateSelection = () => {
    const layers = selectedProjectFlightLayers();
    if (layers.length) {
      fitSingleLayer(L.featureGroup(layers));
      return;
    }
    const references = trackerDateCadReferences();
    if (references.length) {
      fitSingleLayer(L.geoJSON({ type: "FeatureCollection", features: references }));
      return;
    }
    fitSingleLayer(state.licenseContextLayers.get(state.selectedContextLicense));
  };

  const syncProjectFlightVisibility = () => {
    for (const layer of state.projectFlightLayers.values()) state.map.removeLayer(layer);
    if (!state.selectedTrackerProject || !state.selectedTrackerSensor) return;
    for (const layer of selectedProjectFlightLayers()) layer.addTo(state.map);
  };

  const renderTrackerProject = (projectKey) => {
    const project = state.trackerData?.projects?.[projectKey];
    if (!project) {
      ui.projectTrackerPanel.hidden = true;
      return;
    }
    const sensorRows = Object.entries(project.sensors || {}).map(([sensor, stats]) => {
      const altitude = Number.isFinite(stats.altitudeMinM)
        ? `${stats.altitudeMinM}–${stats.altitudeMaxM} м`
        : "өндөр бүртгээгүй";
      const geometryCount = projectFlightFeatures(projectKey, sensor, "all").length;
      return `<button type="button" class="tracker-sensor-row${state.selectedTrackerSensor === sensor ? " is-active" : ""}" data-tracker-sensor="${escapeHtml(sensor)}"><strong>${escapeHtml(sensor)}</strong><span>${escapeHtml(altitude)}</span><b>${formatCount(stats.records)} бүртгэл${geometryCount ? ` · ${geometryCount} line` : ""}</b></button>`;
    }).join("");
    const dailyRows = (project.daily || []).map((item) => {
      const sensors = Object.keys(item.sensors || {});
      const isActive = state.selectedTrackerDate === item.date
        && (!state.selectedTrackerSensor || sensors.includes(state.selectedTrackerSensor));
      return `<button type="button" class="tracker-day${isActive ? " is-active" : ""}" data-tracker-day="${escapeHtml(item.date)}" data-tracker-sensors="${escapeHtml(sensors.join(","))}"><strong>${escapeHtml(item.date)}</strong><span>${escapeHtml(Object.entries(item.sensors || {}).map(([key, count]) => `${key} ${count}`).join(" · "))}</span><b>${formatCount(item.records)}</b></button>`;
    }).join("");
    const copied = project.fileCopy?.["Хуулж дууссан"] || 0;
    const qaQcDone = project.qaqc?.["Тийм"] || 0;
    const baseCount = project.controlPoints?.base || 0;
    const gcpCount = project.controlPoints?.gcp || 0;
    const cadFeatures = projectKey === "hetsuu-hutul" ? (state.hetsuuCadData?.features || []) : [];
    const cadOutlineCount = cadFeatures.filter((feature) => feature.geometry?.type !== "Point").length;
    const cadPointCount = cadFeatures.filter((feature) => ["control_point", "elevation"].includes(feature.properties?.displayRole)).length;
    const cadLabelCount = cadFeatures.filter((feature) => feature.properties?.displayRole === "label").length;
    const cadReference = cadFeatures.length ? `
      <details class="nested-panel" open>
        <summary>CAD reference зураглал <span>${formatCount(cadFeatures.length)}</span></summary>
        <div class="tracker-kpis">
          <div><span>Хүрээний зураас</span><strong>${formatCount(cadOutlineCount)}</strong></div>
          <div><span>Control/elevation цэг</span><strong>${formatCount(cadPointCount)}</strong></div>
          <div><span>Нэр, label</span><strong>${formatCount(cadLabelCount)}</strong></div>
        </div>
        <p class="truth-note">DWG-ийн талбай, участик, хэсгийн хүрээг map дээр зураасаар харуулав. CAD цэгүүдийг Base layers хэсгээс тусад нь асаана. Эдгээр нь reference geometry бөгөөд ниссэн trajectory биш.</p>
      </details>` : "";
    ui.projectTrackerPanel.innerHTML = `
      <div class="tracker-heading"><div><strong>${escapeHtml(project.label)}</strong><span>${escapeHtml(project.licence)} · ${escapeHtml(project.dateFrom)} → ${escapeHtml(project.dateTo)}</span></div><span class="status-badge survey">ACTUAL RECORDS</span></div>
      <div class="tracker-kpis">
        <div><span>Нислэгийн бүртгэл</span><strong>${formatCount(project.records)}</strong></div>
        <div><span>Ниссэн өдөр</span><strong>${formatCount(project.flightDays)}</strong></div>
        <div><span>Зургийн тоо</span><strong>${formatCount(project.images)}</strong></div>
      </div>
      <div class="tracker-sensors">${sensorRows}</div>
      <p class="panel-note">${formatCount(copied)}/${formatCount(project.records)} файл хуулсан · QAQC “Тийм”: ${formatCount(qaQcDone)} · Base ${formatCount(baseCount)} · GCP ${formatCount(gcpCount)}.</p>
      ${cadReference}
      <details class="nested-panel">
        <summary>Өдрийн бүртгэл <span>${formatCount(project.flightDays)}</span></summary>
        <div class="tracker-daily">${dailyRows}</div>
      </details>
      <p class="truth-note">Actual flight register: ${formatCount(project.records)} бүртгэл. Баталгаажсан trajectory geometry: ${formatCount(projectFlightFeatures(projectKey, null, "all").length)}. Geometry байхгүй бүртгэлийг шугам болгон таамаглаагүй.</p>
      <a class="tracker-source" href="${escapeHtml(project.url)}" target="_blank" rel="noopener noreferrer">Эх tracker нээх ↗</a>`;
    for (const button of ui.projectTrackerPanel.querySelectorAll("[data-tracker-sensor]")) {
      button.addEventListener("click", () => selectSensor(button.dataset.trackerSensor));
    }
    for (const button of ui.projectTrackerPanel.querySelectorAll("[data-tracker-day]")) {
      button.addEventListener("click", () => {
        const sensors = button.dataset.trackerSensors.split(",").filter(Boolean);
        const sensor = sensors.includes(state.selectedTrackerSensor) ? state.selectedTrackerSensor : sensors[0];
        if (sensor) {
          state.selectedTrackerSensor = sensor;
          state.activeSensor = sensor;
          renderSensorButtons();
        }
        selectTrackerDate(button.dataset.trackerDay);
      });
    }
    ui.projectTrackerPanel.hidden = false;
  };

  const activateTrackerProject = (projectKey) => {
    state.selectedTrackerProject = projectKey;
    const project = state.trackerData?.projects?.[projectKey];
    state.selectedTrackerSensor = Object.keys(project?.sensors || {})[0] || null;
    state.selectedTrackerDate = "all";
    state.activeSensor = state.selectedTrackerSensor || "MagArrow";
    const control = state.baseLayers.get("trackerControl");
    if (control) {
      control.visible = false;
      if (control.input) control.input.checked = false;
    }
    syncProjectControlVisibility();
    syncProjectFlightVisibility();
    renderTrackerProject(projectKey);
    renderSensorButtons();
    if (state.selectedTrackerSensor) renderTrackerSensorPanel(state.selectedTrackerSensor);
    const flightLayers = selectedProjectFlightLayers();
    return flightLayers.length ? L.featureGroup(flightLayers) : null;
  };

  const loadProjectTrackers = async () => {
    const trackerConfig = dataset("context-project-trackers");
    const pointConfig = dataset("context-project-control-points");
    const [trackerData, pointData] = await Promise.all([
      fetchJson(trackerConfig.webAsset),
      fetchJson(pointConfig.webAsset),
    ]);
    assertProjectOperations(trackerData, "Project trackers", "flight_register_summary");
    assertProjectOperations(pointData, "Project control points", "control_reference_point");
    state.trackerData = trackerData;
    state.projectControlData = pointData;
    // Base/GCP remains source metadata only. It is intentionally not rendered:
    // control points are not a flown trajectory and must not appear as blue dots.
  };

  const loadProjectFlights = async () => {
    const tracksConfig = dataset("context-project-flight-tracks");
    const coverageConfig = dataset("context-project-flight-coverage");
    const [data, coverage] = await Promise.all([
      fetchJson(tracksConfig.webAsset),
      fetchJson(coverageConfig.webAsset),
    ]);
    assertProjectOperations(data, "Project flight tracks", "actual_flight_track");
    if (coverage.project !== "Multi-project operations" || coverage.scope !== "project_operations") {
      throw new Error("Project flight coverage is not isolated correctly");
    }
    state.projectFlightData = data;
    state.projectFlightCoverage = coverage;
    const colors = { L2: "#39d9ff", L3: "#71f6c1", P1: "#ff8fb8" };
    for (const feature of data.features || []) {
      const color = colors[feature.properties?.sensor] || "#ffffff";
      const layer = L.geoJSON(feature, {
        pane: "actualPane",
        style: { color, weight: 3.2, opacity: 0.98 },
        onEachFeature(itemFeature, item) { item.bindPopup(projectFlightPopup(itemFeature)); },
      });
      state.projectFlightLayers.set(feature.id, layer);
    }
  };

  const loadUchastik = async () => {
    const config = dataset("base-uchastik");
    const data = await fetchJson(config.webAsset);
    assertProjectTruth(data, "Uchastik");
    state.uchastikData = data;
    const layer = L.geoJSON(data, {
      pane: "uchastikPane",
      style: { color: "#ff75b5", weight: 2.2, fillColor: "#ff75b5", fillOpacity: 0.08 },
      onEachFeature(feature, item) { item.bindPopup(basePopup(feature)); },
    });
    for (const feature of data.features) {
      state.uchastikFeatureLayers.set(String(feature.id), L.geoJSON(feature, {
        pane: "uchastikPane",
        style: { color: "#ff75b5", weight: 3, fillColor: "#ff75b5", fillOpacity: 0.14 },
        onEachFeature(itemFeature, item) { item.bindPopup(basePopup(itemFeature)); },
      }));
    }
    registerBaseControl("uchastik", "Uchastik", `${data.features.length} polygon`, layer, true, "uchastik");
  };

  const boundaryPoint = (feature, latlng) => {
    const props = feature.properties || {};
    const label = props.block_num || props.text_value || props.uchastic_id || "";
    return L.marker(latlng, {
      pane: "labelsPane",
      interactive: true,
      icon: L.divIcon({ className: "map-label", html: `<span>${escapeHtml(label)}</span>` }),
    });
  };

  const loadBoundaries = async () => {
    const config = dataset("base-survey-boundaries");
    const data = await fetchJson(config.webAsset);
    assertProjectTruth(data, "Survey boundaries");
    const blockTypes = new Set(["block_boundary", "block_label"]);
    const blockFeatures = data.features.filter((feature) => blockTypes.has(feature.properties?.dataType));
    const cadFeatures = data.features.filter((feature) => !blockTypes.has(feature.properties?.dataType));
    const referenceFeatures = cadFeatures.filter((feature) => (
      feature.properties?.dataType === "uchastik_boundary" && geometryWithinNergui(feature.geometry)
    ));
    const hiddenLabelCount = cadFeatures.filter((feature) => feature.properties?.dataType === "uchastik_label").length;
    const unlocatedCadCount = cadFeatures.filter((feature) => !geometryWithinNergui(feature.geometry)).length;
    const blockLayer = L.geoJSON({ type: "FeatureCollection", features: blockFeatures }, {
      pane: "surveyPane",
      style: { color: "#ffb84d", weight: 2, fillColor: "#ffb84d", fillOpacity: 0.05, dashArray: "5 4" },
      pointToLayer: boundaryPoint,
      onEachFeature(feature, item) { item.bindPopup(basePopup(feature)); },
    });
    const cadLayer = L.geoJSON({ type: "FeatureCollection", features: referenceFeatures }, {
      pane: "surveyPane",
      style(feature) {
        const type = feature.properties?.dataType;
        if (type === "cad_reference_geometry") {
          return { color: "#f4f7fb", weight: 2.8, opacity: 0.98, fillColor: "#dbeafe", fillOpacity: 0.055 };
        }
        if (type === "uchastik_boundary") {
          return { color: "#d6a5ff", weight: 2.1, opacity: 0.9, fillColor: "#d6a5ff", fillOpacity: 0.035 };
        }
        return { color: "#c5d2df", weight: 1.8, opacity: 0.9, fillColor: "#c5d2df", fillOpacity: 0.025 };
      },
      pointToLayer: boundaryPoint,
      onEachFeature(feature, item) { item.bindPopup(basePopup(feature)); },
    });
    registerBaseControl("blocks", "Block boundaries", `${blockFeatures.length} DWG-derived feature`, blockLayer, false, "block");
    registerBaseControl(
      "cad",
      "References",
      `${referenceFeatures.length} boundary · ${hiddenLabelCount} label hidden · ${unlocatedCadCount} unlocated`,
      cadLayer,
      false,
      "cad",
    );
  };

  const loadMagArrowPlan = async () => {
    const config = dataset("magarrow-planned-survey");
    const data = await fetchJson(config.webAsset);
    assertProjectTruth(data, "MagArrow planned survey");
    const boundaryFeatures = data.features.filter((feature) => feature.properties?.dataType === "planned_survey_boundary");
    const mainFeatures = data.features.filter((feature) => feature.properties?.dataType === "planned_main_line");
    const tieFeatures = data.features.filter((feature) => feature.properties?.dataType === "planned_tie_line");
    state.planArea = boundaryFeatures.reduce((sum, feature) => sum + featureArea(feature), 0);
    state.mainCoverage = mainFeatures.reduce((sum, feature) => sum + lineLength(feature) * (Number(feature.properties?.spacing_m) || 100), 0);
    const boundary = L.geoJSON({ type: "FeatureCollection", features: boundaryFeatures }, {
      pane: "surveyPane",
      style: { color: "#39d9ff", weight: 2.6, fillColor: "#39d9ff", fillOpacity: 0.035 },
      onEachFeature(feature, item) { item.bindPopup(plannedPopup(feature)); },
    });
    const main = L.geoJSON({ type: "FeatureCollection", features: mainFeatures }, {
      pane: "plannedPane",
      style: { color: "#71f6c1", weight: 2.2, opacity: 0.94 },
      onEachFeature(feature, item) { item.bindPopup(plannedPopup(feature)); },
    });
    const tie = L.geoJSON({ type: "FeatureCollection", features: tieFeatures }, {
      pane: "plannedPane",
      style: { color: "#39a8ff", weight: 1.7, opacity: 0.9, dashArray: "8 6" },
      onEachFeature(feature, item) { item.bindPopup(plannedPopup(feature)); },
    });
    state.planLayers.set("boundary", boundary);
    state.planLayers.set("main", main);
    state.planLayers.set("tie", tie);
    restoreMagArrowLayers();
  };

  const flightDateColor = () => "#39d9ff";

  const pointInRing = ([x, y], ring) => {
    let inside = false;
    for (let index = 0, previous = ring.length - 1; index < ring.length; previous = index++) {
      const [x1, y1] = ring[previous];
      const [x2, y2] = ring[index];
      if ((y1 > y) !== (y2 > y) && x < ((x2 - x1) * (y - y1)) / (y2 - y1) + x1) inside = !inside;
    }
    return inside;
  };

  const pointInPolygon = (point, polygon) => polygon?.length
    && pointInRing(point, polygon[0])
    && !polygon.slice(1).some((hole) => pointInRing(point, hole));

  const orientation = (a, b, c) => Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
  const onSegment = (a, b, point) => point[0] >= Math.min(a[0], b[0]) && point[0] <= Math.max(a[0], b[0])
    && point[1] >= Math.min(a[1], b[1]) && point[1] <= Math.max(a[1], b[1]);
  const segmentsIntersect = (a, b, c, d) => {
    const o1 = orientation(a, b, c);
    const o2 = orientation(a, b, d);
    const o3 = orientation(c, d, a);
    const o4 = orientation(c, d, b);
    if (o1 !== o2 && o3 !== o4) return true;
    return (o1 === 0 && onSegment(a, b, c)) || (o2 === 0 && onSegment(a, b, d))
      || (o3 === 0 && onSegment(c, d, a)) || (o4 === 0 && onSegment(c, d, b));
  };

  const lineIntersectsPolygon = (line, polygon) => {
    if (line.some((point) => pointInPolygon(point, polygon))) return true;
    for (let index = 1; index < line.length; index += 1) {
      for (const ring of polygon || []) {
        for (let edge = 1; edge < ring.length; edge += 1) {
          if (segmentsIntersect(line[index - 1], line[index], ring[edge - 1], ring[edge])) return true;
        }
      }
    }
    return false;
  };

  const trackIntersectsAreaScope = (feature) => {
    const scopeFeatures = state.areaScope?.features || [];
    if (!scopeFeatures.length || state.areaScope?.label === "Бүх лиценз") return true;
    const lineGeometry = feature.geometry || {};
    const lines = lineGeometry.type === "MultiLineString" ? lineGeometry.coordinates : [lineGeometry.coordinates || []];
    return scopeFeatures.some((scopeFeature) => {
      const geometry = scopeFeature.geometry || {};
      const polygons = geometry.type === "MultiPolygon" ? geometry.coordinates : geometry.type === "Polygon" ? [geometry.coordinates] : [];
      return lines.some((line) => polygons.some((polygon) => lineIntersectsPolygon(line, polygon)));
    });
  };

  const actualFeaturesInArea = () => (state.actualTrackData?.features || [])
    .filter((feature) => trackIntersectsAreaScope(feature));

  const selectedActualFeatures = () => actualFeaturesInArea().filter((feature) => (
    state.selectedFlightDate === "all" || feature.properties?.date === state.selectedFlightDate
  ));

  const selectedActualLayers = () => [...state.actualTrackLayers.values()];

  const actualTrackLayer = (features) => L.geoJSON({ type: "FeatureCollection", features }, {
    pane: "actualPane",
    style: { color: flightDateColor(), weight: 2.8, opacity: 0.98 },
    onEachFeature(feature, item) { item.bindPopup(actualPopup(feature)); },
  });

  const syncActualTrackVisibility = () => {
    for (const layer of state.actualTrackLayers.values()) state.map.removeLayer(layer);
    state.actualTrackLayers.clear();
    if (state.activeSensor !== "MagArrow") return;
    const grouped = new Map();
    for (const feature of selectedActualFeatures()) {
      const date = feature.properties?.date;
      if (!grouped.has(date)) grouped.set(date, []);
      grouped.get(date).push(feature);
    }
    for (const [date, features] of grouped) {
      const layer = actualTrackLayer(features);
      state.actualTrackLayers.set(date, layer);
      layer.addTo(state.map);
    }
  };

  const loadActualTracks = async () => {
    const trackConfig = dataset("magarrow-actual-tracks");
    const coverageConfig = dataset("magarrow-coverage-stats");
    const [data, coverage] = await Promise.all([
      fetchJson(trackConfig.webAsset),
      fetchJson(coverageConfig.webAsset),
    ]);
    assertProjectTruth(data, "MagArrow actual tracks");
    if (coverage.acquisitionCount !== data.features.length) {
      throw new Error("Actual trajectory болон coverage count зөрүүтэй байна");
    }
    state.actualTrackData = data;
    state.actualCoverage = coverage;
    syncActualTrackVisibility();
    renderAreaSummary();
  };

  const restoreMagArrowLayers = () => {
    if (state.activeSensor !== "MagArrow") return;
    for (const [id, layer] of state.planLayers) {
      if (state.planVisibility[id]) layer.addTo(state.map);
    }
    for (const [id, layer] of state.missionLayers) {
      if (state.selectedMissions.has(id)) layer.addTo(state.map);
    }
    syncActualTrackVisibility();
  };

  const hideSensorLayers = () => {
    for (const layer of state.planLayers.values()) state.map.removeLayer(layer);
    for (const layer of state.missionLayers.values()) state.map.removeLayer(layer);
    for (const layer of state.actualTrackLayers.values()) state.map.removeLayer(layer);
    for (const layer of state.projectFlightLayers.values()) state.map.removeLayer(layer);
  };

  const loadMissionPlans = async () => {
    if (state.missionData) return state.missionData;
    if (state.missionPromise) return state.missionPromise;
    const config = dataset("magarrow-mission-plans");
    state.missionPromise = fetchJson(config.webAsset).then((data) => {
      assertProjectTruth(data, "MagArrow mission plans");
      state.missionData = data;
      for (const plan of data.plans || []) {
        const features = data.features.filter((feature) => (feature.properties?.mission_id || feature.properties?.plan) === plan.label);
        const layer = L.geoJSON({ type: "FeatureCollection", features }, {
          pane: "missionPane",
          style: { color: "#ff9f43", weight: 2.4, opacity: 0.96, dashArray: "11 6" },
          onEachFeature(feature, item) { item.bindPopup(missionPopup(feature)); },
        });
        state.missionLayers.set(plan.label, layer);
      }
      return data;
    }).catch((error) => {
      state.missionPromise = null;
      throw error;
    });
    return state.missionPromise;
  };

  const updateMagArrowSummary = () => {
    if (state.selectedMissions.size && state.missionData) {
      state.activeDatasetId = "magarrow-mission-plans";
    } else if (state.selectedFlightDate) {
      state.activeDatasetId = "magarrow-actual-tracks";
    } else {
      state.activeDatasetId = "magarrow-planned-survey";
    }
    renderDatasetInfo();
  };

  const selectFlightDate = (date, shouldFit = true) => {
    state.selectedFlightDate = date;
    syncActualTrackVisibility();
    for (const button of ui.sensorPanel.querySelectorAll("[data-flight-date]")) {
      button.classList.toggle("is-active", button.dataset.flightDate === date);
    }
    state.activeDatasetId = "magarrow-actual-tracks";
    renderDatasetInfo();
    renderAreaSummary();
    if (shouldFit) {
      const layers = selectedActualLayers();
      if (layers.length) fitSingleLayer(L.featureGroup(layers));
    }
  };

  const makeToggle = (label, detail, checked, onChange, className = "") => {
    const row = document.createElement("label");
    row.className = `toggle-row ${className}`.trim();
    row.innerHTML = `<span class="toggle-copy"><span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(detail)}</small></span></span><span class="switch"><input type="checkbox" ${checked ? "checked" : ""}/><span aria-hidden="true"></span></span>`;
    row.querySelector("input").addEventListener("change", (event) => onChange(event.target.checked, event.target));
    return row;
  };

  const renderMagArrowPanel = () => {
    const missions = dataset("magarrow-mission-plans")?.missions || [];
    const scopedFeatures = actualFeaturesInArea();
    const dates = [...new Set(scopedFeatures.map((feature) => feature.properties?.date).filter(Boolean))].sort();
    const acquisitionCount = scopedFeatures.length;
    ui.sensorPanel.innerHTML = `
      <div class="sensor-heading"><div><strong>MagArrow</strong><span>Heseg Uul hoid</span></div><span class="status-badge available">TRACKS AVAILABLE</span></div>
      <h3>Planned Survey Lines</h3>
      <div id="plan-toggles" class="control-list"></div>
      <details class="nested-panel">
        <summary>DJI Mission Plans <span>${missions.length}</span></summary>
        <p class="panel-note">L01–L11 нь planned WPMZ/KMZ route. Actual flown track биш.</p>
        <div id="mission-list" class="mission-list"></div>
      </details>
      <h3>Ниссэн trajectory</h3>
      <div id="flight-date-list" class="flight-date-list"></div>
      <p class="panel-note">${acquisitionCount} acquisition · Огноо сонгоход тухайн өдрийн trajectory болон ниссэн талбай харагдана.</p>`;
    const planToggles = ui.sensorPanel.querySelector("#plan-toggles");
    const planRows = [
      ["boundary", "Survey boundary", "Planning footprint"],
      ["main", "Main lines", "E-W · 100 м · AZ≈88°/268°"],
      ["tie", "Tie lines", "N-S · 200 м · AZ≈178°/358°"],
    ];
    for (const [id, label, detail] of planRows) {
      planToggles.appendChild(makeToggle(label, detail, state.planVisibility[id], (checked) => {
        state.planVisibility[id] = checked;
        const layer = state.planLayers.get(id);
        if (!layer) return;
        if (checked) layer.addTo(state.map);
        else state.map.removeLayer(layer);
      }));
    }
    const flightDateList = ui.sensorPanel.querySelector("#flight-date-list");
    const dateOptions = [{ value: "all", label: "Бүх огноо", count: acquisitionCount }]
      .concat(dates.map((date) => ({
        value: date,
        label: date,
        count: scopedFeatures.filter((feature) => feature.properties?.date === date).length,
      })));
    for (const option of dateOptions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `flight-date-button${state.selectedFlightDate === option.value ? " is-active" : ""}`;
      button.dataset.flightDate = option.value;
      button.style.setProperty("--date-color", flightDateColor());
      button.innerHTML = `<span>${escapeHtml(option.label)}</span><small>${option.count} track</small>`;
      button.addEventListener("click", () => selectFlightDate(option.value));
      flightDateList.appendChild(button);
    }
    const missionList = ui.sensorPanel.querySelector("#mission-list");
    const allMissionsSelected = missions.length > 0
      && missions.every((mission) => state.selectedMissions.has(mission.id));
    missionList.appendChild(makeToggle("Бүх mission plan", `L01–L${String(missions.length).padStart(2, "0")} · ${missions.length} planned route`, allMissionsSelected, async (checked, input) => {
      input.disabled = true;
      try {
        await loadMissionPlans();
        for (const mission of missions) {
          const layer = state.missionLayers.get(mission.id);
          if (checked) {
            state.selectedMissions.add(mission.id);
            if (state.activeSensor === "MagArrow") layer?.addTo(state.map);
          } else {
            state.selectedMissions.delete(mission.id);
            if (layer) state.map.removeLayer(layer);
          }
        }
        updateMagArrowSummary();
      } catch (error) {
        input.checked = false;
        addWarning("mission-plans", `Mission plans: ${error.message}`);
      } finally {
        input.disabled = false;
      }
    }));
    for (const button of ui.sensorPanel.querySelectorAll("[data-dataset]")) {
      button.addEventListener("click", () => {
        state.activeDatasetId = button.dataset.dataset;
        renderDatasetInfo();
      });
    }
    updateMagArrowSummary();
  };

  const renderTrackerSensorPanel = (sensor) => {
    const project = state.trackerData?.projects?.[state.selectedTrackerProject];
    if (!project?.sensors?.[sensor]) return;
    const records = (state.trackerData.records || []).filter((record) => (
      record.projectKey === state.selectedTrackerProject && record.sensor === sensor
    ));
    const allGeometry = projectFlightFeatures(state.selectedTrackerProject, sensor, "all");
    const geometryByTrackerId = new Map(allGeometry.map((feature) => [feature.properties?.trackerId, feature]));
    const trajectoryDates = [...new Set(allGeometry
      .map((feature) => feature.properties?.date).filter(Boolean))].sort().reverse();
    const selectedRecords = state.selectedTrackerDate === "all"
      ? records
      : records.filter((record) => (
        record.date === state.selectedTrackerDate
        || geometryByTrackerId.get(record.id)?.properties?.date === state.selectedTrackerDate
      ));
    const geometryCount = allGeometry.length;
    const visibleGeometryCount = projectFlightFeatures(state.selectedTrackerProject, sensor, state.selectedTrackerDate).length;
    const cadReferences = trackerDateCadReferences(state.selectedTrackerProject, sensor, state.selectedTrackerDate);
    const cadReferenceLabels = cadReferences.map((feature) => feature.properties?.text_value).filter(Boolean);
    const dateOptions = geometryCount ? [{ value: "all", label: "Бүх trajectory", count: geometryCount }]
      .concat(trajectoryDates.map((date) => ({
        value: date,
        label: date,
        count: projectFlightFeatures(state.selectedTrackerProject, sensor, date).length,
      }))) : [];
    const missionRows = selectedRecords.map((record) => {
      const geometry = geometryByTrackerId.get(record.id);
      const hasGeometry = Boolean(geometry);
      const displayDate = geometry?.properties?.date || record.date;
      return `<div class="tracker-flight-record${hasGeometry ? " has-geometry" : ""}"><strong>${escapeHtml(record.mission || record.id)}</strong><span>${escapeHtml(displayDate)} · ${escapeHtml(record.altitudeM ? `${record.altitudeM} м` : "өндөргүй")}</span><b>${hasGeometry ? "TRAJECTORY" : "REGISTER"}</b></div>`;
    }).join("");
    ui.sensorPanel.innerHTML = `
      <div class="sensor-heading"><div><strong>${escapeHtml(sensor)}</strong><span>${escapeHtml(project.label)} · ${escapeHtml(project.licence)}</span></div><span class="status-badge ${geometryCount ? "available" : "survey"}">${geometryCount ? `${geometryCount} TRAJECTORY` : "ACTUAL REGISTER"}</span></div>
      <h3>Ниссэн trajectory өдөр</h3>
      ${geometryCount ? '<div id="tracker-flight-date-list" class="flight-date-list"></div>' : '<p class="truth-note">Энэ sensor-д coordinate бүхий trajectory файл олдоогүй. Өдрийн бүртгэлийг доороос харна уу.</p>'}
      <h3>Бодит нислэгийн бүртгэл</h3>
      <div class="tracker-flight-records">${missionRows}</div>
      ${state.selectedTrackerDate !== "all" && !visibleGeometryCount && cadReferenceLabels.length
        ? `<p class="truth-note">Trajectory координат хараахан ирээгүй. Mission нэртэй таарсан CAD хэсэг рүү төвлөрөв: ${escapeHtml(cadReferenceLabels.join(", "))}.</p>`
        : ""}
      <p class="panel-note">${formatCount(selectedRecords.length)} бүртгэл · ${formatCount(visibleGeometryCount)} баталгаажсан trajectory. MRK/KMZ/flight-log байхгүй mission-ийг шугам болгон таамаглаагүй.</p>`;
    const dateList = ui.sensorPanel.querySelector("#tracker-flight-date-list");
    for (const option of dateOptions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `flight-date-button${state.selectedTrackerDate === option.value ? " is-active" : ""}`;
      button.dataset.trackerDate = option.value;
      button.style.setProperty("--date-color", "#39d9ff");
      button.innerHTML = `<span>${escapeHtml(option.label)}</span><small>${formatCount(option.count)} trajectory</small>`;
      button.addEventListener("click", () => selectTrackerDate(option.value));
      dateList?.appendChild(button);
    }
    state.activeDatasetId = geometryCount ? "context-project-flight-tracks" : "context-project-trackers";
    renderDatasetInfo();
  };

  const selectTrackerDate = (date, shouldFit = true) => {
    state.selectedTrackerDate = date;
    syncProjectFlightVisibility();
    renderTrackerProject(state.selectedTrackerProject);
    renderTrackerSensorPanel(state.selectedTrackerSensor);
    renderAreaSummary();
    if (shouldFit) fitTrackerDateSelection();
  };

  const renderStatusPanel = (sensor) => {
    const mapping = {
      L3: {
        datasetId: "l3-survey-family",
        title: "Zenmuse L3",
        badge: "CONFIRMED SURVEY DATA",
        tone: "survey",
        body: "Sant Uul survey family, metadata болон derived products баталгаажсан.",
        extra: `<div class="block-grid">${["N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9"].map((block) => `<span>${block}</span>`).join("")}</div><p class="truth-note">Actual trajectory: NOT AVAILABLE / NOT CONFIRMED</p>`,
      },
      L2: {
        datasetId: "l2-source", title: "Zenmuse L2", badge: "SOURCE PENDING", tone: "pending",
        body: "Canonical LiDAR source structure байна. Nergui Undur actual flight track баталгаажаагүй.", extra: "",
      },
      P1: {
        datasetId: "p1-source", title: "Zenmuse P1", badge: "SOURCE PENDING", tone: "pending",
        body: "Canonical RGB photogrammetry source байна. Raw mission/trajectory баталгаажаагүй.", extra: "",
      },
      Medusa: {
        datasetId: "medusa-source", title: "Medusa MS-700", badge: "NO FLIGHT DATA INGESTED", tone: "unavailable",
        body: "SOP/specification баримт байна. Actual field-flight/acquisition data баталгаажаагүй.", extra: "",
      },
    };
    const config = mapping[sensor];
    state.activeDatasetId = config.datasetId;
    ui.sensorPanel.innerHTML = `<div class="sensor-heading"><div><strong>${escapeHtml(config.title)}</strong><span>Nergui Undur</span></div><span class="status-badge ${config.tone}">${escapeHtml(config.badge)}</span></div><p class="status-copy">${escapeHtml(config.body)}</p>${config.extra}`;
    renderDatasetInfo();
  };

  const renderSensorButtons = () => {
    ui.sensors.replaceChildren();
    const project = state.trackerData?.projects?.[state.selectedTrackerProject];
    const sensors = project
      ? Object.entries(project.sensors || {}).map(([id, stats]) => {
        const base = sensorConfig.find((item) => item.id === id) || { id, label: id };
        const geometryCount = projectFlightFeatures(state.selectedTrackerProject, id, "all").length;
        return {
          ...base,
          status: geometryCount ? `${geometryCount} TRAJECTORY` : `${formatCount(stats.records)} ACTUAL RECORDS`,
          tone: geometryCount ? "available" : "survey",
        };
      })
      : sensorConfig;
    for (const sensor of sensors) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `sensor-button ${sensor.tone} sensor-${sensor.id.toLowerCase()}${state.activeSensor === sensor.id ? " is-active" : ""}`;
      button.dataset.sensor = sensor.id;
      button.innerHTML = `<span>${escapeHtml(sensor.label)}</span><small class="${sensor.tone}">${escapeHtml(sensor.status)}</small>`;
      button.addEventListener("click", () => selectSensor(sensor.id));
      ui.sensors.appendChild(button);
    }
  };

  const selectSensor = (sensor) => {
    state.activeSensor = sensor;
    hideSensorLayers();
    const project = state.trackerData?.projects?.[state.selectedTrackerProject];
    if (project?.sensors?.[sensor]) {
      state.selectedTrackerSensor = sensor;
      state.selectedTrackerDate = "all";
      renderSensorButtons();
      syncProjectFlightVisibility();
      renderTrackerProject(state.selectedTrackerProject);
      renderTrackerSensorPanel(sensor);
      renderAreaSummary();
      return;
    }
    renderSensorButtons();
    if (sensor === "MagArrow") {
      restoreMagArrowLayers();
      renderMagArrowPanel();
    } else {
      renderStatusPanel(sensor);
    }
  };

  const renderDatasetInfo = () => {
    const config = dataset(state.activeDatasetId);
    if (!config) {
      ui.datasetInfo.replaceChildren();
      ui.sourceLinks.replaceChildren();
      return;
    }
    const verified = config.crsVerified === true ? "VERIFIED" : config.crsVerified === false ? "REVIEW / UNVERIFIED" : "N/A";
    const rows = [
      ["Dataset", config.id],
      ["Type", config.dataType],
      ["State", config.plannedActual],
      ["Source CRS", config.sourceCrs || "Not published"],
      ["Web CRS", config.displayCrs || "No web geometry"],
      ["CRS status", verified],
    ];
    ui.datasetInfo.innerHTML = rows.map(([term, value]) => `<div><dt>${escapeHtml(term)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
    const selectedTracker = state.trackerData?.projects?.[state.selectedTrackerProject];
    const links = config.scope === "project_operations" && selectedTracker
      ? [
        { sourceUrl: selectedTracker.url, dataType: `${selectedTracker.key}_tracker` },
        ...projectFlightFeatures().map((feature) => ({
          sourceUrl: feature.properties?.sourceUrl,
          dataType: `${feature.properties?.sensor || "flight"}_${feature.properties?.date || "trajectory"}`,
        })),
      ].filter((item) => item.sourceUrl)
      : [...state.datasets.values()].filter((item) => item.sensor === config.sensor && item.sourceUrl);
    const unique = new Map();
    for (const item of links) if (!unique.has(item.sourceUrl)) unique.set(item.sourceUrl, item);
    ui.sourceLinks.innerHTML = [...unique.values()].map((item) => `<a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.dataType.replaceAll("_", " "))}<span>↗</span></a>`).join("");
  };

  const visibleLayers = () => {
    const layers = [];
    for (const item of state.baseLayers.values()) if (item.visible && state.map.hasLayer(item.layer)) layers.push(item.layer);
    for (const layer of state.uchastikFeatureLayers.values()) if (state.map.hasLayer(layer)) layers.push(layer);
    for (const layer of state.licenseContextLayers.values()) if (state.map.hasLayer(layer)) layers.push(layer);
    for (const layer of state.projectControlLayers.values()) if (state.map.hasLayer(layer)) layers.push(layer);
    for (const layer of selectedProjectFlightLayers()) if (state.map.hasLayer(layer)) layers.push(layer);
    if (state.activeSensor === "MagArrow") {
      for (const [id, layer] of state.planLayers) if (state.planVisibility[id]) layers.push(layer);
      for (const [id, layer] of state.missionLayers) if (state.selectedMissions.has(id)) layers.push(layer);
      for (const layer of selectedActualLayers()) if (state.map.hasLayer(layer)) layers.push(layer);
    }
    return layers;
  };

  const fitMap = () => {
    const group = L.featureGroup(visibleLayers());
    const bounds = group.getBounds();
    if (bounds.isValid()) state.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
  };

  const removeAllDataLayers = () => {
    for (const item of state.baseLayers.values()) state.map.removeLayer(item.layer);
    for (const layer of state.licenseContextLayers.values()) state.map.removeLayer(layer);
    for (const layer of state.uchastikFeatureLayers.values()) state.map.removeLayer(layer);
    for (const layer of state.projectControlLayers.values()) state.map.removeLayer(layer);
    hideSensorLayers();
    state.baseLayers.clear();
    state.licenceData = null;
    state.licenseContextLayers.clear();
    state.licenseContextData = null;
    state.selectedContextLicense = null;
    state.hetsuuCadData = null;
    state.hetsuuCadLayer = null;
    state.hetsuuCadLabelLayers = [];
    state.trackerData = null;
    state.projectControlData = null;
    state.projectControlLayers.clear();
    state.selectedTrackerProject = null;
    state.selectedTrackerSensor = null;
    state.selectedTrackerDate = "all";
    state.projectFlightData = null;
    state.projectFlightCoverage = null;
    state.projectFlightLayers.clear();
    state.licenseBrowserMode = "licence";
    state.uchastikData = null;
    state.uchastikFeatureLayers.clear();
    state.selectedUchastikKey = null;
    state.areaScope = null;
    state.planLayers.clear();
    state.missionLayers.clear();
    state.selectedMissions.clear();
    state.missionData = null;
    state.missionPromise = null;
    state.actualTrackData = null;
    state.actualCoverage = null;
    state.actualTrackLayers.clear();
    state.selectedFlightDate = "all";
    state.planArea = 0;
    state.mainCoverage = 0;
    ui.baseLayers.replaceChildren();
    ui.licenseContextList.replaceChildren();
    ui.licenseBrowserTitle.textContent = "Лицензийн талбай сонгох";
    ui.licenseCount.textContent = "—";
    ui.clearLicenseContext.hidden = true;
    ui.licenseBrowser.classList.remove("is-uchastik");
    ui.projectTrackerPanel.hidden = true;
    ui.projectTrackerPanel.replaceChildren();
  };

  const load = async () => {
    ensureMap();
    ui.loading.hidden = false;
    ui.error.hidden = true;
    clearWarnings();
    removeAllDataLayers();
    try {
      const [manifest, registry] = await Promise.all([
        fetchJson("./data/manifest.json"),
        fetchJson("./data/datasets.json"),
      ]);
      if (registry.project !== PROJECT) throw new Error(`Registry project must be ${PROJECT}`);
      state.manifest = manifest;
      state.registry = registry;
      state.datasets = new Map(registry.datasets.map((item) => [item.id, item]));
      const jobs = [
        { label: "Licence", task: loadLicence, errorType: "base" },
        { label: "Uchastik", task: loadUchastik, errorType: "base" },
        { label: "Survey boundaries", task: loadBoundaries, errorType: "base" },
        { label: "Project trackers", task: loadProjectTrackers, errorType: "warning" },
        { label: "Project flight tracks", task: loadProjectFlights, errorType: "warning" },
        { label: "Hetsuu hutul DWG", task: loadHetsuuCad, errorType: "warning" },
        { label: "MagArrow planned survey", task: loadMagArrowPlan, errorType: "warning" },
        { label: "MagArrow actual tracks", task: loadActualTracks, errorType: "warning" },
        { label: "Licence context", task: loadLicenceContext, errorType: "warning" },
      ];
      const results = await Promise.allSettled(jobs.map(({ task }) => task()));
      results.forEach((result, index) => {
        if (result.status === "rejected") {
          const job = jobs[index];
          if (job.errorType === "base") registerUnavailableBase(job.label, result.reason);
          else addWarning(job.label.toLowerCase().replaceAll(" ", "-"), `${job.label}: ${result.reason.message}`);
        }
      });
      renderSensorButtons();
      selectSensor(state.activeSensor);
      const defaultLicence = state.licenceData?.features?.[0];
      setAreaScope(featureLabel(defaultLicence, "Нэргүй өндөр"), defaultLicence, "licence");
      fitMap();
      const usable = state.baseLayers.size + state.planLayers.size;
      if (!usable) throw new Error("No base or active sensor layers could be loaded.");
    } catch (error) {
      console.error(error);
      ui.errorMessage.textContent = error.message || "Registry/manifest уншигдсангүй.";
      ui.error.hidden = false;
    } finally {
      ui.loading.hidden = true;
    }
  };

  ui.fit.addEventListener("click", fitMap);
  ui.clearLicenseContext.addEventListener("click", returnToLicenceBrowser);
  ui.refresh.addEventListener("click", load);
  ui.retry.addEventListener("click", load);
  load();
})();
