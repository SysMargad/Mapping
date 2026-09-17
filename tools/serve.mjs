import http from "node:http";
import { readFile, stat } from "node:fs/promises";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.join(projectRoot, "dist");
const desktopRoot = path.resolve(projectRoot, "..", "..", "..", "..", "Desktop", "Drone Track");
const driveAreaDataRoot = "G:\\.shortcut-targets-by-id\\1hopqTCeMJknMkkvMa_wCY6OQ_GHXt4ub\\Drone Track";
const legacyAreaDataRoot = path.join(desktopRoot, "Area Data");
const areaDataRoot = process.env.AREA_DATA_ROOT
  ? path.resolve(process.env.AREA_DATA_ROOT)
  : existsSync(driveAreaDataRoot) ? driveAreaDataRoot : legacyAreaDataRoot;
const dwgread = process.env.LIBREDWG_DWGREAD
  ? path.resolve(process.env.LIBREDWG_DWGREAD)
  : path.join(desktopRoot, ".tools", "libredwg", "dwgread.exe");
const sources = {
  plan: path.join(areaDataRoot, "NU_DR_MagArrow_Plan.gpkg"),
  hetsuuCad: path.join(areaDataRoot, "Hetsuu hutul.DWG"),
  licence: path.join(desktopRoot, "Talbain license.zip"),
  uchastik: path.join(desktopRoot, "23099_uchastic_20260806.zip"),
};
const outputs = {
  plan: path.join(root, "data", "magarrow", "planned-survey.geojson"),
  hetsuuCad: path.join(root, "data", "context", "hetsuu-hutul-dwg.geojson"),
  licence: path.join(root, "data", "base", "licenses.geojson"),
  uchastik: path.join(root, "data", "base", "uchastics.geojson"),
  licenceContext: path.join(root, "data", "context", "licenses.geojson"),
};
const scripts = {
  plan: path.join(projectRoot, "tools", "export_gpkg.py"),
  dwg: path.join(projectRoot, "tools", "export_dwg.py"),
  shapefile: path.join(projectRoot, "tools", "export_shapefile.py"),
};
const port = Number(process.env.PORT || 4173);
const host = process.env.HOST || "127.0.0.1";
const types = new Map([
  [".html", "text/html; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".geojson", "application/geo+json; charset=utf-8"],
  [".svg", "image/svg+xml"],
]);
const activeSyncs = new Map();

function pythonCommand(script, args) {
  const bundledPython = path.resolve(
    process.env.USERPROFILE || "",
    ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "python.exe",
  );
  if (process.env.PYTHON) return [process.env.PYTHON, [script, ...args]];
  if (process.platform === "win32" && existsSync(bundledPython)) return [bundledPython, [script, ...args]];
  return process.platform === "win32" ? ["py", ["-3", script, ...args]] : ["python3", [script, ...args]];
}

async function runExport(key, script, args) {
  if (activeSyncs.has(key)) return activeSyncs.get(key);
  const [command, commandArgs] = pythonCommand(script, args);
  const promise = new Promise((resolve, reject) => {
    const child = spawn(command, commandArgs, { stdio: ["ignore", "inherit", "inherit"] });
    child.once("error", reject);
    child.once("close", (code) => code === 0 ? resolve() : reject(new Error(`${key} export failed with code ${code}`)));
  }).finally(() => activeSyncs.delete(key));
  activeSyncs.set(key, promise);
  return promise;
}

async function isNewer(source, output) {
  try {
    const [sourceStat, outputStat] = await Promise.all([stat(source), stat(output)]);
    return sourceStat.mtimeMs > outputStat.mtimeMs;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    try { await stat(source); return true; } catch { return false; }
  }
}

async function syncFor(target) {
  if (target === outputs.plan && await isNewer(sources.plan, outputs.plan)) {
    await runExport("MagArrow plan", scripts.plan, [sources.plan, outputs.plan, "--profile", "magarrow-plan"]);
  }
  if (target === outputs.hetsuuCad && await isNewer(sources.hetsuuCad, outputs.hetsuuCad)) {
    await runExport("Hetsuu hutul DWG", scripts.dwg, [
      sources.hetsuuCad,
      outputs.hetsuuCad,
      "--dwgread", dwgread,
      "--utm-zone", "46",
    ]);
  }
  if (target === outputs.licence && await isNewer(sources.licence, outputs.licence)) {
    await runExport("Nergui Undur licence", scripts.shapefile, [sources.licence, outputs.licence, "--only-nergui-undur"]);
  }
  if (target === outputs.uchastik && await isNewer(sources.uchastik, outputs.uchastik)) {
    await runExport("Uchastik", scripts.shapefile, [sources.uchastik, outputs.uchastik, "--utm-zone-49n", "--dataset-type", "uchastik"]);
  }
  if (target === outputs.licenceContext && await isNewer(sources.licence, outputs.licenceContext)) {
    await runExport("Licence context", scripts.shapefile, [sources.licence, outputs.licenceContext, "--context-only"]);
  }
}

const server = http.createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    let target = path.resolve(root, `.${pathname}`);
    if (!target.startsWith(`${root}${path.sep}`) && target !== root) throw new Error("Invalid path");
    if ((await stat(target)).isDirectory()) target = path.join(target, "index.html");
    await syncFor(target);
    const body = await readFile(target);
    response.writeHead(200, {
      "Content-Type": types.get(path.extname(target)) || "application/octet-stream",
      "Cache-Control": "no-store",
    });
    response.end(body);
  } catch {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Not found");
  }
});

server.listen(port, host, () => {
  console.log(`Local: http://127.0.0.1:${port}`);
  console.log(`Area Data: ${areaDataRoot}`);
  console.log("Only the explicitly named MagArrow plan can auto-export; the L3 plan is never selected as a fallback.");
});
