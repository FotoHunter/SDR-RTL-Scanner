# core/scanner.py
import os, sys, subprocess, time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import numpy as np

from config import DEFAULT_STEP, NARROW_RANGE_MHZ

def snap_freq(freq_mhz: float, snap_khz: float) -> float:
    if snap_khz <= 0:
        return freq_mhz
    step_mhz = snap_khz / 1000.0
    return round(int(freq_mhz / step_mhz + 0.5) * step_mhz, 3)

def split_scan_ranges(
    start_mhz: float,
    stop_mhz: float,
    bands: List[Dict[str, Any]],
    country: str = "",
    subregion: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Разбить диапазон сканирования на поддиапазоны по границам справочника."""
    if not bands:
        return [
            {
                "start_mhz": start_mhz,
                "stop_mhz": stop_mhz,
                "step_khz": DEFAULT_STEP,
                "band_name": "Unknown",
                "is_oirt": False,
                "modulation": "WFM",
            }
        ]

    start_khz = start_mhz * 1000
    stop_khz = stop_mhz * 1000

    overlapping = []
    for b in bands:
        if b["freq_min"] > stop_khz or b["freq_max"] < start_khz:
            continue
        b_country = b.get("country") or b.get("region")
        if b_country is not None and b_country != country:
            continue
        overlapping.append(b)
    overlapping.sort(key=lambda b: b["freq_min"])

    if not overlapping:
        return [
            {
                "start_mhz": start_mhz,
                "stop_mhz": stop_mhz,
                "step_khz": DEFAULT_STEP,
                "band_name": "Unknown",
                "is_oirt": False,
                "modulation": "WFM",
            }
        ]

    sub_ranges = []
    for b in overlapping:
        sub_start = max(start_khz, b["freq_min"]) / 1000.0
        sub_stop = min(stop_khz, b["freq_max"]) / 1000.0
        if sub_start >= sub_stop:
            continue
        is_oirt = "OIRT" in b.get("name", "").upper()
        sub_ranges.append(
            {
                "start_mhz": sub_start,
                "stop_mhz": sub_stop,
                "step_khz": b.get("step", DEFAULT_STEP * 1000) / 1000.0,
                "band_name": b.get("name", "Unknown"),
                "is_oirt": is_oirt,
                "modulation": b.get("modulation", "WFM"),
                "threshold": b.get("threshold", None),
            }
        )


    # Добавляем перекрытие на стыках: каждый диапазон расширяется на 2 шага в обе стороны
    OVERLAP_STEPS = 2
    for sr in sub_ranges:
        overlap_mhz = (sr["step_khz"] * OVERLAP_STEPS) / 1000.0
        sr["start_mhz"] = max(start_mhz, sr["start_mhz"] - overlap_mhz)
        sr["stop_mhz"] = min(stop_mhz, sr["stop_mhz"] + overlap_mhz)

    # Заполняем дыры между диапазонами
    filled = []
    cursor = start_mhz
    for sr in sorted(sub_ranges, key=lambda x: x["start_mhz"]):
        # Если после перекрытия предыдущий диапазон уже покрывает курсор — не добавляем дыру
        if sr["start_mhz"] > cursor + 0.001:
            # Проверяем, не перекрыт ли уже этот участок соседями
            if not filled or filled[-1]["stop_mhz"] < sr["start_mhz"] - 0.001:
                filled.append(
                    {
                        "start_mhz": cursor,
                        "stop_mhz": sr["start_mhz"],
                        "step_khz": DEFAULT_STEP,
                        "band_name": "Gap",
                        "is_oirt": False,
                        "modulation": "WFM",
                    }
                )
        filled.append(sr)
        cursor = max(cursor, sr["stop_mhz"])

    if cursor < stop_mhz - 0.001:
        last_step = sub_ranges[-1]["step_khz"] if sub_ranges else DEFAULT_STEP
        filled.append(
            {
                "start_mhz": cursor,
                "stop_mhz": stop_mhz,
                "step_khz": last_step,
                "band_name": "Gap",
                "is_oirt": False,
                "modulation": "WFM",
            }
        )
    # Ограничение минимального шага для сканирования
    MIN_SCAN_STEP_KHZ = 1.0
    for sr in filled:
        if sr["step_khz"] < MIN_SCAN_STEP_KHZ:
            sr["step_khz"] = MIN_SCAN_STEP_KHZ
    return filled

def run_rtl_power(
    start_mhz: float,
    stop_mhz: float,
    step_khz: int,
    gain_db: int,
    ppm: int = 0,
    snap_khz: int = 0,
    mode: str = "fm",
    dwell_override: Optional[int] = None
) -> Dict[float, float]:

# Если snap больше шага — snap бессмысленен, отключаем
    if snap_khz > step_khz:
        snap_khz = 0

    start_snapped = snap_freq(start_mhz, snap_khz)
    stop_snapped = snap_freq(stop_mhz, snap_khz)
    freq_range = f"{start_snapped}M:{stop_snapped}M:{step_khz}k"
    range_mhz = stop_snapped - start_snapped
# ==================================
    bins_count = int((stop_mhz - start_mhz) * 1000 / step_khz)
    # RTL-SDR делает ~1 проход/сек (-i 1). Число бинов не влияет
    # на время прохода — только на детальность. Нам нужно
    # фиксированное число проходов для усреднения.
    if dwell_override is not None:
        dwell_sec = dwell_override
    elif range_mhz <= 0.2:
        dwell_sec = 20              # узкий КВ-кусок: 20 проходов
    elif range_mhz <= 2.0:
        dwell_sec = 30              # средний КВ-кусок: 30 проходов
    else:
        dwell_sec = max(bins_count * 2, 20)  # FM — как раньше
# ==================================

    cmd = [
        "rtl_power",
        "-f", freq_range,
        "-g", str(gain_db),
        "-p", str(ppm),
        "-i", "1",
        "-e", str(dwell_sec),
        "-F", "0",
    ]

    print(f"  [rtl_power] gain={gain_db} dB, range={start_snapped}-{stop_snapped} MHz, step={step_khz} kHz, mode={mode}, {dwell_sec}s ...")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=dwell_sec + 15
        )
    except subprocess.TimeoutExpired:
        print("[rtl_power] timeout!")
        return {}
    except FileNotFoundError:
        print("[!] rtl_power not found. Install: sudo apt install rtl-sdr")
        sys.exit(1)

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr:
            print(f"[rtl_power] stderr: {stderr[:200]}")
        return {}

    freq_power: Dict[float, float] = {}
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or not line[0].isdigit():
            continue
        parts = line.split(",")
        if len(parts) < 7:
            continue
        try:
            start_hz = float(parts[2].strip())
            step_hz = float(parts[4].strip())
        except (ValueError, IndexError):
            continue
        for i in range(6, len(parts)):
            try:
                pwr = float(parts[i].strip())
            except ValueError:
                continue
            freq = round((start_hz + (i - 6) * step_hz) / 1e6, 3)
            freq_snapped = snap_freq(freq, snap_khz) if snap_khz > 0 else freq
            if freq_snapped not in freq_power or pwr > freq_power[freq_snapped]:
                freq_power[freq_snapped] = pwr
    return freq_power

def multi_gain_scan(
    start_mhz: float,
    stop_mhz: float,
    step_khz: int,
    gains_str: str,
    threshold_db: float,
    ppm: int = 0,
    snap_khz: int = 0,
    antennas_config: Optional[Dict[str, Any]] = None
) -> List[Dict[str, float]]:
    if antennas_config is None:
        antennas_config = {}

    start_mhz = snap_freq(start_mhz, step_khz)
    stop_mhz = snap_freq(stop_mhz, step_khz)

    gains = [int(x.strip()) for x in gains_str.split(",")]

    # Подбор антенны по центральной частоте диапазона
    center_freq = (start_mhz + stop_mhz) / 2.0

    matched_antenna = None
    for freq_range, data in antennas_config.items():
        try:
            min_f, max_f = map(float, freq_range.split("-"))
            if min_f <= center_freq <= max_f:
                matched_antenna = data
                break
        except ValueError:
            continue

    if matched_antenna and "default_gain" in matched_antenna:
        custom_gain = matched_antenna["default_gain"]
        print(f"[*] Antenna profile matched for {center_freq:.1f} MHz: default gain={custom_gain} dB")
        if custom_gain not in gains:
            gains.insert(0, custom_gain)

    range_mhz = stop_mhz - start_mhz
    is_narrow = range_mhz < NARROW_RANGE_MHZ
    # Показываем реальный snap (после автоотключения в run_rtl_power)
    effective_snap = 0 if snap_khz > step_khz else snap_khz
    print(f"\n[*] Multi-gain scan: {gains} dB, threshold={threshold_db} dB")
    print(f"    Range: {start_mhz}-{stop_mhz} MHz, step={step_khz} kHz, ppm={ppm}, snap={effective_snap} kHz")

    if is_narrow:
        print(f"    [!] Narrow range ({range_mhz:.1f} MHz < {NARROW_RANGE_MHZ:.0f} MHz): auto-threshold may apply")
    print()

    all_fp: Dict[float, float] = {}
    for gain in gains:
        fp = run_rtl_power(start_mhz, stop_mhz, step_khz, gain, ppm, snap_khz)
        for freq, pwr in fp.items():
            if freq not in all_fp or pwr > all_fp[freq]:
                all_fp[freq] = pwr
        time.sleep(0.3)

    if all_fp:
        max_pwr, min_pwr = max(all_fp.values()), min(all_fp.values())
        auto_threshold = min_pwr + (max_pwr - min_pwr) * 0.3
        expected_bins = int((stop_mhz - start_mhz) * 1000 / step_khz) + 1

        print(f"  [scan] {len(all_fp)}/{expected_bins} bins, range: {min_pwr:.1f}..{max_pwr:.1f} dB")
        print(f"  [scan] noise floor ~{min_pwr:.1f} dB | auto threshold ~{auto_threshold:.1f} dB | your threshold: {threshold_db} dB")

        if len(all_fp) < expected_bins * 0.7:
            print(f"  [!] Warning: only {len(all_fp)}/{expected_bins} bins scanned — incomplete coverage!")
# ========================================
        if is_narrow:
            if threshold_db > max_pwr:
                effective_threshold = auto_threshold
                print(f"  [scan] User threshold {threshold_db:.1f} dB above max signal {max_pwr:.1f} dB, using auto threshold {auto_threshold:.1f} dB")
            else:
                effective_threshold = threshold_db
                print(f"  [scan] Using your threshold {effective_threshold:.1f} dB")
        else:
            effective_threshold = max(threshold_db, auto_threshold)
            if threshold_db < auto_threshold:
                print(f"  [scan] Using auto threshold {effective_threshold:.1f} dB (your {threshold_db} too low)")
# ========================================
    else:
        print("  [scan] No data from rtl_power!")
        return []

    candidates = [(f, p) for f, p in all_fp.items() if p >= effective_threshold]
    candidates.sort(key=lambda x: x[0])

    if not candidates:
        print(f"  [scan] No bins above {effective_threshold:.1f} dB.")
        return []

    stations = []
    group = []
    for freq, pwr in candidates:
        if group and (freq - group[-1][0]) > 0.2:
            best = max(group, key=lambda x: x[1])
            stations.append({"freq": best[0], "signal": best[1]})
            group = []
        group.append((freq, pwr))

    if group:
        best = max(group, key=lambda x: x[1])
        stations.append({"freq": best[0], "signal": best[1]})

    print(f"[*] Found {len(stations)} station(s):")
    for i, st in enumerate(stations):
        print(f"    {i+1}. {st['freq']:.1f} MHz  ({st['signal']:.1f} dB)")
    print()
    return stations

def bloom_split_scan(
    start_mhz: float,
    stop_mhz: float,
    step_khz: int,
    gains_str: str,
    threshold_db: float,
    ppm: int = 0,
    snap_khz: int = 0,
    antennas_config: Optional[Dict[str, Any]] = None,
    bloom_db: float = 15.0,
    min_sub_range_mhz: float = 2.0,
    overlap_mhz: float = 0.2
) -> List[Dict[str, float]]:
    """Pre-scan для поиска засветки, разбиение по сильным станциям, скан каждого куска."""

    range_mhz = stop_mhz - start_mhz

    # Если диапазон узкий — не разбиваем
    if range_mhz < 4.0:
        return multi_gain_scan(start_mhz, stop_mhz, step_khz, gains_str,
                               threshold_db, ppm, snap_khz, antennas_config)

    # ==================================================
    # ── 1. Быстрый pre-scan (gain=25, короткий) ──
    pre_bins = int((stop_mhz - start_mhz) * 1000 / step_khz)
    pre_dwell = max(pre_bins, 20)  # 1 проход на бин, минимум 20с
    print(f"\n[*] Pre-scan (bloom detection): {start_mhz}-{stop_mhz} MHz, gain=25, {pre_dwell}s ...")
    pre_fp = run_rtl_power(start_mhz, stop_mhz, step_khz, 25, ppm, snap_khz, dwell_override=pre_dwell)
    # ==================================================
    if not pre_fp or len(pre_fp) < 5:
        print("    Pre-scan: нет данных, сканируем целиком")
        return multi_gain_scan(start_mhz, stop_mhz, step_khz, gains_str,
                               threshold_db, ppm, snap_khz, antennas_config)

    # ── 2. Поиск сильных станций (источников засветки) ──
    all_pwr = sorted(pre_fp.values())
    noise_floor = all_pwr[len(all_pwr) // 4]  # нижняя четверть
    bloom_level = noise_floor + bloom_db

    # Группируем подряд идущие бины выше bloom_level
    strong_freqs = []
    group = []
    for freq in sorted(pre_fp.keys()):
        if pre_fp[freq] >= bloom_level:
            group.append((freq, pre_fp[freq]))
        else:
            if group:
                best = max(group, key=lambda x: x[1])
                strong_freqs.append(best[0])
                group = []
    if group:
        best = max(group, key=lambda x: x[1])
        strong_freqs.append(best[0])

    if not strong_freqs:
        print(f"    Bloom: не обнаружено (порог {bloom_level:.1f} dB, шум {noise_floor:.1f} dB)")
        return multi_gain_scan(start_mhz, stop_mhz, step_khz, gains_str,
                               threshold_db, ppm, snap_khz, antennas_config)

    print(f"    Bloom sources ({len(strong_freqs)}): "
          f"{', '.join(f'{f:.1f} MHz ({pre_fp[f]:.1f} dB)' for f in strong_freqs)}")

    # ── 3. Точки разбиения ──
    split_points = [start_mhz]
    for sf in strong_freqs:
        # Не разбиваем слишком близко к краям или друг к другу
        if sf - split_points[-1] >= min_sub_range_mhz and stop_mhz - sf >= min_sub_range_mhz:
            split_points.append(sf)
    split_points.append(stop_mhz)

    if len(split_points) <= 2:
        print("    Bloom: точки разбиения слишком близко, сканируем целиком")
        return multi_gain_scan(start_mhz, stop_mhz, step_khz, gains_str,
                               threshold_db, ppm, snap_khz, antennas_config)

    # ── 4. Скан каждого поддиапазона ──
    all_stations = []
    for i in range(len(split_points) - 1):
        sub_start = split_points[i]
        sub_stop = split_points[i + 1]
        # Перекрытие на стыках
        sub_start_adj = max(start_mhz, sub_start - overlap_mhz) if i > 0 else sub_start
        sub_stop_adj = min(stop_mhz, sub_stop + overlap_mhz) if i < len(split_points) - 2 else sub_stop

        print(f"\n[*] Bloom split {i+1}/{len(split_points)-1}: "
              f"{sub_start_adj:.1f}-{sub_stop_adj:.1f} MHz")

        sts = multi_gain_scan(sub_start_adj, sub_stop_adj, step_khz, gains_str,
                             threshold_db, ppm, snap_khz, antennas_config)
        all_stations.extend(sts)

    # ── 5. Дедупликация (станции на стыках) ──
    if len(all_stations) > 1:
        all_stations.sort(key=lambda x: x["freq"])
        deduped = [all_stations[0]]
        for st in all_stations[1:]:
            if abs(st["freq"] - deduped[-1]["freq"]) < 0.2:
                # Оставляем более сильную
                if st["signal"] > deduped[-1]["signal"]:
                    deduped[-1] = st
            else:
                deduped.append(st)
        all_stations = deduped

    print(f"\n[*] Bloom split: {len(split_points)-1} sub-ranges → {len(all_stations)} station(s) total")
    for i, st in enumerate(all_stations):
        print(f"    {i+1}. {st['freq']:.1f} MHz  ({st['signal']:.1f} dB)")
    print()

    return all_stations
