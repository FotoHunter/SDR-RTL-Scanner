# core/station_metadata.py
import json, os
from pathlib import Path

_CACHE = {}
_LOADED_FLAG = False

def _registry_path(region: str, regions_dir: Path) -> Path:
    for c in [
        regions_dir / f"broadcast_centers_{region}.json",
        regions_dir / f"broadcast_centers_{region.lower()}.json",
    ]:
        if c.exists():
            return c
    return None


def load_broadcast_centers(region: str, regions_dir: Path) -> dict:
    global _LOADED_FLAG
    cache_key = f"{region}_{regions_dir}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    path = _registry_path(region, regions_dir)
    if not path:
        if not region.startswith("RU."):
            print(f"[*] No broadcast centers for {region} "
                  f"(non-RU regions are not pre-populated; add broadcast_centers_{region}.json manually)")
        _CACHE[cache_key] = {"sites": {}, "assignments": []}
        return _CACHE[cache_key]

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("sites", {})
        data.setdefault("assignments", [])

        status = data.get("status", "stable")
        if status == "beta":
            print(f"[!] Beta: broadcast centers for {region} are experimental")
        elif not _LOADED_FLAG:
            print(f"[*] Loaded broadcast centers: {path.name} "
                  f"({len(data['sites'])} sites, {len(data['assignments'])} assignments)")
            _LOADED_FLAG = True

        _CACHE[cache_key] = data
        return data
    except Exception as e:
        print(f"[!] Error loading {path}: {e}")
        _CACHE[cache_key] = {"sites": {}, "assignments": []}
        return _CACHE[cache_key]


def enrich_station(station: dict, region: str, regions_dir: Path,
                   observer_coords=None):
    data = load_broadcast_centers(region, regions_dir)

    freq_str = f"{station.get('freq', 0):.1f}"
    site_id = None
    site_source = None
    site_confidence = None

    # 1. Явная привязка из списка assignments (ищем по частоте)
    for a in data["assignments"]:
        if f"{a.get('freq_mhz', 0):.1f}" == freq_str:
            site_id = a.get("site")
            site_source = a.get("source", "unknown")
            site_confidence = a.get("confidence", "unknown")
            break

    # 2. Автопоиск по координатам наблюдателя (если явной привязки нет)
    site_geo = None
    if not site_id and observer_coords and data["sites"]:
        best_id, best_d = None, None
        olat, olon = observer_coords
        if olat is not None and olon is not None:
            for sid, s in data["sites"].items():
                slat, slon = s.get("lat"), s.get("lon")
                if slat is None or slon is None:
                    continue
                d = ((slat - olat) * 111.0) ** 2 + ((slon - olon) * 55.0) ** 2
                if best_d is None or d < best_d:
                    best_d, best_id = d, sid
        if best_id:
            site_id = best_id
            site_source = "auto"
            site_confidence = "medium"

    # 3. Геоданные вышки
    if site_id and site_id in data["sites"]:
        s = data["sites"][site_id]
        site_geo = {"lat": s.get("lat"), "lon": s.get("lon")}
        site_name = s.get("name_ru", site_id)
    else:
        site_name = ""

    station["site"] = site_name
    station["site_id"] = site_id
    station["site_geo"] = site_geo
    station["site_source"] = site_source
    station["site_confidence"] = site_confidence
    return station
