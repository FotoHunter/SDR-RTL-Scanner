# loc_manager.py
import json
import os
from pathlib import Path

class LocManager:
    def __init__(self, locale_dir: str = "locales"):
        self.locale_dir = Path(locale_dir)
        self.strings = {}

    def load(self, lang: str = "ru_RU") -> None:
        path = self.locale_dir / f"{lang}.json"
        if not path.exists():
            # fallback на ru_RU
            path = self.locale_dir / "ru_RU.json"
        with open(path, "r", encoding="utf-8") as f:
            self.strings = json.load(f)

    def get(self, key: str, default: str = "") -> str:
        return self.strings.get(key, default)

