"""Бібліотека пресетів профілю нагріву (JSON-файли у папці `presets/`).

Формат файлу:
{
  "name": "LGA1151 Socket",
  "holdback": 5.0,
  "segments": [
    {"ramp": 0.70, "target": 160.0, "dwell": 60.0},
    ...
    (рівно 8 елементів; ramp у °C/с, target у °C, dwell у секундах)
  ]
}
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .paths import app_root, sync_bundled_presets
from .profile_controller import ProfileUI, SegmentUI, SEGMENT_COUNT

PRESETS_DIR = app_root() / "presets"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    return slug or "preset"


def ensure_presets_dir() -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    sync_bundled_presets(PRESETS_DIR)


def list_presets() -> list[str]:
    """Повертає список назв пресетів (з поля "name" у файлах), сортовано."""
    ensure_presets_dir()
    names: list[str] = []
    for path in sorted(PRESETS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            names.append(data.get("name", path.stem))
        except (json.JSONDecodeError, OSError):
            continue
    return names


def _path_for_name(name: str) -> Path:
    return PRESETS_DIR / f"{_slugify(name)}.json"


def load_preset(name: str) -> ProfileUI:
    path = _path_for_name(name)
    if not path.exists():
        raise FileNotFoundError(f"Пресет '{name}' не знайдено ({path})")
    data = json.loads(path.read_text(encoding="utf-8"))
    return _from_dict(data)


def save_preset(name: str, profile: ProfileUI) -> None:
    ensure_presets_dir()
    path = _path_for_name(name)
    data = _to_dict(name, profile)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_preset(name: str) -> None:
    path = _path_for_name(name)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Конвертація ProfileUI <-> dict
# ---------------------------------------------------------------------------
def _to_dict(name: str, profile: ProfileUI) -> dict:
    return {
        "name": name,
        "holdback": profile.holdback,
        "segments": [
            {"ramp": seg.ramp, "target": seg.target, "dwell": seg.dwell}
            for seg in profile.segments
        ],
    }


def _from_dict(data: dict) -> ProfileUI:
    profile = ProfileUI(holdback=data.get("holdback"))
    raw_segments = data.get("segments", [])
    for i in range(SEGMENT_COUNT):
        if i < len(raw_segments):
            raw = raw_segments[i]
            profile.segments[i] = SegmentUI(
                ramp=raw.get("ramp"), target=raw.get("target"), dwell=raw.get("dwell")
            )
        else:
            profile.segments[i] = SegmentUI()
    return profile


def seed_default_presets() -> None:
    """Створює приклад пресету 'LGA1151 Socket' (як на скріншотах), якщо він відсутній."""
    ensure_presets_dir()
    if _path_for_name("LGA1151 Socket").exists():
        return
    profile = ProfileUI(holdback=5.0)
    example = [
        (0.70, 160.0, 60.0),
        (0.60, 225.0, 40.0),
        (0.50, 235.0, 120.0),
        (-0.01, 180.0, 30.0),
        (1.50, 210.0, 30.0),
        (1.50, 245.0, 30.0),
        (-0.01, 265.0, 40.0),
        (3.00, 275.0, 40.0),
    ]
    for i, (ramp, target, dwell) in enumerate(example):
        profile.segments[i] = SegmentUI(ramp=ramp, target=target, dwell=dwell)
    save_preset("LGA1151 Socket", profile)
