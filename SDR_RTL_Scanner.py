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

import os, sys, json, argparse, subprocess, select, time, config, shutil
import numpy as np
from pathlib import Path
from datetime import datetime
from src.context import Context, _format_metar_human
from config import *
from core.snr import auto_ppm_calibrate, check_rds_snr, decode_rds, decode_rds_multi
from core.scanner import multi_gain_scan, bloom_split_scan, split_scan_ranges, snap_freq
from config.styles import C_GREEN, C_RED, C_BLUE, C_YELLOW, C_RESET
from config.system import DEFAULT_DWELL, DEFAULT_RDS_RATE, DEFAULT_PPM, PPM_REFERENCES

# --- ФИЛЬТР ESCAPE-КОДОВ МЫШИ (для legacy-консоли Windows) ---
# Если stdout подключён к терминалу и мы в Windows — оборачиваем sys.stdout
if sys.stdout.isatty() and os.name == 'nt':
    class MouseFilterWriter:
        def __init__(self, target):
            self.target = target
            self.escape_seq = False
            self.buffer = []

        def write(self, text):
            # Пропускаем любые последовательности, начинающиеся с ESC (\x1b)
            # Это убирает xterm mouse reporting: 64;35;30M, M, C, D и т.п.
            if not text:
                return

            # Простая эвристика: если видим ESC — игнорируем всё до конца последовательности
            if '\x1b' in text:
                # Разбиваем по ESC, оставляем только чистые куски
                parts = text.split('\x1b')
                clean = parts  # всё до первого ESC
                # Если после ESC есть текст — он часть escape-последовательности, игнорируем
                # (в редких случаях может быть смешанный вывод, но для логов это ок)
                if clean:
                    self.target.write(clean)
                # Остатки после ESC игнорируем
                return

            self.target.write(text)

        def flush(self):
            self.target.flush()

        def __getattr__(self, name):
            # Пробрасываем остальные атрибуты (encoding, errors и т.п.)
            return getattr(self.target, name)

    sys.stdout = MouseFilterWriter(sys.stdout)
    # При желании можно аналогично для stderr, если туда тоже сыпется мышь
    # sys.stderr = MouseFilterWriter(sys.stderr)
# ---------------------------------------------------------------


# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR      = Path(__file__).parent
REGIONS_DIR     = SCRIPT_DIR / "regions"
DATA_DIR        = SCRIPT_DIR / "data"
REPORTS_DIR     = SCRIPT_DIR / "reports"
#
# Определяем базовый путь к проекту (чтобы не зависеть от текущей рабочей директории)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
OBSERVER_PATH = os.path.join(CONFIG_DIR, "observer.json")
ANTENNAS_PATH = os.path.join(CONFIG_DIR, "antennas.json")
#
# --- НАЧАЛО НОВОГО БЛОКА: ЗАГРУЗКА КОНФИГУРАЦИИ ---
try:
    with open(OBSERVER_PATH, "r", encoding="utf-8") as f:
        OBSERVER_CONFIG = json.load(f)
    print(f"[*] Loaded observer config: {OBSERVER_PATH}")
except FileNotFoundError:
    print(f"[!] Warning: observer.json not found at {OBSERVER_PATH}. Using defaults.")
    OBSERVER_CONFIG = {}

try:
    with open(ANTENNAS_PATH, "r", encoding="utf-8") as f:
        ANTENNAS_CONFIG = json.load(f)
    print(f"[*] Loaded antennas config: {ANTENNAS_PATH}")
except FileNotFoundError:
    print(f"[!] Warning: antennas.json not found at {ANTENNAS_PATH}. Using defaults.")
    ANTENNAS_CONFIG = {}
#
#DEFAULT_START    = 87.0
#DEFAULT_STOP     = 109.0
#DEFAULT_STEP     = 100      # kHz
#DEFAULT_THRESHOLD = -25.0   # dB (auto-adjusted if too low)
#DEFAULT_PPM      = 0
#DEFAULT_DWELL    = 20       # seconds per station for RDS
#DEFAULT_GAINS    = "0,10,25,50"
#DEFAULT_RDS_GAIN = 50
#DEFAULT_RDS_GAINS = "-1,50,25,10,0"
#DEFAULT_RDS_RATE = 228000   # Hz
#DEFAULT_SNAP     = 100      # kHz
#FM_MIN, FM_MAX   = 87.5, 108.0
#NARROW_RANGE_MHZ = 5.0
# ── ANSI colors ──
#C_RED    = '\033[91m'
#C_GREEN  = '\033[92m'
#C_BLUE   = '\033[94m'
#C_YELLOW = '\033[93m'
#C_RESET  = '\033[0m'

