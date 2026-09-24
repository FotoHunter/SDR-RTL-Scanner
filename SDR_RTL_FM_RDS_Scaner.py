#!/usr/bin/env python3

"""
FM RDS Scanner v0.8 — RTL-SDR (rtl_power + rtl_fm + redsea)

Universal FM scanner with RDS decoding and multi-region support.

Modes:
  fullscan  — scan range with rtl_power, decode RDS with rtl_fm|redsea
  update    — re-decode RDS for stations from existing base
  edit      — manual editing of name_ru in JSON
  merge     — merge Update file into Base, show differences

Files:
  Regions:  regions/{region}.json (station reference, per region)
  Base:     data/SDR_FM_RDS_Base_{region}.json
  Update:   data/SDR_FM_RDS_Update_{region}_YYYY.MM.DD_hh.mm.json

Author: Andrey E. Smirnov
Email: aes222ripn@gmail.com
License: MIT
"""

import os, sys, json, argparse, subprocess, select, time
from pathlib import Path
from datetime import datetime
from src.context import Context

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR      = Path(__file__).parent
REGIONS_DIR     = SCRIPT_DIR / "regions"
DATA_DIR        = SCRIPT_DIR / "data"
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
DEFAULT_START    = 87.0
DEFAULT_STOP     = 109.0
DEFAULT_STEP     = 100      # kHz
DEFAULT_THRESHOLD = -25.0   # dB (auto-adjusted if too low)
DEFAULT_PPM      = 0
DEFAULT_DWELL    = 20       # seconds per station for RDS
DEFAULT_GAINS    = "0,10,25,50"
DEFAULT_RDS_GAIN = 50
DEFAULT_RDS_GAINS = "-1,50,25,10,0"
DEFAULT_RDS_RATE = 228000   # Hz
DEFAULT_SNAP     = 100      # kHz
FM_MIN, FM_MAX   = 87.5, 108.0
NARROW_RANGE_MHZ = 5.0
# ── ANSI colors ──
C_RED    = '\033[91m'
C_GREEN  = '\033[92m'
C_BLUE   = '\033[94m'
C_YELLOW = '\033[93m'
C_RESET  = '\033[0m'

# Выбираем антенну по умолчанию (первый ключ из справочника)
_default_antenna = ANTENNAS_CONFIG[list(ANTENNAS_CONFIG.keys())[0]] if ANTENNAS_CONFIG else {}
app_context = Context(OBSERVER_CONFIG, _default_antenna)
print(f"[*] Context ready. Observer: {app_context.get_observer_name()}, Antenna: {app_context.get_antenna_model()}")

# Устанавливаются в main() после выбора региона
CURRENT_REGION  = "default"
BASE_FILE       = ""
STATIONS_FILE   = ""

def get_base_file(region=None):
    r = region or CURRENT_REGION
    return str(DATA_DIR / f"SDR_FM_RDS_Base_{r}.json")

def get_update_file(region=None):
    r = region or CURRENT_REGION
    ts = datetime.now().strftime("%Y.%m.%d_%H.%M")
    return str(DATA_DIR / f"SDR_FM_RDS_Update_{r}_{ts}.json")

