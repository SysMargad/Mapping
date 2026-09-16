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
    manifestStatus: $("#manifest-status"),
    primaryLabel: $("#summary-label-primary"),
    primaryValue: $("#summary-value-primary"),
    secondaryLabel: $("#summary-label-secondary"),
    secondaryValue: $("#summary-value-secondary"),
    summaryNote: $("#summary-note"),
    baseLayers: $("#base-layer-list"),
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
    licenseContextLayers: new Map(),
    licenseContextData: null,
    selectedContextLicense: null,
    planLayers: new Map(),
    planVisibility: { boundary: true, main: true, tie: true },
    missionLayers: new Map(),
    selectedMissions: new Set(),
    missionData: null,
    missionPromise: null,
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

  const assertLicenceContext = (payload) => {
    if (payload?.scope !== "external_reference" || payload?.project !== "External licence reference") {
      throw new Error("Licence context must be isolated as an external reference dataset");
    }
    for (const feature of payload.features || []) {
      const props = feature.properties || {};
      if (props.dataType !== "licence_context" || props.contextOnly !== true) {
        throw new Error(`Licence context feature ${feature.id} is not marked context-only`);
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

  const setSummary = (primaryLabel, primaryValue, secondaryLabel, secondaryValue, note = "") => {
    ui.primaryLabel.textContent = primaryLabel;
    ui.primaryValue.textContent = primaryValue;
    ui.secondaryLabel.textContent = secondaryLabel;
    ui.secondaryValue.textContent = secondaryValue;
    ui.summaryNote.textContent = note;
  };

  const registerBaseControl = (id, label, detail, layer, visible, tone = "base") => {
    state.baseLayers.set(id, { layer, visible, label });
    if (visible) layer.addTo(state.map);
    const row = document.createElement("label");
    row.className = "toggle-row";
    row.style.order = String({ licence: 1, uchastik: 2, blocks: 3, cad: 4 }[id] || 99);
    row.innerHTML = `
      <span class="toggle-copy"><span class="mini-symbol ${tone}" aria-hidden="true"></span><span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(detail)}</small></span></span>
      <span class="switch"><input type="checkbox" ${visible ? "checked" : ""} /><span aria-hidden="true"></span></span>`;
    row.querySelector("input").addEventListener("change", (event) => {
      state.baseLayers.get(id).visible = event.target.checked;
      if (event.target.checked) layer.addTo(state.map);
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
    const layer = L.geoJSON(data, {
      pane: "licencePane",
      style: { color: "#ffd166", weight: 3, fillColor: "#ffd166", fillOpacity: 0.05, dashArray: "9 5" },
      onEachFeature(feature, item) { item.bindPopup(basePopup(feature)); },
    });
    registerBaseControl("licence", "Licence", `${data.features.length} polygon`, layer, true, "licence");
  };

  const clearLicenceContext = () => {
    for (const layer of state.licenseContextLayers.values()) state.map.removeLayer(layer);
    state.selectedContextLicense = null;
    ui.clearLicenseContext.hidden = true;
    for (const button of ui.licenseContextList.querySelectorAll("button")) button.classList.remove("is-active");
  };

  const fitSingleLayer = (layer) => {
    const bounds = layer?.getBounds?.();
    if (bounds?.isValid()) state.map.fitBounds(bounds, { padding: [44, 44], maxZoom: 15 });
  };

  const selectLicenceContext = (key, button) => {
    clearLicenceContext();
    state.selectedContextLicense = key;
    button.classList.add("is-active");
    ui.clearLicenseContext.hidden = false;
    if (key === "all") {
      for (const layer of state.licenseContextLayers.values()) layer.addTo(state.map);
      fitSingleLayer(L.featureGroup([...state.licenseContextLayers.values()]));
    } else {
      const layer = state.licenseContextLayers.get(key);
      layer?.addTo(state.map);
      fitSingleLayer(layer);
    }
    state.activeDatasetId = "base-licence-context";
    renderDatasetInfo();
  };

  const loadLicenceContext = async () => {
    const config = dataset("base-licence-context");
    const data = await fetchJson(config.webAsset);
    assertLicenceContext(data);
    state.licenseContextData = data;
    ui.licenseCount.textContent = `${data.features.length} талбай`;
    ui.licenseContextList.replaceChildren();
    const entries = [{ key: "all", label: "Бүх лиценз", licence: `${data.features.length} талбай`, feature: null }]
      .concat(data.features.map((feature) => ({
        key: String(feature.id),
        label: feature.properties?.AREANAME_L || feature.properties?.AREANAME || feature.id,
        licence: feature.properties?.LICENSE || "Licence number unavailable",
        feature,
      })));
    for (const entry of entries) {
      if (entry.feature) {
        const layer = L.geoJSON(entry.feature, {
          pane: "licencePane",
          style: { color: "#ffe08a", weight: 3.2, opacity: 1, fillColor: "#ffd166", fillOpacity: 0.12, dashArray: "10 5" },
          onEachFeature(feature, item) { item.bindPopup(basePopup(feature)); },
        });
        state.licenseContextLayers.set(entry.key, layer);
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "license-context-button";
      button.innerHTML = `<strong>${escapeHtml(entry.label)}</strong><small>${escapeHtml(entry.licence)}</small>`;
      button.addEventListener("click", () => selectLicenceContext(entry.key, button));
      ui.licenseContextList.appendChild(button);
    }
  };

  const loadUchastik = async () => {
    const config = dataset("base-uchastik");
    const data = await fetchJson(config.webAsset);
    assertProjectTruth(data, "Uchastik");
    const layer = L.geoJSON(data, {
      pane: "uchastikPane",
      style: { color: "#ff75b5", weight: 2.2, fillColor: "#ff75b5", fillOpacity: 0.08 },
      onEachFeature(feature, item) { item.bindPopup(basePopup(feature)); },
    });
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

  const restoreMagArrowLayers = () => {
    if (state.activeSensor !== "MagArrow") return;
    for (const [id, layer] of state.planLayers) {
      if (state.planVisibility[id]) layer.addTo(state.map);
    }
    for (const [id, layer] of state.missionLayers) {
      if (state.selectedMissions.has(id)) layer.addTo(state.map);
    }
  };

  const hideSensorLayers = () => {
    for (const layer of state.planLayers.values()) state.map.removeLayer(layer);
    for (const layer of state.missionLayers.values()) state.map.removeLayer(layer);
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
      const selected = state.missionData.features.filter((feature) => state.selectedMissions.has(feature.properties?.mission_id || feature.properties?.plan));
      const estimatedCoverage = selected.reduce((sum, feature) => sum + lineLength(feature) * 100, 0);
      setSummary(
        "Нийт талбай",
        formatArea(state.planArea),
        "Төлөвлөсөн хамрах талбай",
        formatArea(estimatedCoverage),
        "DJI mission plan шугамын урт × 100 м — төлөвлөсөн estimate; verified flown area биш."
      );
      state.activeDatasetId = "magarrow-mission-plans";
    } else {
      setSummary(
        "Нийт талбай",
        formatArea(state.planArea),
        "Төлөвлөсөн хамрах талбай",
        formatArea(state.mainCoverage),
        "Approved main-line length × 100 м. Actual track/covered area гэж тооцоогүй."
      );
      state.activeDatasetId = "magarrow-planned-survey";
    }
    renderDatasetInfo();
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
    ui.sensorPanel.innerHTML = `
      <div class="sensor-heading"><div><strong>MagArrow</strong><span>Heseg Uul hoid</span></div><span class="status-badge available">CONFIRMED</span></div>
      <h3>Planned Survey Lines</h3>
      <div id="plan-toggles" class="control-list"></div>
      <details class="nested-panel">
        <summary>DJI Mission Plans <span>${missions.length}</span></summary>
        <p class="panel-note">L01–L11 нь planned WPMZ/KMZ route. Actual flown track биш.</p>
        <div id="mission-list" class="mission-list"></div>
      </details>
      <h3>Actual Data</h3>
      <button class="status-row" type="button" data-dataset="magarrow-actual-tracks"><span>Actual tracks</span><em>PENDING INGESTION</em></button>
      <button class="status-row" type="button" data-dataset="magarrow-measurements"><span>10 Hz measurements</span><em>OFF · PENDING</em></button>
      <p class="panel-note">Local 10 Hz CSV байхгүй тул track/measurement geometry зохиогоогүй.</p>`;
    const planToggles = ui.sensorPanel.querySelector("#plan-toggles");
    const planRows = [
      ["boundary", "Survey boundary", "Approved footprint"],
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
        summary: ["Survey blocks", "N1–N9", "Data status", "Available"],
      },
      L2: {
        datasetId: "l2-source", title: "Zenmuse L2", badge: "SOURCE PENDING", tone: "pending",
        body: "Canonical LiDAR source structure байна. Nergui Undur actual flight track баталгаажаагүй.", extra: "",
        summary: ["Data status", "Pending", "Flight track", "Not confirmed"],
      },
      P1: {
        datasetId: "p1-source", title: "Zenmuse P1", badge: "SOURCE PENDING", tone: "pending",
        body: "Canonical RGB photogrammetry source байна. Raw mission/trajectory баталгаажаагүй.", extra: "",
        summary: ["Data status", "Pending", "Flight track", "Not confirmed"],
      },
      Medusa: {
        datasetId: "medusa-source", title: "Medusa MS-700", badge: "NO FLIGHT DATA INGESTED", tone: "unavailable",
        body: "SOP/specification баримт байна. Actual field-flight/acquisition data баталгаажаагүй.", extra: "",
        summary: ["Data status", "Pending", "Flight data", "Not ingested"],
      },
    };
    const config = mapping[sensor];
    state.activeDatasetId = config.datasetId;
    ui.sensorPanel.innerHTML = `<div class="sensor-heading"><div><strong>${escapeHtml(config.title)}</strong><span>Nergui Undur</span></div><span class="status-badge ${config.tone}">${escapeHtml(config.badge)}</span></div><p class="status-copy">${escapeHtml(config.body)}</p>${config.extra}`;
    setSummary(...config.summary, "Missing data-г zero гэж үзээгүй; баталгаажаагүй track харуулахгүй.");
    renderDatasetInfo();
  };

  const renderSensorButtons = () => {
    ui.sensors.replaceChildren();
    for (const sensor of sensorConfig) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `sensor-button${state.activeSensor === sensor.id ? " is-active" : ""}`;
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
    const unique = new Map(links.map((item) => [item.sourceUrl, item]));
    ui.sourceLinks.innerHTML = [...unique.values()].map((item) => `<a href="${escapeHtml(item.sourceUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.dataType.replaceAll("_", " "))}<span>↗</span></a>`).join("");
  };

  const visibleLayers = () => {
    const layers = [];
    for (const item of state.baseLayers.values()) if (item.visible) layers.push(item.layer);
    for (const layer of state.licenseContextLayers.values()) if (state.map.hasLayer(layer)) layers.push(layer);
    if (state.activeSensor === "MagArrow") {
      for (const [id, layer] of state.planLayers) if (state.planVisibility[id]) layers.push(layer);
      for (const [id, layer] of state.missionLayers) if (state.selectedMissions.has(id)) layers.push(layer);
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
    hideSensorLayers();
    state.baseLayers.clear();
    state.licenseContextLayers.clear();
    state.licenseContextData = null;
    state.selectedContextLicense = null;
    state.planLayers.clear();
    state.missionLayers.clear();
    state.selectedMissions.clear();
    state.missionData = null;
    state.missionPromise = null;
    state.planArea = 0;
    state.mainCoverage = 0;
    ui.baseLayers.replaceChildren();
    ui.licenseContextList.replaceChildren();
    ui.licenseCount.textContent = "—";
    ui.clearLicenseContext.hidden = true;
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
      ui.manifestStatus.textContent = `Data ${manifest.version} · ${manifest.updated}`;

      const jobs = [
        { label: "Licence", task: loadLicence, errorType: "base" },
        { label: "Uchastik", task: loadUchastik, errorType: "base" },
        { label: "Survey boundaries", task: loadBoundaries, errorType: "base" },
        { label: "MagArrow planned survey", task: loadMagArrowPlan, errorType: "warning" },
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
  ui.clearLicenseContext.addEventListener("click", () => {
    clearLicenceContext();
    selectSensor(state.activeSensor);
    fitMap();
  });
  ui.refresh.addEventListener("click", load);
  ui.retry.addEventListener("click", load);
  load();
})();
