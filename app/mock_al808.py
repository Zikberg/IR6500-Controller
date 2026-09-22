"""Симулятор приладу AL808/PC900 для розробки та демонстрації GUI без апаратури.

Реалізує серверну сторону протоколу: приймає кадри `[EOT]...[ENQ]` (читання) та
`[EOT]...[STX]...[ETX]BCC` (запис), повертає відповіді за тими ж правилами, що
й справжній прилад (BCC, ACK/NAK, мовчання на невалідну мнемоніку).

Об'єкт має той самий мінімальний інтерфейс, що й `serial.Serial`
(`write`, `read`, атрибут `timeout`, `close`), тому підмінює його у
`Al808Client.connect()`, коли обрано порт-симулятор.

Усі значення сегментів програматора зберігаються у ПРИРОДНИХ одиницях
приладу (швидкість рампи — одиниць/СЕКУНДУ, витримка — секунди), як і на
реальному AL808/ACHI IR6500 (підтверджено реальним тестом 18.09.2026 —
див. docstring `profile_controller.py`). Перерахунок в "екранні" одиниці
(°C/с, секунди) робить `profile_controller`, а не цей симулятор.
"""

from __future__ import annotations

import time
from typing import Optional

from . import al808_protocol as proto


class MockAl808Serial:
    """Мінімальна заглушка серійного порту, що емулює прилад AL808."""

    def __init__(self, address: int = 1) -> None:
        self.address = address
        self.timeout = 1.0
        self._rx_buffer = bytearray()

        now = time.monotonic()
        self._last_tick = now

        # --- Стан параметрів (природні одиниці приладу) ---
        self.sl = 25.0  # локальна ставка (ручний режим)
        self.pv = 22.0  # поточна вимірювана температура (симулюється)
        self.op = 0.0

        self.scalars: dict[str, float] = {
            "HA": 300.0, "LA": 0.0, "DA": 5.0,
            "XP": 10.0, "TI": 120.0, "TD": 20.0,
            "HB": 5.0, "LB": 5.0,
            "CH": 20.0, "CC": 20.0, "RG": 1.0,
            "HS": 300.0, "LS": 0.0,
            "BP": 0.0, "HO": 100.0, "SR": 1.0,
            "Hb": 5.0, "Lc": 1.0,
        }

        # 10 незалежних патернів (PTN 0..9). r1/l1/t1 завжди стосуються ПОТОЧНОГО ch.
        base = [
            {"rate": 0.70, "level": 160.0, "dwell": 60.0},
            {"rate": 0.60, "level": 225.0, "dwell": 40.0},
            {"rate": 0.50, "level": 235.0, "dwell": 120.0},
            {"rate": 0.01, "level": 180.0, "dwell": 30.0},
            {"rate": 1.50, "level": 210.0, "dwell": 30.0},
            {"rate": 1.50, "level": 245.0, "dwell": 30.0},
            {"rate": 0.01, "level": 265.0, "dwell": 40.0},
            {"rate": 3.00, "level": 275.0, "dwell": 40.0},
        ]
        self.pattern_banks: list[list[dict]] = []
        for ptn in range(proto.PTN_COUNT):
            bank = [dict(seg) for seg in base]
            bank[0]["level"] = 40.0 + ptn * 10.0
            self.pattern_banks.append(bank)
        self.ptn = 1

        self.xs_self_tune = 0  # 0/1
        # Статус-слово OS як на реальному ACHI: IDLE=0, RUN=0x0142, HOLD=0x0146
        # (біт 0x0002 = run, біт 0x0004 = hold). Запис команд лишається >0000/>0002/>0003.
        self.os_state = 0
        self.sw_word = 0x0000

        self._run_start_pv: Optional[float] = None
        self._run_elapsed_s: float = 0.0

    # ------------------------------------------------------------------
    # Інтерфейс, сумісний з serial.Serial
    # ------------------------------------------------------------------
    def write(self, data: bytes) -> int:
        self._tick_simulation()
        response = self._handle_frame(bytes(data))
        self._rx_buffer += response
        return len(data)

    def read(self, size: int = 1) -> bytes:
        if not self._rx_buffer:
            return b""
        chunk = bytes(self._rx_buffer[:size])
        del self._rx_buffer[:size]
        return chunk

    @property
    def in_waiting(self) -> int:
        return len(self._rx_buffer)

    def close(self) -> None:
        self._rx_buffer.clear()

    @property
    def segments(self) -> list[dict]:
        return self.pattern_banks[self.ptn]

    # ------------------------------------------------------------------
    # Обробка кадрів запиту
    # ------------------------------------------------------------------
    def _handle_frame(self, frame: bytes) -> bytes:
        if len(frame) < 5 or frame[0] != proto.EOT:
            return b""  # некоректний кадр — мовчання

        addr_bytes = frame[1:5]
        expected_addr = proto.encode_address(self.address)
        if addr_bytes != expected_addr:
            return b""  # інша адреса — мовчання

        rest = frame[5:]
        if not rest:
            return b""

        if rest[-1] == proto.ENQ:
            # Читання: rest = C1 C2 [ENQ]
            if len(rest) != 3:
                return b""
            mnemonic = rest[0:2].decode("ascii", errors="replace")
            return self._handle_read(mnemonic)

        if rest[0] == proto.STX:
            # Запис: rest = [STX] C1 C2 DATA [ETX] BCC
            if len(rest) < 6 or rest[-2] != proto.ETX:
                return b""
            payload = rest[1:-1]  # C1 C2 DATA ETX
            bcc_received = rest[-1]
            bcc_calc = proto.calc_bcc(payload)
            if bcc_calc != bcc_received:
                return bytes([proto.NAK])
            mnemonic = payload[0:2].decode("ascii", errors="replace")
            data = payload[2:-1].decode("ascii", errors="replace")
            return self._handle_write(mnemonic, data)

        return b""

    # ------------------------------------------------------------------
    # Читання параметра
    # ------------------------------------------------------------------
    def _handle_read(self, mnemonic: str) -> bytes:
        value = self._get_value(mnemonic)
        if value is None:
            return b""  # невідома/непідтримувана мнемоніка — мовчання, як на реальному приладі

        is_hex = mnemonic in ("SW", "XS", "OS")
        data_str = proto.format_data_value(value, hex_word=is_hex)
        payload = mnemonic.encode("ascii") + data_str.encode("ascii") + bytes([proto.ETX])
        bcc = proto.calc_bcc(payload)
        return bytes([proto.STX]) + payload + bytes([bcc])

    def _get_value(self, mnemonic: str):
        if mnemonic == "PV":
            return round(self.pv, 2)
        if mnemonic == "OP":
            return round(self.op, 1)
        if mnemonic == "SP":
            return round(self._current_sp(), 2)
        if mnemonic == "SL":
            return self.sl
        if mnemonic == "SW":
            return self.sw_word
        if mnemonic == "XS":
            return self.xs_self_tune
        if mnemonic == "OS":
            return self.os_state
        if mnemonic == proto.PTN_MNEMONIC:
            return self.ptn
        if mnemonic == "SE":
            return 0.0 if not self._program_active() else float(self._current_step_number())
        if mnemonic in self.scalars:
            return self.scalars[mnemonic]

        seg = self._segment_field(mnemonic)
        if seg is not None:
            n, field = seg
            return self.segments[n - 1][field]

        return None

    # ------------------------------------------------------------------
    # Запис параметра
    # ------------------------------------------------------------------
    def _handle_write(self, mnemonic: str, data: str) -> bytes:
        try:
            if mnemonic in ("SW", "XS", "OS"):
                value = proto.parse_data_value(data)
            else:
                value = proto.parse_data_value(data)
        except ValueError:
            return bytes([proto.NAK])

        if mnemonic in ("PV", "OP", "SP"):
            return bytes([proto.NAK])  # read-only

        if mnemonic == "SL":
            self.sl = float(value)
            return bytes([proto.ACK])
        if mnemonic == "OS":
            self._apply_os_command(int(value))
            return bytes([proto.ACK])
        if mnemonic == "XS":
            self.xs_self_tune = int(value)
            return bytes([proto.ACK])
        if mnemonic == "SW":
            self.sw_word = int(value)
            return bytes([proto.ACK])
        if mnemonic == proto.PTN_MNEMONIC:
            if self._program_active():
                return bytes([proto.NAK])
            try:
                ptn = int(float(value))
            except (TypeError, ValueError):
                return bytes([proto.NAK])
            if not proto.PTN_MIN <= ptn <= proto.PTN_MAX:
                return bytes([proto.NAK])
            self.ptn = ptn
            return bytes([proto.ACK])
        if mnemonic == "SE":
            return bytes([proto.NAK])  # read-only
        if mnemonic in self.scalars:
            self.scalars[mnemonic] = float(value)
            return bytes([proto.ACK])

        seg = self._segment_field(mnemonic)
        if seg is not None:
            n, field = seg
            # rate (r1..r8) може бути числом АБО сентинелом "END"; level/dwell — завжди числа.
            self.segments[n - 1][field] = value if isinstance(value, str) else float(value)
            return bytes([proto.ACK])

        return bytes([proto.NAK])  # невідома мнемоніка при записі

    @staticmethod
    def _segment_field(mnemonic: str):
        if len(mnemonic) != 2:
            return None
        letter, digit = mnemonic[0], mnemonic[1]
        if not digit.isdigit():
            return None
        n = int(digit)
        if not 1 <= n <= proto.SEGMENT_COUNT:
            return None
        field = {"r": "rate", "l": "level", "t": "dwell"}.get(letter)
        if field is None:
            return None
        return n, field

    def _apply_os_command(self, cmd: int) -> None:
        """Мапить команди OS (>0000/>0002/>0003) на статус-слово як на реальному приладі."""
        if cmd == 0x0000:
            self.os_state = 0
            self._run_start_pv = None
            self._run_elapsed_s = 0.0
            return
        if cmd == 0x0003:
            if self._run_start_pv is not None or (self.os_state & 0x0002):
                self.os_state = 0x0142 | 0x0004
            else:
                self.os_state = 0x0004
            return
        # RUN або сирий статус: 0x0002 / 0x0142 тощо.
        if cmd in (0x0002, 0x0142) or (cmd & 0x0002):
            if self._run_start_pv is None:
                self._run_start_pv = self.pv
                self._run_elapsed_s = 0.0
            self.os_state = 0x0142
            return
        self.os_state = cmd

    def _program_running(self) -> bool:
        return bool(self.os_state & 0x0002) and not bool(self.os_state & 0x0004)

    def _program_active(self) -> bool:
        return bool(self.os_state & 0x0002) or bool(self.os_state & 0x0004)

    # ------------------------------------------------------------------
    # Проста фізична симуляція PV/SP
    # ------------------------------------------------------------------
    def _tick_simulation(self) -> None:
        now = time.monotonic()
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now
        if dt <= 0:
            return

        if self._program_running():
            self._run_elapsed_s += dt

        target_sp = self._current_sp()
        # PV наздоганяє SP з обмеженою швидкістю (~0.5 °C/с) — груба імітація інерції печі.
        max_step = 0.5 * dt
        diff = target_sp - self.pv
        if abs(diff) <= max_step:
            self.pv = target_sp
        else:
            self.pv += max_step if diff > 0 else -max_step

        self.op = max(0.0, min(100.0, 50.0 + (target_sp - self.pv) * 5.0))

        if self._program_running() and self._program_finished():
            self.os_state = 0  # програма сама завершується (як P.END=OFF)
            self._run_start_pv = None
            self._run_elapsed_s = 0.0

    def _current_sp(self) -> float:
        if not self._program_active() or self._run_start_pv is None:
            return self.sl
        prev_level = self._run_start_pv
        remaining = self._run_elapsed_s
        for seg in self.segments:
            if seg["rate"] == "END":
                break  # програма завершується, не досягаючи цього сегмента
            rate_per_s = max(seg["rate"], 1e-6)  # units/с == °C/с (пряма передача)
            ramp_duration = abs(seg["level"] - prev_level) / rate_per_s if rate_per_s > 0 else 0.0
            if remaining < ramp_duration:
                direction = 1.0 if seg["level"] >= prev_level else -1.0
                return prev_level + direction * rate_per_s * remaining
            remaining -= ramp_duration

            # dwell (t1..t8) — СЕКУНДИ на цій станції (ACHI IR6500), без конвертації.
            dwell_duration = seg["dwell"]
            if remaining < dwell_duration:
                return seg["level"]
            remaining -= dwell_duration

            prev_level = seg["level"]
        return prev_level  # програма завершена

    def _program_finished(self) -> bool:
        if self._run_start_pv is None:
            return True
        prev_level = self._run_start_pv
        total = 0.0
        for seg in self.segments:
            if seg["rate"] == "END":
                break
            rate_per_s = max(seg["rate"], 1e-6)  # units/с == °C/с (пряма передача)
            total += abs(seg["level"] - prev_level) / rate_per_s if rate_per_s > 0 else 0.0
            total += seg["dwell"]  # секунди, без конвертації
            prev_level = seg["level"]
        return self._run_elapsed_s >= total

    def _current_step_number(self) -> int:
        """Груба 1-based оцінка поточного сегмента для мнемоніки SE."""
        if self._run_start_pv is None:
            return 0
        prev_level = self._run_start_pv
        remaining = self._run_elapsed_s
        for i, seg in enumerate(self.segments, start=1):
            if seg["rate"] == "END":
                return i
            rate_per_s = max(seg["rate"], 1e-6)
            ramp_duration = abs(seg["level"] - prev_level) / rate_per_s if rate_per_s > 0 else 0.0
            dwell_duration = seg["dwell"]
            if remaining < ramp_duration + dwell_duration:
                return i
            remaining -= ramp_duration + dwell_duration
            prev_level = seg["level"]
        return proto.SEGMENT_COUNT