# Выбираем антенну по умолчанию (первый ключ из справочника)
_default_antenna = ANTENNAS_CONFIG[list(ANTENNAS_CONFIG.keys())[0]] if ANTENNAS_CONFIG else {}
app_context = Context(OBSERVER_CONFIG, _default_antenna)
print(f"[*] Context ready. Observer: {app_context.get_observer_name()}, Antenna: {app_context.get_antenna_model()}")

# Устанавливаются в main() после выбора региона
CURRENT_REGION  = "default"
CURRENT_COUNTRY = ""
CURRENT_SUBREGION = None
BASE_FILE       = ""
STATIONS_FILE   = ""
BANDS_REF       = None

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
    r = region or CURRENT_REGION
    return str(DATA_DIR / f"SDR_Base_{r}.json")

def get_update_file(region=None):
    r = region or CURRENT_REGION
    ts = datetime.now().strftime("%Y.%m.%d_%H.%M")
    return str(DATA_DIR / f"SDR_Update_{r}_{ts}.json")

def get_stations_file(region=None):
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

def save_results(stations, args, mode, filename=None, scan_min=None, scan_max=None):
    global app_context
    if filename is None:
        if mode in ("fullscan", "update"):
            filename = make_update_filename()
        else:
            filename = args.output or BASE_FILE
    if filename == BASE_FILE:
        filtered = [s for s in stations if is_in_fq_range(s["freq"])]
        if len(filtered) < len(stations):
            print(f"[!] {len(stations)-len(filtered)} station(s) outside {FM_MIN}-{FM_MAX} MHz not saved to {BASE_FILE}.")
    else:
        filtered = stations
    scan_band = "OIRT" if (scan_min and scan_min >= 65.0 and scan_max and scan_max <= 74.5) else "FM"
    data = {"scan_date": datetime.now().isoformat(), "mode": mode, "band": scan_band,
            "scan_range": {"min": scan_min, "max": scan_max},
            "params": {"start": args.start, "stop": args.stop, "step": args.step,
                       "threshold": args.threshold, "ppm": args.ppm, "gains": args.gains,
                       "rds_gains": args.rds_gains, "rds_rate": DEFAULT_RDS_RATE,
                       "dwell": args.dwell, "snap_khz": args.snap},
            "stations": filtered}
    # ── Add observation context if available ──
    if app_context is not None:
        data["observation_context"] = app_context.to_dict()
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(json_safe(data), f, ensure_ascii=False, indent=2)
    print(f"\n[*] Saved: {filename}")
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

