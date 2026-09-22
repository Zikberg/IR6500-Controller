"""Бізнес-логіка профілю нагріву: читання/запис вбудованого програматора AL808,
керування RUN/HOLD/IDLE, вибір патерну PTN (`ch`), ручний режим сталої
температури, та евристика визначення поточного кроку профілю.

Одиниці інтерфейсу (UI) відповідають скріншотам застосунку:
    - Ramp Rate  — °C / секунду
    - Target Temp — °C
    - Dwell Time — секунди
    - Holdback   — °C

Одиниці приладу (native):
    - швидкість рампи (r1..r8) — одиниць/СЕКУНДУ (не за хвилину!). Раніше
      тут припускалось "units/хвилину" (за аналогією з генеричним PC900) і
      конвертація множила/ділила на 60 — це підтверджувалось лише
      round-trip зчитуванням записаного числа, а не реальним часом рампи.
      Прямий експеримент на реальному приладі (2026-09-18): запис
      r1=1.0 (сире, без ×60) призвів до зміни SP рівно ~1°C щосекунди
      (SP пройшла 21→30°C за ~9с) — тобто одиниця це °C/с, пряма передача.
      Це також пояснює, чому попередній профільний тест (r1=0.2°C/с*60=12)
      "миттєво" (за <1с) долітав до цілі: прилад читав 12 як 12°C/СЕКУНДУ.
    - витримка (t1..t8) — СЕКУНДИ (ціле число, 0~9999 sec).

    ВАЖЛИВО: генерична специфікація ALTEC PC900 (PC900_EN.pdf) документує
    dwell (D1..D8) у ХВИЛИНАХ. Але ця конкретна станція — ACHI IR6500
    (BGA rework station) — використовує вбудований контролер з dwell у
    СЕКУНДАХ (підтверджено інструкцією ACHI IR6500 User Manual, розділ
    "Program Parameter List": "d1 Dwell Time 1  0~9999 sec"). Це підтвердилось
    і експериментально: запис t1=0.5 (як 0.5 хв) обрізався приладом до 0,
    бо прилад інтерпретував це як 0.5 СЕКУНДИ (округлення до цілого сек).
    Тож конвертація dwell UI<->native тепер ІДЕНТИЧНА (без ×60/÷60).

Спеціальне значення Ramp Rate "END": замість числа в r1..r8 можна записати
текстовий сентинел "END" — це означає, що програма завершується, коли
досягає цього сегмента (сегменти після нього не виконуються). Задокументовано
в ACHI IR6500 User Manual, приклади профілів Lead/Lead-free.

Перерахунок UI <-> native відбувається саме тут, на межі, аби протокол
(`al808_protocol.py`) і клієнт (`al808_client.py`) залишались "тонкими" й не
знали про екранні одиниці.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional
import time

from . import al808_protocol as proto, i18n

SEGMENT_COUNT = proto.SEGMENT_COUNT
PTN_MNEMONIC = proto.PTN_MNEMONIC
PTN_MIN = proto.PTN_MIN
PTN_MAX = proto.PTN_MAX

# Мінімальна різниця (°C), нижче якої SP вважається "на цілі" (dwell), а не в русі (ramp).
STEP_TARGET_EPSILON = 0.5


# ---------------------------------------------------------------------------
# Перерахунок одиниць UI <-> native
# ---------------------------------------------------------------------------
def ramp_ui_to_native(ramp_c_per_s):
    """°C/с -> одиниць/с (пряма передача 1:1, підтверджено реальним тестом
    18.09.2026 — див. docstring модуля). Якщо передано сентинел "END" —
    повертає його без змін."""
    if isinstance(ramp_c_per_s, str):
        return ramp_c_per_s
    return ramp_c_per_s


def ramp_native_to_ui(ramp_per_s):
    """одиниць/с -> °C/с (пряма передача 1:1). Якщо прилад повернув "END" —
    повертає його без змін."""
    if isinstance(ramp_per_s, str):
        return ramp_per_s
    return ramp_per_s


def dwell_ui_to_native(dwell_s: float) -> float:
    """секунди -> секунди (пряма передача).

    На цій станції (ACHI IR6500) dwell (t1..t8) зберігається в СЕКУНДАХ, а не
    хвилинах — на відміну від генеричної специфікації PC900. Підтверджено
    емпірично: дробові "хвилини" обрізались приладом до 0. Функція лишена
    заради єдиної точки конвертації (якщо колись знадобиться інша станція
    з іншими одиницями)."""
    return dwell_s


def dwell_native_to_ui(dwell_native: float) -> float:
    """секунди -> секунди (пряма передача). Див. dwell_ui_to_native."""
    return dwell_native


# ---------------------------------------------------------------------------
# Моделі даних
# ---------------------------------------------------------------------------
@dataclass
class SegmentUI:
    ramp: Optional[object] = None      # °C/с (float) АБО сентинел "END" (str)
    target: Optional[float] = None     # °C
    dwell: Optional[float] = None      # секунди
    supported: bool = True             # False, якщо прилад не відповів на мнемоніку сегмента
    enabled: bool = True               # False = цей і всі наступні кроки вимкнені користувачем


@dataclass
class ProfileUI:
    segments: list[SegmentUI] = field(default_factory=lambda: [SegmentUI() for _ in range(SEGMENT_COUNT)])
    holdback: Optional[float] = None


def active_segment_count(segments: list[SegmentUI]) -> int:
    """Кількість активних кроків від початку профілю.

    Кроки вимикаються послідовно (crок N вимкнений => N..8 вимкнені), тому
    досить знайти перший вимкнений і повернути його індекс (1-based кількість
    активних кроків перед ним)."""
    count = 0
    for seg in segments:
        if not seg.enabled:
            break
        count += 1
    return count


# ---------------------------------------------------------------------------
# PTN (номер патерну) — мнемоніка `ch`
# ---------------------------------------------------------------------------
def parse_ptn(value) -> int:
    """Перетворює відповідь приладу (`1`, `   1.`) на ціле 0..9."""
    ptn = int(float(value))
    if not PTN_MIN <= ptn <= PTN_MAX:
        raise ValueError(f"PTN має бути {PTN_MIN}..{PTN_MAX}, отримано {value!r}")
    return ptn


def _os_is_run_or_hold(os_state) -> bool:
    try:
        word = int(os_state)
    except (TypeError, ValueError):
        return False
    return bool(word & 0x0002) or bool(word & 0x0004)


def _require_idle_for_ptn(client) -> None:
    """PTN не змінюється в RUN/HOLD — як на панелі, так і по COM."""
    try:
        os_state = client.read_param_sync("OS")
    except proto.ProtocolError:
        return
    if _os_is_run_or_hold(os_state):
        raise proto.ProtocolError(i18n.t("ptn_locked"))


def _emit_progress(client, current: int, total: int, text: str = "") -> None:
    report = getattr(client, "report_progress", None)
    if callable(report):
        report(current, total, text)


def _read_ptn(client) -> int:
    return parse_ptn(client.read_param_sync(PTN_MNEMONIC))


def _write_ptn(client, ptn: int) -> int:
    ptn = parse_ptn(ptn)
    _require_idle_for_ptn(client)
    client.write_param_sync(PTN_MNEMONIC, ptn)
    return _read_ptn(client)


def _read_profile(client, *, done: int = 0, total: Optional[int] = None) -> ProfileUI:
    profile = ProfileUI()
    if total is None:
        total = done + 1 + SEGMENT_COUNT
    _emit_progress(client, done, total, "Hb")
    try:
        hb = client.read_param_sync("Hb")
        profile.holdback = float(hb)
    except proto.ProtocolError:
        profile.holdback = None
    done += 1

    for n in range(1, SEGMENT_COUNT + 1):
        _emit_progress(client, done, total, i18n.t("progress_step", n=n))
        r_m, l_m, t_m = proto.segment_mnemonics(n)
        seg = SegmentUI()
        try:
            rate = client.read_param_sync(r_m)
            level = client.read_param_sync(l_m)
            dwell = client.read_param_sync(t_m)
            seg.ramp = ramp_native_to_ui(rate)  # може бути "END" (str) або float
            seg.target = float(level)
            seg.dwell = dwell_native_to_ui(float(dwell))
            seg.supported = True
        except proto.ProtocolError:
            seg.supported = False
        profile.segments[n - 1] = seg
        done += 1
    _emit_progress(client, total, total, i18n.t("progress_done"))
    return profile


# ---------------------------------------------------------------------------
# Job-builders — виконуються у фоновому потоці клієнта (submit_job)
# ---------------------------------------------------------------------------
def build_read_ptn_job() -> Callable:
    def job(client) -> int:
        return _read_ptn(client)

    return job


def build_select_ptn_job(ptn: int) -> Callable:
    def job(client) -> int:
        return _write_ptn(client, ptn)

    return job


def build_select_ptn_and_download_job(ptn: int) -> Callable:
    """Перемикає слот `ch` і читає його профіль. Повертає (ptn, ProfileUI)."""

    def job(client) -> tuple[int, ProfileUI]:
        total = 1 + 1 + SEGMENT_COUNT
        _emit_progress(client, 0, total, "PTN")
        actual = _write_ptn(client, ptn)
        return actual, _read_profile(client, done=1, total=total)

    return job


def build_download_job(ptn: Optional[int] = None) -> Callable:
    """Job: читає всі 8 сегментів + Hb з приладу. Повертає ProfileUI.

    Якщо задано `ptn` — спочатку перемикає слот (`ch`), потім читає.
    """

    def job(client) -> ProfileUI:
        if ptn is not None:
            total = 1 + 1 + SEGMENT_COUNT
            _emit_progress(client, 0, total, "PTN")
            _write_ptn(client, ptn)
            return _read_profile(client, done=1, total=total)
        return _read_profile(client)

    return job


def build_upload_job(profile: ProfileUI, ptn: Optional[int] = None) -> Callable:
    """Job: записує Hb + усі 8 сегментів у прилад. Повертає список помилок (порожній = успіх).

    Якщо задано `ptn` — спочатку перемикає слот (`ch`), потім пише сегменти туди.
    """

    def job(client) -> list[str]:
        errors: list[str] = []
        has_ptn = ptn is not None
        has_hb = profile.holdback is not None
        total = (1 if has_ptn else 0) + (1 if has_hb else 0) + SEGMENT_COUNT
        done = 0

        if has_ptn:
            _emit_progress(client, done, total, "PTN")
            try:
                _write_ptn(client, ptn)
            except (proto.ProtocolError, ValueError) as exc:
                errors.append(f"PTN/ch: {exc}")
                return errors
            done += 1

        if has_hb:
            _emit_progress(client, done, total, "Hb")
            try:
                client.write_param_sync("Hb", profile.holdback)
            except proto.ProtocolError as exc:
                errors.append(f"Hb: {exc}")
            done += 1

        active_n = active_segment_count(profile.segments)

        for n, seg in enumerate(profile.segments, start=1):
            _emit_progress(client, done, total, i18n.t("progress_step", n=n))
            r_m, l_m, t_m = proto.segment_mnemonics(n)

            if n <= active_n:
                if seg.ramp is not None:
                    try:
                        native_ramp = ramp_ui_to_native(seg.ramp)
                        if isinstance(native_ramp, str):
                            client.write_param_sync(r_m, native_ramp)  # сентинел "END"
                        else:
                            client.write_param_sync(r_m, round(native_ramp, 2))
                    except proto.ProtocolError as exc:
                        errors.append(f"{r_m} (Step {n} Ramp Rate): {exc}")
                if seg.target is not None:
                    try:
                        client.write_param_sync(l_m, round(seg.target, 2))
                    except proto.ProtocolError as exc:
                        errors.append(f"{l_m} (Step {n} Target Temp): {exc}")
                if seg.dwell is not None:
                    try:
                        # Dwell — ціле число секунд (0~9999 sec), див. docstring модуля.
                        client.write_param_sync(t_m, round(dwell_ui_to_native(seg.dwell)))
                    except proto.ProtocolError as exc:
                        errors.append(f"{t_m} (Step {n} Dwell Time): {exc}")
            else:
                # Вимкнений крок: у всі три поля пишемо 0. Сентинел "END" прилад
                # по COM не приймає (NAK), тож нулі + auto-stop у GUI, коли
                # трекер доходить до цього кроку.
                for mnemonic, value in ((r_m, 0), (l_m, 0), (t_m, 0)):
                    try:
                        client.write_param_sync(mnemonic, value)
                    except proto.ProtocolError as exc:
                        errors.append(f"{mnemonic} (Step {n}, вимкнено): {exc}")
            done += 1
        _emit_progress(client, total, total, i18n.t("progress_done"))
        return errors

    return job


def build_start_job(ptn: Optional[int] = None, restore_step1: Optional[dict] = None) -> Callable:
    def job(client) -> bool:
        if restore_step1:
            client.write_param_sync("OS", 0x0000, hex_word=True)
            time.sleep(0.15)
            _restore_step1(client, restore_step1)
        if ptn is not None:
            _write_ptn(client, ptn)
        return client.write_param_sync("OS", 0x0002, hex_word=True)

    return job


def build_stop_job(restore_step1: Optional[dict] = None) -> Callable:
    def job(client) -> bool:
        client.write_param_sync("OS", 0x0000, hex_word=True)
        if restore_step1:
            time.sleep(0.15)
            _restore_step1(client, restore_step1)
        return True

    return job


def build_hold_job() -> Callable:
    def job(client) -> bool:
        return client.write_param_sync("OS", 0x0003, hex_word=True)

    return job


# На IR6500 у IDLE вихід нагріву завжди 0 %, навіть якщо SL=SP і PV нижче ставки.
# Підтверджено на COM1 2026-09-21: IDLE+SL → OP=0; RUN однокрокового dwell → OP=100 %.
MANUAL_RAMP_C_PER_S = 5.0
MANUAL_DWELL_S = 9999


def _read_step1(client) -> dict:
    return {
        "r1": client.read_param_sync("r1"),
        "l1": client.read_param_sync("l1"),
        "t1": client.read_param_sync("t1"),
        "SL": client.read_param_sync("SL"),
    }


def _restore_step1(client, backup: dict) -> None:
    r1 = backup.get("r1")
    l1 = backup.get("l1")
    t1 = backup.get("t1")
    sl = backup.get("SL")
    if r1 is not None:
        if isinstance(r1, str):
            client.write_param_sync("r1", r1)
        else:
            client.write_param_sync("r1", round(float(r1), 2))
    if l1 is not None:
        client.write_param_sync("l1", round(float(l1), 2))
    if t1 is not None:
        client.write_param_sync("t1", int(round(float(t1))))
    if sl is not None:
        client.write_param_sync("SL", round(float(sl), 2))


def build_manual_setpoint_job(temp_c: float, keep_backup: Optional[dict] = None) -> Callable:
    """Стала температура: на цьому приладі нагрів є лише в RUN, тому пишемо
    однокроковий профіль (рамп + довга витримка) і запускаємо програматор.

    Повертає dict {sl, backup} — backup це попередні r1/l1/t1, щоб STOP міг
    їх відновити. Якщо keep_backup вже є (повторне Apply), його не затираємо.
    """

    def job(client) -> dict:
        temp = round(float(temp_c), 2)
        client.write_param_sync("OS", 0x0000, hex_word=True)
        time.sleep(0.15)
        backup = keep_backup if keep_backup is not None else _read_step1(client)
        client.write_param_sync("SL", temp)
        client.write_param_sync("r1", MANUAL_RAMP_C_PER_S)
        client.write_param_sync("l1", temp)
        client.write_param_sync("t1", int(MANUAL_DWELL_S))
        client.write_param_sync("OS", 0x0002, hex_word=True)
        sl = float(client.read_param_sync("SL"))
        return {"sl": sl, "backup": backup}

    return job


def build_probe_segments_job() -> Callable:
    """Job: пробне читання мнемонік усіх 8 сегментів — визначає, які підтримує прилад.

    Безпечно: лише читання (poll), без запису. Повертає dict {n: bool}.
    """

    def job(client) -> dict[int, bool]:
        support: dict[int, bool] = {}
        for n in range(1, SEGMENT_COUNT + 1):
            r_m, l_m, t_m = proto.segment_mnemonics(n)
            ok = True
            for mnemonic in (r_m, l_m, t_m):
                try:
                    client.read_param_sync(mnemonic)
                except proto.ProtocolError:
                    ok = False
                    break
            support[n] = ok
        return support

    return job


def build_read_param_job(mnemonic: str) -> Callable:
    def job(client):
        return client.read_param_sync(mnemonic)

    return job


def build_write_param_job(mnemonic: str, value: float, hex_word: bool = False) -> Callable:
    def job(client) -> bool:
        return client.write_param_sync(mnemonic, value, hex_word=hex_word)

    return job


# ---------------------------------------------------------------------------
# Евристика визначення поточного кроку профілю
# ---------------------------------------------------------------------------
class StepTracker:
    """Визначає, який сегмент (ramp/dwell) профілю зараз виконується.

    ПРИМІТКА: AL808 не надає по протоколу номер активного сегмента чи
    залишок часу. Ця евристика відстежує, до якого цільового рівня `l_i`
    прямує поточне `SP`, і просувається вперед, коли `SP` починає рухатись
    до наступного рівня. Дробова частина, що показується як `Step: n.xxx`,
    є ОЦІНКОЮ прогресу всередині сегмента за часом, а не точним значенням
    з приладу.
    """

    def __init__(self, targets: list[Optional[float]]):
        self.targets = targets
        self.index = 0  # 0-based; поточний сегмент = index + 1
        self.phase = "ramp"  # 'ramp' | 'dwell'
        self._phase_started_at: Optional[float] = None
        self._last_sp: Optional[float] = None

    def reset(self) -> None:
        self.index = 0
        self.phase = "ramp"
        self._phase_started_at = None
        self._last_sp = None

    def update(self, sp: Optional[float], now: float) -> None:
        if sp is None or self.index >= len(self.targets):
            return
        target = self.targets[self.index]
        if target is None:
            self.index += 1
            self._last_sp = sp
            return

        if self._phase_started_at is None:
            self._phase_started_at = now

        at_target = abs(sp - target) <= STEP_TARGET_EPSILON
        if at_target:
            self.phase = "dwell"
        elif self._last_sp is not None:
            moving_away_from_target = abs(sp - target) > abs(self._last_sp - target) + 1e-6
            if self.phase == "dwell" and not at_target and moving_away_from_target:
                # SP знову почало рухатись — dwell завершився, перейшли до наступного ramp.
                self.index = min(self.index + 1, len(self.targets) - 1)
                self.phase = "ramp"
                self._phase_started_at = now
            else:
                self.phase = "ramp"

        self._last_sp = sp

    def current_step_number(self) -> int:
        return min(self.index + 1, len(self.targets))

    def display_value(self) -> float:
        """Значення для статус-рядка на кшталт `Step: 2.014` (ціла частина — крок,
        дробова — груба оцінка часу від початку поточної фази у хвилинах)."""
        import time as _time

        step = self.current_step_number()
        if self._phase_started_at is None:
            return float(step)
        elapsed_min = max(0.0, (_time.monotonic() - self._phase_started_at) / 60.0)
        frac = min(0.999, elapsed_min / 10.0)  # умовна нормалізація для відображення
        return step + frac
