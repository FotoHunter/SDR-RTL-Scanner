# core/gps_quality.py
import statistics

def analyze(tpv, sky):
    if not tpv or not sky:
        return {"status": "NO_DATA", "quality": "UNKNOWN"}

    sats = sky.get("satellites") if isinstance(sky, dict) else B
    seen = len(sats)

#    mode = tpv.get("mode", 1)
#    sats = sky.get("satellites", )
#    seen = len(sats)
    used = sum(1 for s in sats if s.get("used"))
    snrs = [s.get("ss", 0) for s in sats if s.get("ss") is not None]

    avg_snr = statistics.mean(snrs) if snrs else 0.0
    alive_count = sum(1 for s in snrs if s > 10)
    alive_ratio = alive_count / seen if seen > 0 else 0.0

    mode = tpv.get("mode", 1)

    if mode >= 2:
        return {
            "status": "GOOD",
            "quality": "HIGH",
            "metrics": {"mode": mode, "seen": seen, "used": used, "avg_snr": avg_snr, "alive_ratio": alive_ratio}
        }

    if alive_ratio < 0.2:
        return {
            "status": "TRACK_LOST_SELECTIVE",
            "quality": "CRITICAL",
            "reason": "Severe selective interference or antenna fault",
            "metrics": {"mode": mode, "seen": seen, "used": used, "avg_snr": avg_snr, "alive_ratio": alive_ratio}
        }

    return {
        "status": "NO_FIX",
        "quality": "LOW",
        "metrics": {"mode": mode, "seen": seen, "used": used, "avg_snr": avg_snr, "alive_ratio": alive_ratio}
    }
