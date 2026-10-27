# core/region_detect.py
"""
Определение региона по доступным источникам.

Приоритеты:
  1. observer.json → region      (пользователь сказал — главное)
  2. GPS (mode >= 2)             (точные координаты)
  3. IPv4                        (работает всегда, но VPN врёт)
  4. Cell ID                     (заглушка, потом)
  5. fallback                    (последний рубеж)
"""
import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any


# ── Таблица маппинга «английское название города → слаг региона» ──
# Нужна, потому что reverse geocoding даёт "Saint Petersburg",
# а в проекте — "RU.spb". Расширяй по мере необходимости.
CITY_SLUG_MAP = {
    "saint petersburg": "spb",
    "st petersburg": "spb",
    "st. petersburg": "spb",
    "санкт-петербург": "spb",
    "moscow": "msk",
    "москва": "msk",
    "yekaterinburg": "ekb",
    "екатеринбург": "ekb",
    "novosibirsk": "nsk",
    "новосибирск": "nsk",
    "kazan": "kzn",
    "казань": "kzn",
    "nizhny novgorod": "nnv",
    "нижний новгород": "nnv",
    # ... добавляй по мере надобности
}


def _city_to_slug(city: str) -> str:
    """«Saint Petersburg» → «spb», «Moscow» → «msk», иначе — slug."""
    if not city:
        return ""
    c = city.strip().lower()
    if c in CITY_SLUG_MAP:
        return CITY_SLUG_MAP[c]
    # fallback: транслит/замена пробелов
    return c.replace(" ", "_").replace("-", "_")[:20]


def detect_region_from_ipv4(timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """Определяет регион по внешнему IPv4.

    Возвращает {"region": "RU.spb", "country": "RU", "city": "spb", "source": "ipv4"} или None.
    """
    try:
        # ipwho.is — без ключа, простой JSON
        req = urllib.request.Request(
            "https://ipwho.is/",
            headers={"User-Agent": "SDR-Scanner/0.8.6"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if not data.get("success", False):
        return None

    country = data.get("country_code")  # "RU"
    city = data.get("city")             # "Saint Petersburg"
    if not country:
        return None

    city_slug = _city_to_slug(city)
    region = f"{country}.{city_slug}" if city_slug else country

    return {
        "region": region,
        "country": country,
        "city": city_slug,
        "source": "ipv4",
        "raw": {"ip": data.get("ip"), "city_raw": city},
    }


def detect_region_from_gps(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Определяет регион по координатам GPS (офлайн, через offlineRevGeocoder)."""
    try:
        from offlineRevGeocoder import get as rev_geocode
    except ImportError:
        return None

    try:
        r = rev_geocode(lat, lon)
    except Exception:
        return None

    if not r.country_iso2:
        return None

    city_slug = _city_to_slug(r.city or "")
    region = f"{r.country_iso2}.{city_slug}" if city_slug else r.country_iso2

    return {
        "region": region,
        "country": r.country_iso2,
        "city": city_slug,
        "source": "gps",
        "raw": {"lat": lat, "lon": lon, "city_raw": r.city},
    }


def detect_region_from_cellid() -> Optional[Dict[str, Any]]:
    """Заглушка: определение по вышке сотовой связи (потом)."""
    return None


def resolve_region(
    observer_cfg: dict,
    gps_quality: Optional[dict] = None,
    allow_network: bool = True,
) -> Optional[Dict[str, Any]]:
    """Определяет регион по цепочке приоритетов.

    Возвращает dict с полем "source": "config" | "gps" | "ipv4" | "cellid" | None.
    """
    # ── 1. observer.json ──
    if observer_cfg.get("region"):
        return {
            "region": observer_cfg["region"],
            "source": "config",
        }

    # ── 2. GPS (только если есть фикс!) ──
    if gps_quality:
        pos = gps_quality.get("position") or {}
        mode = (gps_quality.get("metrics") or {}).get("mode")
        if mode is not None and mode >= 2 and pos.get("lat") is not None:
            r = detect_region_from_gps(pos["lat"], pos["lon"])
            if r:
                return r

    # ── 3. IPv4 ──
    if allow_network:
        r = detect_region_from_ipv4()
        if r:
            return r

    # ── 4. Cell ID (заглушка) ──
    r = detect_region_from_cellid()
    if r:
        return r

    return None

def save_region_to_observer(region: str, path: str = "config/observer.json") -> bool:
    """Сохраняет подтверждённый регион в observer.json.

    Возвращает True при успехе, False при ошибке.
    Не падает: если файл битый/нет прав — печатает предупреждение и возвращает False.
    """
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    except json.JSONDecodeError as e:
        print(f"[!] Cannot save region: invalid JSON in {path}: {e}")
        return False

    cfg["region"] = region

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except (PermissionError, OSError) as e:
        print(f"[!] Cannot save region to {path}: {e}")
        return False

    print(f"[*] Region saved to {path}: {region}")
    return True
