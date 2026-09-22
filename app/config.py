"""Збереження/завантаження налаштувань застосунку (порт, бод, адреса) у config.json."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import i18n
from .paths import app_root

CONFIG_PATH = app_root() / "config.json"


@dataclass
class AppConfig:
    port: str = ""
    baudrate: int = 9600
    address: int = 1
    last_preset: str = ""
    language: str = "uk"


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return AppConfig(
            port=data.get("port", ""),
            baudrate=int(data.get("baudrate", 9600)),
            address=int(data.get("address", 1)),
            last_preset=data.get("last_preset", ""),
            language=i18n.normalize_lang(str(data.get("language", "uk") or "uk")),
        )
    except (json.JSONDecodeError, OSError, ValueError):
        return AppConfig()


def save_config(config: AppConfig) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
