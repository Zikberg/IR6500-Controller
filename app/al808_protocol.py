"""Низькорівневе кодування/декодування кадрів протоколу ALTEC AL808 / PC900.

Джерело: AL808CommsProtocol.pdf (див. AL808_Protocol_Reference.md у корені проєкту).

Формат кадрів:
    Читання (poll):  [EOT] ADR_H ADR_H ADR_L ADR_L C1 C2 [ENQ]
                      -> [STX] C1 C2 <DATA> [ETX] BCC   (або мовчання при помилці)
    Запис (select):  [EOT] ADR_H ADR_H ADR_L ADR_L [STX] C1 C2 <DATA> [ETX] BCC
                      -> [ACK] | [NAK] | мовчання

BCC = XOR усіх байтів після STX (не включаючи STX), включно з ETX.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Керуючі символи
# ---------------------------------------------------------------------------
STX = 0x02
ETX = 0x03
EOT = 0x04
ENQ = 0x05
ACK = 0x06
NAK = 0x15


# ---------------------------------------------------------------------------
# Винятки
# ---------------------------------------------------------------------------
class ProtocolError(Exception):
    """Базовий виняток протоколу AL808."""


class ProtocolTimeout(ProtocolError):
    """Прилад не відповів у відведений час (типово: невалідна мнемоніка/адреса)."""


class ProtocolNak(ProtocolError):
    """Прилад повернув NAK на запис (BCC невірний, параметр read-only, поза межами тощо)."""


class ProtocolFramingError(ProtocolError):
    """Кадр відповіді має неправильну структуру (немає STX/ETX)."""


class ProtocolChecksumError(ProtocolError):
    """BCC відповіді не збігається з розрахованим."""


# ---------------------------------------------------------------------------
# Адресація
# ---------------------------------------------------------------------------
def encode_address(address: int) -> bytes:
    """Кодує адресу приладу (0..99) у 4-байтовий ASCII-кадр з подвоєнням цифр."""
    if not 0 <= address <= 99:
        raise ValueError(f"Адреса приладу має бути 0..99, отримано {address}")
    digits = f"{address:02d}"
    h, l = digits[0], digits[1]
    return f"{h}{h}{l}{l}".encode("ascii")


# ---------------------------------------------------------------------------
# BCC
# ---------------------------------------------------------------------------
def calc_bcc(payload: bytes) -> int:
    """XOR усіх байтів payload (payload вже має включати ETX, не включати STX)."""
    bcc = 0
    for byte in payload:
        bcc ^= byte
    return bcc


# ---------------------------------------------------------------------------
# Побудова кадрів запиту (master -> прилад)
# ---------------------------------------------------------------------------
def build_poll_frame(address: int, mnemonic: str) -> bytes:
    """Кадр читання параметра (poll): [EOT] ADR ADR ADR ADR C1 C2 [ENQ]."""
    if len(mnemonic) != 2:
        raise ValueError(f"Мнемоніка має бути з 2 символів, отримано {mnemonic!r}")
    frame = bytearray()
    frame.append(EOT)
    frame += encode_address(address)
    frame += mnemonic.encode("ascii")
    frame.append(ENQ)
    return bytes(frame)


def build_select_frame(address: int, mnemonic: str, data: str) -> bytes:
    """Кадр запису параметра (select): [EOT] ADR ADR ADR ADR [STX] C1 C2 DATA [ETX] BCC."""
    if len(mnemonic) != 2:
        raise ValueError(f"Мнемоніка має бути з 2 символів, отримано {mnemonic!r}")
    payload = mnemonic.encode("ascii") + data.encode("ascii") + bytes([ETX])
    bcc = calc_bcc(payload)
    frame = bytearray()
    frame.append(EOT)
    frame += encode_address(address)
    frame.append(STX)
    frame += payload
    frame.append(bcc)
    return bytes(frame)


# ---------------------------------------------------------------------------
# Розбір відповідей (прилад -> master)
# ---------------------------------------------------------------------------
@dataclass
class ReadResult:
    mnemonic: str
    data: str


def parse_read_response(resp: bytes, expected_mnemonic: Optional[str] = None) -> ReadResult:
    """Розбирає відповідь на читання: [STX] C1 C2 DATA [ETX] BCC.

    Піднімає ProtocolFramingError / ProtocolChecksumError при помилках.
    """
    if not resp:
        raise ProtocolFramingError("Порожня відповідь")
    if resp[0] != STX:
        raise ProtocolFramingError(f"Відповідь не починається з STX: {resp!r}")
    if len(resp) < 5:  # STX + C1 + C2 + ETX + BCC (мінімум, DATA може бути порожнім)
        raise ProtocolFramingError(f"Відповідь занадто коротка: {resp!r}")

    payload = resp[1:-1]  # C1 C2 DATA ETX
    bcc_received = resp[-1]

    if not payload or payload[-1] != ETX:
        raise ProtocolFramingError(f"Відповідь не містить ETX у очікуваній позиції: {resp!r}")

    bcc_calc = calc_bcc(payload)
    if bcc_calc != bcc_received:
        raise ProtocolChecksumError(
            f"BCC не збігається: розраховано {bcc_calc:#04x}, отримано {bcc_received:#04x}"
        )

    mnemonic = payload[0:2].decode("ascii", errors="replace")
    data = payload[2:-1].decode("ascii", errors="replace")

    if expected_mnemonic is not None and mnemonic != expected_mnemonic:
        raise ProtocolError(
            f"Відлуння мнемоніки не збігається: очікувалось {expected_mnemonic!r}, отримано {mnemonic!r}"
        )

    return ReadResult(mnemonic=mnemonic, data=data)


def parse_write_response(resp: bytes) -> bool:
    """Розбирає відповідь на запис: один байт ACK(0x06) або NAK(0x15).

    Повертає True при ACK. Піднімає ProtocolNak при NAK, ProtocolTimeout при відсутності байтів.
    """
    if not resp:
        raise ProtocolTimeout("Прилад не відповів на запис (мовчання)")
    if resp[0] == ACK:
        return True
    if resp[0] == NAK:
        raise ProtocolNak("Прилад повернув NAK на запис")
    raise ProtocolFramingError(f"Неочікувана відповідь на запис: {resp!r}")


# ---------------------------------------------------------------------------
# Кодування/декодування значень DATA
# ---------------------------------------------------------------------------
#: Спеціальне текстове значення параметра Ramp Rate (r1..r8): "END" означає, що
#: програма завершується, коли доходить до цього сегмента (замість числового
#: значення швидкості рампи). Задокументовано в ACHI IR6500 User Manual та
#: генеричній специфікації PC900 ("If R1 = END, the program will be ended...").
END_VALUE = "END"


def parse_data_value(data: str):
    """Перетворює текстове поле DATA у число (або спеціальне значення "END").

    Підтримує звичайні числа ('99.9', '  22.', '-999'), hex-слова ('>ABCD')
    та текстовий сентинел 'END' (для мнемонік ramp rate r1..r8).
    Повертає int для hex-слів, float для звичайних чисел, str('END') для сентинелу.
    """
    text = data.strip()
    if not text:
        raise ValueError("Порожнє значення DATA")
    if text.upper() == END_VALUE:
        return END_VALUE
    if text.startswith(">"):
        return int(text[1:], 16)
    # '22.' -> '22.0', '-999' -> -999.0
    if text.endswith("."):
        text = text + "0"
    return float(text)


def format_data_value(value, hex_word: bool = False) -> str:
    """Перетворює число (або "END") у текстове поле DATA для запису в прилад."""
    if isinstance(value, str):
        if value.upper() == END_VALUE:
            return END_VALUE
        raise TypeError(f"Непідтримуване рядкове значення DATA: {value!r}")
    if hex_word:
        return f">{int(value) & 0xFFFF:04X}"
    if isinstance(value, bool):
        raise TypeError("bool не є допустимим значенням DATA")
    if isinstance(value, int):
        return str(value)
    # float: прибираємо надлишкові нулі, але залишаємо хоча б одну цифру після крапки
    text = f"{float(value):.2f}"
    text = text.rstrip("0")
    if text.endswith("."):
        text += "0"
    return text


# ---------------------------------------------------------------------------
# Таблиця параметрів (мнемоніки) — з AL808_Protocol_Reference.md
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ParamInfo:
    mnemonic: str
    name: str
    access: str  # 'ro' | 'rw'
    description: str
    hex_word: bool = False


PARAMETERS: dict[str, ParamInfo] = {
    p.mnemonic: p
    for p in [
        ParamInfo("PV", "Process Value", "ro", "Останнє виміряне значення"),
        ParamInfo("OP", "Output power", "ro", "Потужність виходу в автоматичному режимі, %"),
        ParamInfo("SP", "Setpoint (working)", "ro", "Поточна робоча ставка (у програмі — SV)"),
        ParamInfo("SL", "Local setpoint", "rw", "Локальна ставка 1 (ручний режим)"),
        ParamInfo("HA", "High alarm setpoint", "rw", "Уставка аварії верхньої межі"),
        ParamInfo("LA", "Low alarm setpoint", "rw", "Уставка аварії нижньої межі"),
        ParamInfo("DA", "Deviation alarm setpoint", "rw", "Уставка аварії відхилення"),
        ParamInfo("XP", "Proportional band", "rw", "Пропорційна смуга (PID)"),
        ParamInfo("TI", "Integral time", "rw", "Інтегральний час (PID)"),
        ParamInfo("TD", "Derivative time", "rw", "Диференціальний час (PID)"),
        ParamInfo("HB", "High cutback", "rw", "Обмеження перерегулювання зверху"),
        ParamInfo("LB", "Lower cutback", "rw", "Обмеження перерегулювання знизу"),
        ParamInfo("CH", "Cycle time 1 (heat)", "rw", "Період циклу виходу нагріву"),
        ParamInfo("CC", "Cycle time 2 (cool)", "rw", "Період циклу виходу охолодження"),
        ParamInfo("RG", "Relative cooling gain", "rw", "Відносний коефіцієнт охолодження"),
        ParamInfo("HS", "Setpoint high limit", "rw", "Верхня межа допустимої ставки"),
        ParamInfo("LS", "Setpoint low limit", "rw", "Нижня межа допустимої ставки"),
        ParamInfo("BP", "Sensor break power", "rw", "Потужність при обриві датчика"),
        ParamInfo("HO", "Heat output limit", "rw", "Ліміт потужності виходу нагріву"),
        ParamInfo("SR", "Ramp-to-setpoint rate", "rw", "Швидкість рампу до ставки"),
        ParamInfo("Hb", "Holdback", "rw", "Смуга holdback програматора"),
        ParamInfo("Lc", "Loop counter", "rw", "Лічильник циклів програми"),
        ParamInfo(
            "ch", "Pattern number (PTN)", "rw",
            "Номер патерну програматора 0..9. Не плутати з CH (час циклу нагріву). "
            "Не змінюється в RUN/HOLD. Підтверджено на ACHI IR6500, COM1, 2026-09-18.",
        ),
        ParamInfo(
            "SE", "Current segment", "ro",
            "Номер поточного сегмента програми (read-only). У IDLE повертає 0.",
        ),
        ParamInfo("SW", "Status word", "rw", "Основне статус-слово (>ABCD)", hex_word=True),
        ParamInfo("XS", "Self-tuning control", "rw", "Керування self-tuning (>ABCD)", hex_word=True),
        ParamInfo("OS", "Program control", "rw", "Керування програмою (>ABCD)", hex_word=True),
    ]
}

# Кількість сегментів (ramp+dwell пар), доступних на панелі PC900/AL808.
SEGMENT_COUNT = 8

# Номер патерну (PTN) на ACHI IR6500 / PC900: 10 незалежних програм 0..9.
# Мнемоніка `ch` (63 68) — з таблиці комунікацій PC900 («程序编号»); у PDF AL808
# її немає. Підтверджено емпірично: запис ch=0..9 = ACK і зміна банку r/l/t;
# ch=10 = NAK. Не плутати з `CH` (43 48, cycle time).
PTN_MNEMONIC = "ch"
PTN_MIN = 0
PTN_MAX = 9
PTN_COUNT = PTN_MAX - PTN_MIN + 1

# Документовано у офіційному PDF лише сегменти 1 і 2. Мнемоніки для сегментів
# 3..8 — екстраполяція за тим самим патерном (нижній регістр літери + цифра).
# Це НЕ підтверджено виробником і має бути перевірено пробним читанням (probe)
# при підключенні до реального приладу.
DOCUMENTED_SEGMENTS = frozenset({1, 2})


def segment_mnemonics(n: int) -> tuple[str, str, str]:
    """Повертає мнемоніки (ramp, level, dwell) для сегмента n (1..SEGMENT_COUNT)."""
    if not 1 <= n <= SEGMENT_COUNT:
        raise ValueError(f"Номер сегмента має бути 1..{SEGMENT_COUNT}, отримано {n}")
    return (f"r{n}", f"l{n}", f"t{n}")


def is_segment_documented(n: int) -> bool:
    """True, якщо мнемоніки сегмента n офіційно підтверджені у PDF-специфікації."""
    return n in DOCUMENTED_SEGMENTS
