#!/usr/bin/env python3
from _version import __version__

"""
SDR-RTL Scanner v{__version__} — RTL-SDR (rtl_power + rtl_fm + redsea)

Universal SDR-scanner with RDS decoding and multi-region support.

Modes:
  fullscan  — scan range with rtl_power, decode RDS with rtl_fm|redsea
  update    — re-decode RDS for stations from existing base
  edit      — manual editing of name_ru in JSON
  merge     — merge Update file into Base, show differences

Files:
  Regions:  regions/{region}.json (station reference, per region)
  Base:     data/SDR_Base_{region}.json
  Update:   data/SDR_Update_{region}_YYYY.MM.DD_hh.mm.json
  Reports:  reports/Last_Report_SDR_RTL_RU.spb.html
  Reports:  reports/Report_SDR_RTL_RU.spb.html

Author: Andrey E. Smirnov
Email: aes222ripn@gmail.com
License: MIT
"""
import os, sys, json, argparse, subprocess, select, time, config, shutil, html
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from core.context import Context, _format_metar_human
from config import *
from core.snr import auto_ppm_calibrate, check_rds_snr, decode_rds, decode_rds_multi
from core.scanner import multi_gain_scan, bloom_split_scan, split_scan_ranges, snap_freq, format_freq_mhz
from core.region_detect import resolve_region, save_region_to_observer
from config.styles import C_GREEN, C_RED, C_BLUE, C_YELLOW, C_RESET
from config.system import DEFAULT_DWELL, DEFAULT_RDS_RATE, DEFAULT_PPM, PPM_REFERENCES

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════
# Здесь определяются базовые пути к директориям проекта и загружаются
# конфигурационные файлы (observer, antennas).
# ВАЖНО: пути строятся относительно расположения самого скрипта,
# чтобы не зависеть от текущей рабочей директории (cwd).
# ===============================================================
# -----------------------------------------------------------------------------
# 1. ОПРЕДЕЛЕНИЕ ПУТЕЙ (сначала говорим, где лежат папки)
# -----------------------------------------------------------------------------
SCRIPT_DIR      = Path(__file__).parent
REGIONS_DIR     = SCRIPT_DIR / "regions"
DATA_DIR        = SCRIPT_DIR / "data"
REPORTS_DIR     = SCRIPT_DIR / "reports"

# Определяем базовый путь к проекту (чтобы не зависеть от текущей рабочей директории)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
OBSERVER_PATH = os.path.join(CONFIG_DIR, "observer.json")
ANTENNAS_PATH = os.path.join(CONFIG_DIR, "antennas.json")

# === ПРОВЕРКА И СОЗДАНИЕ ДИРЕКТОРИЙ (CRITICAL FOR ASTRA LINUX / PROD) ===
# Гарантируем, что папки для данных и отчётов существуют.
# Если нет прав на создание — скрипт упадёт сразу с понятной ошибкой, а не позже.
for dir_path in [DATA_DIR, REPORTS_DIR]:
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"[!] CRITICAL: No write permission for directory: {dir_path}")
        sys.exit(1)
    except OSError as e:
        print(f"[!] CRITICAL: Failed to create directory {dir_path}: {e}")
        sys.exit(1)

# -----------------------------------------------------------------------------
# 3. ЗАГРУЗКА КОНФИГУРАЦИЙ (теперь безопасно писать JSON-файлы в data/)
# Используем универсальную функцию для чистоты кода (см. ниже)
# -----------------------------------------------------------------------------

def load_json_config(path: Path, default: dict = None) -> dict:
    """
    Безопасно загружает JSON-конфиг.
    Если файла нет — возвращает default (по умолчанию пустой dict).
    Если JSON битый — выводит ошибку и возвращает default.
    """
    if default is None:
        default = {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            print(f"[*] Loaded config: {path}")
            return data
    except FileNotFoundError:
        # Это не ошибка, а ожидаемый сценарий (первый запуск, CI и т.д.)
        print(f"[!] Warning: Config not found at {path}. Using defaults.")
        return default
    except json.JSONDecodeError as e:
        # А вот битый JSON — это проблема. Лучше упасть или хотя бы громко предупредить.
        print(f"[!] ERROR: Invalid JSON in {path}: {e}")
        return default

OBSERVER_CONFIG = load_json_config(Path(OBSERVER_PATH))
ANTENNAS_CONFIG = load_json_config(Path(ANTENNAS_PATH))

# -----------------------------------------------------------------------------
# 4. ИНИЦИАЛИЗАЦИЯ КОНТЕКСТА
# -----------------------------------------------------------------------------
# Выбираем антенну по умолчанию (первый ключ из справочника)
# Если ANTENNAS_CONFIG пуст — будет пустой словарь, Context обработает это корректно.
_default_antenna = (
    ANTENNAS_CONFIG[list(ANTENNAS_CONFIG.keys())[0]]
    if ANTENNAS_CONFIG else {}
)
app_context = Context(OBSERVER_CONFIG, _default_antenna)
print(
    f"[*] Context ready. Observer: {app_context.get_observer_name()}, "
    f"Antenna: {app_context.get_antenna_model()}"
)

# Глобальные переменные для хранения состояния по региону.
# Инициализируются в main() после выбора региона.
CURRENT_REGION  = "default"
CURRENT_COUNTRY = ""
CURRENT_SUBREGION = None
BASE_FILE       = ""
STATIONS_FILE   = ""
BANDS_REF       = None


def get_git_hash():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=os.path.dirname(__file__), stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"
# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С РЕГИОНАМИ
# =============================================================================


def parse_region_name(region_str: str) -> dict:
    """Разобрать имя региона в формате CC.city или CC.city.SUB.

    Возвращает:
      country   — ISO-код страны, uppercase (всегда)
      city      — название города, lowercase (всегда)
      subregion — код субрегиона, uppercase (или None)

    Примеры:
      "RU.spb"     → {"country": "RU", "city": "spb", "subregion": None}
      "US.spb.FL"  → {"country": "US", "city": "spb", "subregion": "FL"}
      "JP.tokyo"   → {"country": "JP", "city": "tokyo", "subregion": None}

    Зачем нужно:
      - Унифицирует ввод пользователя: независимо от регистра и формата,
        на выходе всегда нормализованные значения.
      - Позволяет легко формировать имена файлов вида SDR_Base_RU.spb.json.
      - Валидирует формат: если частей слишком мало/много — выбрасывает ValueError.
    """

    if region_str == "default":
        return {"country": "", "city": "default", "subregion": None}

    parts = region_str.split(".")

    if len(parts) < 2:
        raise ValueError(
            f"Имя региона '{region_str}' должно быть в формате CC.city "
            f"или CC.city.SUB (например: RU.spb, US.spb.FL)"
        )

    if len(parts) > 3:
        raise ValueError(
            f"Имя региона '{region_str}' содержит слишком много частей. "
            f"Ожидается 2 или 3, получено {len(parts)}"
        )

    result = {
        "country": parts[0].upper(),
        "city": parts[1].lower(),
        "subregion": parts[2].upper() if len(parts) == 3 else None,
    }

    return result

def get_base_file(region=None):
    """
    Возвращает путь к файлу базы станций для указанного региона.
    Формат: data/SDR_Base_{region}.json
    Если region не передан, используется CURRENT_REGION.
    """
    r = region or CURRENT_REGION
    return str(DATA_DIR / f"SDR_Base_{r}.json")

def get_update_file(region=None):
    """
    Возвращает путь к файлу обновлений для указанного региона.
    Формат: data/SDR_Update_{region}_{timestamp}.json
    Timestamp генерируется как YYYY.MM.DD_HH.MM.
    Если region не передан, используется CURRENT_REGION.
    """
    r = region or CURRENT_REGION
    ts = datetime.now().strftime("%Y.%m.%d_%H.%M")
    return str(DATA_DIR / f"SDR_Update_{r}_{ts}.json")

def get_stations_file(region=None):
    """
    Возвращает путь к справочному JSON-файлу региона (диапазоны, пороги и т.д.).
    Формат: regions/{region}.json
    Если region не передан, используется CURRENT_REGION.
    """
    r = region or CURRENT_REGION
    return str(REGIONS_DIR / f"{r}.json")

# ═══════════════════════════════════════════════════════════════
#  FQ REFERENCE per region)
# ═══════════════════════════════════════════════════════════════

def load_bands_reference(path="regions/radio_bands_reference.json"):
    global BANDS_REF
    if not os.path.exists(path):
        print(f"[!] Warning: bands reference not found at {path}")
        return
    with open(path, "r", encoding="utf-8") as f:
        BANDS_REF = json.load(f)["bands"]
    print(f"[*] Loaded {len(BANDS_REF)} band definitions from {path}")


def lookup_band(freq_khz: float, bands: list, country: str = "", subregion: str | None = None) -> dict | None:
    """Найти диапазон по частоте, стране и (опционально) субрегиону.
    ================================================================
    Логика приоритета:
    1. Точное совпадение: country + subregion (если subregion задан в обоих местах)
    2. Страна без субрегиона (глобальный для страны)
    3. Глобальный диапазон (без country)
    4. Fallback на ближайший нижний диапазон
    """
    if not bands:
        return None

    sorted_bands = sorted(bands, key=lambda b: b["freq_min"])

    def match_country(b):
        b_country = b.get("country") or b.get("region")
        if b_country is None:
            return True
        return b_country == country

    def match_subregion(b):
        b_sub = b.get("subregion")
        # Если ни в справочнике, ни в запросе нет subregion — OK
        if b_sub is None and subregion is None:
            return True
        # В справочнике нет, в запросе есть — всё равно OK (глобальный диапазон покрывает)
        if b_sub is None and subregion is not None:
            return True
        # В справочнике есть, в запросе нет — не подходит (это специфичный диапазон)
        if b_sub is not None and subregion is None:
            return False
        # Оба есть — должно совпадать
        return b_sub == subregion

    # 1. Ищем точное совпадение по country и subregion
    for b in sorted_bands:
        if (b["freq_min"] <= freq_khz <= b["freq_max"]
                and match_country(b)
                and match_subregion(b)
                and b.get("country") is not None):
            return b

    # 2. Ищем по стране (без учёта subregion)
    for b in sorted_bands:
        if (b["freq_min"] <= freq_khz <= b["freq_max"]
                and match_country(b)
                # subregion игнорируем на этом шаге
                and b.get("country") is not None):
            return b

    # 3. Ищем глобальный (без country)
    for b in sorted_bands:
        if (b["freq_min"] <= freq_khz <= b["freq_max"]
                and b.get("country") is None):
            return b

    # 4. Fallback: ближайший нижний диапазон (по max)
    fallback = None
    for b in sorted_bands:
        if b["freq_max"] < freq_khz:
            fallback = b
        else:
            break

    if fallback:
        return {
            "name": "Unknown",
            "modulation": fallback.get("modulation", "NFM"),
            "bandwidth": fallback.get("bandwidth", 12000),
            "step": fallback.get("step", 25000),
            "description": f"Fallback from {fallback['name']} ({fallback['freq_min']}-{fallback['freq_max']} kHz)",
            "fallback": True,
            "source_band": fallback["name"],
        }

    return None


# ═══════════════════════════════════════════════════════════════
#  STATION REFERENCE (external JSON, per region)
# ═══════════════════════════════════════════════════════════════

_station_map = {}

def ensure_dirs():
    REGIONS_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    default_path = REGIONS_DIR / "default.json"
    if not default_path.exists():
        with open(default_path, "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1, "region": "default",
                       "city": "Unknown", "country": "Unknown",
                       "description": "Empty template", "stations": {}}, f,
                      ensure_ascii=False, indent=2)

