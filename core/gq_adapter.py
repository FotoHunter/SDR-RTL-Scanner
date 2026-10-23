# core/gq_adapter.py
from enum import Enum

class GQStatus(Enum):
    CLEAR = "CLEAR"   # можно доверять, RDS декодировать
    INDOOR = "INDOOR" # ограничения среды, данные сомнительны
    ANOMALY = "ANOMALY" # данные противоречивы, не доверять
    NO_GPS_HARDWARE = "NO_GPS_HARDWARE"  # GPS модуля нет или он недоступен

def map_gps_status_to_gq(tpv, sky, gps_result=None):
    """
    tpv, sky — сырые данные от gpsd_data.get_data()
    gps_result — результат core.gps_quality.analyze (если уже посчитан)

    Возвращает GQStatus.
    """
    # Случай 1: GPS вообще не ответил (нет модуля, порт занят, сеть упала)
    if tpv is None or sky is None:
        return GQStatus.NO_GPS_HARDWARE

    # Если результат анализа ещё не передан — считаем его
    if gps_result is None:
        from core.gps_quality import analyze
        gps_result = analyze(tpv, sky)

    status = gps_result.get("status", "UNKNOWN")
    quality = gps_result.get("quality", "UNKNOWN")

    # Случай 2: есть решение — CLEAR
    if status == "GOOD":
        return GQStatus.CLEAR

    # Случай 3: явная проблема (помехи/антенна) — ANOMALY
    if status == "TRACK_LOST_SELECTIVE":
        return GQStatus.ANOMALY

    # Случай 4: нет решения, но модуль есть и отвечает — INDOOR
    # Сюда попадают NO_FIX, LOW и т.п.
    return GQStatus.INDOOR