def show_diff(base_data, update_data, scan_min, scan_max):
    base_sts = {round(s["freq"], 1): s for s in base_data.get("stations", [])}
    upd_sts  = {round(s["freq"], 1): s for s in update_data.get("stations", [])}
    new_f = sorted(set(upd_sts) - set(base_sts))
    gone_all = sorted(set(base_sts) - set(upd_sts))
    common_f = sorted(set(base_sts) & set(upd_sts))
    gone_in_range = [f for f in gone_all if scan_min and scan_max and scan_min <= f <= scan_max]
    gone_outside = [f for f in gone_all if not (scan_min and scan_max and scan_min <= f <= scan_max)]
    changed = []
    for f in common_f:
        b, u, diffs = base_sts[f], upd_sts[f], []
        for key in ["signal", "name_ru"]:
            if b.get(key) != u.get(key): diffs.append((key, b.get(key), u.get(key)))
        br, ur = b.get("rds", {}), u.get("rds", {})
        for key in ["PI", "PS", "PTY", "RadioText", "Stereo", "TP", "TA"]:
            if br.get(key) != ur.get(key): diffs.append((f"rds.{key}", br.get(key), ur.get(key)))
        if diffs: changed.append((f, diffs))
    print("\n" + "=" * 70)
    print("СРАВНЕНИЕ: Base vs Update")
    print("=" * 70)
    if new_f:
        print(f"\n  Новые станции ({len(new_f)}):")
        for i, f in enumerate(new_f):
            s = upd_sts[f]; ps = s.get("rds", {}).get("PS", "?")
            print(f"    {i+1}. + {f:.1f} MHz  ({s.get('signal', 0):.1f} dB)  PS={ps}")
    if gone_in_range:
        print(f"\n  Потерянные в диапазоне сканирования ({len(gone_in_range)}):")
        for i, f in enumerate(gone_in_range):
            s = base_sts[f]; ps = s.get("rds", {}).get("PS", "?")
            print(f"    {i+1}. - {f:.1f} MHz  PS={ps}")
    if gone_outside:
        print(f"\n  Вне диапазона сканирования (не трогаются, {len(gone_outside)}):")
        for f in gone_outside:
            s = base_sts[f]; ps = s.get("rds", {}).get("PS", "?")
            print(f"    ~ {f:.1f} MHz  PS={ps}")
    if changed:
        print(f"\n  Изменены ({len(changed)}):")
        for i, (f, diffs) in enumerate(changed):
            print(f"    {i+1}. ~ {f:.1f} MHz:")
            for key, old, new in diffs: print(f"        {key}: {old} -> {new}")
    if not new_f and not gone_in_range and not changed: print("\n  Нет различий.")
    print("=" * 70)
    return {"new": new_f, "new_sts": upd_sts, "gone": gone_in_range, "gone_sts": base_sts,
            "changed": [f for f, _ in changed], "changed_sts": upd_sts}

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
        freq = st.get("freq", 0); signal = st.get("signal", 0)
        name_ru = st.get("name_ru", "") or "—"
        mod = st.get("modulation", "") or "—"
        band = st.get("band_name", "") or "—"
        rds = st.get("rds", {})
        pi = rds.get("PI", "—"); ps = rds.get("PS", "—") or "—"
        pty = rds.get("PTY", "—") or "—"
        tp = rds.get("TP", "—"); ta = rds.get("TA", "—")
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
        print(f"{freq:>6.1f}M | {signal:>6.1f} | {name_ru:<22} | {mod:>5} | {band:<12} | "
              f"{pi_str:>8} | {ps:<24} | {pty:<16} | {ster_str:>5} | {str(tp):>4} | {str(ta):>4} | {rt_str}")
    print("=" * 155)
    print(f"\nВсего станций: {len(stations)} | PPM: {ppm} | Snap: {snap} kHz")
    print(f"С RDS (PI): {n_pi} | С PS: {n_ps} | С RT: {n_rt} | С русским названием: {n_ru}")

# ═══════════════════════════════════════════════════════════════
#  Export to html
# ═══════════════════════════════════════════════════════════════


