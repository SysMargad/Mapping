# HUS NU Area Data map

`HUS_NU_Mag_plan.gpkg` файлын талбайг браузер дээр интерактив газрын зураг болгон харуулна.

## Өгөгдлийг шинэчлэх

GeoPackage файл өөрчлөгдвөл төслийн хавтаснаас дараах командыг ажиллуулна:

```powershell
& "C:\Users\margad.p\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" `
  .\tools\export_gpkg.py `
  "C:\Users\margad.p\Desktop\Drone Track\Area Data\HUS_NU_Mag_plan.gpkg" `
  .\dist\data\area.geojson
```

Хөрвүүлэгч нь нэмэлт сан шаарддаггүй, Python-ийн стандарт сангаар ажиллана.

## Локал харах

Файлыг шууд давхар дарж биш, жижиг веб серверээр нээнэ:

```powershell
node .\tools\serve.mjs
```

Дараа нь браузерт `http://127.0.0.1:4173` хаягийг нээнэ.
