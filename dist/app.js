(() => {
  const loading = document.querySelector("#loading");
  const errorCard = document.querySelector("#error");
  const errorMessage = document.querySelector("#error-message");
  const retryButton = document.querySelector("#retry");
  const totalArea = document.querySelector("#total-area");
  const flownArea = document.querySelector("#flown-area");
  const flownAreaLabel = document.querySelector("#flown-area-label");
  const planList = document.querySelector("#plan-list");
  const lPlanList = document.querySelector("#l-plan-list");
  const dailyTracksSection = document.querySelector("#daily-tracks-section");
  const licenseList = document.querySelector("#license-list");
  const licenseHeading = document.querySelector("#licenses-heading");
  const licenseBack = document.querySelector("#license-back");
  const layerList = document.querySelector("#layer-list");
  const layersSection = document.querySelector(".layers-section");
  const fitButton = document.querySelector("#fit-map");

  const layerConfig = {
    HUS_NU_boundary: { label: "Лицензийн шугам", color: "#39d9ff", visible: true },
    Survey_Area: { label: "Лицензийн шугам", color: "#39d9ff", visible: true },
    Flight_Blocks: { label: "Нислэгийн блокууд", color: "#ffb84d", visible: true },
    P1_Main_50m: { label: "Үндсэн шугам · 50 м", color: "#71f6c1", visible: true },
    P1_Main_100m_AZ88: { label: "Үндсэн шугам · 100 м", color: "#71f6c1", visible: true },
    P1_Tie_300m: { label: "Хөндлөн шугам · 300 м", color: "#39a8ff", visible: true },
    P1_Tie_200m_AZ178: { label: "Хөндлөн шугам · 200 м", color: "#39a8ff", visible: true },
    Track_Start_End: { label: "Эхлэл / төгсгөлийн цэг", color: "#f5fbff", visible: false },
    Plan_Metadata: { label: "Төлөвлөгөөний төв", color: "#ffe073", visible: false },
  };

  const fieldLabels = {
    block: "Блок",
    role: "Байрлал",
    track_id: "Шугам",
    type: "Төрөл",
    direction: "Чиглэл",
    spacing_m: "Алхам",
    sensor_agl_m: "Мэдрэгчийн өндөр",
    speed_mps: "Хурд",
    point_role: "Цэгийн үүрэг",
    plan_name: "Төлөвлөгөө",
    note: "Тэмдэглэл",
  };

  let map;
  const mapLayers = new Map();
  const licenseLayers = new Map();
  const uchasticLayers = new Map();
  const lPlanLayers = new Map();
  let l3Layer;
  let licenseFeatures = [];
  let dataSignature;
  let activePlan = "MagArrow";
  let selectedLicenseKey = "all";
  let selectedLicenseFeature = null;
  let selectedUchasticKey = "all";
  let selectedUchasticFeature = null;
  let selectedLPlan = "all";
  let l3Features = [];
  let lPlanData = null;
  const planConfig = {
    L2: { label: "L2", color: "#ffb84d", dash: "10 7" },
    L3: { label: "L3", color: "#c18cff", dash: "3 8" },
    P1: { label: "P1", color: "#71f6c1", dash: null },
    Medusa: { label: "Medusa", color: "#ff75b5", dash: "16 5 3 5" },
    MagArrow: { label: "MagArrow", color: "#71f6c1", dash: null },
  };
  const isBoundaryLayer = (name) => ["HUS_NU_boundary", "Survey_Area"].includes(name);
  const isMainLineLayer = (name) => ["P1_Main_50m", "P1_Main_100m_AZ88"].includes(name);
  const isTieLineLayer = (name) => ["P1_Tie_300m", "P1_Tie_200m_AZ178"].includes(name);
  const isPlanLayer = (name) => !isBoundaryLayer(name);

  const formatArea = (squareMetres) => {
    if (!Number.isFinite(squareMetres)) return "—";
    return squareMetres >= 1_000_000
      ? `${(squareMetres / 1_000_000).toLocaleString("mn-MN", { maximumFractionDigits: 2 })} км²`
      : `${squareMetres.toLocaleString("mn-MN", { maximumFractionDigits: 0 })} м²`;
  };

  const geometryArea = (geometry) => {
    if (!geometry) return 0;
    const polygons = geometry.type === "Polygon"
      ? [geometry.coordinates]
      : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
    return polygons.reduce((total, polygon) => {
      const latitude = polygon[0]?.reduce((sum, point) => sum + point[1], 0) / (polygon[0]?.length || 1);
      const scale = 111320 * Math.cos(latitude * Math.PI / 180);
      const ringArea = (ring) => Math.abs(ring.reduce((sum, point, index) => {
        const next = ring[(index + 1) % ring.length];
        return sum + point[0] * scale * (next[1] * 111320) - next[0] * scale * (point[1] * 111320);
      }, 0)) / 2;
      return total + ringArea(polygon[0]) - polygon.slice(1).reduce((sum, ring) => sum + ringArea(ring), 0);
    }, 0);
  };

  const featureArea = (feature) => Number(feature?.properties?.area_m2) || geometryArea(feature?.geometry);
  const formatHectares = (squareMetres) => `${(squareMetres / 10_000).toLocaleString("mn-MN", { maximumFractionDigits: 2 })} га`;
  const lineLengthMetres = (feature) => {
    const coordinates = feature?.geometry?.coordinates || [];
    const lines = feature?.geometry?.type === "MultiLineString" ? coordinates : [coordinates];
    return lines.reduce((total, line) => line.slice(1).reduce((sum, point, index) => {
      const previous = line[index];
      const dx = (point[0] - previous[0]) * 111320 * Math.cos(point[1] * Math.PI / 180);
      const dy = (point[1] - previous[1]) * 111320;
      return sum + Math.hypot(dx, dy);
    }, total), 0);
  };
  const dailyFlightArea = (planKey) => {
    if (!lPlanData || planKey === "all") return 0;
    return lPlanData.features
      .filter((feature) => feature.properties?.plan === planKey)
      .reduce((sum, feature) => sum + lineLengthMetres(feature) * 100, 0);
  };
  const setFlownArea = (label, squareMetres, hectares = false) => {
    flownAreaLabel.textContent = label;
    flownArea.textContent = hectares ? formatHectares(squareMetres) : formatArea(squareMetres);
  };
  const setTotalArea = (features) => {
    const list = Array.isArray(features) ? features : [features];
    totalArea.textContent = formatArea(list.reduce((sum, feature) => sum + featureArea(feature), 0));
  };

  const escapeHtml = (value) => String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  const layerSettings = (name) => layerConfig[name] || {
    label: name.replaceAll("_", " "),
    color: "#9ab0c4",
    visible: true,
  };

  const popupContent = (feature) => {
    const properties = feature.properties || {};
    const settings = layerSettings(properties.layer);
    const rows = [];
    if (Number.isFinite(properties.area_m2)) rows.push(["Талбай", formatArea(properties.area_m2)]);
    for (const [key, label] of Object.entries(fieldLabels)) {
      const value = properties[key];
      if (value === null || value === undefined || value === "") continue;
      const unit = key.endsWith("_m") ? " м" : key.endsWith("_mps") ? " м/с" : "";
      rows.push([label, `${value}${unit}`]);
    }
    const detailRows = rows
      .map(([label, value]) => `<span class="popup-label">${escapeHtml(label)}</span><span class="popup-value">${escapeHtml(value)}</span>`)
      .join("");
    return `<span class="popup-label">Давхарга</span><span class="popup-value">${escapeHtml(settings.label)}</span>${detailRows}`;
  };

  const licensePopup = (feature) => {
    const properties = feature.properties || {};
    const name = properties.AREANAME_L || properties.AREANAME || properties.LICENSE || "Лицензийн талбай";
    return `<span class="popup-label">Лицензийн талбай</span><span class="popup-value">${escapeHtml(name)}</span>` +
      (properties.LICENSE ? `<span class="popup-label">Лиценз</span><span class="popup-value">${escapeHtml(properties.LICENSE)}</span>` : "");
  };

  const vectorStyle = (feature) => {
    const geometryType = feature.geometry?.type || "";
    const settings = layerSettings(feature.properties?.layer);
    const layerName = feature.properties?.layer;
    const isPlanLine = isMainLineLayer(layerName) || isTieLineLayer(layerName);
    const selectedPlan = planConfig[activePlan];
    if (geometryType.includes("Polygon")) {
      const isBoundary = isBoundaryLayer(layerName);
      return {
        color: settings.color,
        weight: isBoundary ? 3.5 : 1.5,
        opacity: 1,
        fillColor: settings.color,
        fillOpacity: isBoundary ? 0.08 : 0.13,
      };
    }
    return {
      color: isPlanLine
        ? isTieLineLayer(layerName) && activePlan === "MagArrow"
          ? "#39a8ff"
          : selectedPlan.color
        : settings.color,
      weight: isPlanLine ? (isMainLineLayer(layerName) ? 2.2 : 1.5) : 1.35,
      opacity: 0.95,
      dashArray: isPlanLine ? selectedPlan.dash : null,
    };
  };

  const showError = (message) => {
    loading.hidden = true;
    errorMessage.textContent = message;
    errorCard.hidden = false;
  };

  const fitToArea = () => {
    if (!map) return;
    const selectedLayer = selectedLPlan !== "all"
      ? lPlanLayers.get(selectedLPlan)
      : selectedUchasticKey !== "all"
      ? uchasticLayers.get(selectedUchasticKey)
      : selectedLicenseKey !== "all" ? licenseLayers.get(selectedLicenseKey) : null;
    const preferred = selectedLayer || [...mapLayers.entries()].find(([name]) => isBoundaryLayer(name))?.[1];
    const boundsSource = preferred || L.featureGroup([...mapLayers.values()]);
    const bounds = boundsSource.getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [36, 36], maxZoom: 15 });
  };

  const renderLPlanControls = (plans) => {
    lPlanList.replaceChildren();
    const entries = [{ label: "Бүгд", date: "", key: "all" }, ...plans.map((plan) => ({
      label: `${plan.label} · ${plan.date}`, date: plan.date, key: plan.label,
    }))];
    for (const entry of entries) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `license-button${entry.key === "all" ? " is-active" : ""}`;
      button.textContent = entry.date ? `${entry.label} (${entry.date})` : entry.label;
      button.addEventListener("click", () => {
        selectedLPlan = entry.key;
        for (const [key, layer] of lPlanLayers) {
          if (entry.key === "all" || key === entry.key) layer.addTo(map);
          else map.removeLayer(layer);
        }
        for (const item of lPlanList.children) item.classList.remove("is-active");
        button.classList.add("is-active");
        const selectedFeatures = lPlanData.features.filter((feature) => feature.properties?.plan === entry.key);
        const squareMetres = entry.key === "all"
          ? l3Features.filter((feature) => feature.properties?.layer === "BLOCK_BOUNDARY")
            .reduce((sum, feature) => sum + featureArea(feature), 0)
          : selectedFeatures.reduce((sum, feature) => sum + lineLengthMetres(feature) * 100, 0);
        setFlownArea(entry.key === "all" ? "Нийт нислэг" : "Өдрийн нислэг", squareMetres, entry.key !== "all");
        fitToArea();
      });
      lPlanList.appendChild(button);
    }
  };

  const updatePlanVisibility = () => {
    for (const [name, geoLayer] of mapLayers) {
      if (isBoundaryLayer(name)) continue;
      if (activePlan === "MagArrow") {
        geoLayer.setStyle(vectorStyle);
        if (layerSettings(name).visible) geoLayer.addTo(map);
      } else map.removeLayer(geoLayer);
    }
    const showL3 = activePlan === "L3" && selectedLicenseKey !== "all" &&
      (selectedLicenseFeature?.properties?.AREANAME || "").toLocaleLowerCase().includes("nergui");
    if (l3Layer) {
      if (showL3) l3Layer.addTo(map);
      else map.removeLayer(l3Layer);
    }
    dailyTracksSection.hidden = activePlan !== "L3";
    for (const [key, layer] of lPlanLayers) {
      if (activePlan !== "L3" || (selectedLPlan !== "all" && key !== selectedLPlan)) map.removeLayer(layer);
      else layer.addTo(map);
    }
    if (activePlan === "L3" && l3Features.length) {
      const total = l3Features.filter((feature) => feature.properties?.layer === "BLOCK_BOUNDARY")
        .reduce((sum, feature) => sum + featureArea(feature), 0);
      setFlownArea(selectedLPlan === "all" ? "Нийт нислэг" : "Өдрийн нислэг",
        selectedLPlan === "all" ? total : dailyFlightArea(selectedLPlan), selectedLPlan !== "all");
    }
  };

  const createLayerControl = (summary, geoLayer) => {
    if (isBoundaryLayer(summary.name)) return;
    const settings = layerSettings(summary.name);
    const card = document.createElement("div");
    card.className = "layer-card plan-layer-control";
    card.style.setProperty("--layer-color", settings.color);
    const geometryClass = summary.geometry_type.includes("POINT")
      ? "point"
      : summary.geometry_type.includes("LINE") ? "line" : "polygon";
    const controlId = `layer-${summary.name.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    card.innerHTML = `
      <div class="layer-copy">
        <span class="swatch ${geometryClass}" aria-hidden="true"></span>
        <div>
          <strong>${escapeHtml(settings.label)}</strong>
          <span>${summary.feature_count.toLocaleString("mn-MN")} объект · ${escapeHtml(summary.geometry_type)}</span>
        </div>
      </div>
      <label class="switch" title="Давхаргыг харуулах эсвэл нуух">
        <input id="${controlId}" type="checkbox" ${settings.visible ? "checked" : ""} />
        <span aria-hidden="true"></span>
        <span class="sr-only">${escapeHtml(settings.label)} давхаргыг харуулах</span>
      </label>`;
    const checkbox = card.querySelector("input");
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) geoLayer.addTo(map);
      else map.removeLayer(geoLayer);
    });
    layerList.appendChild(card);
  };

  const updatePlanLayerControls = () => {
    layersSection.hidden = activePlan !== "MagArrow";
    for (const card of layerList.querySelectorAll(".plan-layer-control")) {
      card.hidden = activePlan !== "MagArrow";
    }
  };

  const renderLicenseControls = (features) => {
    licenseFeatures = features;
    licenseHeading.textContent = "Лицензийн талбай";
    licenseBack.hidden = true;
    licenseList.replaceChildren();
    const entries = [["all", "Бүгд", null], ...features.map((feature, index) => {
      const properties = feature.properties || {};
      return [String(index), properties.AREANAME || properties.LICENSE || `Лиценз ${index + 1}`, feature];
    })];
    for (const [key, label, feature] of entries) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `license-button${key === "all" ? " is-active" : ""}`;
      button.textContent = label;
      button.addEventListener("click", () => {
        if (label.toLocaleLowerCase("mn-MN").includes("nergui") || label.toLocaleLowerCase("mn-MN").includes("нэргүй")) {
          selectedLicenseKey = key;
          selectedLicenseFeature = feature;
          selectedUchasticKey = "all";
          selectedUchasticFeature = null;
          setTotalArea(feature);
          fitToArea();
          showUchasticList(String(features.indexOf(feature)));
          return;
        }
        selectedLicenseKey = key;
        selectedLicenseFeature = feature;
        selectedUchasticKey = "all";
        selectedUchasticFeature = null;
        for (const layer of uchasticLayers.values()) map.removeLayer(layer);
        for (const [layerKey, layer] of licenseLayers) {
          if (key === "all" || layerKey === key) layer.addTo(map);
          else map.removeLayer(layer);
        }
        for (const item of licenseList.children) item.classList.remove("is-active");
        button.classList.add("is-active");
        setTotalArea(key === "all" ? features : feature);
        updatePlanVisibility();
        fitToArea();
      });
      licenseList.appendChild(button);
    }
  };

  const showUchasticList = async (licenseKey) => {
    for (const [key, layer] of licenseLayers) {
      if (key === licenseKey) layer.addTo(map);
      else map.removeLayer(layer);
    }
    const response = await fetch("./data/uchastics.geojson", { cache: "no-store" });
    if (!response.ok) throw new Error(`Участикийн өгөгдлийн хүсэлт амжилтгүй (${response.status})`);
    const data = await response.json();
    for (const layer of uchasticLayers.values()) map.removeLayer(layer);
    uchasticLayers.clear();
    for (const [index, feature] of data.features.entries()) {
      const layer = L.geoJSON(feature, {
        style: { color: "#ff75b5", weight: 2, opacity: 1, fillColor: "#ff75b5", fillOpacity: 0.12 },
        onEachFeature(item, itemLayer) {
          const properties = item.properties || {};
          itemLayer.bindPopup(`<span class="popup-label">Участик</span><span class="popup-value">${escapeHtml(properties.name || `Участик ${index + 1}`)}</span>`);
        },
      }).addTo(map);
      uchasticLayers.set(String(index), layer);
    }
    licenseHeading.textContent = "Nergui Undur · участикууд";
    licenseBack.hidden = false;
    licenseList.replaceChildren();
    const planButton = document.createElement("button");
    planButton.type = "button";
    planButton.className = "license-button uchastic-plan-button is-active";
    planButton.textContent = "Дорнын төмөр";
    planButton.addEventListener("click", () => {
      for (const layer of uchasticLayers.values()) map.removeLayer(layer);
      for (const [name, layer] of mapLayers) {
        if (!isBoundaryLayer(name) && layerSettings(name).visible) layer.addTo(map);
      }
      selectedUchasticKey = "all";
      selectedUchasticFeature = null;
      setTotalArea(selectedLicenseFeature);
      updatePlanVisibility();
      fitToArea();
      for (const item of licenseList.children) item.classList.remove("is-active");
      planButton.classList.add("is-active");
    });
    licenseList.appendChild(planButton);
    const allUchasticButton = document.createElement("button");
    allUchasticButton.type = "button";
    allUchasticButton.className = "license-button uchastic-button is-active";
    allUchasticButton.textContent = "Бүгд";
    allUchasticButton.addEventListener("click", () => {
      for (const layer of uchasticLayers.values()) layer.addTo(map);
      selectedUchasticKey = "all";
      selectedUchasticFeature = null;
      setTotalArea(data.features);
      updatePlanVisibility();
      fitToArea();
      for (const item of licenseList.children) item.classList.remove("is-active");
      allUchasticButton.classList.add("is-active");
    });
    licenseList.appendChild(allUchasticButton);
    for (const [index, feature] of data.features.entries()) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "license-button uchastic-button";
      button.textContent = feature.properties?.name || `Участик ${index + 1}`;
      button.addEventListener("click", () => {
        for (const [layerIndex, layer] of uchasticLayers) {
          if (layerIndex === String(index)) layer.addTo(map);
          else map.removeLayer(layer);
        }
        for (const item of licenseList.children) item.classList.remove("is-active");
        button.classList.add("is-active");
        selectedUchasticKey = String(index);
        selectedUchasticFeature = feature;
        setTotalArea(feature);
        updatePlanVisibility();
        fitToArea();
      });
      licenseList.appendChild(button);
    }
  };

  licenseBack.addEventListener("click", () => {
    for (const layer of uchasticLayers.values()) map.removeLayer(layer);
    for (const layer of licenseLayers.values()) layer.addTo(map);
    renderLicenseControls(licenseFeatures);
    selectedLicenseKey = "all";
    selectedLicenseFeature = null;
    selectedUchasticKey = "all";
    selectedUchasticFeature = null;
    setTotalArea(licenseFeatures);
    updatePlanVisibility();
    fitToArea();
  });

  const renderPlanControls = () => {
    planList.replaceChildren();
    for (const plan of Object.values(planConfig)) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `plan-button${plan.label === activePlan ? " is-active" : ""}`;
      button.style.setProperty("--plan-color", plan.color);
      button.textContent = plan.label;
      button.setAttribute("aria-pressed", String(plan.label === activePlan));
      button.addEventListener("click", () => {
        activePlan = plan.label;
        renderPlanControls();
        updatePlanLayerControls();
        updatePlanVisibility();
        fitToArea();
      });
      planList.appendChild(button);
    }
  };

  async function start(showLoading = true) {
    errorCard.hidden = true;
    if (showLoading) loading.hidden = false;

    if (!window.L) {
      showError("Газрын зургийн сан ачаалагдсангүй. Интернэт холболтоо шалгана уу.");
      return;
    }

    try {
      if (!map) {
        map = L.map("map", { zoomControl: false, attributionControl: false, preferCanvas: true });
        L.control.zoom({ position: "topright" }).addTo(map);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: "© OpenStreetMap contributors",
        }).addTo(map);
      }

      const response = await fetch("./data/area.geojson", { cache: "no-store" });
      if (!response.ok) throw new Error(`Өгөгдлийн хүсэлт амжилтгүй (${response.status})`);
      const data = await response.json();
      const nextSignature = JSON.stringify(data);
      if (nextSignature === dataSignature) return;
      dataSignature = nextSignature;

      for (const geoLayer of mapLayers.values()) map.removeLayer(geoLayer);
      mapLayers.clear();
      layerList.replaceChildren();

      for (const summary of data.layers || []) {
        const settings = layerSettings(summary.name);
        const features = data.features.filter((feature) => feature.properties?.layer === summary.name);
        const geoLayer = L.geoJSON({ type: "FeatureCollection", features }, {
          style: vectorStyle,
          pointToLayer(feature, latlng) {
            const isMetadata = feature.properties?.layer === "Plan_Metadata";
            return L.circleMarker(latlng, {
              radius: isMetadata ? 7 : 3.5,
              color: "#07111f",
              weight: 1,
              fillColor: settings.color,
              fillOpacity: 0.95,
            });
          },
          onEachFeature(feature, layer) {
            layer.bindPopup(popupContent(feature));
          },
        });
        mapLayers.set(summary.name, geoLayer);
        if (settings.visible && (activePlan === "MagArrow" || !isPlanLayer(summary.name))) geoLayer.addTo(map);
        createLayerControl(summary, geoLayer);
      }
      renderPlanControls();
      updatePlanLayerControls();

      const l3Response = await fetch("./data/l3.geojson", { cache: "no-store" });
      if (!l3Response.ok) throw new Error(`L3 өгөгдлийн хүсэлт амжилтгүй (${l3Response.status})`);
      const l3Data = await l3Response.json();
      l3Features = l3Data.features;
      if (l3Layer) map.removeLayer(l3Layer);
      const l3VisibleFeatures = l3Data.features.filter((feature) =>
        feature.geometry?.type !== "Point" || feature.properties?.layer === "BLOCK_NUM_LABELS");
      l3Layer = L.geoJSON({ ...l3Data, features: l3VisibleFeatures }, {
        style: { color: "#c18cff", weight: 2.5, opacity: 1, fillColor: "#c18cff", fillOpacity: 0.12, dashArray: "4 6" },
        pointToLayer(feature, latlng) {
          const text = escapeHtml(feature.properties?.text_value || "");
          return L.marker(latlng, {
            icon: L.divIcon({
              className: "l3-block-label",
              html: `<span>${text}</span>`,
              iconSize: null,
            }),
            keyboard: false,
          });
        },
        onEachFeature(feature, layer) { layer.bindPopup(popupContent(feature)); },
      });
      if (activePlan === "L3") l3Layer.addTo(map);

      const lPlanResponse = await fetch("./data/l-plans.geojson", { cache: "no-store" });
      if (!lPlanResponse.ok) throw new Error(`L дата хүсэлт амжилтгүй (${lPlanResponse.status})`);
      const lPlanDataPayload = await lPlanResponse.json();
      for (const layer of lPlanLayers.values()) map.removeLayer(layer);
      lPlanLayers.clear();
      for (const plan of lPlanDataPayload.plans || []) {
        const features = lPlanDataPayload.features.filter((feature) => feature.properties?.plan === plan.label);
        const layer = L.geoJSON({ type: "FeatureCollection", features }, {
          style: { color: "#ff9f43", weight: 2, opacity: 0.9, dashArray: "8 5" },
          onEachFeature(feature, itemLayer) { itemLayer.bindPopup(popupContent(feature)); },
        });
        lPlanLayers.set(plan.label, layer);
      }
      lPlanData = lPlanDataPayload;
      renderLPlanControls(lPlanDataPayload.plans || []);

      const licenseResponse = await fetch("./data/licenses.geojson", { cache: "no-store" });
      if (!licenseResponse.ok) throw new Error(`Лицензийн өгөгдлийн хүсэлт амжилтгүй (${licenseResponse.status})`);
      const licenseData = await licenseResponse.json();
      for (const layer of licenseLayers.values()) map.removeLayer(layer);
      licenseLayers.clear();
      for (const [index, feature] of licenseData.features.entries()) {
        const layer = L.geoJSON(feature, {
          style: {
            color: "#ffd166",
            weight: 1.8,
            opacity: 0.95,
            fillColor: "#ffd166",
            fillOpacity: 0.08,
            dashArray: "7 5",
          },
          onEachFeature(item, itemLayer) {
            itemLayer.bindPopup(licensePopup(item));
          },
        }).addTo(map);
        licenseLayers.set(String(index), layer);
      }
      renderLicenseControls(licenseData.features);

      const boundaryFeatures = data.features.filter((feature) => isBoundaryLayer(feature.properties?.layer));
      const areaSum = boundaryFeatures.reduce((sum, feature) => sum + (Number(feature.properties?.area_m2) || 0), 0);
      const flownFeatures = data.features
        .filter((feature) => feature.properties?.layer === "Flight_Blocks")
      const flownAreaSum = flownFeatures.length > 0
        ? flownFeatures.reduce((sum, feature) => sum + (Number(feature.properties?.area_m2) || 0), 0)
        : null;
      setTotalArea(licenseData.features);
      setFlownArea("Нийт нислэг", flownAreaSum || 0);
      flownArea.textContent = formatArea(flownAreaSum);
      fitToArea();
      loading.hidden = true;
    } catch (error) {
      console.error(error);
      if (showLoading) showError("Шинэчилсэн GeoJSON мэдээллийг уншиж чадсангүй.");
    }
  }

  fitButton.addEventListener("click", fitToArea);
  retryButton.addEventListener("click", start);
  start();
  setInterval(() => start(false), 30000);
})();
