#!/usr/bin/env python3
from _version import __version__
"""
Context Module — observer, antenna, weather (METAR), artifacts, interference.

Part of SDR-RTL-Scanner v{__version__} Radio Catalogizer project.
License: MIT
"""
import json
import urllib.request
import re
from datetime import datetime
from typing import Optional

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


class Context:
    """Context module: observer profile, antenna, weather (METAR), artifacts."""

    def __init__(self, observer_cfg: dict, antenna_cfg: dict):
        self.observer = observer_cfg or {}
        self.antenna = antenna_cfg or {}
        self.state = "idle"
        self.weather = {}
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
        return self.parse_metar(metar_raw)

    def _metar_from_json(self, m: dict) -> dict:
        """Convert AWC JSON METAR to the same dict structure as parse_metar()."""
        result = {
            "raw": m.get("rawOb", ""),
            "station": m.get("icaoId", ""),
            "datetime": m.get("obsTime", ""),
            "temperature_c": m.get("tempC"),
            "dewpoint_c": m.get("dewpC"),
            "visibility_m": m.get("visib", ""),
            "wind_dir": m.get("wdir"),
            "wind_speed_kt": m.get("wspd"),
            "qnh_hpa": m.get("altim"),
            "wx_codes": [],
            "clouds": [],
            "inversion": False,
            "inversion_type": None,
        }
        # visibility: AWC отдаёт в милях, переводим в метры
        vis = m.get("visib")
        if vis is not None:
            try:
                result["visibility_m"] = int(float(vis) * 1609)
            except (ValueError, TypeError):
                result["visibility_m"] = None

        # qnh: AWC отдаёт в дюймах ртутного столба (altim), переводим в гПа
        altim = m.get("altim")
        if altim is not None:
            try:
                result["qnh_hpa"] = round(float(altim) * 33.8639)
            except (ValueError, TypeError):
                result["qnh_hpa"] = None

        # wind_dir: AWC отдаёт число, нормализуем
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
            "artifact": {
                "type": self.artifact,
                "notes": self.artifact_notes,
            },
            "interference": {
                "type": self.interference,
                "notes": self.interference_notes,
            },
        }

