# Drone Track

Nergui Undur төслийн суурь хил, sensor бүрийн баталгаажсан төлөв болон Арцат, Хэцүү хөтөл, Бүдүүн хад төслийн нислэгийн бүртгэл, баталгаажсан trajectory-г харуулдаг static Leaflet map.

Public URL: <https://sysmargad.github.io/Mapping/>

## Өдөр тутмын Google Drive sync

`.github/workflows/drive-sync.yml` нь өдөр бүр **08:00 Asia/Shanghai** (`00:00 UTC`)-д GitHub Actions дээр ажиллана. OpenAI/Codex runtime ашиглахгүй. Workflow нь дөрвөн project tracker-ийг Drive API-аар read-only татаж, Checklist-ийн mission folder холбоосууд дотроос coordinate агуулсан `KMZ`, `MRK`, `KML`, `CSV`, `TXT` эх үүсвэрийг mission/огноотой нь шалгаад trajectory болон coverage asset-ийг шинэчилнэ. Өөрчлөлт гарсан үед л `main` руу commit хийж, Pages deployment-ийг өдөөдөг.

Нэг удаагийн тохиргоо:

1. Google Cloud project дээр Google Drive API-г enable хийж service account үүсгэн JSON key татна.
2. Дөрвөн tracker файл, Checklist-ээс холбоостой нислэгийн folder-ууд болон төслийн root folder-уудыг service account-ын email-д **Viewer** эрхээр share хийнэ. Shared Drive хэрэглэж байвал service account-ыг тухайн Shared Drive-д viewer/member болгоно.
3. GitHub repository-н `Settings → Secrets and variables → Actions` хэсэгт `GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON` нэртэй repository secret үүсгээд JSON key-ийн бүтэн агуулгыг оруулна. Key файлыг repository-д commit хийж болохгүй.
4. `Actions → Sync Google Drive survey data → Run workflow` гэж нэг удаа гараар ажиллуулж, амжилттай болсныг шалгана. Secret байхгүй үед scheduled run нь өгөгдөл өөрчлөхгүйгээр safe skip хийнэ.

Sync ажилласны дараа `Actions → Sync Google Drive survey data → <run>` хуудасны **Summary** хэсэгт шатны тоолуур гарна: аль secret тохируулагдсан, хэдэн хавтас скан хийсэн, хэдэн файл шалгасан, хэдэн header probe зарцуулсан, хэдийг татсан, project бүрийн reason code-ийн тоо. Уг хүснэгт нь зөвхөн тоо агуулах бөгөөд Drive-ийн файл/хавтасны нэр, secret-ийн утгыг хэвлэдэггүй; файлын нэр бүхий дэлгэрэнгүй audit нь зөвхөн job-ийн log-д үлдэнэ. `Unreadable` багана өндөр байвал энэ нь код биш, **share эрхийн** асуудал.

Tracker file ID болон баталгаажсан project root folder ID нь `drive-sync-sources.json`-д байна. `HETSUU_HUTUL_ROOT_FOLDER_ID`, `ARTSAT_ROOT_FOLDER_ID`, `BUDUUNKHAD_ROOT_FOLDER_ID`, `NERGUI_UNDUR_RAW_ROOT_FOLDER_ID` secret тохируулбал config-ийн root-ийг override хийнэ; secret байхгүй үед config-ийн fallback root ашиглагдана. Ингэснээр root secret орхигдсоноос болж шинэ нислэг чимээгүй алга болохгүй. Root discovery нь project root-ийн хамгийн сүүлийн mission огнооноос хойшхи **60 хоногийн** mission folder-уудыг coordinate sidecar-аар шалгана; Checklist-ийн хамгийн сүүлийн огнооноос хойшхи 21 хоногийн шууд mission-folder холбоосыг мөн давхар шалгана. Hyperlink дутуу үед root доторх recent DJI mission-оос зөвхөн `metadata.csv`, `Timestamp.MRK`, `DJIFlightRecord` sidecar-уудыг зурагнуудыг жагсаалгүй хайна. Coordinate-bearing `metadata.csv` байвал MRK-ээс түрүүлж ашиглана. Ингэснээр root-ийн ерөнхий хайлтад хараахан ороогүй хамгийн шинэ P1/L2/L3 trajectory алга болохгүй бөгөөд улирлын ажил завсарласан үед ч tracker/root-ийн өөрийн огноонд тулгуурлана. Source file нь tracker огноотой зөрвөл эсвэл тухайн лицензийн гадна координаттай бол importer түүнийг вебийн trajectory болгохгүй.

