#!/usr/bin/env python3
# core/snr.py
import subprocess, select, json, config
#import select
#import json
import numpy as np
from typing import Optional, Tuple, Dict, Any, List
#from config import DEFAULT_START, DEFAULT_STOP, ANTENNAS_CONFIG, DEFAULT_RDS_RATE
from config import *
from .bands import RadioBands

# Инициализация один раз при старте приложения
BANDS_CONFIG = RadioBands("regions/radio_bands_reference.json")

def _is_oirt(freq_mhz: float) -> bool:
    band = BANDS_CONFIG.get_band_for_freq(freq_mhz)
    return band is not None and band.get("name") == "OIRT"

def _parse_rds_json(data: dict, result: dict) -> None:
    """Парсит JSON от redsea и заполняет словарь result."""
    if "pi" in data:
        result["PI"] = data["pi"]
    if "ps" in data and isinstance(data["ps"], str):
        result["PS"] = data["ps"].strip()
    if "prog_type" in data:
        result["PTY"] = data["prog_type"]
    if "stereo" in data:
        result["Stereo"] = data["stereo"]
    if "tp" in data:
        result["TP"] = data["tp"]
    if "ta" in data:
        result["TA"] = data["ta"]
    if "af" in data:
        result["AF"] = data["af"]
    if "radiotext" in data and isinstance(data["radiotext"], str):
        result["RadioText"] = data["radiotext"].strip()
    elif "rt" in data and isinstance(data["rt"], str):
        result["RadioText"] = data["rt"].strip()

