#!/usr/bin/env python3
# core/bands.py
import os
import json
from typing import Dict, Any, List, Tuple, Optional
from pathlib import Path

# Глобальные константы (чтобы не тянуть из config в этом модуле)
FM_MIN = 87.5
FM_MAX = 108.0

class RadioBands:
    def __init__(self, config_path: str):
        path = Path(config_path)
        if not path.is_file():
            raise FileNotFoundError(f"Radio bands config not found: {config_path}")
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        # Ключевой момент: поддерживаем и список, и словарь
        raw_bands = data.get("bands")
        if raw_bands is None:
            # fallback: пустой список
            self.bands: List[Dict[str, Any]] = []
        elif isinstance(raw_bands, list):
            # Список диапазонов — самый частый случай
            self.bands = raw_bands
        elif isinstance(raw_bands, dict):
            # Если вдруг словарь — превращаем в список для единообразия
            self.bands = [v for v in raw_bands.values()]
        else:
            self.bands = []

    def get_band_for_freq(self, freq_mhz: float) -> Optional[Dict[str, Any]]:
        """Ищет диапазон, в который попадает частота (первый подходящий)."""
        for band in self.bands:
            # Поддерживаем оба варианта ключей: min_mhz/max_mhz или start/stop
            min_val = band.get("min_mhz") or band.get("start")
            max_val = band.get("max_mhz") or band.get("stop")
            if min_val is None or max_val is None:
                continue
            if float(min_val) <= freq_mhz <= float(max_val):
                return band
        return None

    def is_oirt(self, freq_mhz: float) -> bool:
        band = self.get_band_for_freq(freq_mhz)
        if band is None:
            return False
        # Проверяем по имени диапазона или флагу is_oirt
        name = band.get("name", "").upper()
        is_oirt_flag = band.get("is_oirt", False)
        return name == "OIRT" or is_oirt_flag


def snap_freq(freq_mhz: float, snap_khz: float) -> float:
    if snap_khz <= 0:
        return freq_mhz
    step_mhz = snap_khz / 1000.0
    return round(int(freq_mhz / step_mhz + 0.5) * step_mhz, 3)


def is_in_fm_range(freq: float) -> bool:
    return FM_MIN <= freq <= FM_MAX


def load_bands_reference(path: str = "regions/radio_bands_reference.json") -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Нормализуем: country вместо region, если нужно
    normalized = {}
    for k, v in data.items():
        country = v.get("country") or k
        normalized[country] = v
    return normalized


def get_scan_ranges_for_country(bands: Dict, country: str) -> List[Tuple[float, float]]:
    """Возвращает список (start, stop) в МГц для страны."""
    entry = bands.get(country)
    if not entry:
        return [(FM_MIN, FM_MAX)]
    ranges = entry.get("ranges", [])
    result = []
    for r in ranges:
        s = r.get("start", FM_MIN)
        e = r.get("stop", FM_MAX)
        result.append((float(s), float(e)))
    return result if result else [(FM_MIN, FM_MAX)]


def load_antennas(path: str = "config/antennas.json") -> Dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