Sync эхлэхдээ project root бүрийг `files.get`-ээр шууд шалгана. Actions summary дахь `rootReadable: false` нь config буруу эсвэл Shared Drive-ийг service account-д share хийгээгүйг, `rootReadable: true` боловч `matchedBranchCount: 0` нь project root ID буруу түвшний folder заасныг илтгэнэ. Shared Drive access-ийг хувийн `@netgroup.mn` хаягт бус, `GOOGLE_SERVICE_ACCOUNT_JSON` доторх `client_email` (`...iam.gserviceaccount.com`)-д өгөх шаардлагатай.

Нэргүй өндөр төслийн root folder ID-г `NERGUI_UNDUR_RAW_ROOT_FOLDER_ID` repository secret-д хадгална. Sync нь дөрөв дэх project tracker-ийг татаж, root-ийн `KMZ & Flight record` салбаруудаас L3 mission-ийг tracker-ийн огноо/нэртэй баталгаажуулан импортлоно. MagArrow-ийн хувьд `SRVY*-ACQU*` нэртэй `.magdata`, стандарт 10 Hz CSV, мөн header дотроо latitude/longitude баганатай ердийн CSV/TXT хүснэгтийг уншина. CSV/TXT-ийн координатыг файлын агуулгаар баталгаажуулж, тасарсан хэсгийг шулуун шугамаар зохиомлоор холбохгүй. Acquisition нэр давтагдсан ч огноогоор нь тусдаа track болгон хадгална. Шинэ эсвэл өөрчлөгдсөн эх үүсвэрийг импортлоод trajectory, 50 м coverage болон dataset registry-г шинэчилнэ. `Raw Data` доторх `DJI...` файлын тоогоор яг **4 файлтай хавтсыг P1**, яг **11 файлтай хавтсыг L3** гэж нийлбэрээр ангилна. Private folder ID, нэр, замыг public Pages asset болон Actions summary-д нийтлэхгүй.

### DJI (L2/L3/P1) — tracker-ийн бүх мөрийг Drive даяар нэрээр нь

DJI mission хавтас бүрт `<mission нэр>_Timestamp.MRK` камерын байрлалын лог байдаг. Sync нь service account-ын уншиж чадах **бүх Drive**-аас эдгээрийг хайж (`"djiSidecarDriveSearch": true`), tracker-ийн Checklist-ийн **бүх** mission мөртэй (Link баганагүй, хуучин огноотой ч) mission нэрээр тааруулна. Tracker-т богино үндсэн нэр (`DJI_..._001`) бичигдсэн ч MRK файлд араас нь төхөөрөмж/route-ийн нэмэлт нэр залгагдсан бол бүтэн tracker нэрийн prefix-ээр зөв mission гэж танина. Trajectory нь аль хэдийн нийтлэгдсэн мөрийг дахин татахгүй; нэг mission-ы хэд хэдэн хуулбар байвал `(delete)` гэх мэт устгах хавтаснаас бусдыг сонгоно. Tracker-т хараахан бүртгэгдээгүй нислэг (жишээ нь tracker шинэчлэгдэхээс өмнөх) tracker-т орсон өдрийн sync-ээр автоматаар нэмэгдэнэ. MRK-ийн `541,N  -456,E` талбарууд нь антенны мм офсет тул байрлалыг зөвхөн `…,Lat` / `…,Lon` талбараас уншина.