def auto_ppm_calibrate_carrier(
    ref_freq_mhz: float,
    gain_db: int = -1,
    rate: int = DEFAULT_RDS_RATE,
    duration: float = 2.0,
    search_range_khz: float = 10.0
) -> Tuple[int, Optional[bool]]:
    """Калибровка PPM по несущей AM-станции (для КВ/HF/Air-band).
    
    Сканирует узкий диапазон вокруг ref_freq_mhz, находит пик несущей
    и вычисляет PPM. Не требует пилот-тона.
    """
    freq_hz = int(ref_freq_mhz * 1e6)
    search_lo = ref_freq_mhz - search_range_khz / 1000.0
    search_hi = ref_freq_mhz + search_range_khz / 1000.0

    cmd = [
        "rtl_power",
        "-f", f"{search_lo}M:{search_hi}M:0.5k",
        "-g", str(gain_db) if gain_db != -1 else "50",
        "-i", "1",
        "-e", str(int(duration) + 5),
        "-F", "0",
    ]

    print(f"  [rtl_power] carrier scan: {search_lo:.3f}-{search_hi:.3f} MHz, "
          f"step=0.5 kHz, {int(duration)+5}s ...")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=int(duration) + 20)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0, None

    if result.returncode != 0:
        return 0, None

    freq_power = {}
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
        for j in range(6, len(parts)):
            try:
                pwr = float(parts[j].strip())
            except ValueError:
                continue
            f = round((start_hz + (j - 6) * step_hz) / 1e6, 4)
            freq_power[f] = pwr

    if not freq_power:
        return 0, None

    peak_freq = max(freq_power, key=freq_power.get)
    peak_pwr = freq_power[peak_freq]

    # Оцениваем шум
    sorted_pwr = sorted(freq_power.values())
    noise_floor = sorted_pwr[len(sorted_pwr) // 4]  # нижняя четверть
    snr = peak_pwr - noise_floor if noise_floor > 0 else 0

    if snr < 5.0:
        print(f"    Carrier SNR too low ({snr:.1f} dB) — skipping")
        return 0, None

    ppm = round((peak_freq / ref_freq_mhz - 1) * 1e6)

    print(f"    Carrier peak: {peak_freq:.4f} MHz (ref: {ref_freq_mhz:.4f}), "
          f"SNR={snr:.1f} dB → PPM = {ppm}")

    return ppm, True

def auto_ppm_calibrate(freq_mhz: float, gain_db: int = -1, rate: int = DEFAULT_RDS_RATE, duration: float = 2.0) -> Tuple[int, Optional[bool]]:
    """Калибровка PPM по пилот-тону: 19 кГц (FM) или 31.25 кГц (OIRT)."""
    freq_hz = int(freq_mhz * 1e6)
    is_oirt = _is_oirt(freq_mhz)
    ref_freq = 31250.0 if is_oirt else 19000.0
    search_lo = ref_freq - 500
    search_hi = ref_freq + 500

    cmd_fm = [
        "rtl_fm", "-f", str(freq_hz), "-s", str(rate), "-r", str(rate),
        "-M", "fm", "-A", "std", "-F", "9"
    ]
    if gain_db != -1:
        cmd_fm.extend(["-g", str(gain_db)])

    try:
        proc = subprocess.Popen(cmd_fm, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return 0, None

    n_bytes = int(rate * duration) * 2
    audio = b""
    while len(audio) < n_bytes:
        chunk = proc.stdout.read(min(8192, n_bytes - len(audio)))
        if not chunk:
            break
        audio += chunk
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()

    # ИСПРАВЛЕНИЕ: анализ выполняется, если данных достаточно
    if len(audio) < n_bytes:
        return 0, None

    samples = np.frombuffer(audio, dtype=np.int16).astype(np.float64)
    win = np.hanning(len(samples))
    fft = np.fft.rfft(samples * win)
    freqs = np.fft.rfftfreq(len(samples), 1.0 / rate)
    power = np.abs(fft) ** 2

    pilot_mask = (freqs >= search_lo) & (freqs <= search_hi)
    if not np.any(pilot_mask):
        return 0, None

    pilot_freqs = freqs[pilot_mask]
    pilot_power = power[pilot_mask]
    peak_idx = np.argmax(pilot_power)

    noise_mask = (
        (freqs >= 2000) & (freqs <= 10000) if is_oirt
        else (freqs >= 15000) & (freqs <= 17000)
    )
    noise_pwr = np.mean(power[noise_mask]) if np.any(noise_mask) else 1e-10
    pilot_snr = 10 * np.log10(pilot_power[peak_idx] / noise_pwr) if noise_pwr > 0 else 0

    stereo_threshold = 10.0
    if pilot_snr < stereo_threshold:
        return 0, False

    # Параболическая интерполяция
    if 0 < peak_idx < len(pilot_power) - 1:
        alpha = pilot_power[peak_idx - 1]
        beta = pilot_power[peak_idx]
        gamma = pilot_power[peak_idx + 1]
        denom = alpha - 2 * beta + gamma

        if denom != 0:
            p = 0.5 * (alpha - gamma) / denom
            bin_width = freqs[1] - freqs[0]  # ← ИСПРАВЛЕНО: ширина одного бина
            peak_freq = pilot_freqs[peak_idx] + p * bin_width
        else:
            peak_freq = pilot_freqs[peak_idx]
    else:
        peak_freq = pilot_freqs[peak_idx]

    peak_val = float(peak_freq)  # ← скаляр, .item() не нужен
    ppm = round((peak_val / ref_freq - 1) * 1e6)

    return ppm, True

def check_rds_snr(freq_mhz: float, gain_db: int, ppm: int, rate: int = DEFAULT_RDS_RATE, duration: float = 1.0) -> Tuple[Optional[float], Optional[bool]]:
    freq_hz = int(freq_mhz * 1e6)
    is_oirt = _is_oirt(freq_mhz)

    cmd_fm = [
        "rtl_fm", "-f", str(freq_hz), "-s", str(rate), "-r", str(rate),
        "-M", "fm", "-A", "std", "-F", "9"
    ]
    if gain_db != -1:
        cmd_fm.extend(["-g", str(gain_db)])
    if ppm != 0:
        cmd_fm.extend(["-p", str(ppm)])

    try:
        proc = subprocess.Popen(cmd_fm, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return None, None

    n_bytes = int(rate * duration) * 2
    audio = b""
    while len(audio) < n_bytes:
        chunk = proc.stdout.read(min(8192, n_bytes - len(audio)))
        if not chunk:
            break
        audio += chunk
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()

    if len(audio) < n_bytes:
        return None, None

    samples = np.frombuffer(audio, dtype=np.int16).astype(np.float64)
    win = np.hanning(len(samples))
    fft = np.fft.rfft(samples * win)
    freqs = np.fft.rfftfreq(len(samples), 1.0 / rate)
    power = np.abs(fft) ** 2

    if is_oirt:
        pilot_mask = (freqs >= 30750) & (freqs <= 31750)
        noise_mask = (freqs >= 35000) & (freqs <= 45000)
        pilot_pwr = np.max(power[pilot_mask]) if np.any(pilot_mask) else 0
        noise_pwr = np.median(power[noise_mask]) if np.any(noise_mask) else 1e-10
        pilot_snr = 10 * np.log10(pilot_pwr / noise_pwr) if noise_pwr > 0 else 0
        is_stereo = bool(pilot_snr > 10)
        return None, is_stereo
    else:
        rds_mask = (freqs >= 54600) & (freqs <= 59400)
        noise_mask = (freqs >= 68000) & (freqs <= 78000)
        rds_pwr = np.mean(power[rds_mask]) if np.any(rds_mask) else 0
        noise_pwr = np.mean(power[noise_mask]) if np.any(noise_mask) else 1e-10
        if noise_pwr <= 0:
            return None, None
        snr = 10 * np.log10(rds_pwr / noise_pwr)

        pilot_mask = (freqs >= 18500) & (freqs <= 19500)
        pilot_pwr = np.max(power[pilot_mask]) if np.any(pilot_mask) else 0
        pilot_snr = 10 * np.log10(pilot_pwr / noise_pwr) if noise_pwr > 0 else 0
        is_stereo = bool(pilot_snr > 10)
        return round(snr, 1), is_stereo


def decode_rds(freq_mhz: float, gain_db: int, ppm: int, dwell_sec: float, rate: int = DEFAULT_RDS_RATE) -> Dict[str, Any]:
    freq_hz = int(freq_mhz * 1e6)
    cmd_fm = [
        "rtl_fm", "-f", str(freq_hz), "-s", str(rate), "-r", str(rate),
        "-M", "fm", "-A", "std", "-F", "9"
    ]
    if gain_db != -1:
        cmd_fm.extend(["-g", str(gain_db)])
    if ppm != 0:
        cmd_fm.extend(["-p", str(ppm)])
    cmd_rs = ["redsea", "-r", str(rate)]

    g_str = "auto" if gain_db == -1 else f"{gain_db} dB"
    print(f"  [rtl_fm] {freq_mhz:.1f} MHz, gain={g_str}, ppm={ppm}, {rate} Hz, {dwell_sec}s ...")

    try:
        proc_fm = subprocess.Popen(cmd_fm, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        proc_rs = subprocess.Popen(cmd_rs, stdin=proc_fm.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        proc_fm.stdout.close()
    except FileNotFoundError as e:
        print(f"  [!] Command not found: {e}")
        return {}

    result = {
        "PI": None, "PS": None, "PTY": None, "Stereo": None,
        "TP": None, "TA": None, "AF": None, "RadioText": None
    }

# ==================================================================
    elapsed, chunk_timeout = 0, 0.5
    next_progress = 10
    while elapsed < dwell_sec:
        ready, _, _ = select.select([proc_rs.stdout], [], [], chunk_timeout)
        if ready:
            line = proc_rs.stdout.readline()
            if not line:
                break
            try:
                data = json.loads(line)
                _parse_rds_json(data, result)
            except json.JSONDecodeError:
                continue
        elapsed += chunk_timeout
        if elapsed >= next_progress:
            pi_str = f"PI={result['PI']}" if result.get("PI") else "no PI"
            ps_str = f" | PS={result['PS']}" if result.get("PS") else ""
            print(f"    [{int(elapsed)}s] {pi_str}{ps_str}", flush=True)
            next_progress += 10
# ==================================================================
    proc_fm.terminate()
    for proc in (proc_rs, proc_fm):
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    # Дочитываем остатки
    try:
        while True:
            ready, _, _ = select.select([proc_rs.stdout], [], [], 0.1)
            if not ready:
                break
            line = proc_rs.stdout.readline()
            if not line:
                break
            try:
                _parse_rds_json(json.loads(line), result)
            except Exception:
                pass
    except Exception:
        pass

    if result["PI"]:
        return {k: v for k, v in result.items() if v is not None}
    return {}


def decode_rds_multi(freq_mhz: float, rds_gains: List[int], ppm: int, dwell_sec: float, rate: int = DEFAULT_RDS_RATE) -> Tuple[Dict[str, Any], int, Optional[float], Optional[bool]]:
    is_oirt = _is_oirt(freq_mhz)

    best_snr: Optional[float] = None
    best_gain: int = rds_gains[0] if rds_gains else -1
    best_stereo: Optional[bool] = None

    stereo_votes: List[bool] = []
    snr_list: List[Optional[float]] = []

    for gain in rds_gains:
        snr, stereo = check_rds_snr(freq_mhz, gain, ppm, rate)
        g_str = "auto" if gain == -1 else f"{gain} dB"
        if snr is not None:
            ster_str = f" | {C_GREEN}STEREO{C_RESET}" if stereo else " | mono"
            print(f"    [SNR] gain={g_str} → RDS 57kHz SNR = {snr} dB{ster_str}")
            snr_list.append(snr)
            stereo_votes.append(stereo if stereo is not None else False)
            if best_snr is None or snr > best_snr:
                best_snr = snr
                best_gain = gain
                best_stereo = stereo
        else:
            # OIRT: snr=None, но stereo может быть определён
            if stereo is not None:
                ster_str = f" | {C_GREEN}STEREO{C_RESET}" if stereo else " | mono"
                if is_oirt:
                    print(f"    [SNR] gain={g_str} → OIRT 31.25kHz{ster_str}")
                    stereo_votes.append(stereo)
                    # Для OIRT берём первый gain, где есть стерео, или оставляем текущий
                    if best_stereo is None or (stereo and not best_stereo):
                        best_stereo = stereo
                        best_gain = gain
                else:
                    print(f"    [SNR] gain={g_str} → no data")
                    stereo_votes.append(False)
            else:
                stereo_votes.append(False)

    # Логика большинства для стерео
    if stereo_votes:
        stereo_majority = sum(stereo_votes) >= len(stereo_votes) / 2
        if best_stereo is not None and stereo_majority != best_stereo:
            best_stereo = stereo_majority

    if is_oirt:
        return {}, best_gain, best_snr, best_stereo

    # ── Декодирование RDS: до 3 попыток ──
    # 1. Лучший gain по SNR
    # 2. Gain 25 dB (на практике — самый успешный для redsea)
    # 3. Auto-gain (если ещё не пробовали)

    tried = set()
    attempts = []

    # 1. Лучший gain
    attempts.append(best_gain)
    tried.add(best_gain)

    # 2. Gain 25 — статистически чаще всего декодирует RDS
    if 25 not in tried and 25 in rds_gains:
        attempts.append(25)
        tried.add(25)

    # 3. Auto-gain
    if -1 not in tried and -1 in rds_gains:
        attempts.append(-1)
        tried.add(-1)

    for gain in attempts:
        rds = decode_rds(freq_mhz, gain, ppm, dwell_sec, rate)
        if rds:
            snr, stereo = check_rds_snr(freq_mhz, gain, ppm, rate)
            rds["gain_db"] = gain
            rds["rds_snr_db"] = snr if snr is not None else 0.0
            rds["stereo"] = stereo if stereo is not None else False
            return rds, gain, snr, stereo

    return {}, best_gain, best_snr, best_stereo

if __name__ == "__main__":
    # Внимание: этот тест реально запустит rtl_fm и потребует подключенного донгла!
    # Для сухого прогона лучше мокать subprocess, но для проверки железа так быстрее.
    print("Testing PPM calibration on 100.0 MHz...")
    ppm, success = auto_ppm_calibrate(freq_mhz=100.0, gain_db=25, duration=1.0)
    print(f"Result: PPM={ppm}, Success={success}")

