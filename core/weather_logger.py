# core/weather_logger.py
import json
import os
from datetime import datetime, timezone

LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "weather_history.jsonl"
)

def log_snapshot(context):
    """Сохраняет текущий снимок погоды и космической погоды в JSONL."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "observer_region": context.observer.get("region", "unknown"),
        "metar": context.weather,
        "space_weather": context.space_weather,
        "gps": context.gps_quality
    }
    
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

