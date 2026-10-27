# core/gq_adapter.py
"""
Адаптер GPS-качества к Context.

Приводит сырые данные gpsd + результат gps_quality.analyze
к единому dict-формату, который Context, save_results и export_html
ожидают в observation_context["gps_quality"].

Терминология:
  - interference     — индикатор помех
  - anomaly          — аномалия приёма (обобщённое)
  - indoor           — ограничения среды (закрытое помещение)

Никаких спекуляций о природе помех — только то, что видно по метрикам.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any

from core.gpsd_data import get_data
from core.gps_quality import analyze


class GQStatus(Enum):
    CLEAR = "CLEAR"                       # GPS работает, координаты есть
    INDOOR = "INDOOR"                     # сигнал слабый, фикса нет (помещение)
    ANOMALY = "ANOMALY"                   # нет сигнала (аномалия приёма)
    NO_GPS_HARDWARE = "NO_GPS_HARDWARE"   # модуля нет / gpsd недоступен


def map_gps_status_to_gq(tpv, sky, gps_result=None) -> GQStatus:
    """gps_quality.status → GQStatus (внутренний enum)."""
    if tpv is None or sky is None:
        return GQStatus.NO_GPS_HARDWARE

    if gps_result is None:
        gps_result = analyze(tpv, sky)

    status = gps_result.get("status", "NO_GPS_HARDWARE")

    if status == "GPS_OK":
        return GQStatus.CLEAR
    if status == "INDOOR":
        return GQStatus.INDOOR
    if status == "ANOMALY":
        return GQStatus.ANOMALY
    return GQStatus.NO_GPS_HARDWARE


def read_gps_quality(
    host: str = "127.0.0.1",
    port: int = 2947,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Считывает GPS-качество и возвращает dict в едином формате.

    Формат (все поля могут быть None, если данных нет):
      {
        "available": bool,
        "source": "gpsd" | "none" | "error",
        "timestamp_utc": "...",
        "gq_status": "CLEAR" | "INDOOR" | "ANOMALY" | "NO_GPS_HARDWARE",
        "status": "GPS_OK" | "INDOOR" | "ANOMALY" | "NO_GPS_HARDWARE",
        "quality": "HIGH" | "LOW" | "CRITICAL" | "UNKNOWN",
        "reason": str | None,
        "metrics": {
            "mode": int, "mode_text": "3D" | "2D" | "no fix" | "unknown",
            "seen": int, "used": int,
            "snrs_with_signal": int, "alive_count": int, "alive_ratio": float,
            "avg_snr": float, "avg_snr_used": float,
            "hdop": float | None, "vdop": float | None,
            "pdop": float | None, "gdop": float | None,
            "jam": int | None, "jam_ratio": float | None,
        },
        "position": {"lat": float|None, "lon": float|None, "alt_m": float|None},
        "anomaly": {"suspected": bool, "reasons": [str], "severity": "none|low|high"},
        "error": str | None,
      }
    """
    # ── Читаем сырые данные ──
    try:
        tpv, sky = get_data(host=host, port=port, timeout=timeout)
    except Exception as e:
        return {
            "available": False,
            "source": "error",
            "gq_status": GQStatus.NO_GPS_HARDWARE.value,
            "status": "NO_GPS_HARDWARE",
            "quality": "UNKNOWN",
            "reason": str(e),
            "metrics": {},
            "position": {"lat": None, "lon": None, "alt_m": None},
            "anomaly": {"suspected": False, "reasons": [], "severity": "none"},
            "error": str(e),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

    # ── GPS недоступен ──
    if tpv is None or sky is None:
        return {
            "available": False,
            "source": "none",
            "gq_status": GQStatus.NO_GPS_HARDWARE.value,
            "status": "NO_GPS_HARDWARE",
            "quality": "UNKNOWN",
            "reason": "no data from gpsd",
            "metrics": {},
            "position": {"lat": None, "lon": None, "alt_m": None},
            "anomaly": {"suspected": False, "reasons": [], "severity": "none"},
            "error": "no data from gpsd",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

    # ── Анализ ──
    gps_result = analyze(tpv, sky)
    gq = map_gps_status_to_gq(tpv, sky, gps_result)

    metrics_raw = gps_result.get("metrics", {}) or {}
    mode = metrics_raw.get("mode")
    mode_text = {0: "unknown", 1: "no fix", 2: "2D", 3: "3D"}.get(mode, "?") if mode is not None else "?"

    position = {
        "lat": tpv.get("lat"),
        "lon": tpv.get("lon"),
        "alt_m": tpv.get("altMSL") or tpv.get("alt"),
    }

    is_anomaly = (gq == GQStatus.ANOMALY)
    anomaly_reasons = []
    if is_anomaly and gps_result.get("reason"):
        anomaly_reasons.append(gps_result["reason"])

    severity = "none"
    if gq == GQStatus.ANOMALY:
        severity = "high"
    elif gq == GQStatus.INDOOR:
        severity = "low"

    return {
        "available": True,
        "source": "gpsd",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "gq_status": gq.value,
        "status": gps_result.get("status", "NO_GPS_HARDWARE"),
        "quality": gps_result.get("quality", "UNKNOWN"),
        "reason": gps_result.get("reason"),
        "metrics": {
            "mode": mode,
            "mode_text": mode_text,
            "seen": metrics_raw.get("seen", 0),
            "used": metrics_raw.get("used", 0),
            "snrs_with_signal": metrics_raw.get("snrs_with_signal", 0),
            "alive_count": metrics_raw.get("alive_count", 0),
            "alive_ratio": metrics_raw.get("alive_ratio", 0.0),
            "avg_snr": metrics_raw.get("avg_snr", 0.0),
            "avg_snr_used": metrics_raw.get("avg_snr_used", 0.0),
            "hdop": metrics_raw.get("hdop"),
            "vdop": metrics_raw.get("vdop"),
            "pdop": metrics_raw.get("pdop"),
            "gdop": metrics_raw.get("gdop"),
            "jam": metrics_raw.get("jam"),
            "jam_ratio": metrics_raw.get("jam_ratio"),
        },
        "position": position,
        "anomaly": {
            "suspected": is_anomaly,
            "reasons": anomaly_reasons,
            "severity": severity,
        },
        "error": None,
    }