Дата 4 өөр Shared Drive-д байдаг (Нэргүйн DJI, Хэцүү хөтөл/Арцатын DJI, Бүдүүн хад, MagArrow геофизик). Service account бүгдийг унших эрхтэй байх ёстой. Actions summary-ийн `Per project` хүснэгтийн `MRK matched by name` багана 0 бол тухайн төслийн Drive-д эрх дутуу байна.

### MagArrow — бүх лицензэд автоматаар

MagArrow нэг төхөөрөмж лиценз хооронд шилжиж нисдэг (жишээ нь Нэргүй өндөр 10/27 хүртэл ACQU47–59, дараа нь Арцат 11/04-өөс ACQU60+). Иймээс sync нь лиценз бүрийн root folder-оос хамаарахгүйгээр service account-ын **уншиж чадах бүх Drive**-аас `SRVY*-ACQU*` нэртэй `.magdata`, `-10Hz.csv`, `_10Hz.csv`, `.txt` файлыг нэрээр нь хайна (`drive-sync-sources.json` → `"magArrowDriveSearch": true`). Огноог хамгийн гүн огноотой хавтаснаас (`2026.11.05`, `2026-10-27` г.м.) авна; MagArrow-ийн UTC цаг орон нутгийн хавтасны өдрөөс 1 хоногоор зөрөхийг зөвшөөрч, түүнээс их зөрвөл GNSS огноог үнэн гэж үзнэ.

- **Лиценз оноолт:** trajectory-ийн өөрийн координат аль лицензийн полигонд хамгийн олон цэгтэй байна, тэр лицензэд оноогдоно (tie line хил давдаг тул олонх шаардахгүй). Аль ч полигонд ороогүй бол 10 км доторх хамгийн ойр лицензэд, түүнээс хол бол нийтлэхгүй (`unassignedCount`).
- **Нэргүй өндөр** (`XV-023099`) өмнөх шигээ `dist/data/magarrow/actual-tracks.geojson` болон участикийн coverage-д орно. **Бусад лиценз** `dist/data/context/project-flight-tracks.geojson`-д `sensor: "MagArrow"`, `coverageStatus: "calculated"` (50 м зурвас) байдлаар орж, тухайн лицензийн project panel дээр MagArrow sensor болж харагдана. Tracker-гүй лиценз `projectKey: "licence-<код>"`, `autoDiscovered: true`-тэй гарна.
- **Давхардал:** нэг нислэгийн CSV болон raw `.magdata` (мөн Drive-ийн `(1)` хуулбар, өөр өдрийн хавтсанд хуулсан хувилбар) acquisition-ий үндсэн нэр + эхлэх цаг (≤ 5 мин)-аар нэг track болно; CSV-г илүүд үзнэ. Нэр давтагдсан ч өөр цагт эхэлсэн нислэгүүд тусдаа үлдэнэ.
- **Дахин татахгүй:** track бүр өөрийн бүх хуулбарын hash-лагдсан fingerprint (`sourceFingerprints`, `sourceIdHashes`)-ийг хадгалдаг тул нэг файлыг нэг л удаа татна. Файлын нэр, ID нийтлэгдэхгүй.
- **Эрх:** Арцат болон бусад лицензийн MagArrow хавтсыг service account унших ёстой. Хамгийн хялбар нь service account-ыг тухайн **Shared Drive**-д Viewer гишүүн болгох. Actions summary-ийн `MagArrow (all licences)` хүснэгтэд project бүрийн acquisition тоо болон сүүлийн огноо гарна; Арцат тэнд харагдахгүй бол энэ нь share эрхийн асуудал.

