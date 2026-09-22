"""Обчислення точок для графіка запланованого профілю нагріву (без залежності від Tk/matplotlib)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .profile_controller import SegmentUI


@dataclass
class SegmentLayout:
    """Часова "розкладка" одного сегмента на графіку — потрібна і для малювання,
    і для інтерактивного редагування профілю мишкою (перетворення пікселя/координати
    графіка назад у Ramp Rate / Target Temp / Dwell Time)."""

    index: int          # 0-based індекс кроку (Step index+1 для користувача)
    t_start: float       # час початку рампи, с
    temp_start: float     # температура на початку рампи, °C (= ціль попереднього кроку)
    t_ramp_end: float     # час завершення рампи / початку витримки, с
    t_dwell_end: float    # час завершення витримки, с
    target_temp: float    # ціль цього кроку, °C


def compute_planned_profile_points(
    segments: list[SegmentUI], start_temp: float = 25.0
) -> tuple[list[tuple[float, float]], list[tuple[float, float, float]]]:
    """Розраховує вершини лінії запланованого профілю (ramp+dwell) для графіка.

    Повертає:
        points: список (час_с, температура) — вершини лінії профілю.
        labels: список (час_середини_dwell_с, температура, тривалість_dwell_с)
                для підписів на графіку (як "60s" / "160°C" на скріншоті).
    """
    points: list[tuple[float, float]] = [(0.0, start_temp)]
    labels: list[tuple[float, float, float]] = []
    t = 0.0
    prev = start_temp

    for seg in segments:
        if not seg.enabled:
            # Крок вимкнено користувачем — профіль на графіку завершується тут.
            break
        if isinstance(seg.ramp, str):
            # Сентинел "END": програма завершується, не досягаючи цього сегмента.
            break
        if seg.ramp is None or seg.target is None or seg.dwell is None:
            continue
        rate = abs(seg.ramp)
        duration = abs(seg.target - prev) / rate if rate > 1e-9 else 0.0
        t += duration
        points.append((t, seg.target))

        dwell = max(seg.dwell, 0.0)
        if dwell > 0:
            t_start_dwell = t
            t += dwell
            points.append((t, seg.target))
            labels.append((t_start_dwell + dwell / 2.0, seg.target, dwell))

        prev = seg.target

    return points, labels


def total_profile_duration(segments: list[SegmentUI], start_temp: float = 25.0) -> float:
    points, _ = compute_planned_profile_points(segments, start_temp=start_temp)
    return points[-1][0] if points else 0.0


def compute_segment_layouts(
    segments: list[SegmentUI], start_temp: float = 25.0
) -> list[SegmentLayout]:
    """Розкладка кожного визначеного кроку в часі — для інтерактивних "ручок" на графіку.

    Зупиняється на першому вимкненому кроці, на кроці з ramp="END", або з
    відсутніми даними (None).
    """
    layouts: list[SegmentLayout] = []
    t = 0.0
    prev_temp = start_temp

    for i, seg in enumerate(segments):
        if not seg.enabled:
            break  # користувач вимкнув цей і всі наступні кроки
        if isinstance(seg.ramp, str):
            break  # сентинел "END" — далі профіль не виконується
        if seg.ramp is None or seg.target is None or seg.dwell is None:
            continue

        rate = abs(seg.ramp)
        ramp_duration = abs(seg.target - prev_temp) / rate if rate > 1e-9 else 0.0
        t_ramp_end = t + ramp_duration
        dwell = max(seg.dwell, 0.0)
        t_dwell_end = t_ramp_end + dwell

        layouts.append(
            SegmentLayout(
                index=i,
                t_start=t,
                temp_start=prev_temp,
                t_ramp_end=t_ramp_end,
                t_dwell_end=t_dwell_end,
                target_temp=seg.target,
            )
        )

        t = t_dwell_end
        prev_temp = seg.target

    return layouts
