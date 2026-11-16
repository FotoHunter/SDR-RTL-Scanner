# core/metar_registry.py
"""
Локальный справочник METAR-станций (regions/metar_registry.json).

Логика:
  - lookup(region)     → запись из справочника или None
  - resolve(region, lat, lon) → запись, автоматически добытая и сохранённая
  - Все сетевые запросы кэшируются: TTL 24ч, rate-limit 100/мин (AWC).
"""
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "regions", "metar_registry.json",
)

AWC_URL = "https://aviationweather.gov/api/data/stationinfo?bbox={lat_min},{lon_min},{lat_max},{lon_max}&format=json"
USER_AGENT = "SDR-Scanner/0.8.8"
TTL = timedelta(hours=24)


def _load() -> dict:
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 1, "updated_at": None, "stations": {}}


def _save(data: dict) -> None:
    data["updated_at"] = datetime.now(timezone.utc).date().isoformat()
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def lookup(region: str) -> Optional[Dict[str, Any]]:
    """Возвращает запись без обращения к сети."""
    data = _load()
    return data.get("stations", {}).get(region)


def _is_fresh(entry: dict) -> bool:
    lv = entry.get("last_verified")
    if not lv:
        return False
    try:
        d = datetime.fromisoformat(lv)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - d) < TTL


def _query_awc(lat: float, lon: float, radius_deg: float = 1.5) -> Optional[dict]:
    """Ищет ближайшую станцию с METAR в окне ±radius_deg."""
    url = AWC_URL.format(
        lat_min=lat - radius_deg, lat_max=lat + radius_deg,
        lon_min=lon - radius_deg, lon_max=lon + radius_deg,
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[!] METAR registry: AWC query failed: {e}")
        return None

    if not isinstance(data, list) or not data:
        return None

    # Отбираем станции с METAR и ищем ближайшую
    best, best_d = None, None
    for s in data:
        if not s.get("metar"):
            continue
        try:
            slat, slon = float(s["lat"]), float(s["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        d = (slat - lat) ** 2 + (slon - lon) ** 2
        if best_d is None or d < best_d:
            best_d, best = d, s

    if not best:
        return None

    return {
        "icao": best.get("icaoId", "").strip(),
        "name": best.get("site", "").strip(),
        "lat": float(best["lat"]),
        "lon": float(best["lon"]),
        "last_verified": datetime.now(timezone.utc).date().isoformat(),
        "source": "auto",
    }


def resolve(region: str, lat: Optional[float] = None, lon: Optional[float] = None,
            allow_network: bool = True) -> Optional[Dict[str, Any]]:
    """
    Возвращает запись для региона; при наличии сети и отсутствии кэша — добывает и сохраняет.
    """
    entry = lookup(region)
    if entry and _is_fresh(entry):
        return entry

    if not allow_network:
        return entry  # устаревшая, но лучше, чем ничего

    if lat is None or lon is None:
        # Без координат не можем искать — возвращаем что есть
        return entry

    fresh = _query_awc(lat, lon)
    if not fresh or not fresh.get("icao"):
        print(f"[!] METAR registry: no station with METAR near {lat},{lon}")
        return entry

    data = _load()
    data.setdefault("stations", {})[region] = fresh
    _save(data)
    print(f"[*] METAR registry: {region} → {fresh['icao']} ({fresh['name']}) "
          f"at {fresh['lat']:.4f},{fresh['lon']:.4f}")
    return fresh