Хэцүү хөтлийн үндсэн project folder ID-г `HETSUU_HUTUL_ROOT_FOLDER_ID` repository secret-д хадгална. Sync нь `Drone`, `UAV`, `Magnetic_Survey`, `MagArrow`, `Flight log/record` нэртэй салбаруудыг олж, `DJIFlightRecord`, `Timestamp.MRK`, `SRVY*-ACQU*`, KML/KMZ зэрэг coordinate эх үүсвэрийг бүртгэнэ. Project root дахь 2025 UAV magnetic survey болон 2026 tracker бүртгэлийг огноо/mission баталгаагүйгээр хооронд нь холихгүй; tracker-ийн огноо эсвэл бүтэн mission нэр таарсан үед л trajectory importer-т дамжуулна. Coordinate source-д altitude/relative-height багана байвал trajectory asset-д min/max/mean өндөр хадгалагдаж, map popup болон нислэгийн бүртгэлд харагдана.

## Data truth model

- `dist/data/datasets.json` — dataset registry ба canonical metadata.
- `dist/data/manifest.json` — build version, шинэчлэгдсэн огноо, asset hash.
- `dist/data/base/` — licence, uchastik, DWG/CAD-аас гарсан base/control geometry. Sensor track биш.
- `dist/data/context/licenses.geojson` — 24 лицензийн external reference. Default-аар зурагт харагдахгүй; хэрэглэгч сонгоход тухайн polygon дээр төвлөрнө.
- `dist/data/context/hetsuu-hutul-dwg.geojson` — Drive дахь `Hetsuu hutul.DWG`-ийн 628 байрлалтай entity. `Hetsuu hutul` лиценз сонгоход автоматаар нээгдэнэ.
- `dist/data/context/project-trackers.json` — Арцат, Хэцүү хөтөл, Бүдүүн хадын Checklist-ээс гаргасан 780 actual flight-register record. Энэ бүртгэл нь trajectory geometry биш; pilot нэр web asset-д ороогүй.
- `dist/data/context/project-control-points.geojson` — гурван tracker-ийн Base/GCP хүснэгтээс нэгтгэсэн provenance asset. Control point нь trajectory биш тул map дээр цэнхэр цэгээр дүрслэхгүй.
- `dist/data/context/project-flight-tracks.geojson` — DJI FlightRecord KMZ, зураг авалтын `Timestamp.MRK`, Medusa-гийн QC-cleaned traverse GNSS байрлалаас баталгаажсан бодит trajectory. Бүдүүн хадын 120 DJI нислэг, Арцатын 1 L2 нислэг болон 24 Medusa traverse шугам огноо/sensor-оор харагдана.
- `dist/data/context/project-flight-coverage.json` — зурвасын өргөн баталгаажсан trajectory-г 50 м өргөн зурвасаар тооцож, лицензийн polygon-д тайрсан sensor/өдрийн coverage summary. Medusa-гийн footprint/swath баталгаажаагүй тул улаан track харагдах боловч coverage-д оруулахгүй.
- `dist/data/context/project-campaign-tracks.geojson` — tracker бүртгэлд хамаарахгүй, бие даасан acquisition campaign-ийн GNSS зам. Tracker-ийн mission, огноо, sensor-ийг энэ dataset-руу хуулахыг `validate_data.py` хориглоно. Campaign-ийн зурвасын өргөн баталгаажаагүй тул `coverageStatus: not_calculated` буюу хамрах талбайг тооцохгүй.
- `dist/data/magarrow/mission-plans.geojson` — L01–L11 DJI mission plan. Actual flown track биш.
- `dist/data/magarrow/actual-tracks.geojson` — verified 10 Hz CSV, coordinate-bearing CSV/TXT болон raw `.magdata`-гийн embedded NMEA-аас үүссэн Нэргүй өндөрийн actual trajectory. Өдөр тутмын Drive sync шинэ acquisition-ийг автоматаар нэмнэ. Бусад лицензийн MagArrow trajectory `project-flight-tracks.geojson`-д ордог (дээрх “MagArrow — бүх лицензэд автоматаар” хэсгийг үз).
- `dist/data/magarrow/archive-planned-lines.geojson` — 2026-10-21, 2026-10-24, 2026-10-25 өдрийн Drive KMZ/WPMZ дотор хадгалсан 32 MagArrow survey/tie-line маршрут. Эдгээрийг вебийн MagArrow хэсэгт цэнхэр/цэнхэр-ногоон тасархай **planned** шугамаар харуулна; actual flown trajectory гэж тооцохгүй. `drive-sync-sources.json`-д тохируулсан хавтсыг өдөр тутмын sync дахин уншина.
- `dist/data/l3/metadata.json` — L3 survey family N1–N9 болон `sant laz` metadata. Actual trajectory баталгаажаагүй.

