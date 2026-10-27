#!/usr/bin/env python3
# SDR_RTL_Receiver.py
# Отдельный приёмник: читает JSON-базу от сканера, применяет GQ-статус, локализацию и рисует панель.
# Не трогает логику сканирования и не ломает существующий проект.

import argparse
import json
from pathlib import Path
from enum import Enum

# Импорты из твоего существующего ядра (без изменений кода)
from core.gq_adapter import GQStatus, map_gps_status_to_gq
from core.loc_manager import LocManager

DATA_DIR = Path("data")
BASE_PATH = DATA_DIR / "SDR_Base_RU.spb.json"

class Colors:
    CLEAR = "\033[94m"       # голубой
    INDOOR = "\033[93m"      # жёлтый
    ANOMALY = "\033[91m"     # красный
    NO_GPS_HARDWARE = "\033[90m"  # тёмно‑серый
    RESET = "\033[0m"

def colorize_marker(status: GQStatus) -> str:
    colors = {
        GQStatus.CLEAR: Colors.CLEAR,
        GQStatus.INDOOR: Colors.INDOOR,
        GQStatus.ANOMALY: Colors.ANOMALY,
        GQStatus.NO_GPS_HARDWARE: Colors.NO_GPS_HARDWARE,
    }
    markers = {
        GQStatus.CLEAR: "✓",
        GQStatus.INDOOR: "⚠",
        GQStatus.ANOMALY: "✗",
        GQStatus.NO_GPS_HARDWARE: "⊘",
    }
    return f"{colors[status]}{markers[status]}{Colors.RESET}"

def load_last_entry():
    path = BASE_PATH
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries", [])
    return entries[-1] if entries else None

def render_receiver(lang: str):
    loc = LocManager()
    loc.load(lang)

    entry = load_last_entry()
    if not entry:
        print("Нет данных сканирования. Запустите сканер, чтобы заполнить базу.")
        return

    # GQ-статус уже сохранён в базе как строка: CLEAR/INDOOR/ANOMALY/NO_GPS_HARDWARE
    try:
        gq_status = GQStatus(entry["gq_status"])
    except ValueError:
        # На случай, если в старых записях нет gq_status — считаем INDOOR
        gq_status = GQStatus.INDOOR

    marker = colorize_marker(gq_status)

    rds_ok = (gq_status == GQStatus.CLEAR)
    gps_missing = (gq_status == GQStatus.NO_GPS_HARDWARE)

    print()
    print(f"{loc.get('panel_lf')} |===========| 0.1–3.0 МГц GQ: {marker}")
    print(f"{loc.get('panel_hf')} |----¦------| 100.5 МГц")
    print(f"{loc.get('panel_fm')} |===========| мГц Стерео: {loc.get('stereo_yes')}")
    print()

    print(f"{loc.get('meteo_prefix')}: {entry.get('meteo', 'нет данных')}")

    if gps_missing:
        print(f"{loc.get('gps_prefix')}: модуль GPS отсутствует или недоступен")
    else:
        gps_raw = entry.get("gps_raw", {})
        tpv = gps_raw.get("tpv")
        metrics = None
        if tpv and "mode" in tpv:
            mode = tpv["mode"]
            metrics = gps_raw.get("metrics", {})
            seen = metrics.get("seen", "?")
            used = metrics.get("used", "?")
            print(f"{loc.get('gps_prefix')}: GPS активен, режим={mode}, спутников видно={seen}, используется={used}")
        else:
            print(f"{loc.get('gps_prefix')}: GPS активен, статус GQ учтён")

    stations = entry.get("stations", [])
    if stations:
        stations_str = ", ".join(s.get("name", "?") for s in stations)
        # Если очень длинный список — можно обрезать до N штук
        if len(stations_str) > 200:
            stations_str = stations_str[:197] + "..."
        print(f"{loc.get('stations_prefix')}: {stations_str}")
    else:
        print(f"{loc.get('stations_prefix')}: нет станций")

    print(f"RDS: {loc.get('rds_available') if rds_ok else loc.get('rds_unavailable')}")
    print()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SDR-RTL-Receiver: панель приёмника на базе JSON-сканов")
    parser.add_argument("--lang", default="ru_RU", help="Язык интерфейса (ru_RU / en_US)")
    args = parser.parse_args()
    render_receiver(args.lang)


