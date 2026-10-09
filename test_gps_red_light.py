#!/usr/bin/env python3
# test_red_light.py
import sys
from core.gpsd_data import get_data
from core.gps_quality import analyze

def main():
    tpv, sky = get_data()
    result = analyze(tpv, sky)
    
    print(f"Status: {result['status']}")
    print(f"Quality: {result['quality']}")
    
    if "reason" in result:
        print(f"Reason: {result['reason']}")
    
    if "metrics" in result:
        m = result["metrics"]
        print(f"Seen: {m['seen']}, Used: {m['used']}")
        print(f"Avg SNR: {m['avg_snr']:.2f} dBHz, Alive Ratio: {m['alive_ratio']:.2f}")
    
    # Эмуляция «красной лампочки»
    if result["status"] == "TRACK_LOST_SELECTIVE":
        print("🚨 RED LIGHT: Critical selective interference detected!")
        sys.exit(1)
    elif result["status"] == "GOOD":
        print("✅ GREEN LIGHT: Navigation solution valid.")
        sys.exit(0)
    else:
        print("⚠️ YELLOW LIGHT: No fix or degraded signal.")
        sys.exit(2)

if __name__ == "__main__":
    main()
