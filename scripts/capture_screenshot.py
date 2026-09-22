"""Capture the main window for README (run from project root)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import ImageGrab  # noqa: E402

from app import i18n, presets  # noqa: E402
from app.gui import IR6500App  # noqa: E402

PRESET_NAME = "Lead-Free SAC305 ACHI PTN-2"


def _apply_achi_preset(app: IR6500App) -> None:
    profile = presets.load_preset(PRESET_NAME)
    for i in range(5, 8):
        profile.segments[i].enabled = False
    app.profile = profile
    app._apply_profile_to_fields(profile)
    app.preset_var.set(PRESET_NAME)
    app._apply_section_heights()
    app._redraw_static_profile()


def main() -> None:
    out = ROOT / "docs" / "screenshot.png"
    out.parent.mkdir(exist_ok=True)

    i18n.set_language(i18n.LANG_EN)
    app = IR6500App()
    app.geometry("1100x980")
    app.update_idletasks()

    def capture_and_quit() -> None:
        app.update_idletasks()
        app.update()
        app.lift()
        app.attributes("-topmost", True)
        app.update()
        app.attributes("-topmost", False)
        app.update_idletasks()

        x = app.winfo_rootx()
        y = app.winfo_rooty()
        w = app.winfo_width()
        h = app.winfo_height()
        if w > 1 and h > 1:
            ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(out)
            ramp = app.seg_vars[0]["ramp"].get()
            target = app.seg_vars[0]["target"].get()
            print(f"Saved {out} ({w}x{h}), step1={ramp}/{target}")
        app.destroy()

    def after_connect() -> None:
        # Після connect GUI автоматично робить Download з mock — чекаємо і перезаписуємо профіль.
        _apply_achi_preset(app)
        app.after(900, capture_and_quit)

    app.port_combo.set("MOCK (симулятор)")
    app._on_connect()
    app.after(2800, after_connect)
    app.mainloop()


if __name__ == "__main__":
    main()
