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

  const assertLicenceContext = (payload) => assertExternalReference(payload, "Licence context", "licence_context");

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
    const coverage = state.areaScope?.coverageKey
      ? state.actualCoverage?.scopes?.[state.areaScope.coverageKey]
      : null;
    const selectedDate = state.selectedFlightDate !== "all" ? state.selectedFlightDate : null;
    const dailyAreas = Object.values(coverage?.dailyAreaM2 || {}).filter(Number.isFinite);
    const selectedArea = selectedDate
      ? coverage?.dailyAreaM2?.[selectedDate]
      : coverage
        ? dailyAreas.reduce((sum, area) => sum + area, 0)
        : NaN;
    ui.primaryLabel.textContent = "Нийт талбай";
    ui.primaryValue.textContent = formatArea(totalArea);
    ui.secondaryLabel.textContent = "Ниссэн нийт талбай";
    ui.secondaryValue.textContent = formatArea(coverage?.totalAreaM2);
    ui.tertiaryLabel.textContent = selectedDate ? "Өдрийн ниссэн талбай" : "Бүх өдрийн нийлбэр";
    ui.tertiaryValue.textContent = formatArea(selectedArea);
    ui.summaryNote.textContent = coverage
      ? `${state.areaScope.label} · ${selectedDate || "Бүх огноо"} · GNSS trajectory-д суурилсан 50 м зурвасын тооцоо.${selectedDate ? "" : " Өдрийн нийлбэрт огноо хоорондын давхардал орж болно."}`
      : state.areaScope
        ? `${state.areaScope.label} · Энэ сонголтод нислэгийн талбайн тооцоо байхгүй.`
        : "Талбай сонгоход үзүүлэлт шинэчлэгдэнэ.";
  };

  const setAreaScope = (label, features, coverageKey = null) => {
    state.areaScope = {
      label,
      features: (Array.isArray(features) ? features : [features]).filter(Boolean),
      coverageKey,
    };
    renderAreaSummary();
  };

  const registerBaseControl = (id, label, detail, layer, visible, tone = "base") => {
    if (visible) layer.addTo(state.map);
    const row = document.createElement("label");
    row.className = "toggle-row";
    row.style.order = String({ licence: 1, uchastik: 2, blocks: 3, cad: 4, hetsuuCad: 5 }[id] || 99);
    row.innerHTML = `
      <span class="toggle-copy"><span class="mini-symbol ${tone}" aria-hidden="true"></span><span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(detail)}</small></span></span>
      <span class="switch"><input type="checkbox" ${visible ? "checked" : ""} /><span aria-hidden="true"></span></span>`;
    const input = row.querySelector("input");
    state.baseLayers.set(id, { layer, visible, label, input });
    input.addEventListener("change", (event) => {
      state.baseLayers.get(id).visible = event.target.checked;
      if (id === "uchastik") syncUchastikLayerVisibility();
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
    const cadControl = state.baseLayers.get("hetsuuCad");
    if (cadControl) {
      cadControl.visible = false;
      if (cadControl.input) cadControl.input.checked = false;
    }
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

  const isHetsuuLicence = (feature) => {
    const props = feature?.properties || {};
    const value = `${props.AREANAME || ""} ${props.AREANAME_L || ""} ${props.LICENSE || ""}`.toLowerCase();
    return value.includes("hetsuu hutul") || value.includes("xv-022905");
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
      } else if (isHetsuuLicence(feature) && state.hetsuuCadLayer) {
        const cadControl = state.baseLayers.get("hetsuuCad");
        if (cadControl) {
          cadControl.visible = true;
          if (cadControl.input) cadControl.input.checked = true;
        }
        state.hetsuuCadLayer.addTo(state.map);
        syncHetsuuCadLabels();
        fitSingleLayer(L.featureGroup([layer, state.hetsuuCadLayer].filter(Boolean)));
        setAreaScope(featureLabel(feature, key), feature, null);
        activeContextDataset = "context-hetsuu-hutul-dwg";
      } else {
        fitSingleLayer(layer);
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
      radius: 2.2,
      color: "#56d7ff",
      weight: 1,
      fillColor: "#56d7ff",
      fillOpacity: 0.7,
    });
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
    const layer = L.geoJSON(data, {
      pane: "surveyPane",
      style(feature) {
        const role = feature.properties?.displayRole;
        if (role === "boundary") return { color: "#ffca5c", weight: 3, opacity: 1, fillColor: "#ffca5c", fillOpacity: 0.045 };
        if (role === "block") return { color: "#ff8fb8", weight: 1.8, opacity: 0.9, fillColor: "#ff8fb8", fillOpacity: 0.04 };
        if (role === "section") return { color: "#71f6c1", weight: 1.7, opacity: 0.86, fillColor: "#71f6c1", fillOpacity: 0.03 };
        return { color: "#56d7ff", weight: 1.5, opacity: 0.82, fillColor: "#56d7ff", fillOpacity: 0.025 };
      },
      pointToLayer: hetsuuCadPoint,
      onEachFeature(feature, item) { item.bindPopup(basePopup(feature)); },
    });
    state.hetsuuCadLayer = layer;
    registerBaseControl(
      "hetsuuCad",
      "Hetsuu hutul DWG",
      `${data.featureCount || data.features.length} feature · ${data.droppedUnlocatedCount || 0} unlocated hidden`,
      layer,
      false,
      "hetsuu",
    );
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

  const selectedActualLayers = () => {
    if (state.selectedFlightDate === "all") return [...state.actualTrackLayers.values()];
    const layer = state.actualTrackLayers.get(state.selectedFlightDate);
    return layer ? [layer] : [];
  };

  const syncActualTrackVisibility = () => {
    for (const layer of state.actualTrackLayers.values()) state.map.removeLayer(layer);
    if (state.activeSensor !== "MagArrow") return;
    for (const layer of selectedActualLayers()) layer.addTo(state.map);
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
    const grouped = new Map();
    for (const feature of data.features) {
      const date = feature.properties?.date;
      if (!grouped.has(date)) grouped.set(date, []);
      grouped.get(date).push(feature);
    }
    for (const [date, features] of grouped) {
      const color = flightDateColor(date);
      const layer = L.geoJSON({ type: "FeatureCollection", features }, {
        pane: "actualPane",
        style: { color, weight: 2.5, opacity: 0.95 },
        onEachFeature(feature, item) { item.bindPopup(actualPopup(feature)); },
      });
      state.actualTrackLayers.set(date, layer);
    }
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
    if (state.selectedFlightDate) {
      state.activeDatasetId = "magarrow-actual-tracks";
    } else if (state.selectedMissions.size && state.missionData) {
      state.activeDatasetId = "magarrow-mission-plans";
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
    const dates = state.actualCoverage?.dates || [];
    const acquisitionCount = state.actualCoverage?.acquisitionCount || 0;
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
      <button class="status-row" type="button" data-dataset="magarrow-measurements"><span>10 Hz measurements</span><em>OFF · SOURCE AVAILABLE</em></button>
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
        count: state.actualTrackData?.features.filter((feature) => feature.properties?.date === date).length || 0,
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
    for (const mission of missions) {
      missionList.appendChild(makeToggle(`${mission.id} · ${mission.date}`, "DJI mission plan · Planned", state.selectedMissions.has(mission.id), async (checked, input) => {
        input.disabled = true;
        try {
          await loadMissionPlans();
          const layer = state.missionLayers.get(mission.id);
          if (checked) {
            state.selectedMissions.add(mission.id);
            if (state.activeSensor === "MagArrow") layer?.addTo(state.map);
          } else {
            state.selectedMissions.delete(mission.id);
            if (layer) state.map.removeLayer(layer);
          }
          updateMagArrowSummary();
        } catch (error) {
          input.checked = false;
          addWarning("mission-plans", `Mission plans: ${error.message}`);
        } finally {
          input.disabled = false;
        }
      }));
    }
    for (const button of ui.sensorPanel.querySelectorAll("[data-dataset]")) {
      button.addEventListener("click", () => {
        state.activeDatasetId = button.dataset.dataset;
        renderDatasetInfo();
      });
    }
    updateMagArrowSummary();
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
    for (const sensor of sensorConfig) {
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
    const links = [...state.datasets.values()].filter((item) => item.sensor === config.sensor && item.sourceUrl);
    const unique = new Map();
    for (const item of links) if (!unique.has(item.sourceUrl)) unique.set(item.sourceUrl, item);
    ui.sourceLinks.innerHTML = [...unique.values()].map((item) => `<a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.dataType.replaceAll("_", " "))}<span>↗</span></a>`).join("");
  };

  const visibleLayers = () => {
    const layers = [];
    for (const item of state.baseLayers.values()) if (item.visible && state.map.hasLayer(item.layer)) layers.push(item.layer);
    for (const layer of state.uchastikFeatureLayers.values()) if (state.map.hasLayer(layer)) layers.push(layer);
    for (const layer of state.licenseContextLayers.values()) if (state.map.hasLayer(layer)) layers.push(layer);
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
    hideSensorLayers();
    state.baseLayers.clear();
    state.licenceData = null;
    state.licenseContextLayers.clear();
    state.licenseContextData = null;
    state.selectedContextLicense = null;
    state.hetsuuCadData = null;
    state.hetsuuCadLayer = null;
    state.hetsuuCadLabelLayers = [];
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
