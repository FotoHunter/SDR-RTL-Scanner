# core/gps_quality.py
import statistics


def analyze(tpv, sky):
    """Оценивает качество GPS-приёма.

    Возвращает dict:
      status  : NO_GPS_HARDWARE | GPS_OK | INDOOR | ANOMALY
      quality : UNKNOWN | HIGH | LOW | CRITICAL
      reason  : человекочитаемая причина (для лога/отчёта)
      metrics : справочные числа (НЕ влияют на status)

    Логика (строгий порядок):
      1. Нет данных от gpsd       → NO_GPS_HARDWARE
      2. Есть фикс (mode >= 2)    → GPS_OK
      3. Фикса нет, но есть
         спутники с сигналом      → INDOOR
      4. Фикса нет, все спутники
         без сигнала              → ANOMALY

    Метрики (jam, DOP, avg_snr, avg_snr_used, alive_ratio, snrs_with_signal)
    сохраняются для статистики, но на status не влияют.
    """
    # ── 1. Нет данных от gpsd ──
    if not tpv or not sky:
        return {
            "status": "NO_GPS_HARDWARE",
            "quality": "UNKNOWN",
            "reason": "No data from gpsd (no GPS module or gpsd unavailable)",
            "metrics": {},
        }

    sats = sky.get("satellites") if isinstance(sky, dict) else None
    if not isinstance(sats, list) or len(sats) == 0:
        return {
            "status": "NO_GPS_HARDWARE",
            "quality": "UNKNOWN",
            "reason": "gpsd returned no satellite list",
            "metrics": {},
        }

    seen = len(sats)
    used = sum(1 for s in sats if s.get("used"))
    snrs = [s.get("ss", 0) for s in sats if s.get("ss") is not None]
    used_snrs = [s.get("ss", 0) for s in sats if s.get("used") and s.get("ss") is not None]
    snrs_with_signal = sum(1 for snr in snrs if snr > 0)
    alive_count = sum(1 for snr in snrs if snr > 10)

    avg_snr = statistics.mean(snrs) if snrs else 0.0
    avg_snr_used = statistics.mean(used_snrs) if used_snrs else 0.0
    alive_ratio = alive_count / seen if seen > 0 else 0.0

    # gpsd mode: 0=unknown, 1=no fix, 2=2D fix, 3=3D fix
    mode = tpv.get("mode", 0)

    # Индикатор помех приёмника (gpsd "jam", 0..255). Не вердикт — числовое поле.
    jam = tpv.get("jam")
    jam_ratio = (jam / 255.0) if isinstance(jam, (int, float)) else None

    # ── Полные метрики (для статистики, не для решения) ──
    metrics = {
        "mode": mode,
        "seen": seen,
        "used": used,
        "snrs_with_signal": snrs_with_signal,
        "alive_count": alive_count,
        "alive_ratio": alive_ratio,
        "avg_snr": avg_snr,
        "avg_snr_used": avg_snr_used,
        "hdop": sky.get("hdop") if isinstance(sky, dict) else None,
        "vdop": sky.get("vdop") if isinstance(sky, dict) else None,
        "pdop": sky.get("pdop") if isinstance(sky, dict) else None,
        "gdop": sky.get("gdop") if isinstance(sky, dict) else None,
        "jam": jam,
        "jam_ratio": jam_ratio,
    }

    # ── 2. Есть координаты → GPS_OK ──
    if mode >= 2:
        return {
            "status": "GPS_OK",
            "quality": "HIGH",
            "reason": "Fix acquired",
            "metrics": metrics,
        }

    # ── 3. Нет фикса, но есть сигнал хотя бы у одного спутника → INDOOR ──
    if seen >= 4 and snrs_with_signal >= 1:
        return {
            "status": "INDOOR",
            "quality": "LOW",
            "reason": "No fix, weak signal (indoor conditions expected)",
            "metrics": metrics,
        }

    # ── 4. Нет фикса, все спутники без сигнала → ANOMALY ──
    if seen >= 4 and snrs_with_signal == 0:
        return {
            "status": "ANOMALY",
            "quality": "CRITICAL",
            "reason": "No fix, all visible satellites have no signal",
            "metrics": metrics,
        }

    # ── 5. Пограничный случай (мало спутников, нет фикса) → INDOOR ──
    return {
        "status": "INDOOR",
        "quality": "LOW",
        "reason": "No fix, insufficient satellites",
        "metrics": metrics,
    }