def interactive_region_select():
    ensure_dirs()
    files = sorted(REGIONS_DIR.glob("*.json"))
    names = [f.stem for f in files]
    print("\n[*] Доступные регионы:")
    for i, n in enumerate(names, 1):
        print(f"  {i}. {n}")
    print(f"  {len(names)+1}. Создать новый регион")
    while True:
        choice = input("\nВыберите номер или имя: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(names):
                return names[idx]
            if idx == len(names):
                new = input("Имя нового региона: ").strip().lower()
                if new:
                    return new
        elif choice:
            return choice.lower()

def ensure_region_file(region):
    path = REGIONS_DIR / f"{region}.json"
    if path.exists():
        return
    default = REGIONS_DIR / "default.json"
    if default.exists():
        with open(default, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"schema_version": 1, "region": "default",
                "city": "Unknown", "country": "Unknown", "stations": {}}
    data["region"] = region
    data["city"] = region.upper()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[*] Создан файл региона: {path}")

def ensure_base_file(region):
    base = get_base_file(region)
    if os.path.exists(base):
        return
    data = {"schema_version": 1, "region": region,
            "scan_date": datetime.now().isoformat(), "mode": "init",
            "stations": []}
    with open(base, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[*] Создана базу: {base}")

def load_stations(filepath):
    global _station_map
    if not filepath or not os.path.exists(filepath):
        _station_map = {}
        return
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("stations", data)
        _station_map = {float(k): v for k, v in raw.items()}
    except Exception as e:
        print(f"[!] Error loading {filepath}: {e}")
        _station_map = {}

def lookup_name_ru(freq):
    for f, name in _station_map.items():
        if abs(freq - f) < 0.2:
            return name
    return None

# ═══════════════════════════════════════════════════════════════
#  UTILITIES
# ═══════════════════════════════════════════════════════════════

def is_in_fq_range(freq):
    return FQ_MIN <= freq <= FQ_MAX

def make_update_filename():
    return get_update_file()

# ═══════════════════════════════════════════════════════════════
#  JSON I/O
# ═══════════════════════════════════════════════════════════════

def json_safe(obj):
    """Convert numpy types to native Python types for JSON serialization."""
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if hasattr(obj, 'item') and not isinstance(obj, (str, bytes, dict, list, tuple)):
        return obj.item()
    return obj

def save_results(stations, args, mode, ctx, filename=None, scan_min=None, scan_max=None):
    """
    Сохраняет результаты сканирования в JSON-файл.

    Что делает:
      - Формирует имя файла (update или BASE_FILE).
      - Фильтрует станции по диапазону FQ_MIN–FQ_MAX при сохранении в BASE_FILE.
      - Считает количество станций вне диапазона (для метрики качества сканирования).
      - Добавляет в JSON:
          * параметры сканирования (start, stop, step и т.д.)
          * диапазон и полосу (FM/OIRT)
          * станции (отфильтрованные)
          * погоду (METAR) из app_context.weather
          * космическую погоду из app_context.space_weather
          * контекст наблюдения (observer, antenna, артефакты, помехи)
      - Выводит в консоль путь к файлу и количество станций вне диапазона.

    Аргументы:
      stations: список станций (dict) — сырые данные до фильтрации.
      args: аргументы командной строки (Namespace).
      ctx: dict с контекстом наблюдения (observer, antenna, metar, space_weather и т.п.)
      mode: режим сканирования ("fullscan", "update", и т.п.).
      filename: имя файла (опционально). Если None — выбирается автоматически.
      scan_min, scan_max: границы диапазона сканирования (МГц).

    Возвращает:
      Путь к сохранённому файлу (str).
    """
#    global app_context

    # ── Выбор имени файла ──
    if filename is None:
        if mode in ("fullscan", "update"):
            filename = make_update_filename()
        else:
            filename = args.output or BASE_FILE

    # ── Фильтрация по диапазону только для BASE_FILE ──
    # Для update-файлов сохраняем всё, чтобы не терять данные вне диапазона.
    if filename == BASE_FILE:
        filtered = [s for s in stations if is_in_fq_range(s.get("freq",0))]
        outside_count = len(stations) - len(filtered)
        if outside_count > 0:
            print(f"[!] {outside_count} station(s) outside {FQ_MIN}-{FQ_MAX} MHz not saved to {BASE_FILE}.")
    else:
        # Для update-файла сохраняем полный список — это нужно для diff/merge
        filtered = stations
        outside_count = 0

    # ── Определение полосы (FM / OIRT) ──
#    scan_band = "OIRT" if (scan_min and scan_min >= 65.0 and scan_max and scan_max <= 74.5) else "FM"

#    scan_band = ctx.get("band", "FM")  # Если вдруг забыли положить — дефолт FM

    # ── Времена (понятные имена) ──
#    scan_start_utc = ctx.get("scan_date")
#    scan_end_utc = datetime.now(timezone.utc).isoformat()

    # ── Сбор данных для JSON ──
    data = {
        "scan_date_start_utc": ctx.get("scan_date"),
        "scan_date_end_utc": datetime.now(timezone.utc).isoformat(),
        "timezone": "UTC",
        "mode": mode,
#        "band": scan_band,
        "scan_range": {"min": scan_min, "max": scan_max},
        "params": {
            "start_mhz": args.start,
            "stop_mhz": args.stop,
            "step": args.step,
            "threshold": args.threshold,
            "ppm": args.ppm,
            "gains": args.gains,
            "rds_gains": args.rds_gains,
            "rds_rate": DEFAULT_RDS_RATE,
            "dwell": args.dwell,
            "snap_khz": args.snap,
        },
        "stations": filtered,
        # Метрика: сколько станций было найдено вне рабочего диапазона
        "outside_range_count": outside_count,
    }

    # Теперь берём из ctx, а не из app_context
    if ctx is not None:
        # Погода
#        data["weather"] = ctx.get("metar")

        # Космическая погода
#        data["space_weather"] = ctx.get("space_weather")

        # observation_context — можно либо брать целиком из ctx, либо делать отдельный блок
        # Если хочешь сохранить формат observation_context как раньше, можно сделать:
        data["observation_context"] = {
            "observer": ctx.get("observer"),
            "antenna": ctx.get("antenna"),
            "weather": ctx.get("metar"),
            "space_weather": ctx.get("space_weather"),
            "gps_quality": ctx.get("gps_quality"),
            "artifact": ctx.get("artifact"),
            "interference": ctx.get("interference"),
        }

        # ── Приватность: координаты НЕ пишем в JSON (только локально в observer.json) ──
        gq = data["observation_context"].get("gps_quality")
        if gq and isinstance(gq, dict) and gq.get("position"):
            gq["position"] = {"lat": None, "lon": None, "alt_m": None}

    # ── Чистим служебные поля (только для записи, не мутируем исходный список) ──
    # Копируем, чтобы не портить данные, которые ещё пригодятся в print_summary/export_html.
    def _clean(st):
        return {k: v for k, v in st.items() if not k.startswith("_")}

    data["stations"] = [_clean(st) for st in data.get("stations", [])]

    # ── Запись JSON ──
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(json_safe(data), f, ensure_ascii=False, indent=2)

    print(f"\n[*] Saved: {filename} | Вне диапазона: {outside_count}")
    return filename

def load_json(fname):
    if not os.path.exists(fname): return None
    with open(fname, "r", encoding="utf-8") as f: return json.load(f)

# ═══════════════════════════════════════════════════════════════
#  MERGE / DIFF
# ═══════════════════════════════════════════════════════════════

def interactive_select(prompt, items, default_all=False):
    if not items: return set()
    resp = input(prompt).strip().lower()
    if resp in ("a", "all", "y"): return set(range(len(items)))
    if resp in ("n", "x"): return set()
    if resp == "": return set(range(len(items))) if default_all else set()
    selected = set()
    for part in resp.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(items): selected.add(idx)
    return selected

# ==========================================================================
def show_diff(base_data, update_data, scan_min, scan_max, tolerance_mhz=0.1):
    # Сортируем обе базы по частоте
    base_list = sorted(base_data.get("stations", []), key=lambda s: s["freq"])
    upd_list  = sorted(update_data.get("stations", []), key=lambda s: s["freq"])

    # Сопоставление с допуском ±tolerance_mhz
    matched_base = [False] * len(base_list)
    matched_upd  = [False] * len(upd_list)
    pairs = []  # (upd_st, base_st) — для changed

    for i, u in enumerate(upd_list):
        # Ищем ближайшую base-станцию в пределах tolerance
        best_j = None
        best_d = None
        for j, b in enumerate(base_list):
            if matched_base[j]:
                continue
            d = abs(u["freq"] - b["freq"])
            if d <= tolerance_mhz and (best_d is None or d < best_d):
                best_d = d
                best_j = j
        if best_j is not None:
            matched_upd[i] = True
            matched_base[best_j] = True
            pairs.append((u, base_list[best_j]))

    new_f = [u["freq"] for i, u in enumerate(upd_list) if not matched_upd[i]]
    gone_all = [b["freq"] for j, b in enumerate(base_list) if not matched_base[j]]
    common = [(u, b) for u, b in pairs]

    new_sts = {u["freq"]: u for i, u in enumerate(upd_list) if not matched_upd[i]}

    base_sts_all = {round(b["freq"], 2): b for b in base_list}
    upd_sts_all  = {round(u["freq"], 2): u for u in upd_list}

    gone_in_range = [f for f in gone_all if scan_min and scan_max and scan_min <= f <= scan_max]
    gone_outside = [f for f in gone_all if not (scan_min and scan_max and scan_min <= f <= scan_max)]

    changed = []
    for u, b in common:
        diffs = []
        for key in ["signal", "name_ru"]:
            if b.get(key) != u.get(key):
                diffs.append((key, b.get(key), u.get(key)))
        br, ur = b.get("rds", {}), u.get("rds", {})
        for key in ["PI", "PS", "PTY", "RadioText", "Stereo", "TP", "TA"]:
            if br.get(key) != ur.get(key):
                diffs.append((f"rds.{key}", br.get(key), ur.get(key)))
        if diffs:
            changed.append((u["freq"], diffs, u, b))

    print("\n" + "=" * 70)
    print("СРАВНЕНИЕ: Base vs Update")
    print("=" * 70)

    if new_f:
        print(f"\n  Новые станции ({len(new_f)}):")
        for i, f in enumerate(new_f):
            s = upd_sts_all.get(round(f, 2), {})
            ps = s.get("rds", {}).get("PS", "?")
            print(f"    {i+1}. + {f:.3f} MHz  ({s.get('signal', 0):.1f} dB)  PS={ps}")

    if gone_in_range:
        print(f"\n  Потерянные в диапазоне сканирования ({len(gone_in_range)}):")
        for i, f in enumerate(gone_in_range):
            s = base_sts_all.get(round(f, 2), {})
            ps = s.get("rds", {}).get("PS", "?")
            print(f"    {i+1}. - {f:.3f} MHz  PS={ps}")

    if gone_outside:
        print(f"\n  Вне диапазона сканирования (не трогаются, {len(gone_outside)})")

    if changed:
        print(f"\n  Изменены ({len(changed)}):")
        for i, (f, diffs, u, b) in enumerate(changed):
            # Показываем и base, и update частоты, если они различаются
            if abs(u["freq"] - b["freq"]) > 0.001:
                print(f"    {i+1}. ~ {b['freq']:.3f} → {u['freq']:.3f} MHz:")
            else:
                print(f"    {i+1}. ~ {f:.3f} MHz:")
            for key, old, new in diffs:
                print(f"        {key}: {old} -> {new}")

    if not new_f and not gone_in_range and not changed:
        print("\n  Нет различий.")

    print("=" * 70)

    # Возвращаем структуру, совместимую с interactive_merge
    # changed: список (f, diffs, u, b) — 4 элемента
    changed_freqs = [f for f, _, _, _ in changed]
    changed_sts = {f: u for f, _, u, _ in changed}
    # gone_sts — только потерянные в диапазоне (не все base)
    gone_sts = {b["freq"]: b for b in base_list if b["freq"] in gone_in_range}
    return {
        "new": new_f, "new_sts": new_sts,
        "gone": gone_in_range, "gone_sts": gone_sts,
        "changed": changed_freqs, "changed_sts": changed_sts,
    }

# ==========================================================================

def interactive_merge(base_data, diff_info, scan_min, scan_max):
    base_sts = {round(s["freq"], 1): s for s in base_data.get("stations", [])}
    to_add, to_remove, to_update = [], [], []
    if diff_info["new"]:
        print("\n--- Добавить новые станции ---")
        sel = interactive_select("Добавить: все (a), номера (1,2), нет (n) [по умолчанию a]: ",
                                 diff_info["new"], default_all=True)
        for idx in sel: to_add.append(diff_info["new"][idx])
    if diff_info["gone"]:
        print("\n--- Удалить потерянные станции ---")
        sel = interactive_select("Удалить: все (a), номера (1,2), нет (n) [по умолчанию n]: ",
                                 diff_info["gone"], default_all=False)
        for idx in sel: to_remove.append(diff_info["gone"][idx])
    if diff_info["changed"]:
        print("\n--- Обновить изменённые станции ---")
        sel = interactive_select("Обновить: все (a), номера (1,2), нет (n) [по умолчанию a]: ",
                                diff_info["changed"], default_all=True)
        for idx in sel: to_update.append(diff_info["changed"][idx])
    print(f"\nИтого: +{len(to_add)} добавить, -{len(to_remove)} удалить, ~{len(to_update)} обновить")

#    resp = input("Подтвердить? (y/N): ").strip().lower()
    try:
        resp = input("Confirm? (y/N): ").strip().lower()
    except UnicodeDecodeError:
        # Если ввод битый — считаем отказом
        resp = "n"

    if resp == "y" or resp == "yes":
        pass  # логика подтверждения уже ниже
    else:
        print("[*] Слияние отменено.")
        return None

    for f in to_remove:
        base_sts.pop(f, None)

    for f in to_add:
        s = diff_info["new_sts"][f]
        if f in base_sts and not s.get("name_ru") and base_sts[f].get("name_ru"):
            s["name_ru"] = base_sts[f]["name_ru"]
        base_sts[f] = s

    for f in to_update:
        s = diff_info["changed_sts"][f]
        old = base_sts.get(f, {})
        if not s.get("name_ru") and old.get("name_ru"):
            s["name_ru"] = old["name_ru"]
        old_rds = old.get("rds", {})
        new_rds = s.get("rds", {})
        if not new_rds and old_rds:
            s["rds"] = old_rds
            print(f"    [preserved] {f:.1f} MHz: RDS сохранён (PI={old_rds.get('PI','?')})")
        elif new_rds and old_rds:
            for key in ["PI", "PS", "PTY", "RadioText", "Stereo", "TP", "TA", "AF"]:
                if not new_rds.get(key) and old_rds.get(key):
                    new_rds[key] = old_rds[key]
                    print(f"    [preserved] {f:.1f} MHz: rds.{key} сохранён")
        base_sts[f] = s
    base_data["stations"] = sorted(base_sts.values(), key=lambda x: x["freq"])
    base_data["last_merge"] = datetime.now().isoformat()
    return base_data

# ═══════════════════════════════════════════════════════════════
#  PRETTY PRINT Summary
# ═══════════════════════════════════════════════════════════════

def print_summary(stations, ppm=0, snap=0):
    print("\n" + "=" * 155)
    # Добавлены колонки Mod и Band
    hdr = (f"{'Freq':>7} | {'Signal':>7} | {'Name RU':<22} | {'Mod':>5} | {'Band':<12} | "
           f"{'PI':>8} | {'PS':<24} | {'PTY':<16} | {'Ster':>5} | {'TP':>4} | {'TA':>4} | RadioText")
    print(hdr)
    print("-" * 155)
    n_pi = n_ps = n_rt = n_ru = 0
    for st in stations:
        freq = st.get("freq", 0)
        signal = st.get("signal", 0)
        name_ru = st.get("name_ru", "") or "—"
        mod = st.get("modulation", "") or "—"
        band = st.get("band_name", "") or "—"
        rds = st.get("rds", {})
        pi = rds.get("PI", "—")
        ps = rds.get("PS_full") or rds.get("PS", "—") or "—"
        pty = rds.get("PTY", "—") or "—"
        tp = rds.get("TP", "—")
        ta = rds.get("TA", "—")
        rt = rds.get("RadioText", "—") or "—"
        stereo = st.get("stereo")
        if stereo is None:
            stereo = rds.get("Stereo")
        if stereo is True:
            ster_str = f"{C_GREEN}●{C_RESET}"
        elif stereo is False:
            ster_str = f"{C_RED}●{C_RESET}"
        else:
            ster_str = "?"
        pi_str = f"{C_BLUE}{pi}{C_RESET}" if pi != "—" else "—"
        rt_str = f"{C_BLUE}{rt}{C_RESET}" if rt != "—" else "—"
        if pi != "—": n_pi += 1
        if ps != "—": n_ps += 1
        if rt != "—": n_rt += 1
        if name_ru != "—": n_ru += 1
        # Выводим Mod и Band
        # Формат частоты — по шагу (та же логика, что в mode_fullscan)
        band_info = st.get("_band") or {}
        step_hz_sum = band_info.get("step") or (st.get("_step_khz", 100) * 1000)
        freq_sum = format_freq_mhz(freq, step_hz_sum)
        print(f"{freq:>7.3f}M | {signal:>7.1f} | {name_ru:<22} | {mod:>5} | {band:<12} | "
              f"{pi_str:>8} | {ps:<24} | {pty:<16} | {ster_str:>5} | {str(tp):>4} | {str(ta):>4} | {rt_str}")
    print("=" * 155)
    print(f"\nВсего станций: {len(stations)} | PPM: {ppm} | Snap: {snap} kHz")
    print(f"С RDS (PI): {n_pi} | С PS: {n_ps} | С RT: {n_rt} | С русским названием: {n_ru}")

# ═══════════════════════════════════════════════════════════════
#  Export to html
# ═══════════════════════════════════════════════════════════════

def export_html(data: dict, region: str | None, output_path: str | Path | None = None) -> str | None:
    """
    Экспортирует отчёт в HTML.
    region: всегда непустая строка (нормализуется в main()).
    """
    if output_path is None:
        print("[!] export_html: output_path не передан, HTML не сохранён.")
        return None

    # Нормализуем в Path, даже если передали строку
#    p = Path(output_path)

    # Создаём родительскую директорию, если её нет
#    p.parent.mkdir(parents=True, exist_ok=True)

#    with p.open("w", encoding="utf-8") as f:
#        f.write(html)

#    print(f"[*] HTML report: {p}")
#    return str(p)
# =================================
    # Защита от None
    region_display = region.upper() if region else "UNKNOWN"
    stations = data.get("stations", [])
    scan_date = data.get("scan_date", "")
    ppm = data.get("ppm", "?")
    mode = data.get("mode", "?")

    def format_metar(m) -> str:
        if not m:
            return "<p class='warn'>METAR: данные недоступны</p>"
        # Конвертируем видимость в км для отображения (если в метрах)
        vis = m.get("visibility_m")
        vis_display = "?"
        if vis is not None:
            if vis >= 9999:
                vis_display = ">10 км"
            elif vis >= 1000:
                vis_display = f"{vis / 1000:.1f} км"
            else:
                vis_display = f"{vis} м"
        return f"""
        <h3>Погода (METAR): {m.get('station', '?')}</h3>
        <ul>
          <li>Температура: {m.get('temperature_c', '?')}°C</li>
          <li>Точка росы: {m.get('dewpoint_c', '?')}°C</li>
          <li>Ветер: {m.get('wind_dir', '?')}° ({m.get('wind_speed_kt', '?')} уз)</li>
          <li>Видимость: {vis_display}</li>
          <li>Давление: {m.get('qnh_hpa', '?')} гПа</li>
        </ul>
        """

    def format_space_weather(s) -> str:
        if not s:
            return "<p class='warn'>Космическая погода: данные недоступны</p>"
        return f"""
        <h3>Космическая погода (SWPC)</h3>
        <ul>
          <li>Kp индекс: {s.get('kp_index', '?')} ({s.get('storm_scale', '?')})</li>
          <li>F10.7: {s.get('f107', '?')} sfu</li>
          <li>Bz: {s.get('bz', '?')} nT</li>
          <li>Скорость солнечного ветра: {s.get('solar_wind_speed', '?')} км/с</li>
          <li>Состояние: {s.get('condition', '?')}</li>
        </ul>
        """
    def format_gps_quality(g) -> str:
        """Блок «Условия приёма» для HTML.

        Принцип: только факты, без интерпретаций.
        - «норма» / «слабый сигнал» / «нет сигнала» / «GPS недоступен»
        """
        if not g or not g.get("available"):
            return (
                "<div style='border-left:4px solid #888; padding:8px 12px; "
                "margin:10px 0; background:#16213e'>"
                "<b>Условия приёма:</b> "
                "<span style='color:#888'>⚪ GPS недоступен</span>"
                "</div>"
            )

        m = g.get("metrics", {}) or {}
        gq = g.get("gq_status", "")

        # Только факты — без «подавлены», «помехи», «группировки».
        status_label = {
            "CLEAR":            "✅ норма",
            "INDOOR":           "⚠️ слабый сигнал",
            "ANOMALY":          "🚨 нет сигнала (все видимые спутники без сигнала)",
            "NO_GPS_HARDWARE":  "⚪ GPS недоступен",
        }.get(gq, "?")

        color = {
            "CLEAR": "#6f6",
            "INDOOR": "#ff6",
            "ANOMALY": "#f66",
            "NO_GPS_HARDWARE": "#888",
        }.get(gq, "#888")

        # Детали — минимум общих меток, без разбивки по группировкам.
        detail_rows = []
        mode_text = m.get("mode_text", "?")
        detail_rows.append(f"<li>Fix: {html.escape(str(m.get('mode_text', '?')))}</li>")
        detail_rows.append(
            f"<li>Спутников: {m.get('seen', '?')} "
            f"(используется: {m.get('used', '?')})</li>"
        )
        detail_rows.append(
            f"<li>Средний SNR: {m.get('avg_snr', 0):.1f} dBHz</li>"
        )
        if m.get("jam") is not None:
            detail_rows.append(
                f"<li>Индикатор помех: {m['jam']}/255</li>"
            )

        return f"""
        <div style="border-left:4px solid {color}; padding:8px 12px; margin:10px 0; background:#16213e">
          <b>Условия приёма:</b> <span style="color:{color}">{html.escape(status_label)}</span>
          <details style="margin-top:6px">
            <summary style="cursor:pointer;color:#888">Детали</summary>
            <ul style="margin:4px 0 0 16px;color:#aaa">
              {''.join(detail_rows)}
            </ul>
          </details>
        </div>
        """

    n_total = len(stations)
    n_rds = sum(1 for s in stations if s.get("rds", {}).get("PI"))
    n_ps = sum(1 for s in stations if s.get("rds", {}).get("PS"))
    n_rt = sum(1 for s in stations if s.get("rds", {}).get("RadioText"))
    n_named = sum(1 for s in stations if s.get("name_ru"))
    n_lost = sum(1 for s in stations if s.get("status") == "lost")
    n_new = sum(1 for s in stations if s.get("status") == "new")

    stations.sort(key=lambda s: s.get("freq", 0))

    rows = ""
    for st in stations:
        freq = st.get("freq", 0)
        signal = st.get("signal", 0)
        name_ru = st.get("name_ru", "") or "—"
        mod = st.get("modulation", "") or "—"
        band = st.get("band_name", "") or "—"
        rds = st.get("rds", {}) or {}
        pi = rds.get("PI", "—")
        ps = rds.get("PS", "—")
        pty = rds.get("PTY", "—")
        tp = rds.get("TP", "—")
        ta = rds.get("TA", "—")
        rt = rds.get("RadioText", "—")
        stereo = st.get("stereo")
        if stereo is True:
            ster_str = "●"
        elif stereo is False:
            ster_str = "○"
        else:
            ster_str = "?"
        status = st.get("status", "active")
        last_seen = st.get("last_seen", "")[:10] if st.get("last_seen") else ""

        # ── Формат частоты по шагу сетки (согласовано с логом и print_summary) ──
        # Приоритет: _band["step"] → _step_khz → 100 кГц (FM-дефолт)
        band_info = st.get("_band") or {}
        step_hz_html = band_info.get("step") or (st.get("_step_khz", 100) * 1000)
        freq_str_html = format_freq_mhz(freq, step_hz_html)

        row_class = ""
        if status == "lost":
            row_class = ' class="row-lost"'
        elif status == "new":
            row_class = ' class="row-new"'

        rows += (
            f"      <tr{row_class}>\n"
            f"        <td>{freq_str_html}</td>\n"
            f"        <td>{signal:.1f}</td>\n"
            f"        <td>{name_ru}</td>\n"
            f"        <td>{mod}</td>\n"
            f"        <td>{band}</td>\n"
            f"        <td>{pi}</td>\n"
            f"        <td>{ps}</td>\n"
            f"        <td>{pty}</td>\n"
            f"        <td>{ster_str}</td>\n"
            f"        <td>{tp}</td>\n"
            f"        <td>{ta}</td>\n"
            f"        <td>{rt}</td>\n"
            f"        <td>{status}</td>\n"
            f"        <td>{last_seen}</td>\n"
            f"      </tr>\n"
        )

    js_code = """
<script>
let sortDir = {};
function sortTable(col) {
  const table = document.getElementById("stations");
  const tbody = table.querySelector("tbody");
  const rows = Array.from(tbody.querySelectorAll("tr"));
  const dir = sortDir[col] = !sortDir[col];
  rows.sort((a, b) => {
    let x = a.cells[col].textContent.trim();
    let y = b.cells[col].textContent.trim();
    let xn = parseFloat(x), yn = parseFloat(y);
    if (!isNaN(xn) && !isNaN(yn)) return dir ? xn - yn : yn - xn;
    return dir ? x.localeCompare(y) : y.localeCompare(x);
  });
  rows.forEach(r => tbody.appendChild(r));
}
</script>
"""

    html_doc = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SDR-TRL Scanner — {region.upper()}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #1a1a2e; color: #e0e0e0; padding: 20px;
  }}
  h1 {{ color: #00d4ff; margin-bottom: 5px; font-size: 1.5em; }}
  .meta {{ color: #888; font-size: 0.85em; margin-bottom: 15px; }}
  .stats {{
    display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 20px;
  }}
  .stat {{
    background: #16213e; border-radius: 8px; padding: 10px 16px;
    font-size: 0.9em; border: 1px solid #1a1a3e;
  }}
  .stat b {{ color: #00d4ff; }}
  table {{
    width: 100%; border-collapse: collapse;
    background: #16213e; border-radius: 8px; overflow: hidden;
    font-size: 0.88em;
  }}
  th {{
    background: #0f3460; color: #00d4ff; padding: 10px 8px;
    text-align: left; position: sticky; top: 0; cursor: pointer;
    white-space: nowrap; user-select: none;
  }}
  th:hover {{ background: #1a4080; }}
  td {{ padding: 8px; border-bottom: 1px solid #1a1a3e; white-space: nowrap; }}
  tr:hover {{ background: #1a1a4e; }}
  .row-lost {{ opacity: 0.4; }}
  .row-lost td {{ color: #666; }}
  .row-new {{ background: #0a3a0a; }}
  .row-new td {{ color: #6f6; }}
  .footer {{ margin-top: 15px; color: #555; font-size: 0.8em; }}
  @media (max-width: 800px) {{
    table {{ font-size: 0.75em; }}
    th, td {{ padding: 5px 4px; }}
  }}
</style>
</head>
<body>
<h1>FM RDS Scanner — {region.upper()}</h1>
<div class="meta">
  Scan: {scan_date[:19]} | Mode: {mode} | PPM: {ppm}
</div>
        {format_metar(data.get("metar"))}
        {format_space_weather(data.get("space_weather"))}
        {format_gps_quality(data.get("gps_quality"))}
<div class="stats">
  <div class="stat">Всего: <b>{n_total}</b></div>
  <div class="stat">С RDS (PI): <b>{n_rds}</b></div>
  <div class="stat">С PS: <b>{n_ps}</b></div>
  <div class="stat">С RT: <b>{n_rt}</b></div>
  <div class="stat">С названием: <b>{n_named}</b></div>
  <div class="stat">Потеряны: <b>{n_lost}</b></div>
  <div class="stat">Новые: <b>{n_new}</b></div>
</div>
<table id="stations">
  <thead>
    <tr>
      <th onclick="sortTable(0)">Freq (MHz)</th>
      <th onclick="sortTable(1)">Signal (dB)</th>
      <th onclick="sortTable(2)">Name RU</th>
      <th onclick="sortTable(3)">Mod</th>
      <th onclick="sortTable(4)">Band</th>
      <th onclick="sortTable(5)">PI</th>
      <th onclick="sortTable(6)">PS</th>
      <th onclick="sortTable(7)">PTY</th>
      <th onclick="sortTable(8)">Ster</th>
      <th onclick="sortTable(9)">TP</th>
      <th onclick="sortTable(10)">TA</th>
      <th onclick="sortTable(11)">RadioText</th>
      <th onclick="sortTable(12)">Status</th>
      <th onclick="sortTable(13)">Last Seen</th>
    </tr>
  </thead>
  <tbody>
{rows}  </tbody>
</table>
<div class="footer">Generated by SDR-RTL-Scanner v{__version__} (git {get_git_hash()}) | {scan_date[:19]}</div>
{js_code}
</body>
</html>"""

    # --- 2. Теперь безопасно пишем в файл ---
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    with p.open("w", encoding="utf-8") as f:
        f.write(html_doc)

    print(f"[*] HTML report: {p}")
    return str(p)

    if output_path is None:
        # Функция не должна сама решать, куда писать. Пусть решает main().
        print("[!] export_html: output_path не передан, HTML не сохранён.")
        return None

#    os.makedirs(os.path.dirname(output_path), exist_ok=True)
#    with open(output_path, "w", encoding="utf-8") as f:
#        f.write(html_doc)
#    print(f"[*] HTML report: {output_path}")
#    return output_path

# ═══════════════════════════════════════════════════════════════
#  MODE 1: FULLSCAN
#  NOTE:
#    - Все расчёты ведутся в Гц (int).
#    - Freq в JSON/аргументах — МГц (float) для удобства ввода.
#    - Для ДВ/СВ (≤3 МГц) вывод в кГц с 3 знаками; для КВ — МГц с 3; VHF+ — МГц с 1.
#    - Дедупликация и маппинг по имени — по ключу в кГц (int), без округления.
# ═══════════════════════════════════════════════════════════════

def mode_fullscan(args, ctx: dict | None, region: str | None, output_path: str | None = None) -> None:
    if ctx is None:
        raise RuntimeError("mode_fullscan: обязательный аргумент ctx не передан")
    if region is None:
        raise RuntimeError("mode_fullscan: обязательный аргумент region не передан")

    print("=" * 60 + f"\nSDR-RTL Scanner v{__version__} (git {get_git_hash()}) — Mode 1: Full Scan\n" + "=" * 60)
    prev_names = {}
    # Жёсткий fallback: если ни справочник, ни CLI не дали порог
    FALLBACK_THRESHOLD = -40
    # ── Загрузка предыдущей базы: маппинг по частоте в кГц (без потерь) ──
    if os.path.exists(BASE_FILE):
        prev = load_json(BASE_FILE)
        if prev:
            for st in prev.get("stations", []):
                # Храним ключ в кГц как int — это сохраняет точность для ДВ/СВ
                freq_khz_key = int(st["freq"] * 1000)
                if st.get("name_ru"):
                    prev_names[round(st["freq"], 1)] = st["name_ru"]

    # ── Разбиваем на поддиапазоны по справочнику ──
    sub_ranges = split_scan_ranges(
        args.start, args.stop, BANDS_REF,
        country=CURRENT_COUNTRY, subregion=CURRENT_SUBREGION
    )

# ==================================
    print(f"[*] Scan range {args.start}–{args.stop} MHz → {len(sub_ranges)} sub-range(s):")
    for sr in sub_ranges:
        tag = "OIRT" if sr["is_oirt"] else sr["modulation"]
        # Границы поддиапазона печатаем с числом знаков, зависящим от его шага.
        # Это важно: для HF (шаг 1 кГц) 10.1–11.6 смотрится грубо, нужно 10.100–11.600.
        step_hz = sr["step_khz"] * 1000
        start_str = format_freq_mhz(sr["start_mhz"], step_hz)
        stop_str  = format_freq_mhz(sr["stop_mhz"],  step_hz)
        print(f"    {start_str}–{stop_str} MHz | "
              f"{sr['band_name']} | {tag} | step={sr['step_khz']:.0f} kHz")
# ==================================

    # ── Сканируем каждый поддиапазон ──
    all_stations = []
    for sr in sub_ranges:
        # --- ШАГ СКАНИРОВАНИЯ: явные единицы и защита от мусора ---
        # Если пользователь задал --step явно — используем его (в кГц).
        # Иначе берём из справочника (тоже в кГц).
        step_khz = args.step if args.step is not None else sr["step_khz"]

        # Валидация шага: должен быть числом > 0
        if not isinstance(step_khz, (int, float)) or step_khz <= 0:
            step_khz = DEFAULT_STEP
            print(f"[!] Step invalid or missing for band {sr['band_name']}; using default {DEFAULT_STEP} kHz")

        # ── Порог сигнала: приоритеты ──
        #   1. sr["threshold"] (уже наследуется для Gap в split_scan_ranges)
        #   2. args.threshold из CLI
        #   3. FALLBACK_THRESHOLD (жёсткий дефолт)
        band_threshold = sr.get("threshold")

        if isinstance(band_threshold, (int, float)):
            effective_threshold = float(band_threshold)
        elif isinstance(args.threshold, (int, float)):
            effective_threshold = float(args.threshold)
        else:
            effective_threshold = FALLBACK_THRESHOLD
            print(f"[!] Threshold invalid for band {sr['band_name']}; using fallback {FALLBACK_THRESHOLD} dB")

        # --- ЛОГИРОВАНИЕ: понятные единицы и точность ---
        # ── Защита форматирования: None → "auto" ──
        thr_str = f"{effective_threshold:.1f}" if effective_threshold is not None else "auto"

        # ── Границы диапазона: формат по шагу (согласовано с multi_gain_scan) ──
        # Было бы {:.3f} всегда → для FM печаталось бы 87.500–108.000,
        # а хочется 87.50–108.00. Для HF (.3f) остаётся 10.000–10.101.
        _step_hz_band = step_khz * 1000
        _start_str = format_freq_mhz(sr["start_mhz"], _step_hz_band)
        _stop_str  = format_freq_mhz(sr["stop_mhz"],  _step_hz_band)

        print(
            f"\n[*] Multi-gain scan: {_start_str}–{_stop_str} MHz "
            f"({sr['band_name']}, step={step_khz:.1f} kHz ({int(step_khz*1000)} Hz), "
            f"threshold={thr_str} dB)"
        )

        # --- ВЫЗОВ СКАНЕРА: строго под сигнатуру bloom_split_scan ---
        # ВАЖНО: bloom_split_scan ожидает шаг (step) в кГц, а не в Гц.
        # Не передавай step_hz и не используй именованный аргумент step_hz.
        # ==============================================================================
        # ЕДИНИЦЫ ИЗМЕРЕНИЯ ДЛЯ bloom_split_scan (СТРОГО!)
        # ------------------------------------------------------------------------------
        # start_mhz, stop_mhz: float, МГц (границы диапазона)
        # step:               float/int, кГц (шаг сканирования)  <-- КРИТИЧНО: НЕ Гц!
        # gains:              list или int, dB (усиление)
        # threshold_db:       float, dB (порог обнаружения)
        # ppm, snap:          int, bool (параметры SDR)
        # antennas_config:    dict (конфигурация антенн)
        #
        # ВНИМАНИЕ:
        # Если передать step в Гц (например, 1000 вместо 1), сканирование станет
        # в 1000 раз грубее — все станции на ДВ/СВ будут пропущены.
        # ==============================================================================
        found = bloom_split_scan(
            sr["start_mhz"],          # float, МГц
            sr["stop_mhz"],           # float, МГц
            step_khz,                 # число, кГц (именно это функция реально ждёт)
            args.gains,               # список/значение усиления
            effective_threshold,      # float, дБ
            args.ppm,                 # int, ppm
            args.snap,                # bool
            antennas_config=ANTENNAS_CONFIG
        )

        if found:
            all_stations.extend(found)

    # ── Дедупликация (на стыках поддиапазонов могут быть дубли) ──
    if len(sub_ranges) > 1:
        seen = set()
        unique = []
        for st in all_stations:
            key = round(st["freq"], 1)
            if key not in seen:
                seen.add(key)
                unique.append(st)
        all_stations = unique

    stations = all_stations
    if not stations:
        save_results([], args, "fullscan", ctx=ctx, scan_min=args.start, scan_max=args.stop)
        print_summary([], args.ppm, args.snap)
        return

    # ── Тип сессии (для лога) ──
    HF_THRESHOLD_MHZ = 30.0
    has_oirt = any(sr["is_oirt"] for sr in sub_ranges)
    has_fm = any(not sr["is_oirt"] and sr["start_mhz"] >= HF_THRESHOLD_MHZ for sr in sub_ranges)
    has_hf = any(sr["start_mhz"] < HF_THRESHOLD_MHZ for sr in sub_ranges)

    if has_hf and (has_fm or has_oirt):
        rds_label = "mixed: RDS (FM) + HF signal detection"
    elif has_hf:
        rds_label = "HF signal detection (no RDS in HF)"
    elif has_oirt and has_fm:
        rds_label = "mixed: RDS (FM) + stereo detection (OIRT)"
    elif has_oirt:
        rds_label = "stereo detection (OIRT — no RDS)"
    else:
        rds_label = "RDS decoding (rtl_fm | redsea)"

    if has_hf and not has_fm and not has_oirt:
        print(f"\n[*] {rds_label}\n")
    else:
        print(f"\n[*] {rds_label}\n    RDS gains: {args.rds_gains} dB | "
              f"Dwell: {args.dwell}s | Rate: {DEFAULT_RDS_RATE} Hz\n")

    # =================================
    rds_gains = args.rds_gains
    rate = DEFAULT_RDS_RATE
    # =================================

    # ── Предрасчёт типа диапазона для каждой станции ──
    for st in stations:
        st["_band"] = lookup_band(st["freq"] * 1000, BANDS_REF,
                                   country=CURRENT_COUNTRY, subregion=CURRENT_SUBREGION)
        st["_is_oirt"] = st["_band"] and "OIRT" in st["_band"].get("name", "").upper()

    # ── Универсальная автокалибровка PPM ──
    from config.system import PPM_REFERENCES
    from core.snr import auto_ppm_calibrate, auto_ppm_calibrate_carrier

    current_ppm = args.ppm
    calibrated_band_type = None

    for i, st in enumerate(stations):
        freq = st["freq"]
        st_band = st["_band"]
        st_is_oirt = st["_is_oirt"]
        st_mod = st_band.get("modulation", "WFM") if st_band else "WFM"
        is_hf = freq < HF_THRESHOLD_MHZ

        # ==========================================================================
        # FORMAT NOTES:
        #   - HF/ДВ/СВ (< HF_THRESHOLD): .3f → критично для точного позиционирования
        #     в узких диапазонах с мелким шагом (1–9 кГц).
        #   - Остальные диапазоны: .1f → достаточно для широкой сетки.
        # ==========================================================================

        # ── Частота для вывода: на КВ — больше знаков ──
        if is_hf:
#            freq_str = f"{freq:.3f} MHz ({freq*1000:.1f} kHz)"
            freq_str = f"{freq:.3f} MHz"
        else:
            freq_str = f"{freq:.1f} MHz"

        # Фиксируем ширину колонок для ровного вывода
        freq_col = f"{freq_str:>11}"        # колонка частоты: 8 символов, по правому краю
        sig_col = f"{st.get('signal', 0):5.1f}"  # колонка сигнала: 5 символов

        # ── HF: пропускаем RDS и PPM, просто записываем сигнал ──
        if is_hf:

            print(f"[*] {i+1:3}/{len(stations):3}: {freq_col} ({sig_col} dB) [{st_mod}]")
            st["name_ru"] = prev_names.get(round(freq, 1)) or ""
            st["rds"] = {}
            st["stereo"] = None
            st["modulation"] = st_mod
            st["band_name"] = st_band.get("name", "Unknown") if st_band else "Unknown"
            continue

        # Для FM/OIRT: сначала общий статус
        print(f"[*] {i+1:3}/{len(stations):3}: {freq_col} ({sig_col} dB)")
        st["name_ru"] = prev_names.get(round(freq, 1)) or lookup_name_ru(freq) or ""
        st["modulation"] = st_mod
        st["band_name"] = st_band.get("name", "Unknown") if st_band else "Unknown"

        # Дальше идёт твоя существующая логика калибровки и декодирования RDS
        # (блок с calibrated_band_type, auto_ppm_calibrate, decode_rds_multi и т.п.)
        # её не трогай, она остаётся как была
        # ── Определяем тип для калибровки (только для FM/OIRT) ──
        if st_is_oirt:
            band_type = "OIRT"
        elif st_mod == "AM":
            band_type = "AM_AIR" if freq > 108 else "AM"
        else:
            band_type = "WFM"

# ==========================================================

        # ── HF/AM калибровка (один раз при смене band_type) ──
        # Для HF используется калибровка по несущей (RWM/WWV).
        # Она НЕ per-station: несущая одна, ppm один.
        if band_type in ("AM", "AM_AIR") and band_type != calibrated_band_type:
            ref_config = PPM_REFERENCES[band_type]
            method = ref_config["method"]
            if method == "carrier":
                ref_found = False
                for ref in ref_config.get("references", []):
                    ref_freq = ref["freq"]
                    print(f"[*] PPM calibration (carrier): {ref['name']} on "
                          f"{ref_freq} MHz ({ref.get('desc', '')})...")
                    ppm_cal, ok = auto_ppm_calibrate_carrier(
                        ref_freq, -1, DEFAULT_RDS_RATE, 3.0, 10.0
                    )
                    if ok:
                        current_ppm = ppm_cal
                        print(f"    → PPM = {current_ppm}")
                        ref_found = True
                        break
                if not ref_found:
                    print(f"    No reference signal found for {band_type} — "
                          f"keeping PPM = {current_ppm}")
            calibrated_band_type = band_type

        # ── Per-station PPM (FM/OIRT) ──
        # Для FM/OIRT — свой ppm на каждую станцию (многолучёвка).
        # Калибровка по пилот-тону ЭТОЙ станции, а не «самой сильной в диапазоне».
        if st_is_oirt or band_type == "WFM":
            if st.get("signal", -99) > -30:
                ppm_st, stereo_st = auto_ppm_calibrate(
                    st["freq"], -1, DEFAULT_RDS_RATE, 1.5
                )
                # Первая успешная калибровка — всегда принимаем
                if ppm_st != 0 and (current_ppm == 0 or abs(ppm_st - current_ppm) <= 15):
                    st_ppm = ppm_st
                    current_ppm = ppm_st
                    print(f"    [PPM] {st['freq']:.1f} MHz: calibration ppm={ppm_st} (per-station)")
                else:
                    st_ppm = current_ppm
                    print(f"    [PPM] {st['freq']:.1f} MHz: calibration failed "
                          f"(ppm={ppm_st}), using {current_ppm}")
            else:
                st_ppm = current_ppm
                print(f"    [PPM] {st['freq']:.1f} MHz: signal too weak "
                      f"({st['signal']:.1f} dB), using {current_ppm}")
        else:
            st_ppm = current_ppm

# ==========================================================

        if st_is_oirt:
            rds, used_gain, snr, stereo = decode_rds_multi(
                st["freq"], rds_gains, st_ppm, args.dwell, DEFAULT_RDS_RATE)
            if stereo is not None:
                st["stereo"] = stereo
            snr_str = f" | SNR={snr}dB" if snr is not None else ""
            ster_str = f" | {C_GREEN}STEREO{C_RESET}" if stereo else \
                       " | mono" if stereo is not None else ""
            print(f"    -> {C_YELLOW}OIRT{ster_str}{snr_str}{C_RESET}")
            st["rds"] = {}
        else:
            rds, used_gain, snr, stereo = decode_rds_multi(
                st["freq"], rds_gains, st_ppm, args.dwell, DEFAULT_RDS_RATE)
            if stereo is not None:
                st["stereo"] = stereo
            if rds:
                extra = f" | PS={rds.get('PS', '')}" if rds.get('PS') else ""
                snr_str = f" | SNR={snr}dB" if snr is not None else ""
                print(f"    -> {C_BLUE}PI={rds.get('PI', '?')}{extra}{snr_str}{C_RESET} "
                      f"(gain={used_gain}, ppm={st_ppm})")
                st["rds"] = rds
            else:
                snr_str = f" | SNR={snr}dB" if snr is not None else ""
                print(f"    -> {C_RED}no RDS{snr_str}{C_RESET}")
                st["rds"] = {}
        print()
# ================================================================
    # Сохраняем итоговый PPM
    args.ppm = current_ppm
# ================================================================
    # ── Чистим служебные поля перед сохранением в JSON ──
    # _band и _step_khz нужны только во время сканирования и печати,
    # в файлы они попадать не должны.
    fname = save_results(stations, args, "fullscan", ctx, scan_min=args.start, scan_max=args.stop)
    print_summary(stations, args.ppm, args.snap)

    final_stations = stations
    if fname != BASE_FILE and os.path.exists(BASE_FILE):
        print(f"\n[*] Base: {BASE_FILE} | Update: {fname}")
        base_data = load_json(BASE_FILE)
        upd_data = load_json(fname)
        if base_data and upd_data:
            diff_info = show_diff(base_data, upd_data, args.start, args.stop)
            merged = interactive_merge(base_data, diff_info, args.start, args.stop)
            if merged:
                with open(BASE_FILE, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)
                print(f"\n[*] База обновлена: {BASE_FILE}")
                print_summary(merged.get("stations", []), args.ppm, args.snap)
                final_stations = merged.get("stations", [])

    # ── Band label для export_html ──
    band_labels = sorted(set(sr["band_name"] for sr in sub_ranges))
    band = " + ".join(band_labels)
    export_html(
        data={
            "stations": final_stations,
            "scan_date": ctx.get("scan_date"),
            "observer": ctx.get("observer"),
            "antenna": ctx.get("antenna"),
            "metar": ctx.get("metar"),
            "space_weather": ctx.get("space_weather"),
            "gps_quality": ctx.get("gps_quality"),
            "artifact": ctx.get("artifact"),
            "interference": ctx.get("interference"),
            "ppm": args.ppm,                  # исправлено: у тебя в конце функции ты делаешь args.ppm = current_ppm
            "mode": "fullscan",
            "band": band,
        },
        region=region,
        output_path=output_path
    )

# ═══════════════════════════════════════════════════════════════
#  MODE 2: UPDATE
# ═══════════════════════════════════════════════════════════════

def mode_update(args, ctx=None):
    src = args.json or BASE_FILE
    data = load_json(src)
    if not data: print(f"[!] File not found: {src}"); return
    stations = data.get("stations", [])
    if not stations: print("[!] No stations in JSON"); return
    print("=" * 60 + f"\nFM RDS Scanner v{__version__} — Mode 2: Update ({src})\n" + "=" * 60)
    print(f"Stations: {len(stations)} | RDS gains: {args.rds_gains} dB | Dwell: {args.dwell}s\n")
    for i, st in enumerate(stations):
        freq = st["freq"]
        print(f"[*] {i+1}/{len(stations)}: {freq:.1f} MHz")
        rds, used_gain, snr, stereo = decode_rds_multi(freq, args.rds_gains, args.ppm, args.dwell)
        if stereo is not None:
            st["stereo"] = stereo
        if rds:
            extra = f" | PS={rds.get('PS', '')}" if rds.get('PS') else ""
            snr_str = f" | SNR={snr}dB" if snr is not None else ""
            print(f"    -> PI={rds.get('PI', '?')}{extra}{snr_str} (gain={used_gain})")
            st["rds"] = rds
        else:
            snr_str = f" | SNR={snr}dB" if snr is not None else ""
            print(f"    -> no RDS (keeping old data){snr_str}")
        print()
    scan_min = data.get("scan_range", {}).get("min")
    scan_max = data.get("scan_range", {}).get("max")
    upd_file = save_results(stations, args, "update", ctx, scan_min=scan_min, scan_max=scan_max)
    print_summary(stations, args.ppm, args.snap)
    if src == BASE_FILE:
        print(f"\n[*] Update file: {upd_file}")
        base_data = load_json(BASE_FILE)
        upd_data = load_json(upd_file)
        if base_data and upd_data:
            diff_info = show_diff(base_data, upd_data, scan_min, scan_max)
            merged = interactive_merge(base_data, diff_info, scan_min, scan_max)
            if merged:
                with open(BASE_FILE, "w", encoding="utf-8") as f:
                    json.dump(merged, f, ensure_ascii=False, indent=2)
                print(f"\n[*] База обновлена: {BASE_FILE}")
                print_summary(merged.get("stations", []), args.ppm, args.snap)

# ═══════════════════════════════════════════════════════════════
#  MODE 3: EDIT
# ═══════════════════════════════════════════════════════════════

def mode_edit(args):
    src = args.json or BASE_FILE
    data = load_json(src)
    if not data: print(f"[!] File not found: {src}"); return
    stations = data.get("stations", [])
    if not stations: print("[!] No stations in JSON"); return
    print("=" * 60 + f"\nFM RDS Scanner v{__version__} — Mode 3: Edit ({src})\n" + "=" * 60)
    print("Enter new name or press Enter to keep current.\n")
    for i, st in enumerate(stations):
        freq = st["freq"]; current = st.get("name_ru", "") or ""
        rds_ps = st.get("rds", {}).get("PS", "") or ""
        hint = rds_ps or lookup_name_ru(freq) or ""
        print(f"{i+1}/{len(stations)}: {freq:.1f} MHz  current: '{current}'  RDS PS: '{rds_ps}'")
        new = input(f"  New name_ru [{hint}]: ").strip()
        if new: st["name_ru"] = new
        elif not current and hint: st["name_ru"] = hint
    with open(src, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n[*] Saved: {src}")
    print_summary(stations, data.get("params", {}).get("ppm", 0), data.get("params", {}).get("snap_khz", 0))

# ═══════════════════════════════════════════════════════════════
#  MODE 4: MERGE
# ═══════════════════════════════════════════════════════════════

def mode_merge(args):
    base_data = load_json(args.json or BASE_FILE)
    upd_file = args.update
    if not upd_file:
        prefix = f"SDR_Update_{CURRENT_REGION}_"
        updates = sorted([f for f in os.listdir(str(DATA_DIR))
                          if f.startswith(prefix) and f.endswith(".json")])
        if not updates:
            print("[!] No update files found."); return
        upd_file = str(DATA_DIR / updates[-1])
        print(f"[*] Using latest update: {upd_file}")
    else:
        if not os.path.isabs(upd_file):
            upd_file = str(DATA_DIR / upd_file)
    upd_data = load_json(upd_file)
    if not base_data or not upd_data:
        print("[!] Missing base or update file."); return
    scan_min = upd_data.get("scan_range", {}).get("min")
    scan_max = upd_data.get("scan_range", {}).get("max")
    diff_info = show_diff(base_data, upd_data, scan_min, scan_max)
    if not diff_info["new"] and not diff_info["gone"] and not diff_info["changed"]:
        print("\nНет изменений для слияния."); return
    merged = interactive_merge(base_data, diff_info, scan_min, scan_max)
    if merged:
        out = args.json or BASE_FILE
        with open(out, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"\n[*] База обновлена: {out}")
        print_summary(merged.get("stations", []),
                      upd_data.get("params", {}).get("ppm", 0),
                      upd_data.get("params", {}).get("snap_khz", 0))

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    global CURRENT_REGION, BASE_FILE, STATIONS_FILE, CURRENT_COUNTRY, CURRENT_SUBREGION

    p = argparse.ArgumentParser(
        description="SDR-RTL Scanner v{__version__} (rtl_power + rtl_fm + redsea) for RTL-SDR\n\n"
                    "Quick start:\n"
                    "  python3 SDR_RTL_Scanner.py\n"
                    "  → fullscan 87–109 MHz, region 'default' (empty reference)\n\n"
                    "  python3 SDR_RTL_Scanner.py --region spb\n"
                    "  → fullscan with Saint Petersburg station reference\n\n"
                    "  python3 SDR_RTL_Scanner.py --region '?'\n"
                    "  → interactive region selection\n\n"
                    "Files:\n"
                    "  regions/{region}.json            — station reference (per region)\n"
                    "  data/SDR_Base_{region}.json — base database\n"
                    "  data/SDR_Update_{region}_*.json — scan results",
        formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--mode", choices=["fullscan", "update", "edit", "merge", "manual"],
                   default="fullscan",
                   help="fullscan: scan + RDS (default)\n"
                        "update: re-decode RDS from base\n"
                        "edit: manual name_ru editing\n"
                        "merge: merge update into base\n"
                        "manual: scan some freqs + RDS (use with --freqs)")
    p.add_argument('--freqs', type=str, default=None,
                   help='Список частот для режима manual, например: "88.4,98.2,102.0,105.9"')
    p.add_argument("--modulation", choices=["FM", "OIRT", "AM", "USB", "LSB"], default=None,
                   help="Тип модуляции: FM, OIRT, AM, USB, LSB. Для FM/OIRT будет декодироваться RDS.")
    p.add_argument("--group-delta", type=float, default=0.2,
                   help="Расстояние между станциями для группировки (МГц)")
    p.add_argument("--region", type=str, default=None,
                   help="Region code in CC.city format, e.g. RU.spb, RU.msk, DE.berlin.\n"
                        "Use '?' for interactive selection.\n"
                        "Default is taken from config/user.py (DEFAULT_REGION).")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Auto-confirm detected region (non-interactive)")
    p.add_argument("--no-detect", action="store_true",
                   help="Disable automatic region detection (use only --region / observer.json)")
    p.add_argument("--ppm", type=int, default=DEFAULT_PPM, help="PPM correction (default: 0)")
    p.add_argument("--gain", type=float, default=DEFAULT_RDS_GAIN, help="Alias for --rds-gain (default: 50)")
    p.add_argument("--rds-gain", type=float, default=None, help="Single RDS gain dB, or -1 for auto (overrides --rds-gains)")
    p.add_argument("--rds-gains", type=str, default=DEFAULT_RDS_GAINS,
                   help="Comma-separated RDS gains (default: -1,50,25,10,0)")
    p.add_argument("--dwell", type=int, default=DEFAULT_DWELL, help="Seconds per station for RDS (default: 20)")

    p.add_argument("--start", type=float, default=DEFAULT_START,
                   help="Start MHz (default: 87.0).\n"
                        "Note: narrow ranges (< 5 MHz) skip auto-threshold — set --threshold manually.")
    p.add_argument("--stop", type=float, default=DEFAULT_STOP,
                   help="Stop MHz (default: 109.0).\n"
                        "Note: narrow ranges (< 5 MHz) skip auto-threshold — set --threshold manually.")
    p.add_argument("--step", type=float, default=None, help="Step kHz for rtl_power (default: 100)")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help="Min dB (default: -25, auto-adjusted for wide ranges).\n"
                        "For narrow ranges (< 5 MHz) auto-threshold is skipped —\n"
                        "set this manually (e.g. 6-10 dB) to avoid missing weak stations.")
    p.add_argument("--gains", type=str, default=DEFAULT_GAINS, help="Comma-separated scan gains (default: 0,10,25,50)")
    p.add_argument("--snap", type=int, default=DEFAULT_SNAP, help="Snap grid kHz (0=off, default: 100)")
    p.add_argument("--stations", type=str, default=None, help="Station reference JSON (default: regions/{region}.json)")
    p.add_argument("--json", type=str, default=None, help="JSON file (for update/edit/merge)")
    p.add_argument("--update", type=str, default=None, help="Update file for merge mode")
    p.add_argument("--output", type=str, default=None, help="Output JSON filename")
    # ── Context parameters ──
    p.add_argument("--with-context", action="store_true", default=True,
                   help="Add observation context (weather, antenna, observer) to output")
    p.add_argument("--no-context", dest="with_context", action="store_false",
                   help="Disable context output (backward compatibility)")
    p.add_argument("--antenna", type=str, default=None,
                   help="Antenna ID from antennas.json (e.g. ant_001)")
    p.add_argument("--artifact", type=str, default=None,
                   help="Artifact type flag (e.g. tropospheric_duct, sporadic_e)")
    p.add_argument("--artifact-notes", type=str, default="",
                   help="Notes about the artifact")
    p.add_argument("--interference", type=str, default=None,
                   help="Interference type flag (e.g. power_line, co_channel)")
    p.add_argument("--interference-notes", type=str, default="",
                   help="Notes about the interference")
    args = p.parse_args()

    # Нормализация: OIRT считаем как FM с флагом
    modulation = args.modulation

    if modulation == "OIRT":
        is_oirt = True
        mode_rtl = "fm"          # rtl_fm всё равно использует -M fm
        pilot_freq = 31250.0     # 31.25 кГц
        rds_enabled = False      # RDS в OIRT нет
    elif modulation in ("FM", "AM", "USB", "LSB"):
        is_oirt = False
        mode_rtl = modulation.lower()  # "fm", "am", "usb", "lsb"
        pilot_freq = 19000.0
        # RDS только для FM
        rds_enabled = (modulation == "FM")
    else:
        # fallback: если None — пробуем угадать по диапазону (опционально)
        modulation = "FM"
        mode_rtl = "fm"
        is_oirt = False
        pilot_freq = 19000.0
        rds_enabled = True

    # Ручной ввод отдельной частоты
    if args.mode == 'manual':
        if not args.freqs:
            p.error('Для режима --mode manual обязательно укажите --freqs с перечнем частот через запятую.')
        try:
            freq_list = [float(x.strip()) for x in args.freqs.split(',') if x.strip()]
            if len(freq_list) == 0:
                raise ValueError
        except ValueError:
            p.error('Некорректный формат --freqs: ожидается список чисел через запятую, например "88.4,98.2".')
    else:
        freq_list = None

    # ── Region selection ──
    ensure_dirs()
    # ── Выбор региона: приоритеты ──
    #  1. --region из CLI            (явное указание — всегда главное)
    #  2. observer.json → region     (пользователь сказал в конфиге)
    #  3. GPS (если mode >= 2)       (точные координаты)
    #  4. IPv4                       (если есть сеть; VPN может врать)
    #  5. Cell ID                    (потом)
    #  6. fallback "RU.spb"
    from core.region_detect import resolve_region, save_region_to_observer

    region = args.region
    region_source = "cli" if args.region else None

    # Если CLI не задан — пробуем детект
    if not region:
        # Загружаем GPS-качество ДО выбора региона (нужно для шага 3)
        gps_for_region = None
        if args.with_context:
            try:
                from core.gq_adapter import read_gps_quality
                gps_for_region = read_gps_quality(
                    host=OBSERVER_CONFIG.get("gps", {}).get("host", "127.0.0.1"),
                    port=int(OBSERVER_CONFIG.get("gps", {}).get("port", 2947)),
                    timeout=float(OBSERVER_CONFIG.get("gps", {}).get("timeout", 5.0)),
                )
            except Exception:
                gps_for_region = None

        detected = resolve_region(
            observer_cfg=OBSERVER_CONFIG,
            gps_quality=gps_for_region,
            allow_network=not args.no_detect,
        )

        if detected:
            region = detected["region"]
            source = detected["source"]
            region_source = source
            print(f"[*] Region detected: {region} (source: {source})")

            # Спрашиваем только если регион НЕ из config и не --yes
            if not args.yes and region_source != "config":
                try:
                    ans = input(f"    Confirm region '{region}'? [Y/n/region]: ").strip().lower()
                except (EOFError, UnicodeDecodeError):
                    ans = "y"

                if ans in ("n", "no"):
                    # Пользователь отверг детект — не сохраняем, пойдём к fallback
                    region = None
                    region_source = None
                elif ans and ans not in ("y", "yes", ""):
                    # Пользователь ввёл свой регион — сохраняем его выбор
                    region = ans
                    region_source = "user"
                    print(f"[*] Using user-specified region: {region}")
                    save_region_to_observer(region)
                else:
                    # Пользователь подтвердил детект — сохраняем
                    if region_source != "config":
                        save_region_to_observer(region)

            elif args.yes and region_source != "config":
                # --yes: авто-подтверждение, сохраняем детект
                print(f"[*] Using detected region: {region} (--yes, auto-saved)")
                save_region_to_observer(region)

            elif region_source == "config":
                # Регион из config — используем как есть, без сохранения
                print(f"[*] Using region from config: {region}")
        else:
            print("[!] Region could not be detected (no config, no GPS fix, no network).")

    # Финальный fallback
    if not region:
        region = "RU.spb"
        region_source = "fallback"
        print(f"[!] Region not determined; falling back to hardcoded default: {region}")

    print(f"[*] Using region: {region} (source: {region_source})")

    # =============================================================================================
    if region == "?":
        region = interactive_region_select()
        if region is None:
            print("[!] Interactive region selection cancelled.")
            sys.exit(1)

    CURRENT_REGION = region
    ensure_region_file(region)
    ensure_base_file(region)
    ts = datetime.now().strftime("%Y.%m.%d_%H.%M.%S")
    html_path_obj = REPORTS_DIR / f"SDR_Report_{region}_{ts}.html"
    html_path = str(html_path_obj)

    # --- НОВЫЙ БЛОК: парсим регион и получаем страну/субрегион ---
    try:
        region_info = parse_region_name(region)
        CURRENT_COUNTRY = region_info["country"]
        load_bands_reference()
        CURRENT_SUBREGION = region_info.get("subregion")
        print(f"[*] Region parsed: country={CURRENT_COUNTRY}, subregion={CURRENT_SUBREGION}")
    except ValueError as e:
        print(f"[!] Invalid region format: {e}")
        sys.exit(1)
        print(f"[DEBUG] args.region={args.region!r}")
        print(f"[DEBUG] OBSERVER_CONFIG['region']={OBSERVER_CONFIG.get('region')!r}")
        print(f"[DEBUG] Final region used={region!r}")

    # -------------------------------------------------------------

    BASE_FILE = get_base_file(region)
    STATIONS_FILE = args.stations or get_stations_file(region)

    # ── Context initialization ──
    if args.with_context:
        # Выбор антенны по ID или дефолтная
        if args.antenna and args.antenna in ANTENNAS_CONFIG:
            selected_antenna = ANTENNAS_CONFIG[args.antenna]
        else:
            selected_antenna = _default_antenna
        app_context = Context(OBSERVER_CONFIG, selected_antenna)
        app_context.set_scan_datetime(datetime.now())

        # Флаги артефактов и помех
        if args.artifact:
            app_context.set_artifact(args.artifact, args.artifact_notes)
        if args.interference:
            app_context.set_interference(args.interference, args.interference_notes)

# ===================================================================================================
        # Запрос METAR
        metar_station = app_context.get_metar_station()
        if metar_station:
            print(f"[*] Fetching METAR for {metar_station}...")
            wx = app_context.fetch_metar()
            if wx:
                app_context.weather = wx
                print(f"    {_format_metar_human(wx)}")
            else:
                print(f"    [!] METAR fetch failed")

        # Запрос космической погоды (NOAA SWPC)
        print(f"[*] Fetching space weather (NOAA SWPC)...")
        sw = app_context.fetch_space_weather()

        if sw:
            app_context.space_weather = sw   # <-- ЭТА СТРОКА ОБЯЗАТЕЛЬНА
            print("[*] Space weather fetched successfully")
        else:
            print(f"    [!] Space weather fetch failed")

        # Считывание GPS-качества (локально, для контекста приёма)
        print(f"[*] Reading GPS quality...")
        gq = app_context.fetch_gps_quality()
        if gq.get("available"):
            print("[*] GPS quality read successfully")
        else:
            print(f"    [!] GPS quality unavailable")

        print(f"[*] Context ready. Observer: {app_context.get_observer_name()}, "
              f"Antenna: {app_context.get_antenna_model()}")
# ===================================================================================================
    else:
        app_context = None

    print(f"[*] Region: {region}")
    print(f"[*] Base:   {BASE_FILE}")
    print(f"[*] Ref:    {STATIONS_FILE}")

    # ── Load station reference ──
    load_stations(STATIONS_FILE)
    if _station_map:
        print(f"[*] Loaded {len(_station_map)} stations from {STATIONS_FILE}")
    else:
        print(f"[*] No station reference — names from RDS only")

    # ── Handle gain overrides ──
    if args.rds_gain is not None:
        if args.rds_gain == -1:
            args.rds_gains = [-1]
        else:
            args.rds_gains = [int(args.rds_gain)]
    else:
        args.rds_gains = [int(x.strip()) for x in args.rds_gains.split(",")]

    # ── Check tools ──
    for tool in ["rtl_power", "rtl_fm", "redsea"]:
        path = shutil.which(tool)
        if not path:
            print(f"[!] '{tool}' not found.")
            if tool == "redsea":
                print("    https://github.com/windytan/redsea")
            elif tool.startswith("rtl"):
                print("    sudo apt install rtl-sdr   # или эквивалент для вашей ОС")
            sys.exit(1)
        print(f"[*] Found {tool} at {path}")

    # ── CONTEXT: единая точка истины для запуска (время, погода, условия приёма) ──
    # Description: все временные метки — в UTC (ISO 8601). Это критично для:
    #   - привязки к METAR/космической погоде (они в UTC);
    #   - воспроизводимости и аудита (гос. контракт, отчёты, CI/CD);
    #   - сопоставления нескольких сканов по времени.
    # Если погода недоступна — кладём None, отчёт остаётся валидным.
    scan_date_utc = datetime.now(timezone.utc).isoformat()

    ctx = {
        "scan_date": scan_date_utc,
        "observer": app_context.get_observer_name() if app_context else None,
        "antenna": app_context.get_antenna_model() if app_context else None,
        "region": region,
        "metar": app_context.weather if app_context else None,
        "gps_quality": app_context.gps_quality if app_context else None,
        "space_weather": app_context.space_weather if app_context else None,
        "artifact": app_context.artifact if app_context else None,
        "interference": app_context.interference if app_context else None,
    }

    # ✅ Вызов нужного режима — теперь ВНЕ цикла, выполняется один раз
    if args.mode == "fullscan":
#        mode_fullscan(args)
        mode_fullscan(args, ctx=ctx, region=region, output_path=html_path)
#        mode_fullscan(args, ctx=ctx, region=region)
    elif args.mode == "update":
        mode_update(args, ctx=ctx)
    elif args.mode == "edit":
        mode_edit(args)
    elif args.mode == "merge":
        mode_merge(args)

if __name__ == "__main__": main()
