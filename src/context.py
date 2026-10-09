#!/usr/bin/env python3
#src/context.py
#
# Данные погоды для оценки прохождения сигнала
#

import time
import ssl
import socket
import json
import urllib.request
import urllib.error
import re
from _version import __version__
from config import *
from datetime import datetime
from typing import Optional
"""
Context Module — observer, antenna, weather (METAR), artifacts, interference.

Part of SDR-RTL-Scanner v{__version__} Radio Catalogizer project.
License: MIT
"""

# ── METAR token groups ──
WX_CODES_FOG = {"FG", "MIFG", "BCFG", "PRFG", "FZFG"}
WX_CODES_HAZE = {"HZ", "DU", "SA", "DS", "SS", "BLDU", "BLSA", "BLSS"}
WX_CODES_PRECIP = {"DZ", "RA", "SN", "SG", "IC", "PL", "GR", "GS", "UP"}
WX_CODES_THUNDER = {"TS", "TSRA", "TSSN", "TSPL", "TSGS", "TSGR"}

# ── METAR ──
# Primary: new AWC API (JSON, no key needed)
METAR_URL_PRIMARY = "https://aviationweather.gov/api/data/metar?ids={station}&format=json&taf=false&hours=1"
# Backup: NOAA NWS direct text (no key, very stable)
METAR_URL_BACKUP = "https://tgftp.nws.noaa.gov/data/observations/metar/stations/{station}.TXT"


ARTIFACT_TYPES = [
    "tropospheric_duct",
    "sporadic_e",
    "ground_wave_enhanced",
    "unexpected_station",
    "anomalous_reception",
    "eme_moon_bounce",
    "meteor_scatter",
    "unknown_propagation",
]

INTERFERENCE_TYPES = [
    "power_line",
    "switching_psu",
    "co_channel",
    "adjacent_channel",
    "rtl_noise",
    "rtl_adc_overflow",
    "imdb",
    "harmonic",
    "mixer_spur",
    "broadband_noise",
]

def _format_metar_human(wx: dict) -> str:
    """Преобразовать распарсенный METAR в человекочитаемую строку."""
    parts = []

    # Температура и точка росы
    temp = wx.get("temperature_c")
    dew = wx.get("dewpoint_c")
    if temp is not None and dew is not None:
        parts.append(f"Температура: {temp}°C")
        parts.append(f"Точка росы: {dew}°C")

    # Ветер: направление в градусах и в сторонах света
    wdir = wx.get("wind_dir")
    wspd = wx.get("wind_speed_kt")
    if wspd is not None:
        wspd_ms = round(int(wspd) * 0.5144)
        if wdir and wdir != "VRB":
            # Перевод градусов в сторону света
            try:
                wd = int(wdir)
                dirs = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]
                idx = (wd + 22) // 45 % 8
                dir_name = dirs[idx]
                parts.append(f"Ветер: {wd}° ({dir_name}) {wspd} уз ({wspd_ms} м/с)")
            except ValueError:
                parts.append(f"Ветер: направление не определено, {wspd} уз ({wspd_ms} м/с)")
        else:
            parts.append(f"Ветер: переменный, {wspd} уз ({wspd_ms} м/с)")

    # Видимость — явно горизонтальная
    vis = wx.get("visibility_m")
    if vis is not None:
        if vis >= 9999:
            parts.append("Видимость (горизонтальная): >10 км")
        elif vis >= 1000:
            parts.append(f"Видимость (горизонтальная): {vis/1000:.1f} км")
        else:
            parts.append(f"Видимость (горизонтальная): {vis} м")

    # Давление: гПа и мм рт. ст.
    qnh = wx.get("qnh_hpa")
    if qnh is not None:
        # 1 гПа ≈ 0.75006 мм рт. ст.
        qnh_mm = round(qnh * 0.75006, 1)
        parts.append(f"Давление: {qnh} гПа ({qnh_mm} мм рт. ст.)")

    # Погодные явления
    wx_codes = wx.get("wx_codes", [])
    wx_ru = {
        "DZ": "морось", "RA": "дождь", "SN": "снег", "SG": "снежная крупа",
        "IC": "ледяные кристаллы", "PL": "ледяной дождь", "GR": "град",
        "GS": "мокрый снег", "FG": "туман", "MIFG": "поземный туман",
        "BR": "дымка", "HZ": "пыльная мгла", "FU": "дым", "DU": "пыль",
        "SA": "пыльная буря", "TS": "гроза", "TSRA": "гроза с дождём",
        "FZFG": "ледяной туман", "UP": "неопределённые осадки",
    }
    if wx_codes:
        translated = [wx_ru.get(c, c) for c in wx_codes]
        parts.append("Явления: " + ", ".join(translated))

    # Облачность
    clouds = wx.get("clouds", [])
    cloud_ru = {
        "FEW": "редкая", "SCT": "рассеянная", "BKN": "значительная",
        "OVC": "сплошная", "CLR": "ясно", "SKC": "ясно", "CAVOK": "CAVOK",
    }
    cloud_strs = []
    for c in clouds:
        cover = ""
        base = ""
        for prefix in ("FEW", "SCT", "BKN", "OVC", "CLR", "SKC"):
            if c.startswith(prefix):
                cover = cloud_ru.get(prefix, prefix)
                rest = c[len(prefix):]
                if rest.isdigit():
                    base = f" ({int(rest)*100} м)"
                break
        if cover:
            cloud_strs.append(f"{cover}{base}")
        elif c == "CAVOK":
            cloud_strs.append("CAVOK")
    if cloud_strs:
        parts.append("Облачность: " + ", ".join(cloud_strs))

    # Инверсия
    if wx.get("inversion"):
        inv_type = wx.get("inversion_type", "?")
        parts.append(f"⚠ Инверсия ({inv_type})")

    return "    METAR: " + " | ".join(parts)