# ══════════════════════════════
def export_html(data, region="RU.spb", output_path=None):
    stations = data.get("stations", )
    scan_date = data.get("scan_date", "")
    ppm = data.get("ppm", "?")
    mode = data.get("mode", "?")

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

        row_class = ""
        if status == "lost":
            row_class = ' class="row-lost"'
        elif status == "new":
            row_class = ' class="row-new"'

        rows += (
            f"      <tr{row_class}>\n"
            f"        <td>{freq:.1f}</td>\n"
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

    html = f"""<!DOCTYPE html>
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
<div class="footer">Generated by SDR-RTL-Scanner v{__version__} | {scan_date[:19]}</div>
{js_code}
</body>
</html>"""

    if output_path is None:
        output_path = os.path.join(REPORTS_DIR, f"Last_Report_SDR_RTL_{region}.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[*] HTML report: {output_path}")
    return output_path

# ═══════════════════════════════════════════════════════════════
#  MODE 1: FULLSCAN
# ═══════════════════════════════════════════════════════════════

def mode_fullscan(args):
    print("=" * 60 + f"\nSDR-RTL Scanner v{__version__} — Mode 1: Full Scan\n" + "=" * 60)
    prev_names = {}
    if os.path.exists(BASE_FILE):
        prev = load_json(BASE_FILE)
        if prev:
            for st in prev.get("stations", []):
                if st.get("name_ru"):
                    prev_names[round(st["freq"], 1)] = st["name_ru"]

    # ── Разбиваем на поддиапазоны по справочнику ──
    sub_ranges = split_scan_ranges(
        args.start, args.stop, BANDS_REF,
        country=CURRENT_COUNTRY, subregion=CURRENT_SUBREGION
    )

    print(f"[*] Scan range {args.start}–{args.stop} MHz → {len(sub_ranges)} sub-range(s):")
    for sr in sub_ranges:
        tag = "OIRT" if sr["is_oirt"] else sr["modulation"]
        print(f"    {sr['start_mhz']:.1f}–{sr['stop_mhz']:.1f} MHz | "
              f"{sr['band_name']} | {tag} | step={sr['step_khz']:.0f} kHz")

    # ── Сканируем каждый поддиапазон ──
    all_stations = []
    for sr in sub_ranges:
        # Если юзер задал --step явно — используем его, иначе берём из справочника
        step = args.step if args.step is not None else sr["step_khz"]
        if not isinstance(step, (int, float)) or step <= 0:
            step = DEFAULT_STEP


        # Порог: из справочника, или --threshold, или дефолт
        band_threshold = sr.get("threshold")
        if band_threshold is not None:
            effective_threshold = band_threshold
        else:
            effective_threshold = args.threshold


        print(f"\n[*] Multi-gain scan: {sr['start_mhz']:.1f}–{sr['stop_mhz']:.1f} MHz "
              f"({sr['band_name']}, step={step:.0f} kHz, threshold={effective_threshold:.1f} dB)")

        found = bloom_split_scan(sr["start_mhz"], sr["stop_mhz"], step,
                                 args.gains, effective_threshold, args.ppm, args.snap,
                                 antennas_config=ANTENNAS_CONFIG)
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
        save_results([], args, "fullscan", scan_min=args.start, scan_max=args.stop)
        print_summary([], args.ppm, args.snap)
        return

# ================================================================
    # ── Тип сессии (для лога) ──
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
# ================================================================
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

        # ── Частота для вывода: на КВ — больше знаков ──
        if is_hf:
            freq_str = f"{freq:.3f} MHz ({freq*1000:.1f} kHz)"
        else:
            freq_str = f"{freq:.1f} MHz"

        # ── HF: пропускаем RDS и PPM, просто записываем сигнал ──
        if is_hf:
            print(f"[*] {i+1}/{len(stations)}: {freq_str} "
                  f"({st['signal']:.1f} dB) [{st_mod}]")
            st["name_ru"] = prev_names.get(round(freq, 1)) or ""
            st["rds"] = {}
            st["stereo"] = None
            st["modulation"] = st_mod
            st["band_name"] = st_band.get("name", "Unknown") if st_band else "Unknown"
            print()
            continue

        # ── Определяем тип для калибровки (только для FM/OIRT) ──
        if st_is_oirt:
            band_type = "OIRT"
        elif st_mod == "AM":
            band_type = "AM_AIR" if freq > 108 else "AM"
        else:
            band_type = "WFM"

        # Калибруем ТОЛЬКО при смене типа диапазона
        if band_type != calibrated_band_type and band_type in PPM_REFERENCES:
            ref_config = PPM_REFERENCES[band_type]
            method = ref_config["method"]

            print(f"\n[*] Calibrating PPM for new band type: {band_type}...")

            if method == "pilot":
                # Калибровка по пилот-тону (FM или OIRT)
                if band_type == "WFM":
                    same_type = [s for s in stations if not s.get("_is_oirt") and s["freq"] >= HF_THRESHOLD_MHZ]
                elif band_type == "OIRT":
                    same_type = [s for s in stations if s.get("_is_oirt")]
                else:
                    same_type = []

                if same_type:
                    # --- ЗАЩИТА ОТ ШУМА ---
                    valid_stations = [
                        s for s in same_type
                        if s.get("signal", -99) > -30 and s.get("freq") is not None
                    ]

                    if not valid_stations:
                        print(f"[!] No strong stations found for PPM calibration "
                              f"in band {band_type} (threshold > -30 dB). "
                              f"Keeping current PPM.")
                    else:
                        strongest = max(valid_stations, key=lambda x: x["signal"])

                        print(f"[*] Calibrating PPM on strongest valid station: "
                              f"{strongest['freq']:.3f} MHz, "
                              f"Signal: {strongest['signal']:.1f} dB")

                        ppm_cal, stereo_cal = auto_ppm_calibrate(
                            strongest["freq"], -1, DEFAULT_RDS_RATE, 2.0
                        )

                        if ppm_cal != 0:
                            print(f"    {C_GREEN}Pilot tone detected → "
                                  f"PPM = {ppm_cal} (was {current_ppm}){C_RESET}")
                            current_ppm = ppm_cal
                        elif stereo_cal is False:
                            print(f"    No pilot tone (mono) → "
                                  f"keeping PPM = {current_ppm}")
                        else:
                            print(f"    No pilot tone detected → "
                                  f"keeping PPM = {current_ppm}")
                else:
                    print(f"    No stations of type {band_type} "
                          f"found for pilot calibration.")
# ======================================================================================
            elif method == "carrier":
                # Калибровка по несущей (AM/HF/Air) — перебор эталонов из PPM_REFERENCES
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
# =============================================================
            else:
                print(f"    Unknown calibration method '{method}' "
                      f"for {band_type}")

            calibrated_band_type = band_type

        # ── Декодирование RDS / определение стерео (только FM/OIRT) ──
        print(f"[*] {i+1}/{len(stations)}: {freq_str} "
              f"({st['signal']:.1f} dB)")
        st["name_ru"] = prev_names.get(round(freq, 1)) or lookup_name_ru(freq) or ""
        st["modulation"] = st_mod
        st["band_name"] = st_band.get("name", "Unknown") if st_band else "Unknown"

        if st_is_oirt:
            rds, used_gain, snr, stereo = decode_rds_multi(
                st["freq"], rds_gains, current_ppm, args.dwell, DEFAULT_RDS_RATE)
            if stereo is not None:
                st["stereo"] = stereo
            snr_str = f" | SNR={snr}dB" if snr is not None else ""
            ster_str = f" | {C_GREEN}STEREO{C_RESET}" if stereo else \
                       " | mono" if stereo is not None else ""
            print(f"    -> {C_YELLOW}OIRT{ster_str}{snr_str}{C_RESET}")
            st["rds"] = {}
        else:
            rds, used_gain, snr, stereo = decode_rds_multi(
                st["freq"], rds_gains, current_ppm, args.dwell, DEFAULT_RDS_RATE)
            if stereo is not None:
                st["stereo"] = stereo
            if rds:
                extra = f" | PS={rds.get('PS', '')}" if rds.get('PS') else ""
                snr_str = f" | SNR={snr}dB" if snr is not None else ""
                print(f"    -> {C_BLUE}PI={rds.get('PI', '?')}{extra}{snr_str}{C_RESET} "
                      f"(gain={used_gain})")
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
    fname = save_results(stations, args, "fullscan", scan_min=args.start, scan_max=args.stop)
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

    export_html({
        "stations": final_stations,
        "scan_date": datetime.now().isoformat(),
        "ppm": args.ppm,
        "mode": args.mode,
        "band": band
    }, region=args.region)

# ═══════════════════════════════════════════════════════════════
#  MODE 2: UPDATE
# ═══════════════════════════════════════════════════════════════

def mode_update(args):
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
    upd_file = save_results(stations, args, "update", scan_min=scan_min, scan_max=scan_max)
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
    p.add_argument("--region", type=str, default="RU.spb",
                   help="Region code in CC.city format, e.g. RU.spb, RU.msk, DE.berlin (default: RU.spb).\n"
                        "Use '?' for interactive selection.")
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
    region = args.region
    if region == "?":
        region = interactive_region_select()
    CURRENT_REGION = region
    ensure_region_file(region)
    ensure_base_file(region)

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
        if not sw:
            print(f"    [!] Space weather fetch failed")

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

    # ✅ Вызов нужного режима — теперь ВНЕ цикла, выполняется один раз
    if args.mode == "fullscan":
        mode_fullscan(args)
    elif args.mode == "update":
        mode_update(args)
    elif args.mode == "edit":
        mode_edit(args)
    elif args.mode == "merge":
        mode_merge(args)

if __name__ == "__main__": main()
