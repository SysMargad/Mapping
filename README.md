# Mapping

Nergui Undur төслийн суурь хил, MagArrow төлөвлөгөө, sensor бүрийн баталгаажсан төлөв болон Арцат, Хэцүү хөтөл, Бүдүүн хад төслийн нислэгийн бүртгэл, баталгаажсан trajectory-г харуулдаг static Leaflet map.

Public URL: <https://sysmargad.github.io/Mapping/>

## Data truth model

- `dist/data/datasets.json` — dataset registry ба canonical metadata.
- `dist/data/manifest.json` — build version, шинэчлэгдсэн огноо, asset hash.
- `dist/data/base/` — licence, uchastik, DWG/CAD-аас гарсан base/control geometry. Sensor track биш.
- `dist/data/context/licenses.geojson` — 24 лицензийн external reference. Default-аар зурагт харагдахгүй; хэрэглэгч сонгоход тухайн polygon дээр төвлөрнө.
- `dist/data/context/hetsuu-hutul-dwg.geojson` — Drive дахь `Hetsuu hutul.DWG`-ийн 628 байрлалтай entity. `Hetsuu hutul` лиценз сонгоход автоматаар нээгдэнэ.
- `dist/data/context/project-trackers.json` — Арцат, Хэцүү хөтөл, Бүдүүн хадын Checklist-ээс гаргасан 780 actual flight-register record. Энэ бүртгэл нь trajectory geometry биш; pilot нэр web asset-д ороогүй.
- `dist/data/context/project-control-points.geojson` — гурван tracker-ийн Base/GCP хүснэгтээс нэгтгэсэн provenance asset. Control point нь trajectory биш тул map дээр цэнхэр цэгээр дүрслэхгүй.
- `dist/data/context/project-flight-tracks.geojson` — DJI FlightRecord KMZ-ээс баталгаажсан бодит trajectory. Одоогоор Бүдүүн хадын L2 sensor-ийн 2026-06-20-ны 3 нислэг байна.
- `dist/data/context/project-flight-coverage.json` — дээрх баталгаажсан trajectory-г 50 м өргөн зурвасаар тооцож, лицензийн polygon-д тайрсан sensor/өдрийн coverage summary.
- `dist/data/magarrow/planned-survey.geojson` — батлагдсан MagArrow survey plan.
- `dist/data/magarrow/mission-plans.geojson` — L01–L11 DJI mission plan. Actual flown track биш.
- `dist/data/magarrow/actual-tracks.geojson` — зөвхөн verified 10 Hz CSV-ээс үүснэ; CSV байхгүй үед хоосон, `pending_ingestion`.
- `dist/data/l3/metadata.json` — L3 survey family N1–N9 болон `sant laz` metadata. Actual trajectory баталгаажаагүй.

Project tracker өгөгдөл нь `project_operations` scope-д тусгаарлагдана. Nergui Undur sensor/trajectory өгөгдөлтэй нийлүүлэхгүй. Tracker-ийн mission бүртгэл дангаараа geometry биш: зөвхөн source filename нь `DJIFlightRecord_*.kmz`, `flightRecordVerified: true` бөгөөд tracker row-той тохирсон үед trajectory болно. Үлдсэн бүртгэлийг map дээр шугам болгон таамаглахгүй.

## Local preview

```powershell
node .\tools\serve.mjs
```

Дараа нь <http://127.0.0.1:4173> хаягийг нээнэ. Сервер `G:\.shortcut-targets-by-id\1hopqTCeMJknMkkvMa_wCY6OQ_GHXt4ub\Drone Track` Drive Desktop хавтсыг үндсэн Area Data эх үүсвэр болгоно; Drive холбогдоогүй үед хуучин `Desktop\Drone Track\Area Data` руу fallback хийнэ. Өөр зам ашиглах бол `AREA_DATA_ROOT` environment variable тохируулна. Сервер зөвхөн яг нэрлэсэн `NU_DR_MagArrow_Plan.gpkg`-ийг MagArrow plan болгон шинэчилнэ; `NU_DT_L3_PLAN.gpkg`-ийг fallback болгон сонгохгүй. Page өөрөө 30 секунд тутам refresh хийхгүй; UI дахь refresh товчийг хэрэглэнэ.

## Export commands

