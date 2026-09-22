"""Tkinter GUI застосунку IR6500 Controller (Python-версія на базі протоколу AL808/PC900).

Функціонал відповідає скріншотам оригінального застосунку (мінус фізичні
джойстик-кнопки/Emergency Shutdown/Max Delta — прибрано за рішенням користувача):

    - Графік запланованого профілю (до старту) та live-графік PV/SP (під час RUN).
    - Port Configuration: порт, бод, підключення.
    - Статус: TX/RX індикатори, поточна/задана температура, потужність виходу.
    - Ручний режим сталої температури (SL).
    - Basic: PTN 0-9 (мнемоніка `ch`, перемикає слот у приладі) + таблиця Step 1-8
      (Ramp Rate/Target Temp/Dwell Time) + Holdback + пресети.
    - Advanced: решта параметрів AL808 (HA, LA, DA, XP, TI, TD, HB, LB, CH, CC, RG,
      HS, LS, BP, HO, SR), self-tuning, перевірка (probe) підтримки сегментів 1-8.
    - Download / Upload / Start / Stop.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from . import al808_client, al808_protocol as proto, chart_utils, config, i18n, presets, profile_controller
from .profile_controller import ProfileUI, SegmentUI, StepTracker

BAUD_RATES = [300, 600, 1200, 2400, 4800, 9600, 19200]
ADVANCED_PARAMS = [
    "HA", "LA", "DA", "XP", "TI", "TD", "HB", "LB",
    "CH", "CC", "RG", "HS", "LS", "BP", "HO", "SR",
]
CHART_HEIGHT_RATIO = 0.60
TOP_HEIGHT_RATIO = 0.15
MIDDLE_HEIGHT_RATIO = 0.25
# Мінімум для бічної колонки Download/Upload/START/STOP/HOLD без обрізання.
SIDE_PANEL_MIN_HEIGHT = 300


class IR6500App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.geometry("1100x980")
        self.minsize(980, 860)

        self.client = al808_client.Al808Client()
        self.app_config = config.load_config()
        i18n.set_language(self.app_config.language)
        self.title(i18n.t("app_title"))

        self.profile = ProfileUI()
        self.step_tracker: StepTracker | None = None
        self.run_history: list[tuple[float, float, float]] = []  # (t_с, pv, sp)
        self.run_start_time: float | None = None
        self.is_running = False
        self.is_held = False
        self._device_ptn: int | None = None
        self._ptn_busy = False

        self.adv_vars: dict[str, tk.StringVar] = {}
        self.seg_vars: list[dict[str, tk.Variable]] = [
            {
                "ramp": tk.StringVar(),
                "target": tk.StringVar(),
                "dwell": tk.StringVar(),
                "enabled": tk.BooleanVar(value=True),
            }
            for _ in range(profile_controller.SEGMENT_COUNT)
        ]
        self._seg_field_widgets: list[dict[str, ttk.Spinbox]] = []  # для сірення вимкнених кроків
        self._auto_stop_sent = False
        self._manual_backup: dict | None = None
        self._manual_hold = False
        self.holdback_var = tk.StringVar()
        self.ptn_var = tk.StringVar(value="1")
        self.preset_var = tk.StringVar()
        self.manual_temp_var = tk.StringVar(value="25.0")

        self.pv_var = tk.StringVar(value="--.- °C")
        self.sp_var = tk.StringVar(value="--.- °C")
        self.power_var = tk.DoubleVar(value=0.0)
        self.mode_var = tk.StringVar(value=i18n.t("mode_prefix", label=i18n.t("mode_idle")))
        self.sw_var = tk.StringVar(value=i18n.t("sw_dash"))
        self.step_var = tk.StringVar(value=i18n.t("step_idle"))
        self.conn_status_var = tk.StringVar(value=i18n.t("conn_disconnected"))
        self.xfer_status_var = tk.StringVar(value="")
        self.lang_var = tk.StringVar(value=i18n.language_label(i18n.current_lang()))
        self._xfer_after_id: str | None = None
        self._i18n_widgets: list[tuple[tk.Widget, str, dict]] = []
        self._help_window: tk.Toplevel | None = None
        self._help_text: ScrolledText | None = None
        self._last_os_state: int | None = None
        self._last_sw: int | None = None
        self._applying_chart_height = False

        self.current_pv: float | None = None
        self._chart_handle_specs: list[dict] = []  # для інтерактивного редагування мишкою
        self._dragging: dict | None = None
        self._last_static_redraw = 0.0

        presets.seed_default_presets()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=0)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(3, weight=0)

        self._build_chart()
        self._build_top_frame()
        self._build_middle_frame()
        self._build_statusbar()
        self._apply_language()

        self._refresh_preset_list()
        self._apply_profile_to_fields(self.profile)

        self.bind("<Configure>", self._on_root_configure)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._poll_client_events)
        self.after_idle(self._apply_section_heights)

    # ------------------------------------------------------------------
    # Побудова UI
    # ------------------------------------------------------------------
    def _bind_text(self, widget, key: str, **kwargs):
        self._i18n_widgets.append((widget, key, kwargs))
        widget.configure(text=i18n.t(key, **kwargs))
        return widget

    def _on_root_configure(self, event) -> None:
        if event.widget is not self or self._applying_chart_height:
            return
        self._apply_section_heights()

    def _apply_section_heights(self) -> None:
        height = self.winfo_height()
        if height <= 1:
            return
        bar_h = 0
        if hasattr(self, "status_bar"):
            bar_h = max(self.status_bar.winfo_reqheight(), 22)
        usable = max(height - bar_h, 1)
        top_h = max(int(usable * TOP_HEIGHT_RATIO), 110)
        middle_h = max(int(usable * MIDDLE_HEIGHT_RATIO), SIDE_PANEL_MIN_HEIGHT)
        chart_h = max(int(usable * CHART_HEIGHT_RATIO), 180)
        if chart_h + top_h + middle_h > usable:
            chart_h = max(180, usable - top_h - middle_h)
        if chart_h + top_h + middle_h > usable:
            middle_h = max(SIDE_PANEL_MIN_HEIGHT, usable - top_h - chart_h)
        specs = (
            (self.chart_frame, chart_h),
            (self.top_frame, top_h),
            (self.middle_frame, middle_h),
        )
        if all(int(frame.cget("height") or 0) == target for frame, target in specs):
            return
        self._applying_chart_height = True
        try:
            for frame, target in specs:
                frame.configure(height=target)
        finally:
            self._applying_chart_height = False

    def _tighten_chart_margins(self) -> None:
        self.fig.patch.set_facecolor("#e6e6e6")
        self.fig.subplots_adjust(left=0.055, right=0.995, top=0.98, bottom=0.10)

    def _build_chart(self) -> None:
        self.chart_frame = tk.Frame(self, height=int(980 * CHART_HEIGHT_RATIO), bg="#e6e6e6")
        self.chart_frame.grid(row=0, column=0, sticky="nsew")
        self.chart_frame.grid_propagate(False)
        self.chart_frame.pack_propagate(False)

        self.fig = Figure(figsize=(8, 3.2), dpi=100, facecolor="#e6e6e6")
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
        self.canvas.get_tk_widget().configure(highlightthickness=0, bd=0, bg="#e6e6e6")
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.canvas.mpl_connect("button_press_event", self._on_chart_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_chart_motion)
        self.canvas.mpl_connect("button_release_event", self._on_chart_release)

        self._redraw_static_profile()

    def _build_top_frame(self) -> None:
        self.top_frame = tk.Frame(self, height=int(980 * TOP_HEIGHT_RATIO))
        self.top_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=2)
        self.top_frame.grid_propagate(False)
        self.top_frame.pack_propagate(False)

        self._build_connection_frame(self.top_frame).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        self._build_status_frame(self.top_frame).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        self._build_manual_frame(self.top_frame).pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        self._build_interface_frame(self.top_frame).pack(side=tk.LEFT, fill=tk.Y)

    def _build_connection_frame(self, parent) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent)
        self._bind_text(frame, "port_config")

        self._bind_text(ttk.Label(frame), "port").grid(row=0, column=0, sticky="w", padx=3, pady=1)
        self.port_combo = ttk.Combobox(frame, width=16, state="readonly")
        self.port_combo.grid(row=0, column=1, padx=3, pady=1)

        self._bind_text(ttk.Label(frame), "baud_rate").grid(row=1, column=0, sticky="w", padx=3, pady=1)
        self.baud_combo = ttk.Combobox(
            frame, width=16, state="readonly", values=[str(b) for b in BAUD_RATES]
        )
        self.baud_combo.set(str(self.app_config.baudrate))
        self.baud_combo.grid(row=1, column=1, padx=3, pady=1)

        self._bind_text(ttk.Label(frame), "device_address").grid(
            row=2, column=0, sticky="w", padx=3, pady=1
        )
        self.address_spin = ttk.Spinbox(frame, from_=0, to=99, width=14)
        self.address_spin.set(str(self.app_config.address))
        self.address_spin.grid(row=2, column=1, padx=3, pady=1)

        btns = ttk.Frame(frame)
        btns.grid(row=3, column=0, columnspan=2, pady=(2, 1))
        self.connect_btn = ttk.Button(btns, command=self._on_connect)
        self._bind_text(self.connect_btn, "connect")
        self.connect_btn.pack(side=tk.LEFT, padx=2)
        self.disconnect_btn = ttk.Button(btns, command=self._on_disconnect, state="disabled")
        self._bind_text(self.disconnect_btn, "disconnect")
        self.disconnect_btn.pack(side=tk.LEFT, padx=2)
        refresh_btn = ttk.Button(frame, command=self._on_refresh_ports)
        self._bind_text(refresh_btn, "refresh")
        refresh_btn.grid(row=0, column=2, rowspan=1, padx=4)

        self._on_refresh_ports()
        return frame

    def _build_status_frame(self, parent) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent)
        self._bind_text(frame, "status")

        ind_frame = ttk.Frame(frame)
        ind_frame.grid(row=0, column=0, columnspan=2, sticky="w", padx=3, pady=0)
        ttk.Label(ind_frame, text="TX").pack(side=tk.LEFT)
        self.tx_indicator = tk.Canvas(ind_frame, width=14, height=14, highlightthickness=1)
        self.tx_indicator.pack(side=tk.LEFT, padx=(2, 8))
        self._set_indicator(self.tx_indicator, "#c0c0c0")
        ttk.Label(ind_frame, text="RX").pack(side=tk.LEFT)
        self.rx_indicator = tk.Canvas(ind_frame, width=14, height=14, highlightthickness=1)
        self.rx_indicator.pack(side=tk.LEFT, padx=2)
        self._set_indicator(self.rx_indicator, "#c0c0c0")

        temps = ttk.Frame(frame)
        temps.grid(row=1, column=0, columnspan=2, sticky="w", padx=3, pady=1)
        pv_box = ttk.Frame(temps)
        pv_box.pack(side=tk.LEFT, padx=(0, 12))
        self._bind_text(ttk.Label(pv_box), "current_temp").pack(anchor="w")
        ttk.Label(pv_box, textvariable=self.pv_var, foreground="red", font=("Arial", 14, "bold")).pack(
            anchor="w"
        )
        sp_box = ttk.Frame(temps)
        sp_box.pack(side=tk.LEFT)
        self._bind_text(ttk.Label(sp_box), "set_temp").pack(anchor="w")
        ttk.Label(sp_box, textvariable=self.sp_var, foreground="blue", font=("Arial", 14, "bold")).pack(
            anchor="w"
        )

        self._bind_text(ttk.Label(frame), "power").grid(row=2, column=0, sticky="w", padx=3, pady=1)
        self.power_bar = ttk.Progressbar(
            frame, orient="horizontal", length=160, maximum=100, variable=self.power_var
        )
        self.power_bar.grid(row=2, column=1, sticky="w", padx=3, pady=1)

        ttk.Label(frame, textvariable=self.mode_var).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=3, pady=0
        )
        ttk.Label(frame, textvariable=self.sw_var, wraplength=280, justify="left").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 2)
        )
        return frame

    def _build_manual_frame(self, parent) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent)
        self._bind_text(frame, "manual_mode")
        self._bind_text(ttk.Label(frame), "target_temp_c").grid(
            row=0, column=0, sticky="w", padx=3, pady=1
        )
        ttk.Entry(frame, textvariable=self.manual_temp_var, width=10).grid(
            row=0, column=1, padx=3, pady=1
        )
        apply_btn = ttk.Button(frame, command=self._on_manual_apply)
        self._bind_text(apply_btn, "apply_sl")
        apply_btn.grid(row=1, column=0, columnspan=2, pady=2)
        hint = ttk.Label(frame, justify="left", foreground="#555555", wraplength=220)
        self._bind_text(hint, "manual_hint")
        hint.grid(row=2, column=0, columnspan=2, sticky="w", padx=3)
        return frame

    def _build_interface_frame(self, parent) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent)
        self._bind_text(frame, "interface")
        self._bind_text(ttk.Label(frame), "language").grid(
            row=0, column=0, sticky="w", padx=4, pady=2
        )
        self.lang_combo = ttk.Combobox(
            frame,
            width=14,
            state="readonly",
            textvariable=self.lang_var,
            values=[label for _code, label in i18n.LANGUAGE_CHOICES],
        )
        self.lang_combo.grid(row=0, column=1, padx=4, pady=2)
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_language_selected)
        help_btn = ttk.Button(frame, command=self._on_help)
        self._bind_text(help_btn, "help")
        help_btn.grid(row=1, column=0, columnspan=2, padx=4, pady=6, sticky="ew")
        return frame

    def _build_middle_frame(self) -> None:
        self.middle_frame = tk.Frame(self, height=int(980 * MIDDLE_HEIGHT_RATIO))
        self.middle_frame.grid(row=2, column=0, sticky="nsew", padx=4, pady=2)
        self.middle_frame.grid_propagate(False)
        self.middle_frame.pack_propagate(False)

        self.notebook = ttk.Notebook(self.middle_frame)
        self.notebook.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.notebook.add(self._build_basic_tab(self.notebook), text=i18n.t("tab_basic"))
        self.notebook.add(self._build_advanced_tab(self.notebook), text=i18n.t("tab_advanced"))

        side = ttk.Frame(self.middle_frame)
        side.pack(side=tk.LEFT, fill=tk.Y, anchor="n", padx=(6, 0))
        self.download_btn = ttk.Button(side, command=self._on_download, width=14)
        self._bind_text(self.download_btn, "download")
        self.download_btn.pack(pady=(2, 2))
        self.upload_btn = ttk.Button(side, command=self._on_upload, width=14)
        self._bind_text(self.upload_btn, "upload")
        self.upload_btn.pack(pady=2)
        self.xfer_progress = ttk.Progressbar(side, length=118, mode="determinate", maximum=100)
        self.xfer_progress.pack(pady=(6, 0))
        ttk.Label(
            side, textvariable=self.xfer_status_var, width=16, wraplength=118,
            foreground="#444444",
        ).pack(pady=(2, 2))
        start_btn = tk.Button(
            side, text="START", command=self._on_start, width=14,
            fg="green", font=("Arial", 11, "bold"),
        )
        start_btn.pack(pady=(8, 2))
        stop_btn = tk.Button(
            side, text="STOP", command=self._on_stop, width=14,
            fg="red", font=("Arial", 11, "bold"),
        )
        stop_btn.pack(pady=2)
        self.hold_btn = tk.Button(
            side, text="HOLD", command=self._on_hold, width=14,
            fg="blue", font=("Arial", 11, "bold"),
        )
        self.hold_btn.pack(pady=(2, 4))

    def _build_basic_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent)

        top_row = ttk.Frame(tab)
        top_row.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(top_row, text="PTN").pack(side=tk.LEFT)
        self.ptn_combo = ttk.Combobox(
            top_row, width=4, textvariable=self.ptn_var, state="readonly",
            values=[str(i) for i in range(proto.PTN_MIN, proto.PTN_MAX + 1)],
        )
        self.ptn_combo.pack(side=tk.LEFT, padx=(2, 4))
        self.ptn_combo.bind("<<ComboboxSelected>>", self._on_ptn_selected)
        ptn_hint = ttk.Label(top_row, foreground="#555555")
        self._bind_text(ptn_hint, "ptn_hint")
        ptn_hint.pack(side=tk.LEFT, padx=(0, 12))

        self._bind_text(ttk.Label(top_row), "preset").pack(side=tk.LEFT, padx=(12, 2))
        self.preset_combo = ttk.Combobox(top_row, width=20, textvariable=self.preset_var, state="readonly")
        self.preset_combo.pack(side=tk.LEFT, padx=2)
        load_btn = ttk.Button(top_row, command=self._on_load_preset)
        self._bind_text(load_btn, "load_preset")
        load_btn.pack(side=tk.LEFT, padx=2)
        store_btn = ttk.Button(top_row, command=self._on_save_preset)
        self._bind_text(store_btn, "store_preset")
        store_btn.pack(side=tk.LEFT, padx=2)

        grid = ttk.LabelFrame(tab)
        self._bind_text(grid, "temp_settings")
        grid.pack(fill=tk.X, padx=4, pady=2)

        self._step_col_labels: list[ttk.Label] = []
        for col in range(1, profile_controller.SEGMENT_COUNT + 1):
            lbl = ttk.Label(grid, anchor="center")
            self._bind_text(lbl, "step_n", n=col)
            lbl.grid(row=0, column=col, padx=3, pady=2, sticky="ew")
            self._step_col_labels.append(lbl)

        self._bind_text(ttk.Label(grid), "active_step").grid(
            row=1, column=0, sticky="w", padx=3, pady=2
        )
        for col in range(profile_controller.SEGMENT_COUNT):
            var = self.seg_vars[col]["enabled"]
            ttk.Checkbutton(
                grid, variable=var, command=lambda c=col: self._on_step_enabled_toggled(c)
            ).grid(row=1, column=col + 1, pady=2)

        row_specs = [
            ("ramp_rate", "ramp", 0.01),
            ("target_temp", "target", 0.5),
            ("dwell_time", "dwell", 1.0),
        ]
        self._seg_field_widgets = [dict() for _ in range(profile_controller.SEGMENT_COUNT)]
        for r, (label_key, key, incr) in enumerate(row_specs, start=2):
            self._bind_text(ttk.Label(grid), label_key).grid(
                row=r, column=0, sticky="w", padx=3, pady=2
            )
            for col in range(profile_controller.SEGMENT_COUNT):
                var = self.seg_vars[col][key]
                sb = ttk.Spinbox(
                    grid, from_=-999.0, to=9999.0, increment=incr, textvariable=var, width=7,
                    command=self._on_fields_changed,
                )
                sb.grid(row=r, column=col + 1, padx=2, pady=2)
                sb.bind("<FocusOut>", lambda e: self._on_fields_changed())
                sb.bind("<Return>", lambda e: self._on_fields_changed())
                self._seg_field_widgets[col][key] = sb

        hb_row = ttk.Frame(tab)
        hb_row.pack(fill=tk.X, padx=4, pady=(0, 2))
        self._bind_text(ttk.Label(hb_row), "holdback").pack(side=tk.LEFT)
        ttk.Spinbox(
            hb_row, from_=0.0, to=9999.0, increment=1.0, textvariable=self.holdback_var, width=8,
            command=self._on_fields_changed,
        ).pack(side=tk.LEFT, padx=4)
        steps_hint = ttk.Label(hb_row, foreground="#555555", wraplength=520, justify="left")
        self._bind_text(steps_hint, "steps_hint")
        steps_hint.pack(side=tk.LEFT, padx=(16, 0))

        return tab

    def _build_advanced_tab(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent)

        top_row = ttk.Frame(tab)
        top_row.pack(fill=tk.X, padx=6, pady=6)
        read_all_btn = ttk.Button(top_row, command=self._on_adv_read_all)
        self._bind_text(read_all_btn, "read_all")
        read_all_btn.pack(side=tk.LEFT, padx=2)
        write_all_btn = ttk.Button(top_row, command=self._on_adv_write_all)
        self._bind_text(write_all_btn, "write_all")
        write_all_btn.pack(side=tk.LEFT, padx=2)
        probe_btn = ttk.Button(top_row, command=self._on_probe_segments)
        self._bind_text(probe_btn, "probe_steps")
        probe_btn.pack(side=tk.LEFT, padx=12)
        tune_on = ttk.Button(top_row, command=lambda: self._on_set_self_tune(1))
        self._bind_text(tune_on, "self_tune_on")
        tune_on.pack(side=tk.LEFT, padx=2)
        tune_off = ttk.Button(top_row, command=lambda: self._on_set_self_tune(0))
        self._bind_text(tune_off, "self_tune_off")
        tune_off.pack(side=tk.LEFT, padx=2)

        grid = ttk.Frame(tab)
        grid.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self._adv_desc_labels: dict[str, ttk.Label] = {}
        for r, mnemonic in enumerate(ADVANCED_PARAMS):
            info = proto.PARAMETERS[mnemonic]
            var = tk.StringVar()
            self.adv_vars[mnemonic] = var
            ttk.Label(grid, text=f"{mnemonic} — {info.name}", width=32).grid(
                row=r, column=0, sticky="w", padx=3, pady=2
            )
            ttk.Entry(grid, textvariable=var, width=10).grid(row=r, column=1, padx=3, pady=2)
            read_btn = ttk.Button(
                grid, width=10, command=lambda m=mnemonic: self._on_adv_read(m)
            )
            self._bind_text(read_btn, "read")
            read_btn.grid(row=r, column=2, padx=2, pady=2)
            write_btn = ttk.Button(
                grid, width=10, command=lambda m=mnemonic: self._on_adv_write(m)
            )
            self._bind_text(write_btn, "write")
            write_btn.grid(row=r, column=3, padx=2, pady=2)
            desc = ttk.Label(grid, foreground="#666666")
            self._bind_text(desc, f"param_{mnemonic}")
            desc.grid(row=r, column=4, sticky="w", padx=6)
            self._adv_desc_labels[mnemonic] = desc

        return tab

    def _build_statusbar(self) -> None:
        self.status_bar = ttk.Frame(self, relief="sunken")
        self.status_bar.grid(row=3, column=0, sticky="ew")
        ttk.Label(self.status_bar, textvariable=self.conn_status_var, font=("Arial", 9, "bold")).pack(
            side=tk.LEFT, padx=6, pady=2
        )
        ttk.Label(self.status_bar, textvariable=self.step_var).pack(side=tk.LEFT, padx=12, pady=2)

    def _on_language_selected(self, _event=None) -> None:
        code = i18n.code_from_label(self.lang_var.get())
        if code == i18n.current_lang():
            return
        i18n.set_language(code)
        self.app_config.language = code
        config.save_config(self.app_config)
        self._apply_language()

    def _apply_language(self) -> None:
        self.title(i18n.t("app_title"))
        for widget, key, kwargs in self._i18n_widgets:
            widget.configure(text=i18n.t(key, **kwargs))
        if hasattr(self, "notebook"):
            self.notebook.tab(0, text=i18n.t("tab_basic"))
            self.notebook.tab(1, text=i18n.t("tab_advanced"))
        if self.client.is_connected:
            self.conn_status_var.set(i18n.t("conn_connected"))
        else:
            self.conn_status_var.set(i18n.t("conn_disconnected"))
        if self._last_os_state is None:
            self.mode_var.set(i18n.t("mode_prefix", label=i18n.t("mode_idle")))
        else:
            self.mode_var.set(i18n.t("mode_prefix", label=self._decode_os_mode(self._last_os_state)))
        if self._last_sw is None:
            self.sw_var.set(i18n.t("sw_dash"))
        else:
            self.sw_var.set(self._decode_sw(self._last_sw))
        if not self.is_running:
            self.step_var.set(i18n.t("step_idle"))
        self._refresh_help_window()
        if self.is_running and self.run_history:
            self._redraw_live_chart()
        else:
            self._redraw_static_profile()

    def _on_help(self) -> None:
        if self._help_window is not None and self._help_window.winfo_exists():
            self._help_window.deiconify()
            self._help_window.lift()
            self._refresh_help_window()
            return
        win = tk.Toplevel(self)
        win.geometry("640x520")
        win.minsize(480, 360)
        win.transient(self)
        text = ScrolledText(win, wrap="word", font=("Segoe UI", 10), padx=8, pady=8)
        text.pack(fill=tk.BOTH, expand=True)
        text.configure(state="disabled")
        self._help_window = win
        self._help_text = text
        win.protocol("WM_DELETE_WINDOW", self._on_help_close)
        self._refresh_help_window()

    def _on_help_close(self) -> None:
        if self._help_window is not None:
            self._help_window.destroy()
        self._help_window = None
        self._help_text = None

    def _refresh_help_window(self) -> None:
        if self._help_window is None or not self._help_window.winfo_exists() or self._help_text is None:
            return
        self._help_window.title(i18n.t("help_title"))
        self._help_text.configure(state="normal")
        self._help_text.delete("1.0", tk.END)
        self._help_text.insert("1.0", i18n.t("help_body"))
        self._help_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Підключення
    # ------------------------------------------------------------------
    def _on_refresh_ports(self) -> None:
        ports = al808_client.list_available_ports()
        self.port_combo["values"] = ports
        if self.app_config.port in ports:
            self.port_combo.set(self.app_config.port)
        elif ports:
            self.port_combo.set(ports[0])

    def _on_connect(self) -> None:
        port = self.port_combo.get()
        if not port:
            messagebox.showwarning(i18n.t("conn_title"), i18n.t("conn_select_port"))
            return
        try:
            baud = int(self.baud_combo.get())
            address = int(self.address_spin.get())
        except ValueError:
            messagebox.showerror(i18n.t("conn_title"), i18n.t("conn_bad_baud"))
            return
        try:
            self.client.connect(port, baudrate=baud, address=address)
        except Exception as exc:
            messagebox.showerror(i18n.t("conn_title"), i18n.t("conn_failed", exc=exc))
            return

        self.conn_status_var.set(i18n.t("conn_connected"))
        self.connect_btn.config(state="disabled")
        self.disconnect_btn.config(state="normal")

        self.app_config.port = port
        self.app_config.baudrate = baud
        self.app_config.address = address
        config.save_config(self.app_config)
        self._begin_xfer(i18n.t("xfer_connecting"))
        self.client.submit_job(
            profile_controller.build_read_param_job("SL"), callback=self._on_sl_read
        )
        self.client.submit_job(
            profile_controller.build_read_ptn_job(), callback=self._on_connect_ptn_read
        )

    def _on_disconnect(self) -> None:
        self.client.disconnect()
        self.conn_status_var.set(i18n.t("conn_disconnected"))
        self.connect_btn.config(state="normal")
        self.disconnect_btn.config(state="disabled")
        self.is_running = False
        self.is_held = False
        self._ptn_busy = False
        self._manual_hold = False
        self._manual_backup = None
        self._set_ptn_combo_locked(False)
        self._end_xfer("")

    def _on_close(self) -> None:
        try:
            self.client.disconnect()
        finally:
            self.destroy()

    # ------------------------------------------------------------------
    # Периодичний drain подій клієнта (викликається з головного потоку Tk)
    # ------------------------------------------------------------------
    def _poll_client_events(self) -> None:
        for ev in self.client.poll_events():
            kind = ev[0]
            if kind == "telemetry":
                self._handle_telemetry(ev[1])
            elif kind == "tx":
                self._flash_indicator(self.tx_indicator)
            elif kind == "rx":
                self._flash_indicator(self.rx_indicator)
            elif kind == "progress":
                _, current, total, text = ev
                self._on_xfer_progress(current, total, text)
            elif kind == "error":
                print(f"[IR6500] {ev[1]}")
        self.after(150, self._poll_client_events)

    def _handle_telemetry(self, snap: al808_client.TelemetrySnapshot) -> None:
        if snap.pv is not None:
            self.pv_var.set(f"{snap.pv:.1f} °C")
            self.current_pv = snap.pv
        if snap.sp is not None:
            self.sp_var.set(f"{snap.sp:.1f} °C")
        if snap.op is not None:
            self.power_var.set(max(0.0, min(100.0, snap.op)))
        if snap.sw is not None:
            self._last_sw = snap.sw
            self.sw_var.set(self._decode_sw(snap.sw))

        was_running = self.is_running
        # Реальний прилад повертає OS як статус-слово (напр. 0x0142=322 у RUN),
        # а не рівно 0x0002. Розпізнаємо RUN за встановленим бітом запуску (0x0002),
        # який присутній і в "чистому" 2, і в статус-слові 322.
        now_running = snap.os_state is not None and bool(snap.os_state & 0x0002)
        self.is_held = snap.os_state is not None and bool(snap.os_state & 0x0004)
        if snap.os_state is not None:
            self._last_os_state = snap.os_state
            self.mode_var.set(i18n.t("mode_prefix", label=self._decode_os_mode(snap.os_state)))
        self._set_ptn_combo_locked(now_running or self.is_held)

        if now_running and not was_running:
            self._start_run_session()
        elif not now_running and was_running:
            self.is_running = False
            self.step_var.set(i18n.t("step_idle"))

        self.is_running = now_running

        if self.is_running and snap.pv is not None and snap.sp is not None:
            t = time.monotonic() - (self.run_start_time or time.monotonic())
            self.run_history.append((t, snap.pv, snap.sp))
            if len(self.run_history) > 3000:
                self.run_history.pop(0)
            if self._manual_hold:
                self.step_var.set(i18n.t("manual_hold_status"))
            elif self.step_tracker is not None:
                self.step_tracker.update(snap.sp, time.monotonic())
                total_steps = len(chart_utils.compute_segment_layouts(self.profile.segments))
                step_num = self.step_tracker.current_step_number()
                phase = i18n.t("phase_ramp") if self.step_tracker.phase == "ramp" else i18n.t("phase_dwell")
                self.step_var.set(i18n.t("step_status", step=step_num, total=total_steps or 8, phase=phase))
                self._maybe_auto_stop_at_disabled_step(step_num)
            self._redraw_live_chart()
        elif snap.pv is not None and self._dragging is None:
            now = time.monotonic()
            if now - self._last_static_redraw > 0.3:
                self._last_static_redraw = now
                self._redraw_static_profile()

    def _start_run_session(self) -> None:
        self.run_history = []
        self.run_start_time = time.monotonic()
        targets = [seg.target for seg in self.profile.segments]
        self.step_tracker = StepTracker(targets)
        self.step_tracker.reset()
        self._auto_stop_sent = False

    def _maybe_auto_stop_at_disabled_step(self, step_num: int) -> None:
        """Прилад не підтримує запис сентинела "END" по COM, тож немає штатного
        способу зупинити вбудований програматор точно на потрібному кроці.
        Замість цього застосунок сам стежить за прогресом (StepTracker) і
        надсилає команду STOP, щойно виконання доходить до вимкненого
        користувачем кроку (див. "Активний крок" у Basic-вкладці)."""
        active_n = profile_controller.active_segment_count(self.profile.segments)
        total_n = len(self.profile.segments)
        if active_n >= total_n or self._auto_stop_sent or self._manual_hold:
            return  # усі кроки активні — вимикати нічого
        if step_num > active_n:
            self._auto_stop_sent = True
            self.client.submit_job(profile_controller.build_stop_job(), callback=self._on_simple_ack)

    def _flash_indicator(self, canvas: tk.Canvas) -> None:
        self._set_indicator(canvas, "#00c000")
        self.after(150, lambda: self._set_indicator(canvas, "#c0c0c0"))

    @staticmethod
    def _set_indicator(canvas: tk.Canvas, color: str) -> None:
        canvas.delete("all")
        canvas.create_oval(1, 1, 12, 12, fill=color, outline="#333333")

    @staticmethod
    def _decode_os_mode(os_state: int) -> str:
        """Декодує статус-слово OS у зрозумілу назву режиму.

        Емпірично на ACHI IR6500: IDLE=0x0000, RUN=0x0142 (біт 0x0002 = program run).
        HOLD (пауза) позначається бітом 0x0004 у статус-слові.
        """
        if os_state == 0:
            return i18n.t("mode_idle_manual")
        parts = []
        if os_state & 0x0002:
            parts.append(i18n.t("mode_run"))
        if os_state & 0x0004:
            parts.append(i18n.t("mode_hold"))
        label = " + ".join(parts) if parts else i18n.t("mode_unknown")
        return f"{label} (0x{os_state:04X})"

    def _decode_sw(self, sw: int) -> str:
        auto_manual = "MANUAL" if (sw >> 15) & 1 else "AUTO"
        alarm_out = i18n.t("yes") if (sw >> 12) & 1 else i18n.t("no")
        hi_alarm = i18n.t("yes") if (sw >> 10) & 1 else i18n.t("no")
        lo_alarm = i18n.t("yes") if (sw >> 8) & 1 else i18n.t("no")
        input_fault = i18n.t("yes") if (sw >> 1) & 1 else i18n.t("no")
        return i18n.t(
            "sw_line",
            sw=sw,
            auto_manual=auto_manual,
            alarm_out=alarm_out,
            hi_alarm=hi_alarm,
            lo_alarm=lo_alarm,
            input_fault=input_fault,
        )

    # ------------------------------------------------------------------
    # Basic tab: поля профілю / пресети / графік
    # ------------------------------------------------------------------
    def _collect_profile_from_fields(self) -> ProfileUI:
        profile = ProfileUI()
        for i, vars_ in enumerate(self.seg_vars):
            profile.segments[i] = SegmentUI(
                ramp=self._safe_ramp(vars_["ramp"].get()),
                target=self._safe_float(vars_["target"].get()),
                dwell=self._safe_float(vars_["dwell"].get()),
                enabled=vars_["enabled"].get(),
            )
        profile.holdback = self._safe_float(self.holdback_var.get())
        return profile

    def _apply_profile_to_fields(self, profile: ProfileUI) -> None:
        for i, seg in enumerate(profile.segments):
            if isinstance(seg.ramp, str):
                self.seg_vars[i]["ramp"].set(seg.ramp)
            else:
                self.seg_vars[i]["ramp"].set("" if seg.ramp is None else f"{seg.ramp:.2f}")
            self.seg_vars[i]["target"].set("" if seg.target is None else f"{seg.target:.1f}")
            self.seg_vars[i]["dwell"].set("" if seg.dwell is None else f"{seg.dwell:.0f}")
            self.seg_vars[i]["enabled"].set(seg.enabled)
        self.holdback_var.set("" if profile.holdback is None else f"{profile.holdback:.1f}")
        self._update_step_field_states()
        self._redraw_static_profile()

    def _on_fields_changed(self) -> None:
        if not self.is_running:
            self._redraw_static_profile()

    def _on_step_enabled_toggled(self, col: int) -> None:
        """Кроки виконуються послідовно, тому "дірки" неможливі: вимкнення кроку
        вимикає всі наступні, а увімкнення — вмикає всі попередні."""
        enabled = self.seg_vars[col]["enabled"].get()
        if not enabled:
            for c in range(col + 1, profile_controller.SEGMENT_COUNT):
                self.seg_vars[c]["enabled"].set(False)
        else:
            for c in range(0, col):
                self.seg_vars[c]["enabled"].set(True)
        self._update_step_field_states()
        self._on_fields_changed()

    def _update_step_field_states(self) -> None:
        for col, vars_ in enumerate(self.seg_vars):
            enabled = vars_["enabled"].get()
            state = "normal" if enabled else "disabled"
            if not enabled:
                vars_["ramp"].set("0.00")
                vars_["target"].set("0.0")
                vars_["dwell"].set("0")
            for widget in self._seg_field_widgets[col].values():
                widget.configure(state=state)

    @staticmethod
    def _safe_float(text: str) -> float | None:
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_ramp(text: str):
        """Парсить поле Ramp Rate: число (°C/с) або текстовий сентинел "END"."""
        if text is not None and text.strip().upper() == "END":
            return "END"
        return IR6500App._safe_float(text)

    def _refresh_preset_list(self) -> None:
        names = presets.list_presets()
        self.preset_combo["values"] = names
        if names and not self.preset_var.get():
            self.preset_var.set(names[0])

    def _on_load_preset(self) -> None:
        name = self.preset_var.get()
        if not name:
            return
        try:
            profile = presets.load_preset(name)
        except FileNotFoundError as exc:
            messagebox.showerror(i18n.t("preset_title"), str(exc))
            return
        self.profile = profile
        self._apply_profile_to_fields(profile)

    def _on_save_preset(self) -> None:
        name = simpledialog.askstring(
            i18n.t("preset_save_title"), i18n.t("preset_save_prompt"),
            initialvalue=self.preset_var.get(), parent=self
        )
        if not name:
            return
        profile = self._collect_profile_from_fields()
        presets.save_preset(name, profile)
        self._refresh_preset_list()
        self.preset_var.set(name)
        messagebox.showinfo(i18n.t("preset_title"), i18n.t("preset_saved", name=name))

    # ------------------------------------------------------------------
    # Графік
    # ------------------------------------------------------------------
    # Узгоджена кольорова схема для ОБОХ графіків (план і live), як у типових
    # кривих термопрофілю (reflow-profile): SV (задана/план) — синій, PV
    # (виміряна) — червоний.
    _COLOR_SV = "#1f4e9c"
    _COLOR_PV = "#d62728"
    _COLOR_STEP_HIGHLIGHT = "#ffe97a"
    _COLOR_PLAN = "#202020"

    def _redraw_static_profile(self) -> None:
        profile = self._collect_profile_from_fields()
        points, labels = chart_utils.compute_planned_profile_points(profile.segments)
        layouts = chart_utils.compute_segment_layouts(profile.segments)

        self.ax.clear()
        self.ax.set_facecolor("#e6e6e6")
        if len(points) > 1:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            # Суцільна (opaque) заливка SV — як у референс-застосунку "IR6500 Controller",
            # а не тонка лінія з ледь помітним підфарбуванням.
            self.ax.fill_between(xs, 0, ys, color=self._COLOR_SV, alpha=1.0, zorder=1, label=i18n.t("chart_sv_plan"))
            self.ax.plot(xs, ys, color=self._COLOR_SV, linewidth=1.0, zorder=3)
            for t_mid, temp, dwell in labels:
                self.ax.annotate(
                    f"{dwell:.0f}s", xy=(t_mid, temp), xytext=(t_mid, temp + 8),
                    ha="center", fontsize=8, color="#8b0000",
                )
                self.ax.annotate(
                    f"{temp:.0f}°C", xy=(t_mid, temp), xytext=(t_mid, temp - 14),
                    ha="center", fontsize=8, color="white", fontweight="bold", zorder=6,
                )

        # Інтерактивні "ручки" для редагування профілю мишкою:
        #  - червоне коло (кінець рампи) — вертикально міняє Target Temp, горизонтально Ramp Rate;
        #  - синій квадрат (кінець витримки) — горизонтально міняє Dwell Time.
        self._chart_handle_specs = []
        for layout in layouts:
            self.ax.plot(
                layout.t_ramp_end, layout.target_temp,
                marker="o", markersize=9, color="#c00000", markeredgecolor="black",
                linestyle="none", zorder=5,
            )
            self._chart_handle_specs.append(
                {"type": "target", "seg": layout.index, "x": layout.t_ramp_end, "y": layout.target_temp}
            )
            self.ax.plot(
                layout.t_dwell_end, layout.target_temp,
                marker="s", markersize=8, color="#0000c0", markeredgecolor="black",
                linestyle="none", zorder=5,
            )
            self._chart_handle_specs.append(
                {"type": "dwell_end", "seg": layout.index, "x": layout.t_dwell_end, "y": layout.target_temp}
            )

        if self.current_pv is not None:
            # Компактний маркер поточного PV при x=0 замість лінії через увесь графік
            # (значення й так дублюється в панелі Status) — щоб не виглядало як "просто
            # штрихована лінія" через весь графік планування.
            self.ax.plot(
                0, self.current_pv, marker="o", markersize=8, color=self._COLOR_PV,
                markeredgecolor="black", zorder=6, label=i18n.t("chart_pv_now", pv=self.current_pv),
            )

        # Явні межі осі Y з запасом — інакше підписи "Ns" (temp+8) можуть виїхати
        # за межі графіка при автомасштабуванні (annotate не обрізається по осях).
        all_temps = [p[1] for p in points] if len(points) > 1 else []
        if self.current_pv is not None:
            all_temps.append(self.current_pv)
        if all_temps:
            y_bottom = min(0.0, min(all_temps) - 20.0)
            y_top = max(all_temps) + 25.0
            self.ax.set_ylim(y_bottom, y_top)
        else:
            self.ax.set_ylim(0, 250)

        self.ax.set_xlabel(i18n.t("chart_xlabel"))
        self.ax.set_ylabel(i18n.t("chart_ylabel"))
        if len(points) > 1 or self.current_pv is not None:
            self.ax.legend(loc="upper left", fontsize=8, framealpha=0.85)
        self._tighten_chart_margins()
        self.canvas.draw_idle()

    def _redraw_live_chart(self) -> None:
        if not self.run_history:
            return
        ts = [p[0] for p in self.run_history]
        pvs = [p[1] for p in self.run_history]
        sps = [p[2] for p in self.run_history]

        self.ax.clear()
        self.ax.set_facecolor("#e6e6e6")

        if self.step_tracker is not None:
            layouts = chart_utils.compute_segment_layouts(self.profile.segments)
            idx = self.step_tracker.index
            if 0 <= idx < len(layouts):
                layout = layouts[idx]
                self.ax.axvspan(
                    layout.t_start, max(layout.t_dwell_end, layout.t_start + 0.01),
                    color=self._COLOR_STEP_HIGHLIGHT, alpha=0.6, zorder=0,
                )
                self.ax.annotate(
                    f"{i18n.t('chart_step', n=idx + 1)}",
                    xy=((layout.t_start + layout.t_dwell_end) / 2.0, 0.97),
                    xycoords=("data", "axes fraction"),
                    ha="center", va="top", fontsize=9, fontweight="bold", color="#8a6d00",
                )

        # Теоретична лінія профілю (той самий розрахунок, що й на графіку планування) —
        # для візуального порівняння "план vs факт" у реальному часі. Точка відліку —
        # SV на момент старту RUN (перший запис у run_history), а не умовні 25°C.
        start_temp_for_plan = sps[0] if sps else 25.0
        plan_points, _ = chart_utils.compute_planned_profile_points(
            self.profile.segments, start_temp=start_temp_for_plan
        )
        plan_duration = plan_points[-1][0] if len(plan_points) > 1 else 0.0

        # Live-режим: лінії PV/SV без заливки, плюс пунктир плану.
        self.ax.plot(ts, sps, color=self._COLOR_SV, linewidth=2.0, zorder=2, label=i18n.t("chart_sv_actual"))
        self.ax.plot(ts, pvs, color=self._COLOR_PV, linewidth=2.0, zorder=3, label=i18n.t("chart_pv_measured"))

        if len(plan_points) > 1:
            plan_xs = [p[0] for p in plan_points]
            plan_ys = [p[1] for p in plan_points]
            self.ax.plot(
                plan_xs, plan_ys, color=self._COLOR_PLAN, linewidth=1.6, linestyle="--",
                label=i18n.t("chart_sv_plan_line"), zorder=4,
            )

        self.ax.plot(
            ts[-1], pvs[-1], marker="o", markersize=7, color="white",
            markeredgecolor=self._COLOR_PV, markeredgewidth=2, zorder=5,
        )
        self.ax.annotate(
            f"PV: {pvs[-1]:.1f}°C", xy=(ts[-1], pvs[-1]),
            xytext=(-6, 10), textcoords="offset points",
            ha="right", fontsize=9, color=self._COLOR_PV, fontweight="bold",
        )
        self.ax.plot(
            ts[-1], sps[-1], marker="o", markersize=6, color="white",
            markeredgecolor=self._COLOR_SV, markeredgewidth=2, zorder=5,
        )
        self.ax.annotate(
            f"SV: {sps[-1]:.1f}°C", xy=(ts[-1], sps[-1]),
            xytext=(-6, -14), textcoords="offset points",
            ha="right", fontsize=9, color=self._COLOR_SV, fontweight="bold",
        )

        self.ax.set_xlabel(i18n.t("chart_xlabel"))
        self.ax.set_ylabel(i18n.t("chart_ylabel"))
        if sps or pvs:
            self.ax.set_ylim(0, max(max(sps, default=0), max(pvs, default=0)) * 1.15 + 10)
        self.ax.set_xlim(0, max(ts[-1], plan_duration) * 1.03 + 1)
        self.ax.legend(loc="upper left", fontsize=8, framealpha=0.85)
        self._tighten_chart_margins()
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    # Інтерактивне редагування профілю мишкою на графіку
    # ------------------------------------------------------------------
    _HANDLE_PICK_RADIUS_PX = 10

    def _find_handle_at_event(self, event) -> dict | None:
        if event.x is None or event.y is None or not self._chart_handle_specs:
            return None
        best = None
        best_dist = self._HANDLE_PICK_RADIUS_PX
        for spec in self._chart_handle_specs:
            px, py = self.ax.transData.transform((spec["x"], spec["y"]))
            dist = ((px - event.x) ** 2 + (py - event.y) ** 2) ** 0.5
            if dist <= best_dist:
                best_dist = dist
                best = spec
        return best

    def _on_chart_press(self, event) -> None:
        if self.is_running or event.button != 1:
            return
        spec = self._find_handle_at_event(event)
        if spec is not None:
            self._dragging = dict(spec)

    def _on_chart_motion(self, event) -> None:
        if self._dragging is None or event.xdata is None or event.ydata is None:
            return
        self._apply_handle_drag(self._dragging, event.xdata, event.ydata)
        self._redraw_static_profile()

    def _on_chart_release(self, event) -> None:
        if self._dragging is not None:
            self._dragging = None
            self._redraw_static_profile()

    def _apply_handle_drag(self, spec: dict, new_t: float, new_temp: float) -> None:
        seg_idx = spec["seg"]
        seg_vars = self.seg_vars[seg_idx]
        layouts = chart_utils.compute_segment_layouts(self._collect_profile_from_fields().segments)
        layout = next((l for l in layouts if l.index == seg_idx), None)
        if layout is None:
            return

        if spec["type"] == "target":
            new_temp = max(0.0, min(230.0, new_temp))
            ramp_duration = max(new_t - layout.t_start, 0.1)
            delta = abs(new_temp - layout.temp_start)
            rate = delta / ramp_duration if delta > 1e-6 else 0.01
            rate = max(0.01, min(50.0, rate))
            seg_vars["target"].set(f"{new_temp:.1f}")
            seg_vars["ramp"].set(f"{rate:.2f}")
        elif spec["type"] == "dwell_end":
            dwell = max(new_t - layout.t_ramp_end, 0.0)
            seg_vars["dwell"].set(f"{dwell:.0f}")

    # ------------------------------------------------------------------
    # Download / Upload / Start / Stop / Hold
    # ------------------------------------------------------------------
    def _require_connected(self) -> bool:
        if not self.client.is_connected:
            messagebox.showwarning(i18n.t("not_connected_title"), i18n.t("not_connected"))
            return False
        return True

    def _set_xfer_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        if hasattr(self, "download_btn"):
            self.download_btn.configure(state=state)
            self.upload_btn.configure(state=state)

    def _begin_xfer(self, title: str) -> None:
        if self._xfer_after_id is not None:
            self.after_cancel(self._xfer_after_id)
            self._xfer_after_id = None
        self.xfer_status_var.set(title)
        self.xfer_progress.configure(mode="determinate", maximum=100)
        self.xfer_progress["value"] = 0
        self._set_xfer_buttons(False)

    def _on_xfer_progress(self, current: int, total: int, text: str) -> None:
        total = max(int(total), 1)
        self.xfer_progress.configure(maximum=total)
        self.xfer_progress["value"] = min(int(current), total)
        if text:
            self.xfer_status_var.set(text)

    def _end_xfer(self, status: str, linger_ms: int = 2500) -> None:
        self._set_xfer_buttons(True)
        if status:
            self.xfer_progress["value"] = self.xfer_progress["maximum"] or 100
            self.xfer_status_var.set(status)
        else:
            self.xfer_progress["value"] = 0
            self.xfer_status_var.set("")
            return

        def _clear() -> None:
            self._xfer_after_id = None
            if self.xfer_status_var.get() == status:
                self.xfer_progress["value"] = 0

        if self._xfer_after_id is not None:
            self.after_cancel(self._xfer_after_id)
        self._xfer_after_id = self.after(linger_ms, _clear)

    def _selected_ptn(self) -> int:
        try:
            return profile_controller.parse_ptn(self.ptn_var.get())
        except ValueError:
            return 1

    def _set_ptn_combo_locked(self, locked: bool) -> None:
        if not hasattr(self, "ptn_combo"):
            return
        if self._ptn_busy:
            self.ptn_combo.configure(state="disabled")
            return
        self.ptn_combo.configure(state="disabled" if locked else "readonly")

    def _revert_ptn_combo(self) -> None:
        if self._device_ptn is not None:
            self.ptn_var.set(str(self._device_ptn))

    def _on_connect_ptn_read(self, ok: bool, result) -> None:
        if ok:
            ptn = int(result)
            self._device_ptn = ptn
            self.ptn_var.set(str(ptn))
        self.client.submit_job(
            profile_controller.build_download_job(), callback=self._on_download_done
        )

    def _on_ptn_selected(self, _event=None) -> None:
        if self._ptn_busy:
            return
        if not self.client.is_connected:
            return
        if self.is_running or self.is_held:
            messagebox.showwarning("PTN", i18n.t("ptn_locked"))
            self._revert_ptn_combo()
            return
        ptn = self._selected_ptn()
        self._ptn_busy = True
        self._set_ptn_combo_locked(True)
        self._begin_xfer("PTN")
        self.client.submit_job(
            profile_controller.build_select_ptn_and_download_job(ptn),
            callback=self._on_ptn_switch_done,
        )

    def _on_ptn_switch_done(self, ok: bool, result) -> None:
        self._ptn_busy = False
        self._set_ptn_combo_locked(self.is_running or self.is_held)
        if not ok:
            self._end_xfer(i18n.t("xfer_ptn_error"))
            messagebox.showerror("PTN", i18n.t("ptn_switch_failed", result=result))
            self._revert_ptn_combo()
            return
        actual, profile = result
        self._device_ptn = int(actual)
        self.ptn_var.set(str(self._device_ptn))
        self.profile = profile
        self._apply_profile_to_fields(profile)
        self._end_xfer(f"PTN {self._device_ptn}")

    def _on_download(self) -> None:
        if not self._require_connected():
            return
        ptn = self._selected_ptn()
        self._begin_xfer("Download")
        self.client.submit_job(
            profile_controller.build_download_job(ptn), callback=self._on_download_done
        )

    def _on_download_done(self, ok: bool, result) -> None:
        if not ok:
            self._end_xfer(i18n.t("xfer_error"))
            messagebox.showerror("Download", i18n.t("download_failed", result=result))
            return
        profile: ProfileUI = result
        unsupported = [i + 1 for i, seg in enumerate(profile.segments) if not seg.supported]
        self.profile = profile
        self._apply_profile_to_fields(profile)
        self._device_ptn = self._selected_ptn()
        if unsupported:
            self._end_xfer(i18n.t("xfer_missing_steps", steps=unsupported))
        else:
            self._end_xfer(i18n.t("xfer_loaded"))

    def _on_upload(self) -> None:
        if not self._require_connected():
            return
        profile = self._collect_profile_from_fields()
        self.profile = profile  # для live-трекера кроків/графіка (враховує "Активний крок")
        self._begin_xfer("Upload")
        self.client.submit_job(
            profile_controller.build_upload_job(profile, ptn=self._selected_ptn()),
            callback=self._on_upload_done,
        )

    def _on_upload_done(self, ok: bool, result) -> None:
        if not ok:
            self._end_xfer(i18n.t("xfer_error"))
            messagebox.showerror("Upload", i18n.t("upload_failed", result=result))
            return
        errors: list[str] = result
        if errors:
            self._end_xfer(i18n.t("xfer_partial"))
            messagebox.showwarning("Upload", i18n.t("upload_partial", errors="\n".join(errors)))
        else:
            self._device_ptn = self._selected_ptn()
            self._end_xfer(i18n.t("xfer_saved_ptn", ptn=self._device_ptn))

    def _on_start(self) -> None:
        if not self._require_connected():
            return
        restore = None
        if self._manual_hold:
            restore = self._manual_backup
            self._manual_hold = False
            self._manual_backup = None
        ptn = None if (self.is_held or self.is_running) and restore is None else self._selected_ptn()
        self.client.submit_job(
            profile_controller.build_start_job(ptn=ptn, restore_step1=restore),
            callback=self._on_simple_ack,
        )

    def _on_stop(self) -> None:
        if not self._require_connected():
            return
        restore = self._manual_backup
        self._manual_hold = False
        self._manual_backup = None
        self.client.submit_job(
            profile_controller.build_stop_job(restore_step1=restore),
            callback=self._on_simple_ack,
        )

    def _on_hold(self) -> None:
        if not self._require_connected():
            return
        self.client.submit_job(profile_controller.build_hold_job(), callback=self._on_simple_ack)

    def _on_manual_apply(self) -> None:
        if not self._require_connected():
            return
        temp = self._safe_float(self.manual_temp_var.get())
        if temp is None:
            messagebox.showerror(i18n.t("manual_title"), i18n.t("manual_bad_temp"))
            return
        self.client.submit_job(
            profile_controller.build_manual_setpoint_job(temp, keep_backup=self._manual_backup),
            callback=self._on_manual_apply_done,
        )

    def _on_manual_apply_done(self, ok: bool, result) -> None:
        if not ok:
            messagebox.showerror(i18n.t("command_title"), i18n.t("command_failed", result=result))
            return
        sl = result
        backup = None
        if isinstance(result, dict):
            sl = result.get("sl")
            backup = result.get("backup")
        if backup is not None and self._manual_backup is None:
            self._manual_backup = backup
        self._manual_hold = True
        try:
            self.manual_temp_var.set(f"{float(sl):.1f}")
        except (TypeError, ValueError):
            pass

    def _on_sl_read(self, ok: bool, result) -> None:
        if not ok:
            return
        try:
            self.manual_temp_var.set(f"{float(result):.1f}")
        except (TypeError, ValueError):
            pass

    def _on_simple_ack(self, ok: bool, result) -> None:
        if not ok:
            messagebox.showerror(i18n.t("command_title"), i18n.t("command_failed", result=result))

    # ------------------------------------------------------------------
    # Advanced tab: read/write окремих параметрів + probe
    # ------------------------------------------------------------------
    def _on_adv_read(self, mnemonic: str) -> None:
        if not self._require_connected():
            return
        self.client.submit_job(
            profile_controller.build_read_param_job(mnemonic),
            callback=lambda ok, val, m=mnemonic: self._on_adv_read_done(m, ok, val),
        )

    def _on_adv_read_done(self, mnemonic: str, ok: bool, result) -> None:
        if ok:
            self.adv_vars[mnemonic].set(f"{result}")
        else:
            self.adv_vars[mnemonic].set("ERR")

    def _on_adv_write(self, mnemonic: str) -> None:
        if not self._require_connected():
            return
        value = self._safe_float(self.adv_vars[mnemonic].get())
        if value is None:
            messagebox.showerror("Advanced", i18n.t("advanced_bad_value", mnemonic=mnemonic))
            return
        self.client.submit_job(
            profile_controller.build_write_param_job(mnemonic, value), callback=self._on_simple_ack
        )

    def _on_adv_read_all(self) -> None:
        for mnemonic in ADVANCED_PARAMS:
            self._on_adv_read(mnemonic)

    def _on_adv_write_all(self) -> None:
        for mnemonic in ADVANCED_PARAMS:
            if self._safe_float(self.adv_vars[mnemonic].get()) is not None:
                self._on_adv_write(mnemonic)

    def _on_set_self_tune(self, value: int) -> None:
        if not self._require_connected():
            return
        self.client.submit_job(
            profile_controller.build_write_param_job("XS", value, hex_word=True),
            callback=self._on_simple_ack,
        )

    def _on_probe_segments(self) -> None:
        if not self._require_connected():
            return
        self.client.submit_job(
            profile_controller.build_probe_segments_job(), callback=self._on_probe_done
        )

    def _on_probe_done(self, ok: bool, result) -> None:
        if not ok:
            messagebox.showerror("Probe", i18n.t("probe_failed", result=result))
            return
        support: dict[int, bool] = result
        unsupported = [n for n, supported in support.items() if not supported]
        if unsupported:
            messagebox.showwarning(
                i18n.t("probe_title"),
                i18n.t("probe_unsupported", unsupported=unsupported),
            )
        else:
            messagebox.showinfo(i18n.t("probe_title"), i18n.t("probe_ok"))


def run() -> None:
    app = IR6500App()
    app.mainloop()
