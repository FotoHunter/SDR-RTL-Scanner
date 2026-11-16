# SDR-RTL-Scanner — TODO

Текущая версия: **v0.8.9**
Обновлено: 2026-11-16

---

## 🎯 Ближайшие задачи (v0.9.0)

### Архитектура и данные
- [ ] **Разделить `weather_history.jsonl` по регионам**
  - Перейти на `data/weather_history_{region}.jsonl` (например, `weather_history_RU.spb.jsonl`)
  - В `weather_logger.py` передавать `region` из `ctx`
  - Обновить `.gitignore` (пока оставить в игноре, но с префиксом)
  - *Решение о коммите — после разделения*

- [ ] **Вынести вещательные центры в отдельную таблицу (Postgres)**
  - Миграция: `sites` (имя, координаты, высота, страна)
  - `transmitters.site_id` → FK на `sites.id`
  - Убрать `broadcast_center_name` из `transmitters` (строка → FK)

- [ ] **Добавить `global_ppm` в сессию**
  - Медиана PPM по всем удачным калибровкам
  - Писать в `ctx["global_ppm"]` и в отчёт
  - Станции хранят только `ppm_delta` (при высоком SNR)

### PPM и стерео-детекция
- [ ] **Починить детектор стерео для OIRT**
  - Проверить, видит ли `rtl_fm` пилот-тон 31.25 кГц
  - Если подавляет — использовать `rtl_fm` с отключённым стерео-декодером
  - Или анализировать IQ-сигнал (а не аудио)

- [ ] **Ограничить PPM в `auto_ppm_calibrate_carrier`** (для HF/AM)
  - Добавить проверку `-150..150` (как в FM-ветке)

### Справочники и регионы
- [ ] **Заполнить `broadcast_centers_RU.spb.json` реальными данными**
  - Уточнить координаты Ольгино и Красного Бора через OSM
  - Добавить `osm_id` и `sources` с `confidence: medium`

- [ ] **Добавить Overpass-скрипт для автосбора вышек**
  - `scripts/fetch_osm_sites.py`: POST на `overpass-api.de/api/interpreter`
  - Мерж в `broadcast_centers_{region}.json` (не затирать ручные правки)

- [ ] **Расширить регионы РФ**
  - `RU.ekb` (Екатеринбург), `RU.nsk` (Новосибирск), `RU.kzn` (Казань)
  - Для каждого — `RU.{city}.json` (станции) + `broadcast_centers_RU.{city}.json`

---

## 🚀 Среднесрочные задачи (v0.9.1 – v1.0.0)

### Аналитика и база данных
- [ ] **Перейти на Postgres для анализа**
  - Таблицы: `broadcasters`, `transmitters`, `observations`, `sessions`, `sites`
  - Вьюха `transmitter_trust` (ppm_spread, rds_blocks_ok, measurement_healthy)
  - Импорт существующих JSON-баз в Postgres

- [ ] **Кластеризация PPM по площадкам**
  - `SELECT ... WHERE ppm_spread < 2 GROUP BY site_id`
  - Проверить гипотезу: «одна вышка = стабильный PPM»

- [ ] **Корреляция с погодой и космической погодой**
  - Связать `observations` с `weather_history` по времени
  - Запросы: «при Kp > 4 и инверсии сигнал на 102.4 вырос?»

### Погода и распространение
- [ ] **Добавить фазы Луны и эфемериды**
  - Поля: `moon_phase_pct`, `moon_distance_km`, `moon_azimuth_deg`
  - Для анализа EME (лунного отражения) и шумов

- [ ] **Интеграция с `gpsd` для мобильного режима**
  - Автоопределение региона по GPS
  - Обновление `observer.json` при переезде

### Отчёты и UI
- [ ] **HTML-отчёт: добавить блок «PPM-здоровье»**
  - Таблица с `ppm_spread`, `rds_blocks_ok`, `site`
  - График `ppm(t)` внутри сессии (диагностика прогрева)

- [ ] **Экспорт в CSV для внешнего анализа**
  - `stations.csv`, `observations.csv`, `weather.csv`

- [ ] **Веб-интерфейс (опционально)**
  - Flask/FastAPI + минимальный dashboard
  - Просмотр базы, фильтры по региону/станции/дате

---

## 🧹 Технический долг

- [ ] **Убрать `cities1000.txt` из репозитория**
  - Добавить в `.gitignore` (сделано)
  - В README — инструкция по скачиванию

- [ ] **Разобраться с `--mode manual`**
  - Сейчас он просто запускает fullscan на диапазоне
  - Сделать настоящий режим «только указанные частоты»

- [ ] **Починить `ppm_stable` в `observations`**
  - Считать во вьюхе (`stddev_samp`), а не писать в колонку
  - Убрать из схемы `ppm_stable` (производная величина)

- [ ] **Добавить тесты**
  - `pytest` для `geo_resolver`, `metar_registry`, `station_metadata`
  - Моки для `rtl_power`, `rtl_fm`, `redsea`

- [ ] **Обновить README**
  - Добавить раздел «Broadcast centers (beta)»
  - Описать `.gitignore` (что не коммитится и почему)

---

## 📌 Отложено / На будущее

- [ ] **Поддержка SDRplay RSP1** (через `SoapySDR` или `sdrplay_api`)
- [ ] **Парсер реестра Роскомнадзора** (если появится API)
- [ ] **Интеграция с FMLIST** (ручная или через FMSCAN)
- [ ] **Мобильное приложение** (Android/iOS) для полевых выездов

---

## ✅ Сделано (для истории)

- [x] PPM-мусор (>150) отбрасывается
- [x] METAR + космическая погода пишутся в `weather_history.jsonl`
- [x] Справочник вышек (`broadcast_centers_*.json`) с `source`/`confidence`
- [x] Формат `assignments` — список (а не словарь)
- [x] Омск (`RU.omsk`) — 26 станций
- [x] Финляндия (`FI.hel`) — бета
- [x] `geo_resolver` (GeoNames + Nominatim)
- [x] `metar_registry` (кэш аэропортов)
- [x] `station_metadata` (enrich_station)
- [x] `.gitignore` настроен (data/update, weather_history, cities1000)