Map дээр баталгаажсан trajectory нь sensor-аар тогтмол өнгөтэй: **MagArrow цэнхэр** (`#2563eb`), **Medusa улаан** (`#e11d48`), **L2 ногоон** (`#16a34a`), **L3 нил ягаан** (`#9333ea`), **P1 улбар шар** (`#f59e0b`). Sensor нь баталгаажаагүй campaign саарал тасархай шугамаар харагдана. Өнгө нь track байгаа мэт таамаглахгүй; coordinate geometry баталгаажсан үед л шугам зурагдана.

Project tracker өгөгдөл нь `project_operations` scope-д тусгаарлагдана. Nergui Undur sensor/trajectory өгөгдөлтэй нийлүүлэхгүй. Tracker-ийн mission бүртгэл дангаараа geometry биш: зөвхөн tracker холбоостой таарсан `DJIFlightRecord_*.kmz` эсвэл `Timestamp.MRK` coordinate эх үүсвэртэй үед trajectory болно. Үлдсэн бүртгэлийг map дээр шугам болгон таамаглахгүй.

## Local preview

```powershell
node .\tools\serve.mjs
```

Дараа нь <http://127.0.0.1:4173> хаягийг нээнэ. Сервер `G:\.shortcut-targets-by-id\1hopqTCeMJknMkkvMa_wCY6OQ_GHXt4ub\Drone Track` Drive Desktop хавтсыг үндсэн Area Data эх үүсвэр болгоно; Drive холбогдоогүй үед хуучин `Desktop\Drone Track\Area Data` руу fallback хийнэ. Өөр зам ашиглах бол `AREA_DATA_ROOT` environment variable тохируулна. Хуучин `NU_DR_MagArrow_Plan.gpkg` source-ийг site asset болгон дахин үүсгэхгүй. Page өөрөө 30 секунд тутам refresh хийхгүй; UI дахь refresh товчийг хэрэглэнэ.

## Export commands

Энэ компьютерийн Codex Python runtime:

```powershell
$python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
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

Coverage нь trajectory-н төв шугамаас тал бүрт 25 м буюу нийт 50 м зурвасыг 10 м grid-ээр тооцож, тухайн төслийн лицензийн polygon-д тайрна. Нийтлэх geometry-г 2.5 м-ийн нарийвчлалаар хялбарчилдаг тул шулуун судалгааны шугам хэдэн км-ийн ганц сегмент болдог; ийм сегментийг бүтнээр нь тооцно. Харин 1 Hz бичлэгт 10 секундээс урт тасалдал байвал parser тэр сегментийг `gapSegments`-д тэмдэглэж, coverage-д оруулахгүй (ниссэнгүй газрыг шулуунаар холбож тооцохгүй). `gapSegments`-гүй хуучин trajectory-д 2 км-ээс урт сегментийг зөвхөн бичлэг тасралтгүй (цэгийн тоо ≈ нислэгийн секунд) үед тооцно. “Ниссэн нийт талбай” нь сонгосон sensor-ийн бүх огнооны давхардлыг нэгтгэсэн талбай. Бүх огноо сонгосон үед гурав дахь үзүүлэлт “Бүх өдрийн нийлбэр”, харин тодорхой өдөр сонгосон үед “Өдрийн ниссэн талбай” болж тухайн өдрийн хэмжээг харуулна.

Арцатын боловсруулсан Medusa traverse CSV-г tracker-оос тусад нь session/line ID-гаар импортлох:

```powershell
& $python .\tools\import_medusa_traverse_tracks.py `
  "<final_traverse_points.csv>" `
  --licences .\dist\data\context\licenses.geojson `
  --existing .\dist\data\context\project-flight-tracks.geojson `
  --output .\dist\data\context\project-flight-tracks.geojson
