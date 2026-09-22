"""Шляхи до даних застосунку (звичайний запуск і PyInstaller exe)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Каталог поруч із exe — для config.json і користувацьких пресетів."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """Каталог із ресурсами, упакованими в exe (_MEIPASS)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_root()))
    return Path(__file__).resolve().parent.parent


def bundled_presets_dir() -> Path:
    return resource_root() / "presets"


def sync_bundled_presets(target_dir: Path) -> None:
    """Копіює вбудовані пресети поруч із exe, якщо їх ще немає."""
    source = bundled_presets_dir()
    if not source.is_dir():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for src in source.glob("*.json"):
        dst = target_dir / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
