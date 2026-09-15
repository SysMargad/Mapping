(() => {
  const loading = document.querySelector("#loading");
  const errorCard = document.querySelector("#error");
  const errorMessage = document.querySelector("#error-message");
  const retryButton = document.querySelector("#retry");
  const featureCount = document.querySelector("#feature-count");
  const totalArea = document.querySelector("#total-area");
  const layerToggle = document.querySelector("#layer-toggle");
  const fitButton = document.querySelector("#fit-map");

  let map;
  let areaLayer;

  const formatArea = (squareMetres) => {
    if (!Number.isFinite(squareMetres)) return "—";
    return squareMetres >= 1_000_000
      ? `${(squareMetres / 1_000_000).toLocaleString("mn-MN", { maximumFractionDigits: 2 })} км²`
      : `${squareMetres.toLocaleString("mn-MN", { maximumFractionDigits: 0 })} м²`;
  };

  const showError = (message) => {
    loading.hidden = true;
    errorMessage.textContent = message;
    errorCard.hidden = false;
  };

  const fitToArea = () => {
    if (map && areaLayer && areaLayer.getBounds().isValid()) {
      map.fitBounds(areaLayer.getBounds(), { padding: [36, 36], maxZoom: 15 });
    }
  };

  async function start() {
    errorCard.hidden = true;
    loading.hidden = false;

    if (!window.L) {
      showError("Газрын зургийн сан ачаалагдсангүй. Интернэт холболтоо шалгана уу.");
      return;
    }

    try {
      if (!map) {
        map = L.map("map", { zoomControl: true, attributionControl: false, preferCanvas: true });
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: "© OpenStreetMap contributors",
        }).addTo(map);
      }

      const response = await fetch("./data/area.geojson", { cache: "no-store" });
      if (!response.ok) throw new Error(`Өгөгдлийн хүсэлт амжилтгүй (${response.status})`);
      const data = await response.json();

      if (areaLayer) map.removeLayer(areaLayer);
      areaLayer = L.geoJSON(data, {
        style: {
          color: "#39d9ff",
          weight: 3,
          opacity: 1,
          fillColor: "#29bddd",
          fillOpacity: 0.24,
        },
        onEachFeature(feature, layer) {
          const area = feature.properties?.area_m2;
          layer.bindPopup(
            `<span class="popup-label">Төлөвлөлтийн талбай</span><span class="popup-value">${formatArea(area)}</span>`
          );
          layer.on({
            mouseover: () => layer.setStyle({ weight: 4, fillOpacity: 0.34 }),
            mouseout: () => areaLayer.resetStyle(layer),
          });
        },
      }).addTo(map);

      const areaSum = data.features.reduce((sum, feature) => sum + (Number(feature.properties?.area_m2) || 0), 0);
      featureCount.textContent = data.features.length.toLocaleString("mn-MN");
      totalArea.textContent = formatArea(areaSum);
      layerToggle.checked = true;
      fitToArea();
      loading.hidden = true;
    } catch (error) {
      console.error(error);
      showError("Талбайн GeoJSON мэдээллийг уншиж чадсангүй.");
    }
  }

  layerToggle.addEventListener("change", () => {
    if (!map || !areaLayer) return;
    if (layerToggle.checked) areaLayer.addTo(map);
    else map.removeLayer(areaLayer);
  });
  fitButton.addEventListener("click", fitToArea);
  retryButton.addEventListener("click", start);
  start();
})();