```

Importer нь зөвхөн `TRAVERSE` ангилалтай, `hard_qc_pass=True` цэгүүдийг авна. Medusa-ийн footprint/swath өргөн баталгаажаагүй тул эдгээр шугамыг coverage тооцоонд оруулахгүй.

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

Tracker-т хамаарахгүй судалгааны campaign (жишээ нь Хэцүү хөтлийн 2025 оны соронзон хэмжилтийн GPS зам):

```powershell
& $python .\tools\import_campaign_tracks.py `
  "<Drive-аас татсан campaign файлын хавтас>" `
  --project hetsuu-hutul `
  --campaigns .\drive-sync-sources.json `
  --licences .\dist\data\context\licenses.geojson `
  --mapping "<campaign-mapping.json>" `
  --existing .\dist\data\context\project-campaign-tracks.geojson `
  --output .\dist\data\context\project-campaign-tracks.geojson `
  --audit "<private audit зам>"
```

Campaign нь `drive-sync-sources.json`-ы `campaigns` хэсэгт **тодорхой зарлагдсан** байх ёстой: зөвхөн байршлаасаа болж ямар ч хүснэгт замд хувирахгүй. Importer нь файлын өөрийнх нь header-ээс latitude/longitude-г танина (`tools/coordinate_sources.py`), GCP/sample цэгийн хүснэгтийг татгалзана, огноо/цагийг файлын өөрийн баганаас л авна, бичлэгийн тасалдал бүрээр замыг салгана, raw болон merged CSV-ийн давхардлыг checksum ба координатын давхцалаар шийдэж canonical сонголтоо audit-д тэмдэглэнэ. Мэдэгдэхүйц огноо/цаггүй эх сурвалж нь `review_required` статустай үлдэнэ. Audit нь `IMPORTED`, `MISSING_SOURCE`, `ACCESS_DENIED`, `UNSUPPORTED_FLIGHT_LOG`, `INVALID_COORDINATES`, `AMBIGUOUS_DATE_TIME`, `DATE_MISMATCH`, `MISSION_UNMATCHED`, `AMBIGUOUS_MATCH`, `DUPLICATE_SOURCE`, `WRONG_PROJECT_LOCATION` шалтгааны кодуудыг ашиглана. Audit нь Drive-ийн файл/хавтасны нэрийг агуулдаг тул зөвхөн workflow-ийн private log-д үлдэж, public asset-д гардаггүй.

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

Actual MagArrow tracks may be built from verified 10 Hz CSV, coordinate-bearing CSV/TXT, or embedded NMEA in `.magdata`. The generic table parser rejects files without latitude/longitude headers and static GCP/sample tables:

```powershell
& $python .\tools\export_magarrow_tracks.py <csv-folder> .\dist\data\magarrow\actual-tracks.geojson
```

The exporter stops instead of guessing ambiguous coordinate or time columns.

## Validation and release

```powershell
& $python -m unittest discover -s tests -t .
& $python .\tools\normalise_assets.py
& $python .\tools\validate_data.py
& $python .\tools\build_manifest.py --version 2026-09-17.7 --updated 2026-09-17
node --check .\dist\app.js
```

`tests/` нь зөвхөн стандарт сангаар ажилладаг (openpyxl, Drive SDK шаардахгүй) тул `pages.yml` болон `drive-sync.yml` хоёулаа ажиллуулдаг.

`main` branch руу push хийхэд `.github/workflows/pages.yml` нь `dist/`-ийг GitHub Pages-д deploy хийнэ. Static Pages deploy хийхийн өмнө registry, GeoJSON, manifest-ийг нэг commit-д хамт шинэчилнэ.
