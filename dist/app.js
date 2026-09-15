(() => {
  const loading = document.querySelector("#loading");
  const errorCard = document.querySelector("#error");
  const errorMessage = document.querySelector("#error-message");
  const retryButton = document.querySelector("#retry");
  const totalArea = document.querySelector("#total-area");
  const flownArea = document.querySelector("#flown-area");
  const planList = document.querySelector("#plan-list");
  const licenseList = document.querySelector("#license-list");
  const licenseHeading = document.querySelector("#licenses-heading");
  const licenseBack = document.querySelector("#license-back");
  const layerList = document.querySelector("#layer-list");
  const layersSection = document.querySelector(".layers-section");
  const fitButton = document.querySelector("#fit-map");

  const layerConfig = {
    HUS_NU_boundary: { label: "Лицензийн шугам", color: "#39d9ff", visible: true },
    Flight_Blocks: { label: "Нислэгийн блокууд", color: "#ffb84d", visible: true },
    P1_Main_50m: { label: "Үндсэн шугам · 50 м", color: "#71f6c1", visible: true },
    P1_Tie_300m: { label: "Хөндлөн шугам · 300 м", color: "#39a8ff", visible: true },
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
  let licenseFeatures = [];
  let dataSignature;
  let activePlan = "MagArrow";
  const planConfig = {
    L2: { label: "L2", color: "#ffb84d", dash: "10 7" },
    L3: { label: "L3", color: "#c18cff", dash: "3 8" },
    P1: { label: "P1", color: "#71f6c1", dash: null },
    Meduse: { label: "Meduse", color: "#ff75b5", dash: "16 5 3 5" },
    MagArrow: { label: "MagArrow", color: "#71f6c1", dash: null },
  };
  const planLayers = new Set(["Flight_Blocks", "P1_Main_50m", "P1_Tie_300m", "Track_Start_End", "Plan_Metadata"]);

  const formatArea = (squareMetres) => {
    if (!Number.isFinite(squareMetres)) return "—";
    return squareMetres >= 1_000_000
      ? `${(squareMetres / 1_000_000).toLocaleString("mn-MN", { maximumFractionDigits: 2 })} км²`
      : `${squareMetres.toLocaleString("mn-MN", { maximumFractionDigits: 0 })} м²`;
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
    const isPlanLine = ["P1_Main_50m", "P1_Tie_300m"].includes(feature.properties?.layer);
    const selectedPlan = planConfig[activePlan];
    if (geometryType.includes("Polygon")) {
      const isBoundary = feature.properties?.layer === "HUS_NU_boundary";
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
        ? feature.properties?.layer === "P1_Tie_300m" && activePlan === "MagArrow"
          ? "#39a8ff"
          : selectedPlan.color
        : settings.color,
      weight: isPlanLine ? (feature.properties?.layer === "P1_Main_50m" ? 2.2 : 1.5) : 1.35,
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
    const preferred = mapLayers.get("HUS_NU_boundary");
    const boundsSource = preferred || L.featureGroup([...mapLayers.values()]);
    const bounds = boundsSource.getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [36, 36], maxZoom: 15 });
  };

  const createLayerControl = (summary, geoLayer) => {
    if (summary.name === "HUS_NU_boundary") return;
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
          showUchasticList(String(features.indexOf(feature)));
          return;
        }
        for (const layer of uchasticLayers.values()) map.removeLayer(layer);
        for (const [layerKey, layer] of licenseLayers) {
          if (key === "all" || layerKey === key) layer.addTo(map);
          else map.removeLayer(layer);
        }
        for (const item of licenseList.children) item.classList.remove("is-active");
        button.classList.add("is-active");
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
        if (name !== "HUS_NU_boundary" && layerSettings(name).visible) layer.addTo(map);
      }
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
      });
      licenseList.appendChild(button);
    }
  };

  licenseBack.addEventListener("click", () => {
    for (const layer of uchasticLayers.values()) map.removeLayer(layer);
    for (const layer of licenseLayers.values()) layer.addTo(map);
    renderLicenseControls(licenseFeatures);
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
        for (const [name, geoLayer] of mapLayers) {
          if (name === "HUS_NU_boundary") continue;
          if (activePlan === "MagArrow") {
            geoLayer.setStyle(vectorStyle);
            if (layerSettings(name).visible) geoLayer.addTo(map);
          } else {
            map.removeLayer(geoLayer);
          }
        }
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
        if (settings.visible && (activePlan === "MagArrow" || !planLayers.has(summary.name))) geoLayer.addTo(map);
        createLayerControl(summary, geoLayer);
      }
      renderPlanControls();
      updatePlanLayerControls();

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

      const boundaryFeatures = data.features.filter((feature) => feature.properties?.layer === "HUS_NU_boundary");
      const areaSum = boundaryFeatures.reduce((sum, feature) => sum + (Number(feature.properties?.area_m2) || 0), 0);
      const flownAreaSum = data.features
        .filter((feature) => feature.properties?.layer === "Flight_Blocks")
        .reduce((sum, feature) => sum + (Number(feature.properties?.area_m2) || 0), 0);
      totalArea.textContent = formatArea(areaSum);
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