class Context:
    """Context module: observer profile, antenna, weather (METAR), artifacts."""


    # ── METAR endpoints ──
    METAR_URL_PRIMARY = "https://aviationweather.gov/api/data/metar?ids={station}&format=json&taf=false&hours=1"
    METAR_URL_BACKUP = "https://tgftp.nws.noaa.gov/data/observations/metar/stations/{station}.TXT"

    def __init__(self, observer_cfg: dict, antenna_cfg: dict):
        self.observer = observer_cfg or {}
        self.antenna = antenna_cfg or {}
        self.state = "idle"
        self.weather = {}
        self.space_weather = {}
        self.artifact = None
        self.interference = None
        self.artifact_notes = ""
        self.interference_notes = ""
        self.scan_datetime = None

    # ── Observer ──

    def get_observer_name(self) -> str:
        return self.observer.get("name", "Unknown")

    def get_observer_coords(self):
        loc = self.observer.get("location", {})
        return loc.get("lat"), loc.get("lon")

    def get_observer_altitude(self) -> float:
        return float(self.observer.get("location", {}).get("altitude_m", 0.0))

    def get_observer_description(self) -> str:
        return self.observer.get("description", "")

    def get_metar_station(self) -> str:
        return self.observer.get("metar_station", "")

    # ── Antenna ──

    def get_antenna_id(self) -> str:
        return self.antenna.get("id", "unknown")

    def get_antenna_model(self) -> str:
        return self.antenna.get("model", "Unknown")

    def get_antenna_type(self) -> str:
        return self.antenna.get("type", "unknown")

    def get_antenna_gain(self) -> float:
        return float(self.antenna.get("gain_dbi", 0.0))

    def get_antenna_azimuth(self) -> Optional[float]:
        a = self.antenna.get("azimuth_deg")
        return float(a) if a is not None else None

    def get_antenna_polarization(self) -> str:
        return self.antenna.get("polarization", "unknown")

    def get_antenna_height(self) -> float:
        return float(self.antenna.get("height_m", 0.0))

    # ── State ──

    def set_state(self, state: str):
        self.state = state

    def set_scan_datetime(self, dt: datetime):
        self.scan_datetime = dt

    # ── Artifacts ──

    def set_artifact(self, artifact_type: str, notes: str = ""):
        if artifact_type and artifact_type not in ARTIFACT_TYPES:
            print(f"[!] Warning: unknown artifact type '{artifact_type}'")
        self.artifact = artifact_type if artifact_type else None
        self.artifact_notes = notes

    def set_interference(self, interference_type: str, notes: str = ""):
        if interference_type and interference_type not in INTERFERENCE_TYPES:
            print(f"[!] Warning: unknown interference type '{interference_type}'")
        self.interference = interference_type if interference_type else None
        self.interference_notes = notes

    # ── METAR ──


    def fetch_metar(self, station_code: str = "") -> dict:
        """Fetch METAR — primary AWC JSON API, fallback to NOAA NWS raw text."""
        station = station_code or self.get_metar_station()
        if not station:
            return {}

        # ── Primary: AWC JSON API ──
        url = self.METAR_URL_PRIMARY.format(station=station)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"SDR-Scanner/{__version__}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8").strip()
        except Exception as e:
            print(f"[!] METAR primary fetch error: {e}")
            raw = ""

        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, list) and len(data) > 0:
                    metar_json = data[0]
                    return self._metar_from_json(metar_json)
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                print(f"[!] METAR JSON parse error: {e}, trying backup...")

        # ── Backup: NOAA NWS raw text ──
        url_b = self.METAR_URL_BACKUP.format(station=station)
        try:
            req = urllib.request.Request(url_b, headers={"User-Agent": f"SDR-Scanner/{__version__}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw_text = resp.read().decode("utf-8").strip()
        except Exception as e:
            print(f"[!] METAR backup fetch error: {e}")
            return {}

        if not raw_text:
            return {}

        # raw_text может содержать пустую строку + METAR, берём последнюю непустую
        lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
        if not lines:
            return {}
        # Последняя строка — свежий METAR
        metar_raw = lines[-1]

        # Проверка на успешное получение данных
        if metar_raw:
            return self.parse_metar(metar_raw)
        else:
            print("[!] METAR: no data received")
            return {}

    def _metar_from_json(self, m: dict) -> dict:
        """Convert AWC JSON METAR to the same dict structure as parse_metar()."""
        result = {
            "raw": m.get("rawOb", ""),
            "station": m.get("icaoId", ""),
            "datetime": m.get("reportTime", ""),
            "temperature_c": m.get("temp"),
            "dewpoint_c": m.get("dewp"),
            "visibility_m": None,
            "wind_dir": m.get("wdir"),
            "wind_speed_kt": m.get("wspd"),
            "qnh_hpa": m.get("altim"),
            "wx_codes": [],
            "clouds": [],
            "inversion": False,
            "inversion_type": None,
        }
        # visibility: AWC отдаёт строкой ("6+", "1/2", "9999" и т.п.)
        vis = m.get("visib")
        if vis is not None:
            try:

                # "6+" → берём число, "+" отбрасываем
                vis_clean = str(vis).replace("+", "").strip()
                if "/" in vis_clean:
                    # дробная видимость, напр. "1/4" мили
                    num, den = vis_clean.split("/")
                    vis_miles = float(num) / float(den)
                else:
                    vis_miles = float(vis_clean)
                result["visibility_m"] = int(vis_miles * 1609)
            except (ValueError, TypeError):
                result["visibility_m"] = None

        # qnh: AWC уже отдаёт в гПа — не переводим!
        altim = m.get("altim")
        if altim is not None:
            result["qnh_hpa"] = int(altim)

        # wind_dir: может быть "VRB"
        wdir = m.get("wdir")
        if wdir is not None and wdir != "VRB":
            result["wind_dir"] = str(wdir)
        else:
            result["wind_dir"] = None

        # погодные явления
        wx = m.get("wxString", "")
        if wx:
            result["wx_codes"] = [w.strip() for w in wx.split() if w.strip()]

        # облака
        clouds = m.get("clouds", [])
        if isinstance(clouds, list):
            for c in clouds:
                layer = c.get("cover", "")
                base = c.get("base", "")
                if layer and base is not None:
                    result["clouds"].append(f"{layer}{int(base):03d}")
                elif layer:
                    result["clouds"].append(layer)

        # инверсию определяем как и раньше
        result["inversion"], result["inversion_type"] = self._detect_inversion(result)
        return result

    def _detect_inversion(self, metar: dict) -> tuple:
        """Detect temperature inversion from METAR data."""
        temp = metar.get("temperature_c")
        dew = metar.get("dewpoint_c")
        vis = metar.get("visibility_m")
        wx = metar.get("wx_codes", [])
        if temp is None or dew is None:
            return False, None
        spread = abs(temp - dew)
        if spread <= 2.0 and vis is not None and vis <= 1000:
            return True, "fog_inversion"
        if any(code in WX_CODES_FOG for code in wx):
            return True, "fog_inversion"
        if any(code in WX_CODES_HAZE for code in wx):
            if spread <= 3.0:
                return True, "haze_inversion"
        if spread <= 1.0:
            return True, "tight_spread"
        return False, None

    def parse_metar(self, raw: str) -> dict:
        """Распарсить сырой текст METAR в dict (тот же формат, что у _metar_from_json)."""
        result = {
            "raw": raw,
            "station": "",
            "datetime": "",
            "temperature_c": None,
            "dewpoint_c": None,
            "visibility_m": None,
            "wind_dir": None,
            "wind_speed_kt": None,
            "qnh_hpa": None,
            "wx_codes": [],
            "clouds": [],
            "inversion": False,
            "inversion_type": None,
        }
        if not raw:
            return result

        # Убираем "METAR" или "SPECI" в начале
        text = raw.strip()
        if text.startswith("METAR"):
            text = text[5:].strip()
        elif text.startswith("SPECI"):
            text = text[5:].strip()

        tokens = text.split()
        if not tokens:
            return result

        idx = 0

        # ИКАО-станция (4 буквы)
        if len(tokens[idx]) == 4 and tokens[idx].isalpha():
            result["station"] = tokens[idx]
            idx += 1

        # Дата/время (DDHHMMZ)
        if idx < len(tokens) and tokens[idx].endswith("Z"):
            result["datetime"] = tokens[idx]
            idx += 1

        # AUTO/COR
        if idx < len(tokens) and tokens[idx] in ("AUTO", "COR"):
            idx += 1

        # Ветер: NNNDD или VRBDD (например, 20003MPS, 18012KT, VRB03KT)
        if idx < len(tokens):
            t = tokens[idx]
            wind_match = re.match(r'^(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(MPS|KT|KMH)$', t)
            if wind_match:
                wdir = wind_match.group(1)
                wspd = wind_match.group(2)
                unit = wind_match.group(4)
                result["wind_dir"] = None if wdir == "VRB" else wdir
                # Нормализуем в узлы
                if unit == "MPS":
                    result["wind_speed_kt"] = round(int(wspd) * 1.944)
                elif unit == "KMH":
                    result["wind_speed_kt"] = round(int(wspd) * 0.540)
                else:  # KT
                    result["wind_speed_kt"] = int(wspd)
                idx += 1

                # Возможно за ветром следует вариация: 160V300
                if idx < len(tokens) and re.match(r'^\d{3}V\d{3}$', tokens[idx]):
                    idx += 1

        # CAVOK — видимость >10 км, облаков нет
        if idx < len(tokens) and tokens[idx] == "CAVOK":
            result["visibility_m"] = 9999
            result["clouds"] = ["CAVOK"]
            idx += 1
        else:
            # Видимость: может быть числом (метры) или дробью (мили)
            if idx < len(tokens):
                vis_t = tokens[idx]
                vis_match = re.match(r'^(\d{4})$', vis_t)
                if vis_match:
                    result["visibility_m"] = int(vis_match.group(1))
                    idx += 1
                elif vis_t in ("9999",):
                    result["visibility_m"] = 9999
                    idx += 1
                elif "/" in vis_t:
                    # дробная видимость в милях, напр. 1/4
                    try:
                        num, den = vis_t.split("/")
                        result["visibility_m"] = int(float(num) / float(den) * 1609)
                        idx += 1
                    except ValueError:
                        pass
                else:
                    try:
                        vis_miles = float(vis_t.replace("SM", ""))
                        result["visibility_m"] = int(vis_miles * 1609)
                        idx += 1
                    except ValueError:
                        pass

            # Погодные явления и облачность — всё до температуры
            while idx < len(tokens):
                t = tokens[idx]
                # Температура/точка росы: MM/DD или M/DD
                if re.match(r'^(M?\d{2})/(M?\d{2})$', t):
                    temp_str = t.split("/")[0]
                    dew_str = t.split("/")[1]
                    def parse_temp(s):
                        val = int(s.replace("M", ""))
                        return -val if s.startswith("M") else val
                    result["temperature_c"] = parse_temp(temp_str)
                    result["dewpoint_c"] = parse_temp(dew_str)
                    idx += 1
                    break

                # Давление: Q1015 или A2992
                if t.startswith("Q") and len(t) == 5:
                    result["qnh_hpa"] = int(t[1:])
                    idx += 1
                    break
                if t.startswith("A") and len(t) == 5:
                    result["qnh_hpa"] = round(int(t[1:]) / 100 * 33.86)
                    idx += 1
                    break

                # Облачность: FEW020, SCT040, BKN100, OVC008, CLR, SKC
                if re.match(r'^(FEW|SCT|BKN|OVC)(\d{3})$', t):
                    result["clouds"].append(t)
                    idx += 1
                    continue
                if t in ("CLR", "SKC", "NSC", "NCD"):
                    result["clouds"].append(t)
                    idx += 1
                    continue

                # Погодные явления: RA, SN, FG, DZ, TS, -RA, +SN и т.д.
                if re.match(r'^[+-]?(DZ|RA|SN|SG|IC|PL|GR|GS|UP|FG|BR|HZ|FU|DU|SA|SS|TS|TSRA|TSSN|FZFG|MIFG|BCFG|PRFG|VCFG|VCSH)$', t):
                    result["wx_codes"].append(t)
                    idx += 1
                    continue

                # Rwy-состояние (R10L/090065) — пропускаем
                if t.startswith("R") and "/" in t:
                    idx += 1
                    continue

                # Прочие токены — пропускаем
                idx += 1

            # Продолжаем: ищем QNH, если ещё не нашли
            while idx < len(tokens):
                t = tokens[idx]
                if t.startswith("Q") and len(t) == 5 and t[1:].isdigit():
                    result["qnh_hpa"] = int(t[1:])
                    break
                if t.startswith("A") and len(t) == 5 and t[1:].isdigit():
                    result["qnh_hpa"] = round(int(t[1:]) / 100 * 33.86)
                    break
                idx += 1

        # Инверсия
        result["inversion"], result["inversion_type"] = self._detect_inversion(result)
        return result

    # ── Space Weather (NOAA SWPC) ──

    def fetch_space_weather(self) -> dict:
        """Получить данные космической погоды от NOAA SWPC (форсированный IPv4)."""
        import socket
        import ssl

        endpoints = {
            "kp":     "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
            "f107":   "https://services.swpc.noaa.gov/products/summary/10cm-flux.json",
            "wind":   "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json",
            "mag":    "https://services.swpc.noaa.gov/products/summary/solar-wind-mag-field.json",
            "scales": "https://services.swpc.noaa.gov/products/noaa-scales.json",
        }

        # Принудительный IPv4: подменяем getaddrinfo, чтобы не пытаться IPv6
        original_getaddrinfo = socket.getaddrinfo
        def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        sw = {}
        overall_start = time.time()

        for key, url in endpoints.items():
            start = time.time()
#            print(f"[space-debug] {key}: запрос...")
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": f"SDR-Scanner/{__version__}"}
                )
                # Подменяем DNS resolver на IPv4 только для этого запроса
                socket.getaddrinfo = ipv4_getaddrinfo
                try:
                    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                        raw = resp.read()
                finally:
                    socket.getaddrinfo = original_getaddrinfo

                elapsed = time.time() - start
#                print(f"[space-debug] {key}: OK, {elapsed:.2f} сек, {len(raw)} байт")
                data = json.loads(raw.decode())

                if isinstance(data, list) and len(data) > 0:
                    sw[key] = data[-1]
                else:
                    sw[key] = data

            except Exception as e:
                socket.getaddrinfo = original_getaddrinfo
                elapsed = time.time() - start
#                print(f"[space-debug] {key}: ошибка {type(e).__name__} после {elapsed:.2f} сек — {e}")
                sw[key] = None

        overall_elapsed = time.time() - overall_start
#        print(f"[space-debug] ВСЕГО: {overall_elapsed:.2f} сек")

        # Парсинг
        kp_val = None
        if sw.get("kp") and isinstance(sw["kp"], dict):
            kp_val = sw["kp"].get("kp_index")

        f107_val = None
        if sw.get("f107") and isinstance(sw["f107"], dict):
            f107_val = sw["f107"].get("flux")

        wind_speed = None
        if sw.get("wind") and isinstance(sw["wind"], dict):
            wind_speed = sw["wind"].get("proton_speed")

        bz = None
        if sw.get("mag") and isinstance(sw["mag"], dict):
            bz = sw["mag"].get("bz_gsm")

        storm_scale = "G0 (none)"
        if sw.get("scales") and isinstance(sw["scales"], dict):
            today = sw["scales"].get("0", {})
            g_info = today.get("G", {})
            g_scale = g_info.get("Scale", "0")
            g_text = g_info.get("Text", "none")
            if g_scale and g_scale != "0":
                storm_scale = f"G{g_scale} ({g_text})"

        if kp_val is not None:
            try:
                kp = float(kp_val)
                if kp >= 5 and storm_scale == "G0 (none)":
                    storm_scale = f"G{min(int(kp) - 4, 5)}"
            except (ValueError, TypeError):
                pass

        cond = "normal"
        if kp_val is not None:
            try:
                if float(kp_val) >= 5:
                    cond = "storm — KV degraded, VHF may enhance"
                elif float(kp_val) >= 4:
                    cond = "active — KV unstable"
            except (ValueError, TypeError):
                pass
        if f107_val is not None:
            try:
                if float(f107_val) < 70:
                    cond = "low solar flux — KV poor"
            except (ValueError, TypeError):
                pass

        print(f"[*] Space weather (NOAA SWPC):")
        print(f"    Kp={kp_val} | {storm_scale} | F10.7={f107_val} sfu | "
              f"Wind={wind_speed} km/s | Bz={bz} nT | {cond}")

        self.space_weather = {
            "kp_index": kp_val,
            "storm_scale": storm_scale,
            "f107": f107_val,
            "solar_wind_speed": wind_speed,
            "bz": bz,
            "condition": cond,
        }
        return self.space_weather

    # ── Export ──

    def to_dict(self) -> dict:
        """Export context as dict for JSON output."""
        lat, lon = self.get_observer_coords()
        return {
            "scan_datetime": (self.scan_datetime or datetime.now()).isoformat(),
            "observer": {
                "name": self.get_observer_name(),
                "lat": lat,
                "lon": lon,
                "altitude_m": self.get_observer_altitude(),
                "description": self.get_observer_description(),
                "metar_station": self.get_metar_station(),
            },
            "antenna": {
                "id": self.get_antenna_id(),
                "model": self.get_antenna_model(),
                "type": self.get_antenna_type(),
                "gain_dbi": self.get_antenna_gain(),
                "azimuth_deg": self.get_antenna_azimuth(),
                "polarization": self.get_antenna_polarization(),
                "height_m": self.get_antenna_height(),
            },
            "weather": self.weather,
            "space_weather": self.space_weather,
            "artifact": {
                "type": self.artifact,
                "notes": self.artifact_notes,
            },
            "interference": {
                "type": self.interference,
                "notes": self.interference_notes,
            },
        }

