#!/usr/bin/env python3

"""
Context Module — observer, antenna, weather (METAR), artifacts, interference.

Part of SDR-RTL-Scanner / Radio Catalogizer project.
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
        """Fetch METAR from NOAA (no API key needed)."""
        station = station_code or self.get_metar_station()
        if not station:
            return {}
        url = f"https://aviationweather.gov/cgi-bin/data/metar.php?ids={station}&format=raw&hours=1"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SDR-Scanner/0.9"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8").strip()
        except Exception as e:
            print(f"[!] METAR fetch error: {e}")
            return {}
        if not raw:
            return {}
        lines = [l for l in raw.split("\n") if l.strip()]
        if not lines:
            return {}
        metar_raw = lines[-1].strip()
        return self.parse_metar(metar_raw)

    def parse_metar(self, metar_raw: str) -> dict:
        """Parse a raw METAR string into structured data."""
        result = {
            "raw": metar_raw,
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
        tokens = metar_raw.split()
        if not tokens:
            return result
        result["station"] = tokens[0]
        for t in tokens[1:4]:
            if re.match(r'^\d{6}Z$', t):
                result["datetime"] = t
                break
        for t in tokens:
            m = re.match(r'^(\d{3}|VRB)(\d{2,3})KT$', t)
            if m:
                result["wind_dir"] = m.group(1) if m.group(1) != "VRB" else None
                result["wind_speed_kt"] = int(m.group(2))
                break
        for t in tokens:
            if re.match(r'^\d{4}$', t):
                result["visibility_m"] = int(t)
                break
            m = re.match(r'^(\d)/(\d)SM$', t)
            if m:
                result["visibility_m"] = int(int(m.group(1)) / int(m.group(2)) * 1609)
                break
            m = re.match(r'^(\d+)SM$', t)
            if m:
                result["visibility_m"] = int(m.group(1)) * 1609
                break
        for t in tokens:
            m = re.match(r'^(M?\d{2})/(M?\d{2})$', t)
            if m:
                def parse_temp(s):
                    neg = s.startswith("M")
                    val = int(s.lstrip("M"))
                    return -val if neg else val
                result["temperature_c"] = parse_temp(m.group(1))
                result["dewpoint_c"] = parse_temp(m.group(2))
                break
        for t in tokens:
            if re.match(r'^Q\d{4}$', t):
                result["qnh_hpa"] = int(t[1:])
                break
            m = re.match(r'^A(\d{4})$', t)
            if m:
                result["qnh_hpa"] = round(int(m.group(1)) * 0.3386)
                break
        for t in tokens:
            if t in WX_CODES_FOG or t in WX_CODES_HAZE or t in WX_CODES_PRECIP or t in WX_CODES_THUNDER:
                result["wx_codes"].append(t)
            m = re.match(r'^[-+]?(VC)?(' + "|".join(WX_CODES_PRECIP | WX_CODES_THUNDER | WX_CODES_FOG) + r')$', t)
            if m and t not in result["wx_codes"]:
                result["wx_codes"].append(t)
        for t in tokens:
            m = re.match(r'^(FEW|SCT|BKN|OVC|CLR|SKC|CAVOK)(\d{3})?$', t)
            if m:
                result["clouds"].append(t)
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

