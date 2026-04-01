"""
INA3221 3-channel power monitor module — Universal Device Studio plugin

Provides I2C auto-scan, real-time voltage/current/power monitoring,
per-channel configuration, and register map view.
"""

from __future__ import annotations

import time
from collections import deque
import math
from typing import Optional, List

from PySide6.QtCore import Qt, Slot, QThread, QSettings
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QComboBox,
    QSpinBox, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QTextEdit, QCheckBox, QLineEdit,
)

from core.ftdi_manager import FtdiManager, FtdiTaskResult
from core.theme_manager import ThemeManager
from core.data_recorder import DataRecorder
from modules.base_module import BaseModule
from modules.ina3221.ina3221_registers import (
    INA3221Reg, REGISTER_NAMES, REGISTER_DESCRIPTIONS, DISPLAY_REGISTERS,
    AVG_OPTIONS, CT_OPTIONS, OP_MODE_OPTIONS,
)
from modules.ina3221.ina3221_worker import INA3221Worker, INA3221Measurement
from modules.ina3221.power_visualizer_3ch import PowerVisualizer3CH

# Per-channel colors (matching visualizer)
CH_COLORS = ["#00d2ff", "#ff64b4", "#ffd000"]  # Cyan, Pink, Yellow


class INA3221Module(BaseModule):
    MODULE_NAME = "INA3221 Monitor"
    MODULE_ICON = "\U0001f4c8"
    MODULE_VERSION = "1.1.0"
    MODULE_ORDER = 21
    REQUIRED_MODE = "I2C"
    REQUIRE_MPSSE = True

    MAX_DATA_POINTS = 2000
    INA3221_SCAN_START = 0x40
    INA3221_SCAN_END = 0x43

    def __init__(self, ftdi_manager: FtdiManager, parent: Optional[QWidget] = None) -> None:
        self._worker: Optional[INA3221Worker] = None
        self._worker_thread: Optional[QThread] = None
        self._slave_addr: int = 0x40
        self._is_monitoring: bool = False
        self._start_pending: bool = False
        self._window_seconds: int = 60
        self._io_hold_mask: int = 0xF0
        self._io_hold_value: int = 0x00
        self._saved_hold: Optional[tuple] = None
        self._settings = QSettings("UniversalDeviceStudio", "INA3221Module")

        self._time_data: deque = deque(maxlen=self.MAX_DATA_POINTS)
        self._vbus_data: List[deque] = [deque(maxlen=self.MAX_DATA_POINTS) for _ in range(3)]
        self._current_data: List[deque] = [deque(maxlen=self.MAX_DATA_POINTS) for _ in range(3)]

        # Per-channel UI references
        self._ch_enable_cbs: list = []
        self._ch_shunt_edits: list = []
        self._ch_value_labels: list = []  # (v_label, i_label, p_label) per channel

        self._start_time: float = 0.0
        self._recorder = DataRecorder()
        super().__init__(ftdi_manager, parent)

    def init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Top: address scan + GPIO hold
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        top_row.addWidget(self._create_address_panel(), 2)
        top_row.addWidget(self._create_io_hold_panel(), 1)
        layout.addLayout(top_row)

        self._load_io_hold_state()

        # Center + bottom splitter
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        v_splitter.setHandleWidth(3)

        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.setHandleWidth(3)
        h_splitter.addWidget(self._create_control_panel())
        h_splitter.addWidget(self._create_visualizer_panel())
        h_splitter.setStretchFactor(0, 1)
        h_splitter.setStretchFactor(1, 4)

        v_splitter.addWidget(h_splitter)
        v_splitter.addWidget(self._create_bottom_panel())
        v_splitter.setStretchFactor(0, 2)
        v_splitter.setStretchFactor(1, 3)

        layout.addWidget(v_splitter, 1)

        self._apply_theme()
        ThemeManager.instance().theme_changed.connect(self._apply_theme)

    # ── Theme ────────────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        tm = ThemeManager.instance()
        # Scan button
        self._scan_btn.setStyleSheet(
            f"QPushButton {{ background: {tm.color('btn_auto_checked_bg')};"
            f" color: {tm.color('btn_auto_checked_text')}; font-weight: 700; border-radius: 6px;"
            f" border: 1px solid {tm.color('btn_auto_checked_border')}; }}"
            f"QPushButton:hover {{ background: {tm.color('btn_auto_checked_bg')}; }}"
            f"QPushButton:disabled {{ background: {tm.color('bg_disabled')};"
            f" color: {tm.color('text_disabled')}; border: 1px solid {tm.color('border_subtle')}; }}"
        )
        self._scan_result_label.setStyleSheet(f"color: {tm.color('text_label')}; font-style: italic;")
        # Hold buttons
        for btn in self._hold_btns.values():
            btn.setStyleSheet(
                f"QPushButton {{ background: {tm.color('btn_hold_bg')};"
                f" color: {tm.color('btn_hold_text')}; font-weight: 700; border-radius: 6px;"
                f" border: 1px solid {tm.color('btn_hold_border')}; }}"
                f"QPushButton:hover {{ background: {tm.color('btn_hold_hover')}; }}"
                f"QPushButton:checked {{ background: {tm.color('btn_hold_checked_bg')};"
                f" color: {tm.color('btn_hold_checked_text')};"
                f" border: 1px solid {tm.color('btn_hold_checked_border')}; }}"
                f"QPushButton:checked:hover {{ background: {tm.color('btn_hold_checked_hover')}; }}"
            )
        # Hold tags & LEDs
        for w in self.findChildren(QLabel, "holdTag"):
            w.setStyleSheet(f"color: {tm.color('text_tag')};")
        for led in self._hold_leds.values():
            led.setStyleSheet(f"background: {tm.color('led_off')}; border-radius: 6px;")
        for w in self.findChildren(QFrame, "holdBarBg"):
            w.setStyleSheet(f"background: {tm.color('bg_bar')}; border-radius: 4px;")
        for bar in self._hold_bars.values():
            bar.setStyleSheet(f"background: {tm.color('bg_bar_fill')}; border-radius: 3px;")
        # Rec button (only when not recording)
        if not self._recorder.is_recording:
            for w in self.findChildren(QPushButton, "recBtn"):
                w.setStyleSheet(
                    f"QPushButton {{ font-weight: bold; font-size: 12px; padding: 6px 10px;"
                    f" border-radius: 6px; background: {tm.color('btn_auto_bg')};"
                    f" color: #cc3333; }}"
                    f"QPushButton:disabled {{ background: {tm.color('bg_disabled')};"
                    f" color: {tm.color('text_disabled')}; }}"
                )
        # Auto range
        for w in self.findChildren(QPushButton, "autoRangeBtn"):
            w.setStyleSheet(
                f"QPushButton {{ font-weight: bold; font-size: 11px; padding: 4px;"
                f" border-radius: 6px; background: {tm.color('btn_auto_bg')};"
                f" color: {tm.color('text_accent')}; }}"
                f"QPushButton:checked {{ background: #1f5eff; color: #ffffff; }}"
            )
        # Separators
        for w in self.findChildren(QFrame, "themeSep"):
            w.setStyleSheet(f"color: {tm.color('separator')};")
        # Section titles
        for w in self.findChildren(QLabel, "sectionTitle"):
            w.setStyleSheet(f"color: {tm.color('text_accent')}; font-weight: bold; font-size: 11px;")
        # Channel frames
        for w in self.findChildren(QFrame, "chFrame"):
            w.setStyleSheet(
                f"QFrame {{ background: {tm.color('ads_ch_frame_bg')};"
                f" border: 1px solid {tm.color('ads_ch_frame_border')}; border-radius: 4px; }}"
            )
        # Param labels
        for w in self.findChildren(QLabel, "paramLbl"):
            w.setStyleSheet(f"color: {tm.color('text_tag')}; font-size: 10px;")
        # Param edits
        for w in self.findChildren(QLineEdit, "paramEdit"):
            w.setStyleSheet(
                f"QLineEdit {{ background: {tm.color('ads_vi_btn_bg')}; color: {tm.color('text_primary')};"
                f" border: 1px solid {tm.color('ads_vi_btn_border')}; border-radius: 3px;"
                f" padding: 1px 4px; font-size: 10px; max-width: 60px; }}"
            )
        # Metric containers
        for w in self.findChildren(QWidget, "metricContainer"):
            w.setStyleSheet(f"background-color: {tm.color('metric_bg')}; border-radius: 6px;")
        # Console views
        for w in self.findChildren(QTextEdit, "themedConsole"):
            w.setStyleSheet(
                f"QTextEdit {{ background: {tm.color('ads_config_bg')}; color: {tm.color('ads_config_text')};"
                f" border: 1px solid {tm.color('ads_config_border')}; border-radius: 4px;"
                f" font-family: 'Consolas', monospace; font-size: 11px; }}"
            )

    # ── UI Builders ──────────────────────────────────────────────────

    def _create_address_panel(self) -> QGroupBox:
        group = QGroupBox("I2C Address Scan (0x40 ~ 0x43)")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(10, 6, 10, 6)

        self._scan_btn = QPushButton("Scan Addresses")
        self._scan_btn.setFixedWidth(140)
        self._scan_btn.setEnabled(False)
        self._scan_btn.clicked.connect(self._on_scan_addresses)
        layout.addWidget(self._scan_btn)

        layout.addSpacing(10)
        layout.addWidget(QLabel("Detected devices:"))
        self._addr_combo = QComboBox()
        self._addr_combo.setMinimumWidth(220)
        self._addr_combo.setPlaceholderText("Run scan")
        self._addr_combo.currentIndexChanged.connect(self._on_addr_changed)
        layout.addWidget(self._addr_combo)

        layout.addSpacing(20)
        self._scan_result_label = QLabel("-")
        layout.addWidget(self._scan_result_label)
        layout.addStretch()
        return group

    def _create_io_hold_panel(self) -> QGroupBox:
        group = QGroupBox("GPIO Hold (D4-D7)")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        self._hold_btns: dict = {}
        self._hold_leds: dict = {}
        self._hold_bars: dict = {}

        row = QHBoxLayout()
        row.setSpacing(6)
        for bit in range(4, 8):
            btn = QPushButton(f"D{bit}: OFF")
            btn.setCheckable(True)
            btn.setMinimumWidth(80)
            btn.setMinimumHeight(28)
            btn.toggled.connect(lambda checked, b=bit: self._on_hold_toggled(b, checked))
            self._hold_btns[bit] = btn
            row.addWidget(btn)
        layout.addLayout(row)

        led_row = QHBoxLayout()
        led_row.setSpacing(8)
        led_row.addWidget(QLabel("Current:"))
        for bit in range(4, 8):
            tag = QLabel(f"D{bit}")
            tag.setObjectName("holdTag")
            led = QLabel("")
            led.setFixedSize(12, 12)
            self._hold_leds[bit] = led
            led_row.addWidget(tag)
            led_row.addWidget(led)
            bar_bg = QFrame()
            bar_bg.setFixedSize(56, 8)
            bar_bg.setObjectName("holdBarBg")
            bar_fill = QFrame(bar_bg)
            bar_fill.setGeometry(1, 1, 6, 6)
            self._hold_bars[bit] = bar_fill
            led_row.addWidget(bar_bg)
        layout.addLayout(led_row)
        return group

    def _on_hold_toggled(self, bit: int, checked: bool) -> None:
        if checked:
            self._io_hold_value |= (1 << bit)
            self._hold_btns[bit].setText(f"D{bit}: ON")
        else:
            self._io_hold_value &= ~(1 << bit)
            self._hold_btns[bit].setText(f"D{bit}: OFF")
        self._save_io_hold_state()
        self._apply_io_hold()

    def _set_hold_controls_enabled(self, enabled: bool) -> None:
        for btn in self._hold_btns.values():
            btn.setEnabled(enabled)
        if not enabled:
            tm = ThemeManager.instance()
            for led in self._hold_leds.values():
                led.setStyleSheet(f"background: {tm.color('led_off')}; border-radius: 6px;")
            for bar in self._hold_bars.values():
                bar.setGeometry(1, 1, 10, 6)
                bar.setStyleSheet(f"background: {tm.color('bg_bar_fill')}; border-radius: 3px;")

    def _apply_io_hold(self) -> None:
        if not self._ftdi.is_connected:
            return
        if not self._ftdi.supports_mpsse(self._ftdi.channel):
            return
        if self._saved_hold is None:
            self._saved_hold = self._ftdi.get_i2c_hold()
        self._ftdi.set_i2c_hold(self._io_hold_mask, self._io_hold_value)
        self._refresh_hold_status()

    def _apply_io_hold_hw(self) -> bool:
        if not self._ftdi.is_connected:
            return False
        if not self._ftdi.supports_mpsse(self._ftdi.channel):
            return False
        self._ftdi.set_i2c_hold(self._io_hold_mask, self._io_hold_value)
        return True

    def _run_async_i2c_task(
        self,
        *,
        force: bool,
        settle_ms: int,
        task,
        on_done,
        sync_hold_ui: bool = False,
    ) -> bool:
        if not self._ftdi.is_connected or not self._ftdi.supports_mpsse(self._ftdi.channel):
            on_done(FtdiTaskResult(False, error="FTDI not ready.", stage="pre-check"))
            return False

        def _handle_done(result: FtdiTaskResult) -> None:
            try:
                if self._ftdi.is_connected:
                    self._refresh_hold_status(sync_buttons=sync_hold_ui)
            except Exception:
                pass
            on_done(result)

        self._ftdi.run_async_protocol_task(
            "I2C",
            force=force,
            settle_ms=settle_ms,
            prepare=self._apply_io_hold_hw,
            task=task,
            on_done=_handle_done,
        )
        return True

    def _force_restore_i2c_in_task(self, settle_ms: int = 60) -> bool:
        if not self._ftdi.set_protocol_mode("I2C", force=True):
            return False
        self._apply_io_hold_hw()
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0)
        self._ftdi.purge_pending_io()
        return True

    def _restore_i2c_context(
        self,
        force: bool = False,
        settle_ms: int = 40,
        sync_hold_ui: bool = False,
    ) -> bool:
        if not self._ftdi.is_connected:
            return False
        if not self._ftdi.supports_mpsse(self._ftdi.channel):
            return False
        if not self._ftdi.set_protocol_mode("I2C", force=force):
            self._append_log("[ERROR] Failed to restore INA3221 I2C mode.")
            return False
        self._apply_io_hold()
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0)
        self._refresh_hold_status(sync_buttons=sync_hold_ui)
        return True

    def _refresh_hold_status(self, sync_buttons: bool = False) -> None:
        if not hasattr(self, "_hold_leds"):
            return
        value = self._ftdi.read_gpio_low()
        if value is None:
            return
        for bit in range(4, 8):
            high = bool(value & (1 << bit))

            tm = ThemeManager.instance()
            led = self._hold_leds.get(bit)
            if led:
                color = tm.color('led_on') if high else tm.color('led_off')
                led.setStyleSheet(f"background: {color}; border-radius: 6px;")

            bar = self._hold_bars.get(bit)
            if bar:
                if high:
                    bar.setGeometry(1, 1, 54, 6)
                    bar.setStyleSheet(f"background: {tm.color('led_on')}; border-radius: 3px;")
                else:
                    bar.setGeometry(1, 1, 10, 6)
                    bar.setStyleSheet(f"background: {tm.color('bg_bar_fill')}; border-radius: 3px;")

            if sync_buttons:
                btn = self._hold_btns.get(bit)
                if btn:
                    btn.blockSignals(True)
                    btn.setChecked(high)
                    btn.setText(f"D{bit}: {'ON' if high else 'OFF'}")
                    btn.blockSignals(False)
                if high:
                    self._io_hold_value |= (1 << bit)
                else:
                    self._io_hold_value &= ~(1 << bit)

    def _save_io_hold_state(self) -> None:
        self._settings.setValue("io_hold_value", int(self._io_hold_value))

    def _load_io_hold_state(self) -> None:
        val = self._settings.value("io_hold_value", 0, type=int)
        self._io_hold_value = val & self._io_hold_mask
        if hasattr(self, "_hold_btns"):
            for bit, btn in self._hold_btns.items():
                checked = bool(self._io_hold_value & (1 << bit))
                btn.blockSignals(True)
                btn.setChecked(checked)
                btn.setText(f"D{bit}: {'ON' if checked else 'OFF'}")
                btn.blockSignals(False)

    def _create_control_panel(self) -> QGroupBox:
        group = QGroupBox("ADC Settings")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Device settings grid
        grid = QGridLayout()
        grid.setSpacing(6)

        grid.addWidget(QLabel("Op. Mode:"), 0, 0)
        self._op_mode_combo = QComboBox()
        for k, v in OP_MODE_OPTIONS.items():
            self._op_mode_combo.addItem(v, k)
        self._op_mode_combo.setCurrentIndex(7)
        grid.addWidget(self._op_mode_combo, 0, 1)

        grid.addWidget(QLabel("AVG samples:"), 1, 0)
        self._avg_combo = QComboBox()
        for k, v in AVG_OPTIONS.items():
            self._avg_combo.addItem(v, k)
        self._avg_combo.setCurrentIndex(2)
        grid.addWidget(self._avg_combo, 1, 1)

        grid.addWidget(QLabel("VBUS CT:"), 2, 0)
        self._vbusct_combo = QComboBox()
        for k, v in CT_OPTIONS.items():
            self._vbusct_combo.addItem(v, k)
        self._vbusct_combo.setCurrentIndex(4)
        grid.addWidget(self._vbusct_combo, 2, 1)

        grid.addWidget(QLabel("VSH CT:"), 3, 0)
        self._vshct_combo = QComboBox()
        for k, v in CT_OPTIONS.items():
            self._vshct_combo.addItem(v, k)
        self._vshct_combo.setCurrentIndex(4)
        grid.addWidget(self._vshct_combo, 3, 1)

        grid.addWidget(QLabel("Polling (ms):"), 4, 0)
        self._interval_spinbox = QSpinBox()
        self._interval_spinbox.setRange(50, 10000)
        self._interval_spinbox.setValue(100)
        grid.addWidget(self._interval_spinbox, 4, 1)

        grid.addWidget(QLabel("Window (s):"), 5, 0)
        self._window_spinbox = QSpinBox()
        self._window_spinbox.setRange(10, 600)
        self._window_spinbox.setValue(60)
        self._window_spinbox.setSingleStep(10)
        grid.addWidget(self._window_spinbox, 5, 1)

        grid.addWidget(QLabel("Auto Range:"), 6, 0)
        self._auto_range_btn = QPushButton("AUTO RANGE: ON")
        self._auto_range_btn.setCheckable(True)
        self._auto_range_btn.setChecked(True)
        self._auto_range_btn.setMinimumHeight(32)
        self._auto_range_btn.setObjectName("autoRangeBtn")
        self._auto_range_btn.toggled.connect(self._on_auto_range_toggled)
        grid.addWidget(self._auto_range_btn, 6, 1)

        layout.addLayout(grid)

        # Separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setObjectName("themeSep")
        layout.addWidget(sep1)

        ch_title = QLabel("Channel Config:")
        ch_title.setObjectName("sectionTitle")
        layout.addWidget(ch_title)

        # Per-channel config (ADS1018-style frames)
        ch_vbox = QVBoxLayout()
        ch_vbox.setSpacing(4)
        for i in range(3):
            ch_frame = QFrame()
            ch_frame.setObjectName("chFrame")
            ch_layout = QHBoxLayout(ch_frame)
            ch_layout.setContentsMargins(6, 4, 6, 4)
            ch_layout.setSpacing(6)

            # Colored channel label
            ch_lbl = QLabel(f"CH{i+1}")
            ch_lbl.setStyleSheet(f"color: {CH_COLORS[i]}; font-weight: bold; font-size: 11px; border: none;")
            ch_lbl.setFixedWidth(32)
            ch_layout.addWidget(ch_lbl)

            # Enable checkbox
            cb = QCheckBox()
            cb.setChecked(True)
            cb.setToolTip(f"Enable channel {i+1}")
            self._ch_enable_cbs.append(cb)
            ch_layout.addWidget(cb)

            # Shunt resistor
            r_lbl = QLabel("R(\u03a9):")
            r_lbl.setObjectName("paramLbl")
            ch_layout.addWidget(r_lbl)
            shunt_edit = QLineEdit("0.01")
            shunt_edit.setObjectName("paramEdit")
            shunt_edit.setFixedWidth(60)
            self._ch_shunt_edits.append(shunt_edit)
            ch_layout.addWidget(shunt_edit)

            ch_layout.addStretch()
            ch_vbox.addWidget(ch_frame)

        layout.addLayout(ch_vbox)

        # Separator
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setObjectName("themeSep")
        layout.addWidget(sep2)

        # Start / Stop
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Monitoring")
        self._start_btn.setMinimumHeight(28)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start_btn_clicked)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop Monitoring")
        self._stop_btn.setMinimumHeight(28)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self.stop_communication)
        btn_row.addWidget(self._stop_btn)
        layout.addLayout(btn_row)

        # Record button
        self._rec_btn = QPushButton("\u2b24 REC")
        self._rec_btn.setObjectName("recBtn")
        self._rec_btn.setMinimumHeight(32)
        self._rec_btn.setEnabled(False)
        self._rec_btn.clicked.connect(self._on_rec_clicked)
        layout.addWidget(self._rec_btn)

        self._refresh_reg_btn = QPushButton("Refresh Register Map")
        self._refresh_reg_btn.clicked.connect(self._refresh_register_map)
        layout.addWidget(self._refresh_reg_btn)

        layout.addStretch()
        return group

    def _create_visualizer_panel(self) -> QGroupBox:
        group = QGroupBox("Real-Time Monitoring")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 6, 6, 6)

        # Metrics row: 3 channels, each shows V / I / P
        metrics_layout = QHBoxLayout()
        metrics_layout.setSpacing(10)

        for i in range(3):
            container = QWidget()
            container.setObjectName("metricContainer")
            vl = QVBoxLayout(container)
            vl.setContentsMargins(8, 4, 8, 4)
            vl.setSpacing(2)

            title = QLabel(f"CH{i+1}")
            title.setStyleSheet(
                f"color: {CH_COLORS[i]}; font-size: 11px; font-weight: bold;"
            )
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.addWidget(title)

            v_lbl = QLabel("- V")
            v_lbl.setStyleSheet(
                f"color: {CH_COLORS[i]}; font-size: 13px; font-weight: bold;"
                f" font-family: 'Consolas', monospace;"
            )
            v_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.addWidget(v_lbl)

            i_lbl = QLabel("- mA")
            i_lbl.setStyleSheet(
                f"color: {CH_COLORS[i]}; font-size: 13px; font-weight: bold;"
                f" font-family: 'Consolas', monospace;"
            )
            i_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.addWidget(i_lbl)

            p_lbl = QLabel("- mW")
            p_lbl.setStyleSheet(
                f"color: {CH_COLORS[i]}; font-size: 11px;"
                f" font-family: 'Consolas', monospace;"
            )
            p_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.addWidget(p_lbl)

            self._ch_value_labels.append((v_lbl, i_lbl, p_lbl))
            metrics_layout.addWidget(container)

        layout.addLayout(metrics_layout)

        self._visualizer = PowerVisualizer3CH(show_toolbar=False)
        layout.addWidget(self._visualizer, 1)

        return group

    def _create_bottom_panel(self) -> QTabWidget:
        tabs = QTabWidget()

        # Register Map tab
        reg_tab = QWidget()
        reg_layout = QVBoxLayout(reg_tab)
        reg_layout.setContentsMargins(6, 6, 6, 6)

        self._reg_table = QTableWidget(len(DISPLAY_REGISTERS), 4)
        self._reg_table.setHorizontalHeaderLabels(["Addr", "Name", "Desc", "Value (Hex)"])
        self._reg_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._reg_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._reg_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._reg_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._reg_table.setColumnWidth(0, 80)
        self._reg_table.setColumnWidth(1, 180)
        self._reg_table.setColumnWidth(3, 110)
        self._reg_table.setAlternatingRowColors(True)
        self._reg_table.verticalHeader().setDefaultSectionSize(26)
        self._reg_table.verticalHeader().setVisible(False)
        self._reg_table.cellChanged.connect(self._on_reg_cell_changed)

        for row, reg in enumerate(DISPLAY_REGISTERS):
            addr_item = QTableWidgetItem(f"0x{reg.value:02X}")
            addr_item.setFlags(addr_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            addr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._reg_table.setItem(row, 0, addr_item)

            name_item = QTableWidgetItem(REGISTER_NAMES.get(reg, "?"))
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._reg_table.setItem(row, 1, name_item)

            desc_item = QTableWidgetItem(REGISTER_DESCRIPTIONS.get(reg, ""))
            desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._reg_table.setItem(row, 2, desc_item)

            val_item = QTableWidgetItem("-")
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            val_item.setFont(QFont("Consolas", 10))
            self._reg_table.setItem(row, 3, val_item)

        reg_layout.addWidget(self._reg_table)
        tabs.addTab(reg_tab, "Register Map")

        # I2C Log tab
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(6, 6, 6, 6)

        clear_btn = QPushButton("Clear Log")
        clear_btn.setFixedWidth(100)
        clear_btn.clicked.connect(lambda: self._log_text.clear())
        log_layout.addWidget(clear_btn, 0, Qt.AlignmentFlag.AlignRight)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setObjectName("themedConsole")
        log_layout.addWidget(self._log_text, 1)

        tabs.addTab(log_tab, "I2C Log")

        return tabs

    # ── BaseModule Overrides ─────────────────────────────────────────

    def on_device_connected(self) -> None:
        self._scan_btn.setEnabled(True)
        if self._addr_combo.count() == 0:
            self._addr_combo.addItem(f"0x{self._slave_addr:02X}", self._slave_addr)
        self._start_btn.setEnabled(True)
        self._set_hold_controls_enabled(True)
        self._apply_io_hold()
        self._append_log("[INFO] FTDI device connected.")

    def on_device_disconnected(self) -> None:
        self.stop_communication()
        self._scan_btn.setEnabled(False)
        self._start_btn.setEnabled(False)
        self._set_hold_controls_enabled(False)
        self._saved_hold = None
        self._ftdi.clear_i2c_hold()
        self._append_log("[INFO] FTDI device disconnected.")

    def on_tab_deactivated(self) -> None:
        super().on_tab_deactivated()
        self.stop_communication()
        if self._saved_hold is not None:
            mask, value = self._saved_hold
            self._ftdi.set_i2c_hold(mask, value)
            self._saved_hold = None

    def on_tab_activated(self) -> None:
        super().on_tab_activated()
        if self._ftdi.is_connected:
            self._set_hold_controls_enabled(True)
            self._run_async_i2c_task(
                force=False,
                settle_ms=0,
                task=lambda: True,
                on_done=lambda _result: None,
                sync_hold_ui=True,
            )

    def on_channel_changed(self, channel: str) -> None:
        if not self._ftdi.supports_mpsse(channel):
            self.stop_communication()
            self._scan_btn.setEnabled(False)
            self._start_btn.setEnabled(False)
        else:
            if self._ftdi.is_connected:
                self._scan_btn.setEnabled(True)
                self._start_btn.setEnabled(True)

    @Slot()
    def _on_start_btn_clicked(self) -> None:
        if not self._ftdi.supports_mpsse(self._ftdi.channel):
            self._show_mpsse_warning(self._ftdi.channel)
            return
        self.start_communication()

    def _build_start_task(self, shunts=None):
        """Build the I2C config+probe task closure for start_communication."""
        cfg = self._build_config_word()

        def _task() -> FtdiTaskResult:
            data = bytes([INA3221Reg.CONFIG.value, (cfg >> 8) & 0xFF, cfg & 0xFF])
            probe = self._probe_slave_ack_task(80)
            if not probe.success:
                return probe

            if not self._ftdi.i2c_write(self._slave_addr, data):
                if not self._force_restore_i2c_in_task(80):
                    return FtdiTaskResult(False, error="Config write restore failed.", stage="restore")
                probe = self._probe_slave_ack_task(0)
                if not probe.success:
                    return probe
                if not self._ftdi.i2c_write(self._slave_addr, data):
                    return FtdiTaskResult(False, error="Config write failed.", stage="write")

            raw = self._ftdi.i2c_read(self._slave_addr, bytes([INA3221Reg.CONFIG.value]), 2)
            if raw is None or len(raw) < 2:
                if not self._force_restore_i2c_in_task(60):
                    return FtdiTaskResult(False, error="Config verify restore failed.", stage="restore")
                probe = self._probe_slave_ack_task(0)
                if not probe.success:
                    return probe
                if not self._ftdi.i2c_write(self._slave_addr, data):
                    return FtdiTaskResult(False, error="Config write retry failed.", stage="write")
                raw = self._ftdi.i2c_read(self._slave_addr, bytes([INA3221Reg.CONFIG.value]), 2)
            if raw is None or len(raw) < 2:
                return FtdiTaskResult(False, error="Config verify read failed.", stage="verify")

            read_cfg = (raw[0] << 8) | raw[1]
            if read_cfg != cfg:
                return FtdiTaskResult(
                    False,
                    error=f"Config verify mismatch: 0x{read_cfg:04X} != 0x{cfg:04X}",
                    stage="verify",
                )

            snapshot = self._read_register_snapshot_task()
            if not snapshot.success:
                return snapshot
            return FtdiTaskResult(
                True,
                payload={"config": cfg, "registers": snapshot.payload},
                stage="start",
            )

        return _task

    def start_communication(self) -> None:
        if self._is_monitoring or self._start_pending:
            return
        if not self._ftdi.is_connected:
            return
        if not self._ftdi.supports_mpsse(self._ftdi.channel):
            return

        # Parse shunt values
        shunts = []
        for i in range(3):
            try:
                shunts.append(float(self._ch_shunt_edits[i].text()))
            except ValueError:
                shunts.append(0.01)

        self._start_pending = True
        self._start_btn.setEnabled(False)
        self._append_log("[INFO] Restoring INA3221 I2C context...")

        if not self._run_async_i2c_task(
            force=True,
            settle_ms=40,
            task=self._build_start_task(shunts),
            on_done=lambda result, shunt_values=shunts: self._on_start_sequence_finished(
                result,
                shunt_values,
            ),
        ):
            self._start_pending = False
            if self._ftdi.is_connected and self._ftdi.supports_mpsse(self._ftdi.channel):
                self._start_btn.setEnabled(True)
            self._append_log("[ERROR] Failed to queue INA3221 start task.")

    def _start_worker_monitoring(self, shunts: List[float]) -> None:
        self._worker = INA3221Worker(self._ftdi)
        self._worker.configure(
            slave_addr=self._slave_addr,
            poll_interval_ms=self._interval_spinbox.value(),
            shunt_resistors=shunts,
        )

        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.measurement_ready.connect(self._on_measurement)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.log_message.connect(self._append_log)
        self._worker_thread.start()

        self._ftdi.data_sent.connect(self._append_log)
        self._ftdi.data_received.connect(self._append_log)
        self._ftdi.log_message.connect(self._append_log)

        self._is_monitoring = True
        self._start_time = time.time()
        self._time_data.clear()
        for d in self._vbus_data:
            d.clear()
        for d in self._current_data:
            d.clear()
        self._visualizer.clear()

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._rec_btn.setEnabled(True)
        self._set_controls_enabled(False)

        self._append_log("[INFO] Monitoring started.")

    def _on_start_sequence_finished(self, result: FtdiTaskResult, shunts: List[float]) -> None:
        self._start_pending = False
        if not result.success:
            self._append_log(f"[ERROR] Monitoring start skipped: {result.error}")
            self._append_log("[INFO] Try FTDI Disconnect \u2192 Connect to recover.")
            self._stop_btn.setEnabled(False)
            self._rec_btn.setEnabled(False)
            self._set_controls_enabled(True)
            start_enabled = self._ftdi.is_connected and self._ftdi.supports_mpsse(self._ftdi.channel)
            self._start_btn.setEnabled(start_enabled)
            return

        payload = result.payload if isinstance(result.payload, dict) else {}
        snapshot = payload.get("registers")
        if snapshot is not None:
            self._apply_register_snapshot(snapshot)
        cfg = payload.get("config")
        if cfg is not None:
            self._append_log(f"[INFO] Config written: 0x{cfg:04X}")

        self._start_worker_monitoring(shunts)

    def stop_communication(self) -> None:
        self._start_pending = False
        if not self._is_monitoring:
            return

        # Stop recording if active
        if self._recorder.is_recording:
            filepath, count = self._recorder.stop()
            self._rec_btn.setText("\u2b24 REC")
            self._rec_btn.setStyleSheet("")
            self._apply_theme()
            self._append_log(f"[INFO] Recording auto-stopped: {count} samples \u2192 {filepath}")

        try:
            self._ftdi.data_sent.disconnect(self._append_log)
            self._ftdi.data_received.disconnect(self._append_log)
            self._ftdi.log_message.disconnect(self._append_log)
        except RuntimeError:
            pass

        if self._worker is not None:
            self._worker.stop()
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
            self._worker_thread.deleteLater()
            self._worker_thread = None
        self._worker = None
        self._is_monitoring = False

        if hasattr(self, "_start_btn"):
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._rec_btn.setEnabled(False)
            self._set_controls_enabled(True)

        self._append_log("[INFO] Monitoring stopped.")

    def update_data(self) -> None:
        self._refresh_register_map()

    # ── Slots ────────────────────────────────────────────────────────

    @Slot()
    def _on_scan_addresses(self) -> None:
        if not self._ftdi.is_connected:
            return
        if not self._ftdi.supports_mpsse(self._ftdi.channel):
            return

        self._scan_result_label.setText("Scanning...")
        self._addr_combo.clear()
        self._scan_btn.setEnabled(False)

        def _task() -> FtdiTaskResult:
            found = self._ftdi.i2c_scan(self.INA3221_SCAN_START, self.INA3221_SCAN_END)
            if not found:
                if not self._force_restore_i2c_in_task(80):
                    return FtdiTaskResult(False, error="I2C restore failed.", stage="restore")
                found = self._ftdi.i2c_scan(self.INA3221_SCAN_START, self.INA3221_SCAN_END)
            return FtdiTaskResult(True, payload=found, stage="scan")

        self._run_async_i2c_task(
            force=False,
            settle_ms=0,
            task=_task,
            on_done=self._on_scan_addresses_finished,
        )

    @Slot(int)
    def _on_addr_changed(self, index: int) -> None:
        if index >= 0:
            data = self._addr_combo.itemData(index)
            if data is not None:
                self._slave_addr = int(data)

    @Slot(object)
    def _on_measurement(self, m: INA3221Measurement) -> None:
        elapsed = m.timestamp - self._start_time
        self._time_data.append(elapsed)

        for i in range(3):
            v = m.vbus_v[i]
            c = m.current_ma[i]
            p = v * c  # mW

            self._vbus_data[i].append(v)
            self._current_data[i].append(c)

            v_lbl, i_lbl, p_lbl = self._ch_value_labels[i]
            v_lbl.setText(f"{v:.4f} V")
            i_lbl.setText(f"{c:.4f} mA")
            p_lbl.setText(f"{p:.3f} mW")

        window_s = self._window_seconds
        while self._time_data and (elapsed - self._time_data[0]) > window_s:
            self._time_data.popleft()
            for i in range(3):
                self._vbus_data[i].popleft()
                self._current_data[i].popleft()

        self._visualizer.update_data(
            list(self._time_data),
            [list(x) for x in self._vbus_data],
            [list(x) for x in self._current_data],
        )

        # Recording
        if self._recorder.is_recording:
            row = []
            for i in range(3):
                row.extend([f"{m.vbus_v[i]:.6f}", f"{m.vshunt_mv[i]:.6f}", f"{m.current_ma[i]:.6f}"])
            self._recorder.add_row(m.timestamp, row)
            cnt = self._recorder.sample_count
            el = self._recorder.elapsed_seconds
            mins, secs = divmod(int(el), 60)
            self._rec_btn.setText(f"\u25a0 STOP ({mins:02d}:{secs:02d}, {cnt})")

    @Slot()
    def _on_rec_clicked(self) -> None:
        if self._recorder.is_recording:
            filepath, count = self._recorder.stop()
            self._rec_btn.setText("\u2b24 REC")
            self._rec_btn.setStyleSheet("")
            self._apply_theme()
            self._append_log(f"[INFO] Recording stopped: {count} samples \u2192 {filepath}")
        else:
            if not self._is_monitoring:
                return
            headers = []
            for i in range(1, 4):
                headers.extend([f"CH{i}_Vbus_V", f"CH{i}_Vshunt_mV", f"CH{i}_Current_mA"])
            filepath = self._recorder.start("INA3221", headers)
            self._rec_btn.setText("\u25a0 STOP")
            self._rec_btn.setStyleSheet(
                "QPushButton { background: #cc2222; color: #ffffff; font-weight: bold;"
                " border-radius: 6px; border: 1px solid #cc3333; }"
            )
            self._append_log(f"[INFO] Recording started \u2192 {filepath}")

    @Slot(bool)
    def _on_auto_range_toggled(self, checked: bool) -> None:
        if hasattr(self, "_visualizer"):
            self._visualizer.set_auto_range(checked)
        self._auto_range_btn.setText("AUTO RANGE: ON" if checked else "AUTO RANGE: OFF")

    @Slot(str)
    def _on_worker_error(self, msg: str) -> None:
        self._append_log(f"[ERROR] {msg}")

    # ── Config ───────────────────────────────────────────────────────

    def _build_config_word(self) -> int:
        mode = self._op_mode_combo.currentData() or 7
        avg = self._avg_combo.currentData() or 2
        vbusct = self._vbusct_combo.currentData() or 4
        vshct = self._vshct_combo.currentData() or 4

        ch_en = [1 if self._ch_enable_cbs[i].isChecked() else 0 for i in range(3)]

        cfg = 0
        cfg |= (ch_en[0] << 14)
        cfg |= (ch_en[1] << 13)
        cfg |= (ch_en[2] << 12)
        cfg |= (avg << 9)
        cfg |= (vbusct << 6)
        cfg |= (vshct << 3)
        cfg |= mode
        return cfg

    def _write_config_from_ui(self) -> bool:
        if not self._ftdi.is_connected:
            return False
        cfg = self._build_config_word()
        data = bytes([INA3221Reg.CONFIG.value, (cfg >> 8) & 0xFF, cfg & 0xFF])
        if not self._ftdi.i2c_write(self._slave_addr, data):
            return False
        self._append_log(f"[INFO] Config written: 0x{cfg:04X}")
        return True

    def _probe_slave_ack_task(self, retry_restore_ms: int = 80) -> FtdiTaskResult:
        found = self._ftdi.i2c_scan(self._slave_addr, self._slave_addr)
        if found:
            return FtdiTaskResult(True, payload=found[0], stage="probe")
        if retry_restore_ms > 0:
            if not self._force_restore_i2c_in_task(retry_restore_ms):
                return FtdiTaskResult(False, error="I2C restore failed before probing INA3221.", stage="restore")
            found = self._ftdi.i2c_scan(self._slave_addr, self._slave_addr)
            if found:
                return FtdiTaskResult(True, payload=found[0], stage="probe")
        return FtdiTaskResult(
            False,
            error=f"INA3221 address 0x{self._slave_addr:02X} did not ACK after restore.",
            stage="probe",
        )

    def _parse_config_from_hex(self, cfg: int) -> None:
        self._op_mode_combo.blockSignals(True)
        self._avg_combo.blockSignals(True)
        self._vbusct_combo.blockSignals(True)
        self._vshct_combo.blockSignals(True)
        for cb in self._ch_enable_cbs:
            cb.blockSignals(True)

        self._ch_enable_cbs[0].setChecked(bool((cfg >> 14) & 1))
        self._ch_enable_cbs[1].setChecked(bool((cfg >> 13) & 1))
        self._ch_enable_cbs[2].setChecked(bool((cfg >> 12) & 1))

        avg = (cfg >> 9) & 0x7
        vbusct = (cfg >> 6) & 0x7
        vshct = (cfg >> 3) & 0x7
        mode = cfg & 0x7

        idx = self._avg_combo.findData(avg)
        if idx >= 0:
            self._avg_combo.setCurrentIndex(idx)
        idx = self._vbusct_combo.findData(vbusct)
        if idx >= 0:
            self._vbusct_combo.setCurrentIndex(idx)
        idx = self._vshct_combo.findData(vshct)
        if idx >= 0:
            self._vshct_combo.setCurrentIndex(idx)
        idx = self._op_mode_combo.findData(mode)
        if idx >= 0:
            self._op_mode_combo.setCurrentIndex(idx)

        self._op_mode_combo.blockSignals(False)
        self._avg_combo.blockSignals(False)
        self._vbusct_combo.blockSignals(False)
        self._vshct_combo.blockSignals(False)
        for cb in self._ch_enable_cbs:
            cb.blockSignals(False)

    def _read_register_snapshot_task(self) -> FtdiTaskResult:
        snapshot = []
        retry_done = False
        for reg in DISPLAY_REGISTERS:
            raw = self._ftdi.i2c_read(self._slave_addr, bytes([reg.value]), 2)
            if (raw is None or len(raw) < 2) and not retry_done:
                retry_done = True
                if not self._force_restore_i2c_in_task(60):
                    return FtdiTaskResult(False, error="Register restore failed.", stage="restore")
                raw = self._ftdi.i2c_read(self._slave_addr, bytes([reg.value]), 2)
            if raw is None or len(raw) < 2:
                return FtdiTaskResult(
                    False,
                    error=f"Register read failed at 0x{reg.value:02X}.",
                    stage="read",
                )
            val = (raw[0] << 8) | raw[1]
            snapshot.append((reg, val))
        return FtdiTaskResult(True, payload=snapshot, stage="read")

    def _apply_register_snapshot(self, snapshot) -> None:
        if snapshot is None:
            return
        self._reg_table.blockSignals(True)
        for row, (reg, val) in enumerate(snapshot):
            val_item = self._reg_table.item(row, 3)
            if val_item:
                val_item.setText(f"0x{val:04X}")
            if reg == INA3221Reg.CONFIG:
                self._parse_config_from_hex(val)
        self._reg_table.blockSignals(False)

    def _refresh_register_map(self) -> None:
        if not self._ftdi.is_connected:
            return
        self._run_async_i2c_task(
            force=False,
            settle_ms=0,
            task=self._read_register_snapshot_task,
            on_done=self._on_refresh_register_map_finished,
        )

    @Slot(int, int)
    def _on_reg_cell_changed(self, row: int, col: int) -> None:
        if col != 3:
            return
        if not self._ftdi.is_connected or self._is_monitoring:
            return

        item = self._reg_table.item(row, 3)
        if item is None:
            return

        text = item.text().strip()
        try:
            value = int(text, 16) if text.startswith(("0x", "0X")) else int(text, 16)
            reg = DISPLAY_REGISTERS[row]
            data = bytes([reg.value, (value >> 8) & 0xFF, value & 0xFF])
            self._run_async_i2c_task(
                force=False,
                settle_ms=0,
                task=lambda reg_value=data, reg_addr=reg, reg_hex=value: self._write_register_task(
                    reg_addr,
                    reg_value,
                    reg_hex,
                ),
                on_done=self._on_register_write_finished,
            )

        except ValueError:
            self._refresh_register_map()

    def _write_register_task(
        self,
        reg: INA3221Reg,
        data: bytes,
        value: int,
    ) -> FtdiTaskResult:
        if not self._ftdi.i2c_write(self._slave_addr, data):
            if not self._force_restore_i2c_in_task(60):
                return FtdiTaskResult(False, error="Register write restore failed.", stage="restore")
            if not self._ftdi.i2c_write(self._slave_addr, data):
                return FtdiTaskResult(False, error="Register write failed.", stage="write")

        snapshot = self._read_register_snapshot_task()
        if not snapshot.success:
            return snapshot
        return FtdiTaskResult(
            True,
            payload={"registers": snapshot.payload, "reg": reg, "value": value},
            stage="write",
        )

    def _on_scan_addresses_finished(self, result: FtdiTaskResult) -> None:
        self._scan_btn.setEnabled(True)
        if not result.success:
            self._scan_result_label.setText("I2C restore failed")
            self._start_btn.setEnabled(False)
            self._append_log(f"[ERROR] Address scan aborted: {result.error}")
            return

        found = list(result.payload or [])
        if not found:
            self._scan_result_label.setText("INA3221 device not found")
            self._start_btn.setEnabled(False)
            self._append_log("[WARN] No INA3221 device found on bus.")
            return

        for addr in found:
            self._addr_combo.addItem(f"0x{addr:02X}", addr)

        self._scan_result_label.setText(f"{len(found)} device(s) found")
        self._slave_addr = found[0]
        self._addr_combo.setCurrentIndex(0)
        if not self._is_monitoring and not self._start_pending:
            self._start_btn.setEnabled(True)
        self._append_log(f"[INFO] Found {len(found)} device(s): {', '.join(f'0x{a:02X}' for a in found)}")

    def _on_refresh_register_map_finished(self, result: FtdiTaskResult) -> None:
        if not result.success:
            self._append_log(f"[ERROR] Register refresh failed: {result.error}")
            return
        self._apply_register_snapshot(result.payload)

    def _on_register_write_finished(self, result: FtdiTaskResult) -> None:
        if not result.success:
            self._append_log(f"[ERROR] Register write failed: {result.error}")
            self._refresh_register_map()
            return

        payload = result.payload if isinstance(result.payload, dict) else {}
        snapshot = payload.get("registers")
        if snapshot is not None:
            self._apply_register_snapshot(snapshot)

    # ── Helpers ──────────────────────────────────────────────────────

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._op_mode_combo.setEnabled(enabled)
        self._avg_combo.setEnabled(enabled)
        self._vbusct_combo.setEnabled(enabled)
        self._vshct_combo.setEnabled(enabled)
        self._interval_spinbox.setEnabled(enabled)
        for cb in self._ch_enable_cbs:
            cb.setEnabled(enabled)
        for edit in self._ch_shunt_edits:
            edit.setEnabled(enabled)

    def _append_log(self, message: str) -> None:
        if not hasattr(self, "_log_text"):
            return
        tm = ThemeManager.instance()
        if "[ERROR]" in message:
            color = tm.color("status_disconnected")
        elif "[WARN]" in message:
            color = tm.color("status_warning")
        elif "[INFO]" in message:
            color = tm.color("status_connected")
        else:
            color = tm.color("text_secondary")
        self._log_text.append(f'<span style="color:{color}">{message}</span>')
        sb = self._log_text.verticalScrollBar()
        sb.setValue(sb.maximum())
