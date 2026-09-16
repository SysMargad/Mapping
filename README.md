# Nergui Undur Survey Map

Nergui Undur төслийн суурь хил, MagArrow төлөвлөгөө болон sensor бүрийн баталгаажсан төлөвийг харуулдаг static Leaflet map.

Public URL: <https://sysmargad.github.io/Mapping/>

## Data truth model

- `dist/data/datasets.json` — dataset registry ба canonical metadata.
- `dist/data/manifest.json` — build version, шинэчлэгдсэн огноо, asset hash.
- `dist/data/base/` — licence, uchastik, DWG/CAD-аас гарсан base/control geometry. Sensor track биш.
- `dist/data/magarrow/planned-survey.geojson` — батлагдсан MagArrow survey plan.
- `dist/data/magarrow/mission-plans.geojson` — L01–L11 DJI mission plan. Actual flown track биш.
- `dist/data/magarrow/actual-tracks.geojson` — зөвхөн verified 10 Hz CSV-ээс үүснэ; CSV байхгүй үед хоосон, `pending_ingestion`.
- `dist/data/l3/metadata.json` — L3 survey family N1–N9 болон `sant laz` metadata. Actual trajectory баталгаажаагүй.

Өөр төслийн Artsat/Buduunkhad мэдээлэл орох ёсгүй. `tools/validate_data.py` энэ тусгаарлалтыг шалгана.

## Local preview

```powershell
node .\tools\serve.mjs
```

Дараа нь <http://127.0.0.1:4173> хаягийг нээнэ. Сервер зөвхөн яг нэрлэсэн `NU_DR_MagArrow_Plan.gpkg`-ийг MagArrow plan болгон шинэчилнэ; `NU_DT_L3_PLAN.gpkg`-ийг fallback болгон сонгохгүй. Page өөрөө 30 секунд тутам refresh хийхгүй; UI дахь refresh товчийг хэрэглэнэ.

## Export commands

Энэ компьютерийн Codex Python runtime:

```powershell
$python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
```

MagArrow plan:

```powershell
& $python .\tools\export_gpkg.py `
  "C:\Users\margad.p\Desktop\Drone Track\Area Data\NU_DR_MagArrow_Plan.gpkg" `
  .\dist\data\magarrow\planned-survey.geojson `
  --profile magarrow-plan
```

Nergui Undur licence only:

```powershell
& $python .\tools\export_shapefile.py `
  "C:\Users\margad.p\Desktop\Drone Track\Talbain license.zip" `
  .\dist\data\base\licenses.geojson `
  --only-nergui-undur
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
& $python .\tools\build_manifest.py --version 2026-09-16.1 --updated 2026-09-16
node --check .\dist\app.js
```

`main` branch руу push хийхэд `.github/workflows/pages.yml` нь `dist/`-ийг GitHub Pages-д deploy хийнэ. Static Pages deploy хийхийн өмнө registry, GeoJSON, manifest-ийг нэг commit-д хамт шинэчилнэ.
