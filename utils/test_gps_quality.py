#!/usr/bin/env python3
# test_gps_quality.py — research script for evaluating GPS reception quality.
# Designed for receiver debugging and analyzing signal conditions in urban environments.
# Does not include any functionality for generating interference or controlling radio hardware.
import os
import sys

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from core.gpsd_data import get_data
from core.gps_quality import analyze


def main():
    tpv, sky = get_data()
    result = analyze(tpv, sky)

    print(f"Status: {result['status']}")
    print(f"Quality: {result['quality']}")

    if result.get("reason"):
        print(f"Reason: {result['reason']}")

    m = result.get("metrics", {})
    if m:
        print(f"Seen: {m.get('seen', '?')}, Used: {m.get('used', '?')}, "
              f"With signal: {m.get('snrs_with_signal', '?')}, "
              f"Alive: {m.get('alive_count', '?')}")
        print(f"Avg SNR: {m.get('avg_snr', 0):.2f} dBHz, "
              f"Used SNR: {m.get('avg_snr_used', 0):.2f} dBHz, "
              f"Alive ratio: {m.get('alive_ratio', 0):.2f}")
        dop = [f"{k.upper()}={m[k]}" for k in ("hdop", "vdop", "pdop", "gdop")
               if m.get(k) is not None]
        if dop:
            print(f"DOP: {', '.join(dop)}")
        if m.get("jam") is not None:
            print(f"Interference indicator: {m['jam']}/255 "
                  f"(ratio {m.get('jam_ratio', 0):.2f})")

    status = result["status"]
    if status == "NO_GPS_HARDWARE":
        print("⚪ GRAY LIGHT: GPS module not detected.")
        sys.exit(0)
    elif status == "GPS_OK":
        print("✅ GREEN LIGHT: GPS works, coordinates available.")
        sys.exit(0)
    elif status == "INDOOR":
        print("🟡 YELLOW LIGHT: Weak signal (indoor conditions).")
        sys.exit(2)
    elif status == "ANOMALY":
        print("🚨 RED LIGHT: All visible satellites have no signal (reception anomaly).")
        sys.exit(1)
    else:
        print(f"⚠️ UNKNOWN status: {status}")
        sys.exit(3)


if __name__ == "__main__":
    main()
