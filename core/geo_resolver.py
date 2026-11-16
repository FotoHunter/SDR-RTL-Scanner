# core/geo_resolver.py
"""
Резолвер «название места → координаты».
Гибридная схема:
  1) Кэш в regions/metar_registry.json
  2) GeoNames cities1000.txt (офлайн)
  3) Nominatim через geopy (онлайн, fallback)
"""
import json
import os
import time
import re
from typing import Optional, Tuple, Dict, Any

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGIONS    = os.path.join(BASE_DIR, "regions")
CITIES_TXT = os.path.join(REGIONS, "cities1000.txt")
REGISTRY   = os.path.join(REGIONS, "metar_registry.json")

# Минимальный маппинг «слаг региона → человекочитаемое имя»,
# чтобы уметь искать в GeoNames/Nominatim по региону без GPS.
REGION_HUMAN = {
    "RU.spb": ("Saint Petersburg", "RU"),
    "RU.msk": ("Moscow", "RU"),
    "RU.ekb": ("Yekaterinburg", "RU"),
    "RU.nsk": ("Novosibirsk", "RU"),
    "RU.kzn": ("Kazan", "RU"),
    "RU.perm": ("Perm", "RU"),
    "FI.hel": ("Helsinki", "FI"),
    "DE.ber": ("Berlin", "DE"),
    "CN.pek": ("Beijing", "CN"),
    "US.nyc": ("New York", "US"),
}


# ─────────────────────── кэш ───────────────────────

def _load_registry() -> dict:
    try:
        with open(REGISTRY, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 1, "updated_at": None, "stations": {}}

def _save_registry(data: dict) -> None:
    from datetime import datetime, timezone
    data["updated_at"] = datetime.now(timezone.utc).date().isoformat()
    os.makedirs(REGIONS, exist_ok=True)
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def lookup_cached(region: str) -> Optional[Dict[str, Any]]:
    """Возвращает запись для региона или None."""
    return _load_registry().get("stations", {}).get(region)

def cache_coords(region: str, lat: float, lon: float, source: str) -> None:
    """Сохраняет координаты в кэш (не затирая icao, если он уже есть)."""
    data = _load_registry()
    entry = data.setdefault("stations", {}).setdefault(region, {})
    entry["lat"] = lat
    entry["lon"] = lon
    entry.setdefault("source_coords", source)
    _save_registry(data)


# ─────────────────────── GeoNames ───────────────────────

_geonames_cache: Optional[list] = None  # ленивая загрузка

def _load_geonames() -> list:
    """Читает cities1000.txt → [(name_lower, ascii_lower, country, lat, lon, population), ...]."""
    global _geonames_cache
    if _geonames_cache is not None:
        return _geonames_cache

    rows: list = []
    if not os.path.isfile(CITIES_TXT):
        print(f"[!] GeoNames dump not found: {CITIES_TXT}")
        _geonames_cache = rows
        return rows

    with open(CITIES_TXT, "r", encoding="utf-8") as f:
        for line in f:
            # Схлопываем множественные пробелы/табы в один пробел
            parts = re.split(r'\s+', line.strip())
            
            # В этом файле минимум 15 колонок, но из-за пустых полей их может быть больше
            if len(parts) < 15:
                continue
            
            try:
                name_lower = parts[1].lower()
                ascii_lower = parts[2].lower() if len(parts) > 2 else name_lower
                country = parts[8].upper() if len(parts) > 8 else ""
                lat = float(parts[4])
                lon = float(parts[5])
                
                # Население может быть пустым или отсутствовать в коротких строках
                try:
                    pop = int(parts[14]) if len(parts) > 14 else 0
                except (ValueError, IndexError):
                    pop = 0
                
                rows.append((name_lower, ascii_lower, country, lat, lon, pop))
            except (ValueError, IndexError):
                continue
                
    _geonames_cache = rows
    return rows

def geonames_lookup(name: str, country: Optional[str] = None) -> Optional[Tuple[float, float]]:
    """Ищет город в GeoNames-дампе. Возвращает (lat, lon) или None."""
    if not name:
        return None
    name_l = name.lower().strip()
    country_u = (country or "").upper() or None

    best = None  # (population, lat, lon)
    for n_lat, n_ascii, n_country, lat, lon, pop in _load_geonames():
        if n_lat != name_l and n_ascii != name_l:
            continue
        if country_u and n_country != country_u:
            continue
        if best is None or pop > best[0]:
            best = (pop, lat, lon)

    if best is None:
        return None
    return best[1], best[2]


# ─────────────────────── Nominatim ───────────────────────

_LAST_NOMINATIM_TS = 0.0
_NOMINATIM_MIN_INTERVAL = 1.1  # сек (политика: 1 req/sec)

def _nominatim_rate_limit() -> None:
    global _LAST_NOMINATIM_TS
    elapsed = time.time() - _LAST_NOMINATIM_TS
    if elapsed < _NOMINATIM_MIN_INTERVAL:
        time.sleep(_NOMINATIM_MIN_INTERVAL - elapsed)
    _LAST_NOMINATIM_TS = time.time()

def nominatim_lookup(name: str, country: Optional[str] = None) -> Optional[Tuple[float, float]]:
    """Онлайн-фолбэк. Возвращает (lat, lon) или None."""
    try:
        from geopy.geocoders import Nominatim
    except ImportError:
        print("[!] geopy not installed, skipping Nominatim fallback")
        return None

    query = name if not country else f"{name}, {country}"
    _nominatim_rate_limit()
    try:
        geo = Nominatim(user_agent="SDR-RTL-Scanner/0.8.8 (+github.com/user/repo)")
        loc = geo.geocode(query, timeout=8)
    except Exception as e:
        print(f"[!] Nominatim error: {e}")
        return None

    if not loc:
        return None
    return float(loc.latitude), float(loc.longitude)


# ─────────────────────── публичный API ───────────────────────

def region_to_coords(region: str,
                     allow_network: bool = True,
                     country_hint: Optional[str] = None) -> Optional[Tuple[float, float]]:
    """
    Регион ("RU.spb") → (lat, lon).
    Порядок: кэш → GeoNames → Nominatim.
    """
    # 1. Кэш
    cached = lookup_cached(region)
    if cached and cached.get("lat") is not None and cached.get("lon") is not None:
        return float(cached["lat"]), float(cached["lon"])

    # 2. GeoNames
    human_name, human_cc = REGION_HUMAN.get(region, (None, None))
    if human_name:
        coords = geonames_lookup(human_name, human_cc or country_hint)
        if coords:
            cache_coords(region, coords[0], coords[1], source="geonames")
            return coords

    # 3. Nominatim
    if allow_network and human_name:
        coords = nominatim_lookup(human_name, human_cc or country_hint)
        if coords:
            cache_coords(region, coords[0], coords[1], source="nominatim")
            return coords

    return None


def place_to_coords(place: str,
                    country: Optional[str] = None,
                    allow_network: bool = True) -> Optional[Tuple[float, float]]:
    """
    Свободная строка ("Санкт-Петербург", "Saint Petersburg, Russia")
    → (lat, lon). GeoNames → Nominatim.
    """
    # 1. GeoNames
    coords = geonames_lookup(place, country)
    if coords:
        return coords

    # 2. Nominatim
    if allow_network:
        return nominatim_lookup(place, country)

    return None