def get_stations_file(region=None):
    r = region or CURRENT_REGION
    return str(REGIONS_DIR / f"{r}.json")

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
    # Создаём пустую базу
    data = {"schema_version": 1, "region": region,
            "scan_date": datetime.now().isoformat(), "mode": "init",
            "stations": []}
    with open(base, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[*] Создана база: {base}")
    # ── Предупреждение о шаге сетки ──
    if args.step == 200:
        print(f"[*] Step=200 kHz — US/FCC channel spacing (88.1, 88.3, 88.5 ...)")
    elif args.step == 100:
        print(f"[*] Step=100 kHz — Europe/Russia channel spacing (87.5, 87.6, 87.7 ...)")


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

def snap_freq(freq_mhz, snap_khz):
    if snap_khz <= 0: return freq_mhz
    step_mhz = snap_khz / 1000.0
    return round(int(freq_mhz / step_mhz + 0.5) * step_mhz, 3)

def is_in_fm_range(freq):
    return FM_MIN <= freq <= FM_MAX

def make_update_filename():
    return get_update_file()

# ═══════════════════════════════════════════════════════════════
#  RTL_POWER SCANNING
# ═══════════════════════════════════════════════════════════════

def run_rtl_power(start_mhz, stop_mhz, step_khz, gain_db, ppm=0, snap_khz=0):
    start_snapped = snap_freq(start_mhz, snap_khz)
    stop_snapped  = snap_freq(stop_mhz,  snap_khz)
    freq_range = f"{start_snapped}M:{stop_snapped}M:{step_khz}k"
    range_mhz = stop_snapped - start_snapped
    dwell_sec = max(int(range_mhz * 3) + 5, 10)
    cmd = ["rtl_power", "-f", freq_range, "-g", str(gain_db), "-p", str(ppm),
           "-i", "1", "-e", str(dwell_sec), "-F", "0"]
    print(f"  [rtl_power] gain={gain_db} dB, range={start_snapped}-{stop_snapped} MHz, step={step_khz} kHz, {dwell_sec}s ...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=dwell_sec + 15)
    except subprocess.TimeoutExpired:
        print("  [rtl_power] timeout!"); return {}
    except FileNotFoundError:
        print("[!] rtl_power not found. Install: sudo apt install rtl-sdr"); sys.exit(1)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr: print(f"  [rtl_power] stderr: {stderr[:200]}")
        return {}
    freq_power = {}
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or not line[0].isdigit(): continue
        parts = line.split(",")
        if len(parts) < 7: continue
        try:
            start_hz = float(parts[2].strip())
            step_hz  = float(parts[4].strip())
        except (ValueError, IndexError): continue
        for i in range(6, len(parts)):
            try:
                pwr = float(parts[i].strip())
            except ValueError: continue
            freq = round((start_hz + (i - 6) * step_hz) / 1e6, 3)
            freq_snapped = snap_freq(freq, snap_khz) if snap_khz > 0 else freq
            if freq_snapped not in freq_power or pwr > freq_power[freq_snapped]:
                freq_power[freq_snapped] = pwr
    return freq_power

def multi_gain_scan(start_mhz, stop_mhz, step_khz, gains_str, threshold_db, ppm=0, snap_khz=0):
    # ── Авто-выравнивание границ на сетку шага ──
    start_mhz = snap_freq(start_mhz, step_khz)
    stop_mhz  = snap_freq(stop_mhz,  step_khz)
    gains = [int(x.strip()) for x in gains_str.split(",")]

    # --- НОВЫЙ БЛОК: Корректировка усиления по антенне ---
    # Если в antennas.json есть запись для диапазона частот, используем её
    # Пример структуры antennas.json: {"87.5-108.0": {"default_gain": 25}}
    
    center_freq = (start_mhz + stop_mhz) / 2.0
    
    # Ищем подходящую антенну в конфиге
    matched_antenna = None
    for freq_range, data in ANTENNAS_CONFIG.items():
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
        # Можно заменить весь список gains на один оптимальный, если нужно
        # gains = [custom_gain] 
        # Или добавить его в начало списка для приоритета
        if custom_gain not in gains:
            gains.insert(0, custom_gain)
    # -------------------------------------------------------
    range_mhz = stop_mhz - start_mhz
    is_narrow = range_mhz < NARROW_RANGE_MHZ
    print(f"\n[*] Multi-gain scan: {gains} dB, threshold={threshold_db} dB")
    print(f"    Range: {start_mhz}-{stop_mhz} MHz, step={step_khz} kHz, ppm={ppm}, snap={snap_khz} kHz")
    if is_narrow:
        print(f"    [!] Narrow range ({range_mhz:.1f} MHz < {NARROW_RANGE_MHZ:.0f} MHz): using user threshold, auto-threshold skipped")
        print(f"    [!] Weak stations may be missed — set --threshold manually if needed")
    print()
    all_fp = {}
    for gain in gains:
        fp = run_rtl_power(start_mhz, stop_mhz, step_khz, gain, ppm, snap_khz)
        for freq, pwr in fp.items():
            if freq not in all_fp or pwr > all_fp[freq]: all_fp[freq] = pwr
        time.sleep(0.3)
    if all_fp:
        max_pwr, min_pwr = max(all_fp.values()), min(all_fp.values())
        auto_threshold = min_pwr + (max_pwr - min_pwr) * 0.3
        expected_bins = int((stop_mhz - start_mhz) * 1000 / step_khz) + 1
        print(f"  [scan] {len(all_fp)}/{expected_bins} bins, range: {min_pwr:.1f}..{max_pwr:.1f} dB")
        print(f"  [scan] noise floor ~{min_pwr:.1f} dB | auto threshold ~{auto_threshold:.1f} dB | your threshold: {threshold_db} dB")
        if len(all_fp) < expected_bins * 0.7:
            print(f"  [!] Warning: only {len(all_fp)}/{expected_bins} bins scanned — incomplete coverage!")
        if is_narrow:
            effective_threshold = threshold_db
            print(f"  [scan] Using your threshold {effective_threshold:.1f} dB (narrow range, auto skipped)")
        else:
            effective_threshold = max(threshold_db, auto_threshold)
            if threshold_db < auto_threshold:
                print(f"  [scan] Using auto threshold {effective_threshold:.1f} dB (your {threshold_db} too low)")
    else:
        print("  [scan] No data from rtl_power!"); return []
    candidates = [(f, p) for f, p in all_fp.items() if p >= effective_threshold]
    candidates.sort(key=lambda x: x[0])
    if not candidates:
        print(f"  [scan] No bins above {effective_threshold:.1f} dB.")
        return []
    stations, group = [], []
    for freq, pwr in candidates:
        if group and (freq - group[-1][0]) > 0.2:
            best = max(group, key=lambda x: x[1])
            stations.append({"freq": best[0], "signal": best[1]}); group = []
        group.append((freq, pwr))
    if group:
        best = max(group, key=lambda x: x[1])
        stations.append({"freq": best[0], "signal": best[1]})
    print(f"[*] Found {len(stations)} station(s):")
    for i, st in enumerate(stations):
        name = lookup_name_ru(st["freq"]) or "?"
        print(f"    {i+1}. {st['freq']:.1f} MHz  ({st['signal']:.1f} dB)  [{name}]")
    print()
    return stations

# ═══════════════════════════════════════════════════════════════
#  RDS SNR CHECK
# ═══════════════════════════════════════════════════════════════

def auto_ppm_calibrate(freq_mhz, gain_db=-1, rate=DEFAULT_RDS_RATE, duration=2.0):
    """Калибровка PPM по пилот-тону 19 кГц. Возвращает (ppm, is_stereo)."""
    freq_hz = int(freq_mhz * 1e6)
    cmd_fm = ["rtl_fm", "-f", str(freq_hz), "-s", str(rate), "-r", str(rate),
              "-M", "fm", "-A", "std", "-F", "9"]
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
        if not chunk: break
        audio += chunk
    proc.terminate()
    try: proc.wait(timeout=2)
    except subprocess.TimeoutExpired: proc.kill()
    if len(audio) < n_bytes: return 0, None
    try:
        import numpy as np
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float64)
        win = np.hanning(len(samples))
        fft = np.fft.rfft(samples * win)
        freqs = np.fft.rfftfreq(len(samples), 1.0 / rate)
        power = np.abs(fft) ** 2
        # Поиск пилот-тона 19 кГц
        pilot_mask = (freqs >= 18500) & (freqs <= 19500)
        if not np.any(pilot_mask): return 0, None
        pilot_freqs = freqs[pilot_mask]
        pilot_power = power[pilot_mask]
        peak_idx = np.argmax(pilot_power)
        # Уровень шума для сравнения (15-17 кГц)
        noise_mask = (freqs >= 15000) & (freqs <= 17000)
        noise_pwr = np.mean(power[noise_mask]) if np.any(noise_mask) else 1e-10
        pilot_snr = 10 * np.log10(pilot_power[peak_idx] / noise_pwr) if noise_pwr > 0 else 0
        if pilot_snr < 10:
            return 0, False  # Пилот-тона нет = моно
        # Параболическая интерполяция для суббинной точности
        if 0 < peak_idx < len(pilot_power) - 1:
            alpha = pilot_power[peak_idx - 1]
            beta  = pilot_power[peak_idx]
            gamma = pilot_power[peak_idx + 1]
            p = 0.5 * (alpha - gamma) / (alpha - 2 * beta + gamma)
            bin_width = pilot_freqs[1] - pilot_freqs[0]
            peak_freq = pilot_freqs[peak_idx] + p * bin_width
        else:
            peak_freq = pilot_freqs[peak_idx]
        ppm = round((peak_freq / 19000 - 1) * 1e6)
        return ppm, True
    except Exception:
        return 0, None

def check_rds_snr(freq_mhz, gain_db, ppm, rate=DEFAULT_RDS_RATE, duration=1.0):
    freq_hz = int(freq_mhz * 1e6)
    cmd_fm = ["rtl_fm", "-f", str(freq_hz), "-s", str(rate), "-r", str(rate),
              "-M", "fm", "-A", "std", "-F", "9"]
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
        if not chunk: break
        audio += chunk
    proc.terminate()
    try: proc.wait(timeout=2)
    except subprocess.TimeoutExpired: proc.kill()
    if len(audio) < n_bytes: return None, None
    try:
        import numpy as np
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float64)
        win = np.hanning(len(samples))
        fft = np.fft.rfft(samples * win)
        freqs = np.fft.rfftfreq(len(samples), 1.0 / rate)
        power = np.abs(fft) ** 2
        # RDS SNR (57 кГц)
        rds_mask = (freqs >= 54600) & (freqs <= 59400)
        noise_mask = (freqs >= 68000) & (freqs <= 78000)
        rds_pwr = np.mean(power[rds_mask]) if np.any(rds_mask) else 0
        noise_pwr = np.mean(power[noise_mask]) if np.any(noise_mask) else 1e-10
        if noise_pwr <= 0: return None, None
        snr = 10 * np.log10(rds_pwr / noise_pwr)
        # Стерео по пилот-тону 19 кГц (в том же FFT!)
        pilot_mask = (freqs >= 18500) & (freqs <= 19500)
        pilot_pwr = np.max(power[pilot_mask]) if np.any(pilot_mask) else 0
        pilot_snr = 10 * np.log10(pilot_pwr / noise_pwr) if noise_pwr > 0 else 0
        is_stereo = pilot_snr > 10
        return round(snr, 1), is_stereo
    except Exception:
        return None, None

