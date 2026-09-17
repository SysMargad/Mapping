# Mapping

Nergui Undur төслийн суурь хил, MagArrow төлөвлөгөө, sensor бүрийн баталгаажсан төлөв болон Арцат, Хэцүү хөтөл, Бүдүүн хад төслийн нислэгийн бүртгэл, баталгаажсан trajectory-г харуулдаг static Leaflet map.

Public URL: <https://sysmargad.github.io/Mapping/>

## Өдөр тутмын Google Drive sync

`.github/workflows/drive-sync.yml` нь өдөр бүр **08:00 Asia/Shanghai** (`00:00 UTC`)-д GitHub Actions дээр ажиллана. OpenAI/Codex runtime ашиглахгүй. Workflow нь гурван project tracker-ийг Drive API-аар read-only татаж, Checklist-ийн mission folder холбоосууд дотроос coordinate агуулсан `KMZ`, `MRK`, `KML`, `CSV`, `TXT` эх үүсвэрийг mission/огноотой нь шалгаад trajectory болон coverage asset-ийг шинэчилнэ. Өөрчлөлт гарсан үед л `main` руу commit хийж, Pages deployment-ийг өдөөдөг.

Нэг удаагийн тохиргоо:

1. Google Cloud project дээр Google Drive API-г enable хийж service account үүсгэн JSON key татна.
2. Доорх гурван tracker файл болон Checklist-ээс холбоостой нислэгийн folder-уудыг service account-ын email-д **Viewer** эрхээр share хийнэ. Shared Drive хэрэглэж байвал service account-ыг тухайн Shared Drive-д viewer/member болгоно.
3. GitHub repository-н `Settings → Secrets and variables → Actions` хэсэгт `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON` нэртэй repository secret үүсгээд JSON key-ийн бүтэн агуулгыг оруулна. Key файлыг repository-д commit хийж болохгүй.
4. `Actions → Sync Google Drive survey data → Run workflow` гэж нэг удаа гараар ажиллуулж, амжилттай болсныг шалгана. Secret байхгүй үед scheduled run нь өгөгдөл өөрчлөхгүйгээр safe skip хийнэ.

Tracker file ID болон scan тохиргоо нь `drive-sync-sources.json`-д байна. Source file нь tracker огноотой зөрвөл эсвэл тухайн лицензийн гадна координаттай бол importer түүнийг вебийн trajectory болгохгүй.

## Data truth model

- `dist/data/datasets.json` — dataset registry ба canonical metadata.
- `dist/data/manifest.json` — build version, шинэчлэгдсэн огноо, asset hash.
- `dist/data/base/` — licence, uchastik, DWG/CAD-аас гарсан base/control geometry. Sensor track биш.
- `dist/data/context/licenses.geojson` — 24 лицензийн external reference. Default-аар зурагт харагдахгүй; хэрэглэгч сонгоход тухайн polygon дээр төвлөрнө.
- `dist/data/context/hetsuu-hutul-dwg.geojson` — Drive дахь `Hetsuu hutul.DWG`-ийн 628 байрлалтай entity. `Hetsuu hutul` лиценз сонгоход автоматаар нээгдэнэ.
- `dist/data/context/project-trackers.json` — Арцат, Хэцүү хөтөл, Бүдүүн хадын Checklist-ээс гаргасан 780 actual flight-register record. Энэ бүртгэл нь trajectory geometry биш; pilot нэр web asset-д ороогүй.
- `dist/data/context/project-control-points.geojson` — гурван tracker-ийн Base/GCP хүснэгтээс нэгтгэсэн provenance asset. Control point нь trajectory биш тул map дээр цэнхэр цэгээр дүрслэхгүй.
- `dist/data/context/project-flight-tracks.geojson` — DJI FlightRecord KMZ болон зураг авалтын `Timestamp.MRK` GNSS байрлалаас баталгаажсан бодит trajectory. Бүдүүн хадын 120, Арцатын 1 нислэг огноо/sensor-оор харагдана.
- `dist/data/context/project-flight-coverage.json` — дээрх баталгаажсан trajectory-г 50 м өргөн зурвасаар тооцож, лицензийн polygon-д тайрсан sensor/өдрийн coverage summary.
- `dist/data/magarrow/planned-survey.geojson` — батлагдсан MagArrow survey plan.
- `dist/data/magarrow/mission-plans.geojson` — L01–L11 DJI mission plan. Actual flown track биш.
- `dist/data/magarrow/actual-tracks.geojson` — зөвхөн verified 10 Hz CSV-ээс үүснэ; CSV байхгүй үед хоосон, `pending_ingestion`.
- `dist/data/l3/metadata.json` — L3 survey family N1–N9 болон `sant laz` metadata. Actual trajectory баталгаажаагүй.

