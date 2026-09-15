# Drone Track · Area Data map

`Area Data` хавтас дахь хамгийн сүүлд шинэчлэгдсэн GeoPackage-ийн талбай, нислэгийн блок,
үндсэн болон хөндлөн шугам, эхлэл/төгсгөлийн цэгийг интерактив газрын зураг болгон харуулна.
`Talbain license.zip` доторх нэмэлт лицензийн талбайнуудыг мөн нэрээр нь сонгон газрын зураг дээр харуулна.

## Өгөгдлийг шинэчлэх

GeoPackage файл өөрчлөгдвөл сервер хүсэлт ирэх үед хамгийн сүүлд шинэчлэгдсэн `.gpkg` файлыг
автоматаар сонгон GeoJSON болгон хөрвүүлнэ. Гараар хөрвүүлэх шаардлагатай бол:

```powershell
& "C:\Users\margad.p\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" `
  .\tools\export_gpkg.py `
  "C:\Users\margad.p\Desktop\Drone Track\Area Data\NU_DT_MagArrow_FlightPlan_P1.gpkg" `
  .\dist\data\area.geojson
```

Хөрвүүлэгч нь нэмэлт сан шаарддаггүй, Python-ийн стандарт сангаар ажиллана.

## Локал харах

Файлыг шууд давхар дарж биш, жижиг веб серверээр нээнэ:

```powershell
node .\tools\serve.mjs
```

Дараа нь браузерт `http://127.0.0.1:4173` хаягийг нээнэ.

## Өөр компьютерээс нээх

`127.0.0.1` нь зөвхөн сервер ажиллаж байгаа компьютерийг заадаг. Серверийг сүлжээнд
харагдахаар ажиллуулаад тухайн компьютерийн IPv4 хаягийг ашиглана:

```powershell
$env:PORT = "4180"
node .\tools\serve.mjs
```

Сервер ажиллаж байгаа компьютер дээр `ipconfig` ажиллуулж `IPv4 Address` хаягийг олно.
Жишээ нь `192.168.1.25` байвал нөгөө компьютерийн браузерт:

```text
http://192.168.1.25:4180
```

Хоёр компьютер нэг Wi-Fi/LAN сүлжээнд байх ёстой. Windows Firewall асуувал Node.js-д
Private network access зөвшөөрөх эсвэл 4180 портыг зөвшөөрнө. Интернэтээр, өөр сүлжээнээс
хандах бол LAN IP хангалтгүй бөгөөд серверийг public hosting/VPS дээр байрлуулах шаардлагатай.

Сервер ажиллаж байх үед `Area Data` хавтасны GeoPackage шинэчлэгдсэн эсэхийг шалгана.
Шинэчлэгдсэн бол веб хүсэлт ирэхэд GeoJSON автоматаар дахин үүсэж, браузер 30 секунд тутамд
мэдээллээ дахин уншин газрын зураг болон талбайн нийлбэрийг шинэчилнэ.

`Talbain license.zip` шинэчлэгдсэн үед сервер Shapefile-ийг автоматаар GeoJSON болгон хөрвүүлж,
вебийн `Лицензийн талбай` хэсэгт нэрээр нь сонгох боломжтой болгоно.

## GitHub Pages

Энэ төслийн `dist` хавтсыг GitHub Pages-д нийтлэх workflow
`.github/workflows/pages.yml` дотор байна. Repository-д push хийсний дараа GitHub дээр:

1. **Settings → Pages** рүү орно.
2. **Build and deployment → Source** хэсгээс **GitHub Actions** сонгоно.
3. `Deploy Drone Track to GitHub Pages` workflow дууссаны дараа
   `https://sysmargad.github.io/Mapping/` хаягийг нээнэ.

GitHub Pages нь static сайт тул `Area Data` болон ZIP өөрчлөгдсөн үед локал сервер шиг
автоматаар export хийхгүй. Шинэ өгөгдөл нийтлэхийн өмнө `dist/data/area.geojson`,
`dist/data/licenses.geojson`, `dist/data/uchastics.geojson` файлуудыг шинэчилж repository-д
хамт push хийнэ.