# ═══════════════════════════════════════════════════════════════
#  RDS DECODING
# ═══════════════════════════════════════════════════════════════

def _parse_rds_json(data, result):
    if "pi" in data: result["PI"] = data["pi"]
    if "ps" in data and isinstance(data["ps"], str): result["PS"] = data["ps"].strip()
    if "prog_type" in data: result["PTY"] = data["prog_type"]
    if "stereo" in data: result["Stereo"] = data["stereo"]
    if "tp" in data: result["TP"] = data["tp"]
    if "ta" in data: result["TA"] = data["ta"]
    if "af" in data: result["AF"] = data["af"]
    if "radiotext" in data and isinstance(data["radiotext"], str): result["RadioText"] = data["radiotext"].strip()
    elif "rt" in data and isinstance(data["rt"], str): result["RadioText"] = data["rt"].strip()

def decode_rds(freq_mhz, gain_db, ppm, dwell_sec, rate=DEFAULT_RDS_RATE):
    freq_hz = int(freq_mhz * 1e6)
    cmd_fm = ["rtl_fm", "-f", str(freq_hz), "-s", str(rate), "-r", str(rate),
              "-M", "fm", "-A", "std", "-F", "9"]
    if gain_db != -1:
        cmd_fm.extend(["-g", str(gain_db)])
    if ppm != 0:
        cmd_fm.extend(["-p", str(ppm)])
    cmd_rs = ["redsea", "-r", str(rate)]
    g_str = "auto" if gain_db == "auto" else f"{gain_db} dB"
    print(f"  [rtl_fm] {freq_mhz:.1f} MHz, gain={g_str}, ppm={ppm}, {rate} Hz, {dwell_sec}s ...")
    try:
        proc_fm = subprocess.Popen(cmd_fm, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        proc_rs = subprocess.Popen(cmd_rs, stdin=proc_fm.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        proc_fm.stdout.close()
    except FileNotFoundError as e:
        print(f"  [!] Command not found: {e}"); return {}
    result = {"PI": None, "PS": None, "PTY": None, "Stereo": None,
              "TP": None, "TA": None, "AF": None, "RadioText": None}
    elapsed, chunk_timeout = 0, 0.5
    while elapsed < dwell_sec:
        ready, _, _ = select.select([proc_rs.stdout], [], [], chunk_timeout)
        if ready:
            line = proc_rs.stdout.readline()
            if not line: break
            try: data = json.loads(line)
            except json.JSONDecodeError: continue
            _parse_rds_json(data, result)
        elapsed += chunk_timeout
    proc_fm.terminate()
    try: proc_rs.wait(timeout=2)
    except subprocess.TimeoutExpired: proc_rs.kill()
    try: proc_fm.wait(timeout=2)
    except subprocess.TimeoutExpired: proc_fm.kill()
    try:
        while True:
            ready, _, _ = select.select([proc_rs.stdout], [], [], 0.1)
            if not ready: break
            line = proc_rs.stdout.readline()
            if not line: break
            try: _parse_rds_json(json.loads(line), result)
            except: pass
    except: pass
    if result["PI"]: return {k: v for k, v in result.items() if v is not None}
    return {}

def decode_rds_multi(freq_mhz, rds_gains, ppm, dwell_sec, rate=DEFAULT_RDS_RATE):
    best_snr, best_gain, best_stereo = None, rds_gains[0], None
    for gain in rds_gains:
        snr, stereo = check_rds_snr(freq_mhz, gain, ppm, rate)
        g_str = "auto" if gain == -1 else f"{gain} dB"
        if snr is not None:
            ster_str = ""
            if stereo is not None:
                ster_str = f" | {C_GREEN}STEREO{C_RESET}" if stereo else f" | mono"
            print(f"    [SNR] gain={g_str} → RDS 57kHz SNR = {snr} dB{ster_str}")
            if best_snr is None or snr > best_snr:
                best_snr = snr
                best_gain = gain
                best_stereo = stereo
        else:
            print(f"    [SNR] gain={g_str} → no data")
    tried = []
    if best_gain in rds_gains:
        tried.append(best_gain)
    for g in rds_gains:
        if g not in tried:
            tried.append(g)
    for gain in tried:
        rds = decode_rds(freq_mhz, gain, ppm, dwell_sec, rate)
        if rds:
            return rds, gain, best_snr, best_stereo
    return {}, best_gain, best_snr, best_stereo

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
        filtered = [s for s in stations if is_in_fm_range(s["freq"])]
        if len(filtered) < len(stations):
            print(f"[!] {len(stations)-len(filtered)} station(s) outside {FM_MIN}-{FM_MAX} MHz not saved to {BASE_FILE}.")
    else:
        filtered = stations
    data = {"scan_date": datetime.now().isoformat(), "mode": mode, "band": "FM",
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
    resp = input("Подтвердить? (y/N): ").strip().lower()
    if resp != "y": print("[*] Слияние отменено."); return None
    for f in to_remove: base_sts.pop(f, None)
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
#  PRETTY PRINT
# ═══════════════════════════════════════════════════════════════

def print_summary(stations, ppm=0, snap=0):
    print("\n" + "=" * 135)
    hdr = f"{'Freq':>7} | {'Signal':>7} | {'Name RU':<22} | {'PI':>8} | {'PS':<24} | {'PTY':<16} | {'Ster':>5} | {'TP':>4} | {'TA':>4} | RadioText"
    print(hdr)
    print("-" * 135)
    n_pi = n_ps = n_rt = n_ru = 0
    for st in stations:
        freq = st.get("freq", 0); signal = st.get("signal", 0)
        name_ru = st.get("name_ru", "") or "—"
        rds = st.get("rds", {})
        pi = rds.get("PI", "—"); ps = rds.get("PS", "—") or "—"
        pty = rds.get("PTY", "—") or "—"
        tp = rds.get("TP", "—"); ta = rds.get("TA", "—")
        rt = rds.get("RadioText", "—") or "—"
        # Стерео: сначала pilot tone, потом RDS flag
        stereo = st.get("stereo")
        if stereo is None:
            stereo = rds.get("Stereo")
        if stereo is True:
            ster_str = f"{C_GREEN}●{C_RESET}"
        elif stereo is False:
            ster_str = f"{C_RED}●{C_RESET}"
        else:
            ster_str = "?"
        # Цвета для PI и RT
        pi_str = f"{C_BLUE}{pi}{C_RESET}" if pi != "—" else "—"
        rt_str = f"{C_BLUE}{rt}{C_RESET}" if rt != "—" else "—"
        if pi != "—": n_pi += 1
        if ps != "—": n_ps += 1
        if rt != "—": n_rt += 1
        if name_ru != "—": n_ru += 1
        print(f"{freq:>6.1f}M | {signal:>6.1f} | {name_ru:<22} | {pi_str:>8} | {ps:<24} | {pty:<16} | {ster_str:>5} | {str(tp):>4} | {str(ta):>4} | {rt_str}")
    print("=" * 135)
    print(f"\nВсего станций: {len(stations)} | PPM: {ppm} | Snap: {snap} kHz")
    print(f"С RDS (PI): {n_pi} | С PS: {n_ps} | С RT: {n_rt} | С русским названием: {n_ru}")

# ═══════════════════════════════════════════════════════════════
#  Export to html
# ═══════════════════════════════════════════════════════════════

def export_html(data, region="spb", output_path=None):
    stations = data.get("stations", [])
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

    # JS отдельно — никаких конфликтов с f-string
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
<title>FM RDS Scanner — {region.upper()}</title>
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
      <th onclick="sortTable(3)">PI</th>
      <th onclick="sortTable(4)">PS</th>
      <th onclick="sortTable(5)">PTY</th>
      <th onclick="sortTable(6)">Ster</th>
      <th onclick="sortTable(7)">TP</th>
      <th onclick="sortTable(8)">TA</th>
      <th onclick="sortTable(9)">RadioText</th>
      <th onclick="sortTable(10)">Status</th>
      <th onclick="sortTable(11)">Last Seen</th>
    </tr>
  </thead>
  <tbody>
{rows}  </tbody>
</table>
<div class="footer">Generated by SDR-RTL-Scanner v0.8 | {scan_date[:19]}</div>
{js_code}
</body>
</html>"""

    if output_path is None:
        output_path = os.path.join(DATA_DIR, f"fm_rds_report_{region}.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[*] HTML report: {output_path}")
    return output_path

# ═══════════════════════════════════════════════════════════════
#  MODE 1: FULLSCAN
# ═══════════════════════════════════════════════════════════════

def mode_fullscan(args):
    print("=" * 60 + "\nFM RDS Scanner v0.8 — Mode 1: Full Scan\n" + "=" * 60)
    prev_names = {}
    if os.path.exists(BASE_FILE):
        prev = load_json(BASE_FILE)
        if prev:
            for st in prev.get("stations", []):
                if st.get("name_ru"): prev_names[round(st["freq"], 1)] = st["name_ru"]
    stations = multi_gain_scan(args.start, args.stop, args.step, args.gains, args.threshold, args.ppm, args.snap)
    if not stations:
        save_results([], args, "fullscan", scan_min=args.start, scan_max=args.stop)
        print_summary([], args.ppm, args.snap); return
    print(f"[*] RDS decoding phase (rtl_fm | redsea)\n    RDS gains: {args.rds_gains} dB | Dwell: {args.dwell}s | Rate: {DEFAULT_RDS_RATE} Hz\n")
    # ── Автокалибровка PPM по сильнейшей станции ──
    if stations:
        strongest = max(stations, key=lambda x: x["signal"])
        print(f"\n[*] Auto-PPM calibration on {strongest['freq']:.1f} MHz ({strongest['signal']:.1f} dB)...")
        ppm_cal, stereo_cal = auto_ppm_calibrate(strongest["freq"], -1, DEFAULT_RDS_RATE, 2.0)
        if ppm_cal != 0:
            print(f"    {C_GREEN}Pilot tone detected{C_RESET} → PPM = {ppm_cal} (was {args.ppm})")
            args.ppm = ppm_cal
        elif stereo_cal is False:
            print(f"    No pilot tone (mono) → keeping PPM = {args.ppm}")
        else:
            print(f"    No pilot tone detected → keeping PPM = {args.ppm}")
    rds_gains = args.rds_gains
    rate = DEFAULT_RDS_RATE
    for i, st in enumerate(stations):
        freq = st["freq"]
        print(f"[*] {i+1}/{len(stations)}: {freq:.1f} MHz ({st['signal']:.1f} dB)")
        st["name_ru"] = prev_names.get(round(freq, 1)) or lookup_name_ru(freq) or ""
        rds, used_gain, snr, stereo = decode_rds_multi(st["freq"], rds_gains, args.ppm, args.dwell, DEFAULT_RDS_RATE)
        if stereo is not None:
            st["stereo"] = stereo
        if rds:
            extra = f" | PS={rds.get('PS', '')}" if rds.get('PS') else ""
            snr_str = f" | SNR={snr}dB" if snr is not None else ""
            print(f"    -> {C_BLUE}PI={rds.get('PI', '?')}{extra}{snr_str}{C_RESET} (gain={used_gain})")
            st["rds"] = rds
        else:
            snr_str = f" | SNR={snr}dB" if snr is not None else ""
            print(f"    -> {C_RED}no RDS{snr_str}{C_RESET}")
            st["rds"] = {}
        print()
    fname = save_results(stations, args, "fullscan", scan_min=args.start, scan_max=args.stop)
    print_summary(stations, args.ppm, args.snap)
    # Переменная для финального списка станций
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
                # Если мердж успешен, финальные данные — это merged
                final_stations = merged.get("stations", [])
    # === ВЫЗОВ EXPORT_HTML ===
    export_html({
        "stations": final_stations, 
        "scan_date": datetime.now().isoformat(), 
        "ppm": args.ppm, 
        "mode": args.mode  # <-- Важно: берём из args
    }, region=args.region)
    # ========================

# ═══════════════════════════════════════════════════════════════
#  MODE 2: UPDATE
# ═══════════════════════════════════════════════════════════════

def mode_update(args):
    src = args.json or BASE_FILE
    data = load_json(src)
    if not data: print(f"[!] File not found: {src}"); return
    stations = data.get("stations", [])
    if not stations: print("[!] No stations in JSON"); return
    print("=" * 60 + f"\nFM RDS Scanner v0.8 — Mode 2: Update ({src})\n" + "=" * 60)
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
    print("=" * 60 + f"\nFM RDS Scanner v0.8 — Mode 3: Edit ({src})\n" + "=" * 60)
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
        prefix = f"SDR_FM_RDS_Update_{CURRENT_REGION}_"
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
    global CURRENT_REGION, BASE_FILE, STATIONS_FILE

    p = argparse.ArgumentParser(
        description="FM RDS Scanner v0.8 (rtl_power + rtl_fm + redsea) for RTL-SDR\n\n"
                    "Quick start:\n"
                    "  python3 SDR_RTL_FM_RDS_Scaner.py\n"
                    "  → fullscan 87–109 MHz, region 'default' (empty reference)\n\n"
                    "  python3 SDR_RTL_FM_RDS_Scaner.py --region spb\n"
                    "  → fullscan with Saint Petersburg station reference\n\n"
                    "  python3 SDR_RTL_FM_RDS_Scaner.py --region '?'\n"
                    "  → interactive region selection\n\n"
                    "Files:\n"
                    "  regions/{region}.json            — station reference (per region)\n"
                    "  data/SDR_FM_RDS_Base_{region}.json — base database\n"
                    "  data/SDR_FM_RDS_Update_{region}_*.json — scan results",
        formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--mode", choices=["fullscan", "update", "edit", "merge"],
                   default="fullscan",
                   help="fullscan: scan + RDS (default)\n"
                        "update: re-decode RDS from base\n"
                        "edit: manual name_ru editing\n"
                        "merge: merge update into base")
    p.add_argument("--region", type=str, default="default",
                   help="Region code, e.g. spb, msk, berlin (default: 'default' = empty).\n"
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
    p.add_argument("--step", type=float, default=DEFAULT_STEP, help="Step kHz for rtl_power (default: 100)")
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
    # ── Region selection ──
    ensure_dirs()
    region = args.region
    if region == "?":
        region = interactive_region_select()
    CURRENT_REGION = region
    ensure_region_file(region)
    ensure_base_file(region)
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

        # Запрос METAR
        metar_station = app_context.get_metar_station()
        if metar_station:
            print(f"[*] Fetching METAR for {metar_station}...")
            wx = app_context.fetch_metar()
            if wx:
                app_context.weather = wx
                inv_str = f" | INVERSION: {wx['inversion_type']}" if wx.get("inversion") else ""
                print(f"    METAR: {wx.get('raw', '?')[:60]}...")
                print(f"    T={wx.get('temperature_c','?')}C, Td={wx.get('dewpoint_c','?')}C, "
                      f"vis={wx.get('visibility_m','?')}m{inv_str}")
            else:
                print(f"    [!] METAR fetch failed")
        print(f"[*] Context ready. Observer: {app_context.get_observer_name()}, "
              f"Antenna: {app_context.get_antenna_model()}")
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
        path = subprocess.run(["which", tool], capture_output=True, text=True).stdout.strip()
        if not path:
            print(f"[!] '{tool}' not found.")
            if tool == "redsea": print("    https://github.com/windytan/redsea")
            elif tool.startswith("rtl"): print("    sudo apt install rtl-sdr")
            sys.exit(1)

    if args.mode == "fullscan": mode_fullscan(args)
    elif args.mode == "update": mode_update(args)
    elif args.mode == "edit": mode_edit(args)
    elif args.mode == "merge": mode_merge(args)

if __name__ == "__main__": main()
