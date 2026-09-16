import http from "node:http";
import { readFile, readdir, stat } from "node:fs/promises";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "dist");
const areaDataDirectory = path.resolve(root, "..", "..", "Area Data");
const output = path.join(root, "data", "area.geojson");
const licenseSource = path.resolve(root, "..", "..", "Talbain license.zip");
const licenseOutput = path.join(root, "data", "licenses.geojson");
const uchasticSource = path.resolve(root, "..", "..", "23099_uchastic_20260806.zip");
const uchasticOutput = path.join(root, "data", "uchastics.geojson");
const l3Source = path.join(areaDataDirectory, "Nergui undur_L3_boundary.dxf");
const l3Output = path.join(root, "data", "l3.geojson");
const exporter = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "export_gpkg.py");
const licenseExporter = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "export_shapefile.py");
const dxfExporter = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "export_dxf.py");
const port = Number(process.env.PORT || 4173);
const host = process.env.HOST || "0.0.0.0";
const types = new Map([
  [".html", "text/html; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".geojson", "application/geo+json; charset=utf-8"],
  [".svg", "image/svg+xml"],
]);

let syncPromise;
let licenseSyncPromise;
let uchasticSyncPromise;
let l3SyncPromise;

async function syncAreaData() {
  const sources = (await readdir(areaDataDirectory, { withFileTypes: true }))
    .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".gpkg"));
  if (sources.length === 0) return;
  const source = path.join(areaDataDirectory, sources[0].name);
  let newestSource = source;
  let newestSourceStats = await stat(source);
  for (const entry of sources.slice(1)) {
    const candidate = path.join(areaDataDirectory, entry.name);
    const candidateStats = await stat(candidate);
    if (candidateStats.mtimeMs > newestSourceStats.mtimeMs) {
      newestSource = candidate;
      newestSourceStats = candidateStats;
    }
  }
  let sourceStats;
  let outputStats;
  try {
    [sourceStats, outputStats] = await Promise.all([stat(newestSource), stat(output)]);
  } catch (error) {
    if (error.code === "ENOENT") return;
    throw error;
  }

  if (outputStats.mtimeMs >= sourceStats.mtimeMs) return;
  if (syncPromise) return syncPromise;

  const python = process.env.PYTHON || (process.platform === "win32" ? "py" : "python3");
  const args = process.platform === "win32"
    ? ["-3", exporter, newestSource, output]
    : [exporter, newestSource, output];
  syncPromise = new Promise((resolve, reject) => {
    const child = spawn(python, args, { stdio: ["ignore", "inherit", "inherit"] });
    child.once("error", reject);
    child.once("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Area Data export failed with code ${code}`));
    });
  }).finally(() => {
    syncPromise = undefined;
  });
  return syncPromise;
}

async function syncLicenseData() {
  let sourceStats;
  let outputStats;
  try {
    sourceStats = await stat(licenseSource);
  } catch (error) {
    if (error.code === "ENOENT") return;
    throw error;
  }

  try {
    outputStats = await stat(licenseOutput);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  if (outputStats && outputStats.mtimeMs >= sourceStats.mtimeMs) return;
  if (licenseSyncPromise) return licenseSyncPromise;
  const python = process.env.PYTHON || (process.platform === "win32" ? "py" : "python3");
  const args = process.platform === "win32"
    ? ["-3", licenseExporter, licenseSource, licenseOutput]
    : [licenseExporter, licenseSource, licenseOutput];
  licenseSyncPromise = new Promise((resolve, reject) => {
    const child = spawn(python, args, { stdio: ["ignore", "inherit", "inherit"] });
    child.once("error", reject);
    child.once("close", (code) => code === 0 ? resolve() : reject(new Error(`License export failed with code ${code}`)));
  }).finally(() => {
    licenseSyncPromise = undefined;
  });
  return licenseSyncPromise;
}

async function syncUchasticData() {
  let sourceStats;
  let outputStats;
  try {
    sourceStats = await stat(uchasticSource);
  } catch (error) {
    if (error.code === "ENOENT") return;
    throw error;
  }
  try {
    outputStats = await stat(uchasticOutput);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  if (outputStats && outputStats.mtimeMs >= sourceStats.mtimeMs) return;
  if (uchasticSyncPromise) return uchasticSyncPromise;
  const python = process.env.PYTHON || (process.platform === "win32" ? "py" : "python3");
  const args = process.platform === "win32"
    ? ["-3", licenseExporter, uchasticSource, uchasticOutput, "--utm-zone-49n"]
    : [licenseExporter, uchasticSource, uchasticOutput, "--utm-zone-49n"];
  uchasticSyncPromise = new Promise((resolve, reject) => {
    const child = spawn(python, args, { stdio: ["ignore", "inherit", "inherit"] });
    child.once("error", reject);
    child.once("close", (code) => code === 0 ? resolve() : reject(new Error(`Uchastic export failed with code ${code}`)));
  }).finally(() => {
    uchasticSyncPromise = undefined;
  });
  return uchasticSyncPromise;
}

async function syncL3Data() {
  let sourceStats;
  try {
    sourceStats = await stat(l3Source);
  } catch (error) {
    if (error.code === "ENOENT") return;
    throw error;
  }
  let outputStats;
  try {
    outputStats = await stat(l3Output);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  if (outputStats && outputStats.mtimeMs >= sourceStats.mtimeMs) return;
  if (l3SyncPromise) return l3SyncPromise;
  const python = process.env.PYTHON || (process.platform === "win32" ? "py" : "python3");
  const args = process.platform === "win32"
    ? ["-3", dxfExporter, l3Source, l3Output]
    : [dxfExporter, l3Source, l3Output];
  l3SyncPromise = new Promise((resolve, reject) => {
    const child = spawn(python, args, { stdio: ["ignore", "inherit", "inherit"] });
    child.once("error", reject);
    child.once("close", (code) => code === 0 ? resolve() : reject(new Error(`L3 export failed with code ${code}`)));
  }).finally(() => {
    l3SyncPromise = undefined;
  });
  return l3SyncPromise;
}

const server = http.createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    let target = path.resolve(root, `.${pathname}`);
    if (!target.startsWith(root)) throw new Error("Invalid path");
    if (target === output) await syncAreaData();
    if (target === licenseOutput) await syncLicenseData();
    if (target === uchasticOutput) await syncUchasticData();
    if (target === l3Output) await syncL3Data();
    if ((await stat(target)).isDirectory()) target = path.join(target, "index.html");
    const body = await readFile(target);
    response.writeHead(200, { "Content-Type": types.get(path.extname(target)) || "application/octet-stream" });
    response.end(body);
  } catch {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Not found");
  }
});

server.listen(port, host, () => {
  console.log(`Local: http://127.0.0.1:${port}`);
  console.log(`Network: http://<this-computer-ip>:${port}`);
});