Энэ компьютерийн Codex Python runtime:

```powershell
$python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
```

MagArrow plan:

```powershell
& $python .\tools\export_gpkg.py `
  "G:\.shortcut-targets-by-id\1hopqTCeMJknMkkvMa_wCY6OQ_GHXt4ub\Drone Track\NU_DR_MagArrow_Plan.gpkg" `
  .\dist\data\magarrow\planned-survey.geojson `
  --profile magarrow-plan
```

Hetsuu hutul DWG (WGS 84 / UTM zone 46N):

```powershell
& $python .\tools\export_dwg.py `
  "G:\.shortcut-targets-by-id\1hopqTCeMJknMkkvMa_wCY6OQ_GHXt4ub\Drone Track\Hetsuu hutul.DWG" `
  .\dist\data\context\hetsuu-hutul-dwg.geojson `
  --dwgread "C:\Users\margad.p\Desktop\Drone Track\.tools\libredwg\dwgread.exe" `
  --utm-zone 46
```

Project trackers (татаж авсан read-only `.xlsx` copy):

```powershell
& $python .\tools\export_project_trackers.py `
  "<Hetsuu Hutul Drone Project Tracker.xlsx>" `
  "<Artsat Drone Project Tracker.xlsx>" `
  "<Buduunkhad Drone Project Tracker.xlsx>" `
  --licences .\dist\data\context\licenses.geojson `
  --summary-output .\dist\data\context\project-trackers.json `
  --points-output .\dist\data\context\project-control-points.geojson
```

Эх сурвалжууд нь `datasets.json`-д Google Sheet URL-аар бүртгэгдсэн. Export хийхдээ source workbook-ийг өөрчлөхгүй; Checklist-ийг нислэгийн operational бүртгэл болгон авч, trajectory гэж таамаглахгүй.

Verified DJI FlightRecord trajectory (татаж авсан read-only `.kmz` copy):

```powershell
& $python .\tools\export_dji_flight_kmz.py `
  "<FlightRecord KMZ folder>" `
  --licences .\dist\data\context\licenses.geojson `
  --tracks-output .\dist\data\context\project-flight-tracks.geojson `
  --coverage-output .\dist\data\context\project-flight-coverage.json
```

Coverage нь trajectory-н төв шугамаас тал бүрт 25 м буюу нийт 50 м зурвасыг 10 м grid-ээр тооцож, Бүдүүн хадын `XV-023222` лицензийн polygon-д тайрна. Sensor ба огноо сонгоход “Ниссэн нийт талбай” тухайн шүүлтүүрээр, “Бүх өдрийн нийлбэр” бүх огнооны нийлбэрээр шинэчлэгдэнэ.

Nergui Undur licence only:

```powershell
& $python .\tools\export_shapefile.py `
  "C:\Users\margad.p\Desktop\Drone Track\Talbain license.zip" `
  .\dist\data\base\licenses.geojson `
  --only-nergui-undur
```

External licence reference list:

```powershell
& $python .\tools\export_shapefile.py `
  "C:\Users\margad.p\Desktop\Drone Track\Talbain license.zip" `
  .\dist\data\context\licenses.geojson `
  --context-only
```

Uchastik:

```powershell
& $python .\tools\export_shapefile.py `
  "C:\Users\margad.p\Desktop\Drone Track\23099_uchastic_20260806.zip" `
  .\dist\data\base\uchastics.geojson `
  --utm-zone-49n --dataset-type uchastik
```

Actual MagArrow tracks are accepted only from locally verified 10 Hz CSV headers:

```powershell
& $python .\tools\export_magarrow_tracks.py <csv-folder> .\dist\data\magarrow\actual-tracks.geojson
```

The exporter stops instead of guessing ambiguous coordinate or time columns.

## Validation and release

```powershell
& $python .\tools\normalise_assets.py
& $python .\tools\validate_data.py
& $python .\tools\build_manifest.py --version 2026-09-17.5 --updated 2026-09-17
node --check .\dist\app.js
```

`main` branch руу push хийхэд `.github/workflows/pages.yml` нь `dist/`-ийг GitHub Pages-д deploy хийнэ. Static Pages deploy хийхийн өмнө registry, GeoJSON, manifest-ийг нэг commit-д хамт шинэчилнэ.