Project tracker өгөгдөл нь `project_operations` scope-д тусгаарлагдана. Nergui Undur sensor/trajectory өгөгдөлтэй нийлүүлэхгүй. Tracker-ийн mission бүртгэл дангаараа geometry биш: зөвхөн tracker холбоостой таарсан `DJIFlightRecord_*.kmz` эсвэл `Timestamp.MRK` coordinate эх үүсвэртэй үед trajectory болно. Үлдсэн бүртгэлийг map дээр шугам болгон таамаглахгүй.

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

Coverage нь trajectory-н төв шугамаас тал бүрт 25 м буюу нийт 50 м зурвасыг 10 м grid-ээр тооцож, тухайн төслийн лицензийн polygon-д тайрна. Sensor ба огноо сонгоход “Ниссэн нийт талбай” тухайн шүүлтүүрээр, “Бүх өдрийн нийлбэр” бүх огнооны нийлбэрээр шинэчлэгдэнэ.

Нэгтгэсэн MRK/KMZ trajectory asset-аас бүх төслийн coverage-г дахин бодох:

```powershell
& $python .\tools\export_project_flight_coverage.py `
  .\dist\data\context\project-flight-tracks.geojson `
  --licences .\dist\data\context\licenses.geojson `
  --output .\dist\data\context\project-flight-coverage.json
```

Хэцүү хөтлийн бодит trajectory файлуудыг tracker mission-тэй холбох:

1. Drive дахь `Timestamp.MRK`, `DJIFlightRecord KMZ/TXT`, KML эсвэл longitude/latitude баганатай CSV файлуудыг нэг local хавтас руу read-only байдлаар татна.
2. Файлын нэрэнд tracker-ийн бүтэн mission нэр байвал автоматаар таарна. `DJIFlightRecord_YYYY-MM-DD_[HH-MM-SS]` нэртэй файл нь ижил өдрийн ганц, 20 минутын доторх mission-тэй л автоматаар таарна.
3. Нэрээр найдвартай таарахгүй бол `flight-source-map.example.json`-ийг хуулж, файл бүрт `trackerId`/`mission` болон Drive-ийн `sourceUrl`-ийг заана.

```powershell
& $python .\tools\import_project_flight_tracks.py `
  "<Drive-аас татсан Хэцүү хөтлийн flight file хавтас>" `
  --project hetsuu-hutul `
  --trackers .\dist\data\context\project-trackers.json `
  --licences .\dist\data\context\licenses.geojson `
  --existing .\dist\data\context\project-flight-tracks.geojson `
  --output .\dist\data\context\project-flight-tracks.geojson `
  --mapping .\flight-source-map.json `
  --report .\hetsuu-flight-import-report.json

& $python .\tools\export_project_flight_coverage.py `
  .\dist\data\context\project-flight-tracks.geojson `
  --licences .\dist\data\context\licenses.geojson `
  --output .\dist\data\context\project-flight-coverage.json
```

Importer нь coordinate-той файлгүй tracker мөрөөс шугам зохиохгүй. Mission/огноо зөрсөн, лицензийн талбайгаас гадуур координаттай, эсвэл нэг mission-тэй давхар таарсан файлыг `rejected`/`unmatched` тайланд үлдээнэ. Амжилттай орсон Хэцүү хөтлийн trajectory нь одоо байгаа map-ийн sensor → өдөр сонголтоор Нэргүй өндөр, Бүдүүн хадтай адил шугамаар харагдаж, ниссэн талбайн тооцоонд орно.

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
& $python .\tools\build_manifest.py --version 2026-09-17.7 --updated 2026-09-17
node --check .\dist\app.js
```

`main` branch руу push хийхэд `.github/workflows/pages.yml` нь `dist/`-ийг GitHub Pages-д deploy хийнэ. Static Pages deploy хийхийн өмнө registry, GeoJSON, manifest-ийг нэг commit-д хамт шинэчилнэ.
