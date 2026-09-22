"""Рядки інтерфейсу IR6500 Controller (українська / англійська)."""

from __future__ import annotations

LANG_UK = "uk"
LANG_EN = "en"
DEFAULT_LANG = LANG_UK
SUPPORTED = (LANG_UK, LANG_EN)

# (код, власна назва мови — не перекладається)
LANGUAGE_CHOICES: tuple[tuple[str, str], ...] = (
    (LANG_UK, "Українська"),
    (LANG_EN, "English"),
)

_STRINGS: dict[str, dict[str, str]] = {
    LANG_UK: {
        "app_title": "IR6500 Controller (Python) — AL808/PC900",
        "language": "Мова",
        "help": "Довідка",
        "help_title": "Довідка — IR6500 Controller",
        "port_config": "Налаштування порту",
        "port": "Порт",
        "baud_rate": "Швидкість",
        "device_address": "Адреса приладу",
        "connect": "Підключити",
        "disconnect": "Відключити",
        "refresh": "Оновити",
        "interface": "Інтерфейс",
        "conn_connected": "ПІДКЛЮЧЕНО",
        "conn_disconnected": "ВІДКЛЮЧЕНО",
        "status": "Статус",
        "current_temp": "Поточна T",
        "set_temp": "Задана T",
        "power": "ПОТУЖНІСТЬ:",
        "mode_idle": "—",
        "mode_prefix": "Режим: {label}",
        "mode_idle_manual": "IDLE / ручний",
        "mode_run": "RUN (профіль)",
        "mode_hold": "HOLD (пауза)",
        "mode_unknown": "невідомо",
        "sw_dash": "SW: —",
        "sw_line": (
            "SW: 0x{sw:04X} | {auto_manual} | Вихід аварії: {alarm_out} | "
            "HiAlarm: {hi_alarm} | LoAlarm: {lo_alarm} | Помилка входу: {input_fault}"
        ),
        "yes": "так",
        "no": "ні",
        "manual_mode": "Ручний режим (стала температура)",
        "target_temp_c": "Цільова температура, °C",
        "apply_sl": "Застосувати (SL)",
        "manual_hint": (
            "Запускає утримання температури (нагрів лише в RUN).\n"
            "STOP повертає крок 1 профілю і вимикає нагрів."
        ),
        "manual_hold_status": "Ручний режим: утримання",
        "tab_basic": "Basic",
        "tab_advanced": "Advanced",
        "download": "Download",
        "upload": "Upload",
        "ptn_hint": "слот профілю в приладі (0–9). Перемикання читає цей слот.",
        "preset": "Пресет",
        "load_preset": "Завантажити",
        "store_preset": "Зберегти",
        "temp_settings": "Налаштування температури (кроки 1–8)",
        "step_n": "Крок {n}",
        "active_step": "Активний крок",
        "ramp_rate": "Ramp Rate (°C/с)",
        "target_temp": "Target Temp (°C)",
        "dwell_time": "Dwell Time (с)",
        "holdback": "Holdback (°C)",
        "steps_hint": (
            'Зніміть галочку «Активний крок», щоб вимкнути цей і всі наступні кроки. '
            "У вимкнені поля записуються нулі (Ramp/Target/Dwell); графік їх не показує. "
            "Застосунок сам зупиняє профіль (STOP), коли доходить до вимкненого кроку."
        ),
        "read_all": "Прочитати всі",
        "write_all": "Записати всі",
        "probe_steps": "Перевірити кроки 1–8 (probe)",
        "self_tune_on": "Self-tune: ON",
        "self_tune_off": "Self-tune: OFF",
        "read": "Прочитати",
        "write": "Записати",
        "step_idle": "Крок: —",
        "step_status": "Крок {step}/{total}: {phase}",
        "phase_ramp": "Рампа",
        "phase_dwell": "Витримка",
        "chart_xlabel": "Час, с",
        "chart_ylabel": "Температура, °C",
        "chart_sv_plan": "SV (план профілю)",
        "chart_pv_now": "PV зараз: {pv:.1f}°C",
        "chart_sv_actual": "SV (задана, факт)",
        "chart_pv_measured": "PV (виміряна)",
        "chart_sv_plan_line": "SV (план)",
        "chart_step": "Крок {n}",
        "conn_title": "Підключення",
        "conn_select_port": "Оберіть порт",
        "conn_bad_baud": "Некоректний бод/адреса",
        "conn_failed": "Не вдалося підключитись: {exc}",
        "xfer_connecting": "Підключення",
        "not_connected_title": "Немає з'єднання",
        "not_connected": "Спочатку підключіться до приладу (Connect)",
        "ptn_locked": "Номер патерну не змінюється під час RUN/HOLD — спочатку STOP.",
        "ptn_switch_failed": "Не вдалося перемкнути патерн: {result}",
        "xfer_error": "Помилка",
        "xfer_ptn_error": "Помилка PTN",
        "download_failed": "Помилка читання профілю: {result}",
        "upload_failed": "Помилка запису профілю: {result}",
        "upload_partial": "Не всі параметри записано:\n{errors}",
        "xfer_missing_steps": "Немає кроків {steps}",
        "xfer_loaded": "Завантажено",
        "xfer_partial": "Не все записано",
        "xfer_saved_ptn": "Записано PTN {ptn}",
        "manual_title": "Ручний режим",
        "manual_bad_temp": "Некоректна температура",
        "command_title": "Команда",
        "command_failed": "Помилка: {result}",
        "advanced_bad_value": "Некоректне значення для {mnemonic}",
        "preset_title": "Пресет",
        "preset_save_title": "Зберегти пресет",
        "preset_save_prompt": "Назва пресету:",
        "preset_saved": "Пресет «{name}» збережено",
        "probe_title": "Перевірка кроків 1–8",
        "probe_failed": "Помилка перевірки: {result}",
        "probe_unsupported": (
            "Прилад НЕ відповів на мнемоніки сегментів: {unsupported}.\n"
            "Офіційно задокументовані лише сегменти 1 і 2 — інші могли бути "
            "невірно екстрапольовані."
        ),
        "probe_ok": "Усі 8 сегментів відповіли на читання.",
        "progress_step": "Крок {n}",
        "progress_done": "Готово",
        "param_HA": "Уставка аварії верхньої межі",
        "param_LA": "Уставка аварії нижньої межі",
        "param_DA": "Уставка аварії відхилення",
        "param_XP": "Пропорційна смуга (PID)",
        "param_TI": "Інтегральний час (PID)",
        "param_TD": "Диференціальний час (PID)",
        "param_HB": "Обмеження перерегулювання зверху",
        "param_LB": "Обмеження перерегулювання знизу",
        "param_CH": "Період циклу виходу нагріву",
        "param_CC": "Період циклу виходу охолодження",
        "param_RG": "Відносний коефіцієнт охолодження",
        "param_HS": "Верхня межа допустимої ставки",
        "param_LS": "Нижня межа допустимої ставки",
        "param_BP": "Потужність при обриві датчика",
        "param_HO": "Ліміт потужності виходу нагріву",
        "param_SR": "Швидкість рампу до ставки",
        "help_body": (
            "Підключення\n"
            "Оберіть COM-порт, швидкість і адресу приладу, натисніть «Підключити».\n"
            "Порт «MOCK (симулятор)» працює без апаратури: зручно перевірити інтерфейс.\n"
            "Після з'єднання програма читає поточний PTN, профіль і локальну ставку SL.\n"
            "\n"
            "Профіль (вкладка Basic)\n"
            "PTN — слот програми в приладі (0–9). Зміна слота зчитує його з контролера.\n"
            "У RUN або HOLD номер патерну змінити не можна — спочатку STOP.\n"
            "Кожен крок: швидкість рампи (°C/с), цільова температура, час витримки.\n"
            "Знята галочка «Активний крок» вимикає цей і всі наступні кроки.\n"
            "Holdback — пауза програми, якщо PV відхиляється від SV більше ніж на задане значення.\n"
            "Download читає профіль з приладу, Upload записує таблицю в обраний PTN.\n"
            "\n"
            "START / STOP / HOLD\n"
            "START запускає програматор (або продовжує після HOLD).\n"
            "HOLD заморожує SV і таймер сегмента. STOP скидає програму в IDLE.\n"
            "\n"
            "Ручний режим\n"
            "«Застосувати» запускає утримання заданої температури через програматор (RUN).\n"
            "На цьому приладі в IDLE нагрів завжди вимкнений, навіть якщо SL уже дорівнює SP.\n"
            "STOP повертає попередній крок 1 профілю і вимикає вихід.\n"
            "\n"
            "Графік\n"
            "До старту показано план профілю. У режимі планування перетягуйте червоні точки\n"
            "(ціль / Ramp Rate) і сині точки (кінець витримки / Dwell Time) прямо на графіку.\n"
            "Під час RUN малюються живі PV (червоний) і SV (синій)."
        ),
    },
    LANG_EN: {
        "app_title": "IR6500 Controller (Python) — AL808/PC900",
        "language": "Language",
        "help": "Help",
        "help_title": "Help — IR6500 Controller",
        "port_config": "Port Configuration",
        "port": "Port",
        "baud_rate": "Baud Rate",
        "device_address": "Device address",
        "connect": "Connect",
        "disconnect": "Disconnect",
        "refresh": "Refresh",
        "interface": "Interface",
        "conn_connected": "CONNECTED",
        "conn_disconnected": "DISCONNECTED",
        "status": "Status",
        "current_temp": "Current Temp",
        "set_temp": "Set Temp",
        "power": "POWER:",
        "mode_idle": "—",
        "mode_prefix": "Mode: {label}",
        "mode_idle_manual": "IDLE / manual",
        "mode_run": "RUN (profile)",
        "mode_hold": "HOLD (pause)",
        "mode_unknown": "unknown",
        "sw_dash": "SW: —",
        "sw_line": (
            "SW: 0x{sw:04X} | {auto_manual} | Alarm output: {alarm_out} | "
            "HiAlarm: {hi_alarm} | LoAlarm: {lo_alarm} | Input fault: {input_fault}"
        ),
        "yes": "yes",
        "no": "no",
        "manual_mode": "Manual mode (constant temperature)",
        "target_temp_c": "Target temperature, °C",
        "apply_sl": "Apply (SL)",
        "manual_hint": (
            "Holds temperature (heating only works in RUN).\n"
            "STOP restores profile step 1 and turns heat off."
        ),
        "manual_hold_status": "Manual mode: holding",
        "tab_basic": "Basic",
        "tab_advanced": "Advanced",
        "download": "Download",
        "upload": "Upload",
        "ptn_hint": "profile slot in the controller (0–9). Switching reads that slot.",
        "preset": "Preset",
        "load_preset": "Load Preset",
        "store_preset": "Store",
        "temp_settings": "Temperature Settings (Step 1-8)",
        "step_n": "Step {n}",
        "active_step": "Active step",
        "ramp_rate": "Ramp Rate (°C/s)",
        "target_temp": "Target Temp (°C)",
        "dwell_time": "Dwell Time (s)",
        "holdback": "Holdback (°C)",
        "steps_hint": (
            'Clear the "Active step" checkbox to disable this and all following steps. '
            "Disabled fields are written as zeros (Ramp/Target/Dwell); the chart hides them. "
            "The app sends STOP when execution reaches a disabled step."
        ),
        "read_all": "Read all",
        "write_all": "Write all",
        "probe_steps": "Probe steps 1-8",
        "self_tune_on": "Self-tune: ON",
        "self_tune_off": "Self-tune: OFF",
        "read": "Read",
        "write": "Write",
        "step_idle": "Step: —",
        "step_status": "Step {step}/{total}: {phase}",
        "phase_ramp": "Ramp",
        "phase_dwell": "Dwell",
        "chart_xlabel": "Time, s",
        "chart_ylabel": "Temperature, °C",
        "chart_sv_plan": "SV (planned profile)",
        "chart_pv_now": "PV now: {pv:.1f}°C",
        "chart_sv_actual": "SV (setpoint, live)",
        "chart_pv_measured": "PV (measured)",
        "chart_sv_plan_line": "SV (plan)",
        "chart_step": "Step {n}",
        "conn_title": "Connection",
        "conn_select_port": "Select a port",
        "conn_bad_baud": "Invalid baud rate or address",
        "conn_failed": "Could not connect: {exc}",
        "xfer_connecting": "Connecting",
        "not_connected_title": "Not connected",
        "not_connected": "Connect to the controller first (Connect)",
        "ptn_locked": "PTN cannot be changed during RUN/HOLD — press STOP first.",
        "ptn_switch_failed": "Failed to switch pattern: {result}",
        "xfer_error": "Error",
        "xfer_ptn_error": "PTN error",
        "download_failed": "Failed to read profile: {result}",
        "upload_failed": "Failed to write profile: {result}",
        "upload_partial": "Not all parameters were written:\n{errors}",
        "xfer_missing_steps": "Missing steps {steps}",
        "xfer_loaded": "Loaded",
        "xfer_partial": "Incomplete write",
        "xfer_saved_ptn": "Saved PTN {ptn}",
        "manual_title": "Manual mode",
        "manual_bad_temp": "Invalid temperature",
        "command_title": "Command",
        "command_failed": "Error: {result}",
        "advanced_bad_value": "Invalid value for {mnemonic}",
        "preset_title": "Preset",
        "preset_save_title": "Save preset",
        "preset_save_prompt": "Preset name:",
        "preset_saved": "Preset '{name}' saved",
        "probe_title": "Step 1-8 probe",
        "probe_failed": "Probe failed: {result}",
        "probe_unsupported": (
            "The controller did not answer segment mnemonics: {unsupported}.\n"
            "Only segments 1 and 2 are officially documented — the others may have "
            "been extrapolated incorrectly."
        ),
        "probe_ok": "All 8 segments answered the read.",
        "progress_step": "Step {n}",
        "progress_done": "Done",
        "param_HA": "High alarm setpoint",
        "param_LA": "Low alarm setpoint",
        "param_DA": "Deviation alarm setpoint",
        "param_XP": "Proportional band (PID)",
        "param_TI": "Integral time (PID)",
        "param_TD": "Derivative time (PID)",
        "param_HB": "High cutback",
        "param_LB": "Lower cutback",
        "param_CH": "Heat output cycle time",
        "param_CC": "Cool output cycle time",
        "param_RG": "Relative cooling gain",
        "param_HS": "Setpoint high limit",
        "param_LS": "Setpoint low limit",
        "param_BP": "Sensor-break power",
        "param_HO": "Heat output limit",
        "param_SR": "Ramp-to-setpoint rate",
        "help_body": (
            "Connection\n"
            "Select the COM port, baud rate and device address, then click Connect.\n"
            "The “MOCK (симулятор)” port runs without hardware — useful to try the UI.\n"
            "After connect the app reads the current PTN, profile and local setpoint SL.\n"
            "\n"
            "Profile (Basic tab)\n"
            "PTN is the program slot in the controller (0–9). Changing it downloads that slot.\n"
            "PTN cannot be changed in RUN or HOLD — press STOP first.\n"
            "Each step: ramp rate (°C/s), target temperature, dwell time.\n"
            "Clearing “Active step” disables this and all following steps.\n"
            "Holdback pauses the program if PV drifts from SV by more than the band.\n"
            "Download reads the profile from the device; Upload writes the table to the selected PTN.\n"
            "\n"
            "START / STOP / HOLD\n"
            "START runs the programmer (or resumes after HOLD).\n"
            "HOLD freezes SV and the segment timer. STOP returns the program to IDLE.\n"
            "\n"
            "Manual mode\n"
            "Apply starts a constant-temperature hold via the programmer (RUN).\n"
            "On this controller IDLE always forces heat output to 0 %, even if SL already equals SP.\n"
            "STOP restores the previous profile step 1 and turns the output off.\n"
            "\n"
            "Chart\n"
            "Before start the planned profile is shown. In planning mode drag the red points\n"
            "(target / Ramp Rate) and blue points (dwell end / Dwell Time) on the chart.\n"
            "During RUN the live PV (red) and SV (blue) traces are drawn."
        ),
    },
}

_current_lang = DEFAULT_LANG


def normalize_lang(code: str | None) -> str:
    if code in SUPPORTED:
        return code
    return DEFAULT_LANG


def current_lang() -> str:
    return _current_lang


def set_language(code: str) -> str:
    global _current_lang
    _current_lang = normalize_lang(code)
    return _current_lang


def language_label(code: str) -> str:
    code = normalize_lang(code)
    for item_code, label in LANGUAGE_CHOICES:
        if item_code == code:
            return label
    return LANGUAGE_CHOICES[0][1]


def code_from_label(label: str) -> str:
    for code, name in LANGUAGE_CHOICES:
        if name == label:
            return code
    return DEFAULT_LANG


def t(key: str, **kwargs) -> str:
    table = _STRINGS.get(_current_lang) or _STRINGS[DEFAULT_LANG]
    text = table.get(key)
    if text is None:
        text = _STRINGS[DEFAULT_LANG].get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text
