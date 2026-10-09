#!/usr/bin/env python3
import json
import sys
from pathlib import Path

def calc_threshold(band: dict) -> float:
    name = band.get("name", "").lower()
    mod = band.get("modulation", "").upper()

    # HF: морские, авиа, фиксированные, любительские (SSB/CW) — слабые сигналы
    if any(k in name for k in ["hf-", "ham-", "marine", "acars", "volmet", "navtex"]):
        if "ssb" in mod or "usb" in mod or "lsb" in mod or mod == "CW":
            return -45.0
        # AM вещание на КВ — чуть сильнее
        if mod == "AM":
            return -35.0
        return -40.0

    # MF/LF вещание — средние уровни
    if any(k in name for k in ["mf-", "lf-", "broadcast"]):
        return -30.0 if mod == "AM" else -35.0

    # OIRT — сильные местные FM
    if "oirt" in name:
        return -5.0

    # FM-вещание (CCIR) — сильные, но с шумами
    if name == "fm-broadcast":
        return -10.0

    # Япония FM — аналогично
    if name == "fm-broadcast-jp":
        return -10.0

    # Авиадиапазон (AM) — умеренные
    if "air" in name or "121.5" in band.get("description", ""):
        return -15.0

    # Метеоспутники, NOAA — слабые
    if "sat-weather" in name or "noaa" in name.lower():
        return -25.0

    # Радиозонды — узкополосные, слабые
    if "radiosonde" in name.lower():
        return -20.0

    # ISM (433, 868, 2.4G, 5G) — разные уровни, ставим по типу
    if any(k in name for k in ["ism", "wifi", "bluetooth"]):
        if "2.4ghz" in name.lower() or "5ghz" in name.lower():
            return -15.0
        return -20.0

    # TETRA, LPD, PMR, UHF land mobile — служебные, разные уровни
    if any(k in name for k in ["tetra", "lpd", "pmr", "land-mobile", "uat"]):
        return -15.0

    # ТВ (аналоговое/цифровое) — мощные
    if any(k in name for k in ["tv", "dvb", "dab"]):
        return -10.0

    # Сотовые (GSM, UMTS, LTE) — мощные базовые станции
    if any(k in name for k in ["gsm", "umts", "lte"]):
        return -10.0

    # GNSS (GPS, ГЛОНАСС и т.п.) — слабые, но устойчивые
    if "gnss" in name.lower():
        return -25.0

    # CB 27 МГц — обычно сильные, но зависит от региона
    if name == "cb-27mhz":
        return -30.0

    # По умолчанию — нейтральный порог
    return -10.0

def main():
    if len(sys.argv) < 2:
        print("Использование: python fix_thresholds.py radio_bands_reference.json")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Файл не найден: {path}")
        sys.exit(1)

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    bands = data.get("bands", [])
    changed = 0
    for b in bands:
        old = b.get("threshold")
        new = calc_threshold(b)
        b["threshold"] = new
        if old != new:
            changed += 1

    out_path = path.with_suffix(".fixed.json")
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Готово: пороги проставлены для {len(bands)} диапазонов, изменено {changed}.")
    print(f"Результат сохранён в: {out_path}")

if __name__ == "__main__":
    main()

