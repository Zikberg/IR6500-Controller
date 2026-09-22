"""Клієнт для зв'язку з приладом AL808/PC900 через COM-порт.

Архітектура (потокобезпека):
    - Весь реальний обмін з портом відбувається у ОКРЕМОМУ фоновому потоці
      (`_worker_loop`). GUI (головний потік Tkinter) НІКОЛИ не звертається
      до порту напряму.
    - GUI надсилає команди двома способами:
        1. `submit_job(fn, callback)` — довільна функція `fn(client)`, що
          виконується у фоновому потоці (може робити кілька read/write
          підряд — наприклад, "завантажити весь профіль"). Результат
          повертається у `callback(ok, result_or_error)`, який GUI
          викликає у себе на головному потоці під час `poll_events()`.
        2. Періодичний телеметричний polling (PV/SP/OP/SW) — результати
          потрапляють у `poll_events()` як події `('telemetry', ...)`.
    - `poll_events()` МАЄ викликатися періодично з головного потоку
      (наприклад, через `root.after(100, ...)`).
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from . import al808_protocol as proto

try:
    import serial  # type: ignore
    from serial.tools import list_ports as _list_ports  # type: ignore
except ImportError:  # pragma: no cover - pyserial може бути не встановлений під час розробки
    serial = None
    _list_ports = None

from . import mock_al808

POLL_INTERVAL_S = 0.4
DEFAULT_TIMEOUT_S = 1.0
MOCK_PORT_NAME = "MOCK (симулятор)"

# Мнемоніки, які опитуються періодично для оновлення телеметрії в GUI.
TELEMETRY_MNEMONICS = ("PV", "SP", "OP", "SW", "OS")


@dataclass
class TelemetrySnapshot:
    pv: Optional[float] = None
    sp: Optional[float] = None
    op: Optional[float] = None
    sw: Optional[int] = None
    os_state: Optional[int] = None


def list_available_ports() -> list[str]:
    """Список доступних COM-портів + запис для вбудованого симулятора."""
    ports: list[str] = []
    if _list_ports is not None:
        try:
            ports = [p.device for p in _list_ports.comports()]
        except Exception:
            ports = []
    ports.append(MOCK_PORT_NAME)
    return ports


class Al808Client:
    """Високорівневий, потокобезпечний клієнт протоколу AL808."""

    def __init__(self) -> None:
        self._ser = None  # serial.Serial-подібний об'єкт (реальний або mock)
        self._address = 1
        self._connected = False

        self._jobs_queue: "queue.Queue[tuple[str, Callable]]" = queue.Queue()
        self._event_queue: "queue.Queue[tuple]" = queue.Queue()
        self._callbacks: dict[str, Callable] = {}

        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        self._telemetry_enabled = True
        self.last_telemetry = TelemetrySnapshot()

    # ------------------------------------------------------------------
    # Підключення / відключення
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self, port: str, baudrate: int = 9600, address: int = 1) -> None:
        if self._connected:
            raise RuntimeError("Клієнт вже підключений")

        if port == MOCK_PORT_NAME:
            self._ser = mock_al808.MockAl808Serial(address=address)
        else:
            if serial is None:
                raise RuntimeError(
                    "Пакет pyserial не встановлено. Виконайте: pip install pyserial"
                )
            self._ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.SEVENBITS,
                parity=serial.PARITY_EVEN,
                stopbits=serial.STOPBITS_ONE,
                timeout=DEFAULT_TIMEOUT_S,
            )

        self._address = address
        self._connected = True
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def disconnect(self) -> None:
        self._stop_event.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=2.0)
        self._worker_thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self._connected = False
        # Очищаємо черги, щоб застарілі задачі не виконались після повторного connect().
        self._drain_queue(self._jobs_queue)
        self._callbacks.clear()

    def set_telemetry_enabled(self, enabled: bool) -> None:
        self._telemetry_enabled = enabled

    # ------------------------------------------------------------------
    # Публічний API для GUI
    # ------------------------------------------------------------------
    def submit_job(self, fn: Callable[["Al808Client"], object], callback: Optional[Callable] = None) -> str:
        """Ставить у чергу функцію fn(client), яка виконається у фоновому потоці.

        fn може використовувати self.read_param_sync()/write_param_sync() —
        вони призначені для виклику ЛИШЕ з фонового потоку (з робочого job).
        """
        job_id = uuid.uuid4().hex
        if callback is not None:
            self._callbacks[job_id] = callback
        self._jobs_queue.put((job_id, fn))
        return job_id

    def report_progress(self, current: int, total: int, text: str = "") -> None:
        """Прогрес довгої задачі (upload/download). GUI читає це з poll_events()."""
        self._event_queue.put(("progress", max(0, int(current)), max(1, int(total)), text))

    def poll_events(self, max_events: int = 200) -> list[tuple]:
        """Витягує події з фонового потоку. Викликати періодично з головного потоку GUI.

        Job-результати диспетчеризуються у відповідні callback тут же (тому цей
        метод має викликатись з того потоку, де безпечно чіпати GUI-віджети).
        Повертає список подій телеметрії/tx/rx для GUI: ('telemetry', TelemetrySnapshot),
        ('tx',), ('rx',), ('error', message).
        """
        out: list[tuple] = []
        for _ in range(max_events):
            try:
                ev = self._event_queue.get_nowait()
            except queue.Empty:
                break
            if ev[0] == "job_done":
                _, job_id, ok, payload = ev
                cb = self._callbacks.pop(job_id, None)
                if cb is not None:
                    try:
                        cb(ok, payload)
                    except Exception as exc:  # не даємо GUI-помилці зупинити цикл подій
                        out.append(("error", f"Помилка в callback: {exc}"))
            else:
                out.append(ev)
        return out

    # ------------------------------------------------------------------
    # Низькорівневі синхронні операції (лише для виклику з фонового потоку!)
    # ------------------------------------------------------------------
    def read_param_sync(self, mnemonic: str, timeout: float = DEFAULT_TIMEOUT_S):
        """Читає параметр, повертає розібране значення (float або int для hex-слів)."""
        raw = self._read_raw(mnemonic, timeout=timeout)
        return proto.parse_data_value(raw)

    def write_param_sync(self, mnemonic: str, value, hex_word: bool = False, timeout: float = DEFAULT_TIMEOUT_S) -> bool:
        """Пише параметр. Повертає True при ACK, піднімає ProtocolNak при NAK."""
        data_str = proto.format_data_value(value, hex_word=hex_word)
        return self._write_raw(mnemonic, data_str, timeout=timeout)

    # ------------------------------------------------------------------
    # Внутрішнє: сирий обмін по протоколу
    # ------------------------------------------------------------------
    def _read_raw(self, mnemonic: str, timeout: float = DEFAULT_TIMEOUT_S) -> str:
        frame = proto.build_poll_frame(self._address, mnemonic)
        resp = self._transceive(frame, timeout=timeout, expect_terminator=proto.ETX)
        result = proto.parse_read_response(resp, expected_mnemonic=mnemonic)
        return result.data

    def _write_raw(self, mnemonic: str, data: str, timeout: float = DEFAULT_TIMEOUT_S) -> bool:
        frame = proto.build_select_frame(self._address, mnemonic, data)
        resp = self._transceive(frame, timeout=timeout, expect_terminator=None, single_byte=True)
        return proto.parse_write_response(resp)

    def _transceive(self, frame: bytes, timeout: float, expect_terminator, single_byte: bool = False) -> bytes:
        """Надсилає кадр і читає відповідь. Викликати лише з фонового потоку."""
        if self._ser is None:
            raise proto.ProtocolTimeout("Порт не підключено")

        self._event_queue.put(("tx",))
        self._ser.timeout = timeout
        self._ser.write(frame)

        deadline = time.monotonic() + timeout
        buf = bytearray()
        if single_byte:
            byte = self._ser.read(1)
            if byte:
                buf += byte
        else:
            # Читаємо, поки не зустрінемо BCC-байт після ETX, або поки не спливе таймаут.
            while time.monotonic() < deadline:
                chunk = self._ser.read(1)
                if not chunk:
                    break
                buf += chunk
                if expect_terminator is not None and len(buf) >= 2 and buf[-2] == expect_terminator:
                    break  # останній прочитаний байт — це BCC після ETX

        if buf:
            self._event_queue.put(("rx",))
        return bytes(buf)

    # ------------------------------------------------------------------
    # Фоновий робочий цикл
    # ------------------------------------------------------------------
    def _worker_loop(self) -> None:
        last_poll = 0.0
        telemetry_index = 0

        while not self._stop_event.is_set():
            # 1) Пріоритет — задачі від GUI (download/upload/start/stop тощо).
            try:
                job_id, fn = self._jobs_queue.get(timeout=0.05)
            except queue.Empty:
                job_id, fn = None, None

            if fn is not None:
                try:
                    result = fn(self)
                    self._event_queue.put(("job_done", job_id, True, result))
                except Exception as exc:
                    self._event_queue.put(("job_done", job_id, False, str(exc)))
                continue  # повертаємось до циклу без телеметрії цього разу

            # 2) Періодична телеметрія PV/SP/OP/SW (по одному параметру за тик,
            #    щоб не блокувати чергу задач надовго).
            now = time.monotonic()
            if self._telemetry_enabled and (now - last_poll) >= (POLL_INTERVAL_S / len(TELEMETRY_MNEMONICS)):
                last_poll = now
                mnemonic = TELEMETRY_MNEMONICS[telemetry_index % len(TELEMETRY_MNEMONICS)]
                telemetry_index += 1
                try:
                    value = self.read_param_sync(mnemonic)
                    self._update_telemetry(mnemonic, value)
                except proto.ProtocolError:
                    pass  # мовчання/помилка одного параметра не критична — спробуємо ще раз пізніше

    def _update_telemetry(self, mnemonic: str, value) -> None:
        snap = self.last_telemetry
        if mnemonic == "PV":
            snap.pv = value
        elif mnemonic == "SP":
            snap.sp = value
        elif mnemonic == "OP":
            snap.op = value
        elif mnemonic == "SW":
            snap.sw = value
        elif mnemonic == "OS":
            snap.os_state = value
        self._event_queue.put(
            ("telemetry", TelemetrySnapshot(snap.pv, snap.sp, snap.op, snap.sw, snap.os_state))
        )

    @staticmethod
    def _drain_queue(q: "queue.Queue") -> None:
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break
