"""
INA228 power monitor module - Universal Device Studio plugin

Provides I2C auto-scan, real-time voltage/current monitoring, and register map view.
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
    QDoubleSpinBox, QSpinBox, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QTextEdit,
)

from core.ftdi_manager import FtdiManager
from core.theme_manager import ThemeManager
from modules.base_module import BaseModule
from modules.ina228.ina228_registers import (
    INA228Reg, REGISTER_SIZE, REGISTER_NAMES, REGISTER_DESCRIPTIONS,
    DISPLAY_REGISTERS, INA228_REGISTER_FIELDS, INA228_FIELD_BY_NAME,
    INA228Conversion,
    ADC_RANGE_OPTIONS, AVG_COUNT_OPTIONS, CONV_TIME_OPTIONS,
)
from modules.ina228.ina228_worker import INA228Worker, INA228Measurement
from modules.ina228.power_visualizer import PowerVisualizer


class INA228Module(BaseModule):
    """INA228 power monitor device module

    Layout:
    - Top: I2C address auto-scan panel
    - Left: Control panel (ADC range, AVG, conversion time, shunt, Start/Stop)
    - Right: pyqtgraph dual chart + live metrics
    - Bottom: INA228 register map table
    """

    MODULE_NAME = "INA228 Monitor"
    MODULE_ICON = "📈"
    MODULE_VERSION = "1.0.0"
    MODULE_ORDER = 20
    REQUIRED_MODE = "I2C"
    REQUIRE_MPSSE = True

    MAX_DATA_POINTS = 2000
    INA228_SCAN_START = 0x40
    INA228_SCAN_END   = 0x4F

    def __init__(self, ftdi_manager: FtdiManager, parent: Optional[QWidget] = None) -> None:
        self._worker: Optional[INA228Worker] = None
        self._worker_thread: Optional[QThread] = None
        self._slave_addr: int = 0x40
        self._is_monitoring: bool = False
        self._window_seconds: int = 60
        self._io_hold_mask: int = 0xF0
        self._io_hold_value: int = 0x00
        self._saved_hold: Optional[tuple[int, int]] = None
        self._settings = QSettings("UniversalDeviceStudio", "INA228Module")

        # Data buffer (sliding window)
        self._time_data: deque = deque(maxlen=self.MAX_DATA_POINTS)
        self._voltage_data: deque = deque(maxlen=self.MAX_DATA_POINTS)
        self._current_data: deque = deque(maxlen=self.MAX_DATA_POINTS)
        self._start_time: float = 0.0
        super().__init__(ftdi_manager, parent)

    # -- BaseModule abstract method implementations --

    def init_ui(self) -> None:
        """Initialize module UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Top: address scan panel + IO hold
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        top_row.addWidget(self._create_address_panel(), 2)
        top_row.addWidget(self._create_io_hold_panel(), 1)
        layout.addLayout(top_row)

        # Center + bottom splitter
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        v_splitter.setHandleWidth(3)

        # Left (control) + right (visualization) splitter
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
        self._load_io_hold_state()

        # Theme support
        self._apply_theme()
        ThemeManager.instance().theme_changed.connect(self._apply_theme)

    def _apply_theme(self) -> None:
        """Re-apply all inline styles from the current theme."""
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
        self._scan_result_label.setStyleSheet(
            f"color: {tm.color('text_label')}; font-style: italic;"
        )
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
        # Auto range
        self._auto_range_btn.setStyleSheet(
            f"QPushButton {{ font-weight: bold; font-size: 12px; padding: 6px 10px;"
            f" border-radius: 6px; background: {tm.color('btn_auto_bg')};"
            f" color: {tm.color('text_accent')}; }}"
            f"QPushButton:checked {{ background: #1f5eff; color: #ffffff; }}"
        )
        # Separator
        if hasattr(self, '_ctrl_sep'):
            self._ctrl_sep.setStyleSheet(f"color: {tm.color('separator')};")
        # Metric containers
        for c in self._metric_containers:
            c.setStyleSheet(f"background-color: {tm.color('metric_bg')}; border-radius: 6px;")

    def on_device_connected(self) -> None:
        self._scan_btn.setEnabled(True)
        if self._addr_combo.count() == 0:
            self._addr_combo.addItem(f"0x{self._slave_addr:02X}", self._slave_addr)
        self._start_btn.setEnabled(True)
        self._apply_io_hold()
        self.status_message.emit("INA228: FTDI connected - address scan available")

    def on_device_disconnected(self) -> None:
        self.stop_communication()
        self._scan_btn.setEnabled(False)
        self._start_btn.setEnabled(False)
        self._saved_hold = None
        self._ftdi.clear_i2c_hold()
        self.status_message.emit("INA228: FTDI disconnected")

    def on_tab_deactivated(self) -> None:
        super().on_tab_deactivated()
        self.stop_communication()
        if self._saved_hold is not None:
            mask, value = self._saved_hold
            self._ftdi.set_i2c_hold(mask, value)
            self._saved_hold = None

    def on_tab_activated(self) -> None:
        super().on_tab_activated()
        self._apply_io_hold()
        # Sync button state to actual GPIO — hardware is the source of truth on tab entry
        if self._ftdi.is_connected and self._ftdi.supports_mpsse(self._ftdi.channel):
            self._refresh_hold_status(sync_buttons=True)

    def on_channel_changed(self, channel: str) -> None:
        if not self._ftdi.supports_mpsse(channel):
            self.stop_communication()
            self._scan_btn.setEnabled(False)
            self._start_btn.setEnabled(False)
            self.status_message.emit(f"INA228: Channel {channel} does not support MPSSE.")
        else:
            if self._ftdi.is_connected:
                self._scan_btn.setEnabled(True)
                self._start_btn.setEnabled(True)
                self._apply_io_hold()

    @Slot()
    def _on_start_btn_clicked(self) -> None:
        """Start button click — show MPSSE warning if channel is incompatible."""
        if not self._ftdi.supports_mpsse(self._ftdi.channel):
            self._show_mpsse_warning(self._ftdi.channel)
            return
        self.start_communication()

    def start_communication(self) -> None:
        """Start worker thread (monitoring ON)."""
        if self._is_monitoring:
            return
        if not self._ftdi.is_connected:
            return
        if not self._ftdi.supports_mpsse(self._ftdi.channel):
            return

        # Ensure MPSSE mode is active — previous tab (e.g., FTDI Verifier GPIO) may have
        # left the channel in bitbang mode which blocks I2C.
        self._ftdi.set_protocol_mode("I2C")

        self._worker = INA228Worker(self._ftdi)
        self._worker.configure(
            slave_addr=self._slave_addr,
            adc_range=self._adc_range_combo.currentIndex(),
            shunt_resistor=self._shunt_spinbox.value(),
            poll_interval_ms=self._interval_spinbox.value(),
            avg_index=self._avg_combo.currentIndex(),
            vbusct_index=self._vbusct_combo.currentIndex(),
            vshct_index=self._vshct_combo.currentIndex(),
        )

        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.measurement_ready.connect(self._on_measurement)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.log_message.connect(self._on_worker_log)
        self._worker_thread.start()

        # Connect FTDI comm log to I2C log tab
        self._ftdi.data_sent.connect(self._append_log)
        self._ftdi.data_received.connect(self._append_log)
        self._ftdi.log_message.connect(self._append_log)

        self._is_monitoring = True
        self._start_time = time.time()
        self._time_data.clear()
        self._voltage_data.clear()
        self._current_data.clear()
        self._visualizer.clear()

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._adc_range_combo.setEnabled(False)
        self._avg_combo.setEnabled(False)
        self._vbusct_combo.setEnabled(False)
        self._vshct_combo.setEnabled(False)
        self._shunt_spinbox.setEnabled(False)

    def stop_communication(self) -> None:
        """Stop worker thread (monitoring OFF)."""
        if not self._is_monitoring:
            return

        # Disconnect FTDI log signal
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
            if not self._worker_thread.wait(3000):
                self._worker_thread.terminate()
                self._worker_thread.wait(1000)
            self._worker_thread.deleteLater()
            self._worker_thread = None
        self._worker = None
        self._is_monitoring = False

        if hasattr(self, "_start_btn"):
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._adc_range_combo.setEnabled(True)
            self._avg_combo.setEnabled(True)
            self._vbusct_combo.setEnabled(True)
            self._vshct_combo.setEnabled(True)
            self._shunt_spinbox.setEnabled(True)

    def update_data(self) -> None:
        """Refresh register map once."""
        self._refresh_register_map()

    # -- UI builders --

    def _create_address_panel(self) -> QGroupBox:
        """I2C address auto-scan panel."""
        group = QGroupBox("I2C Address Scan (0x40 ~ 0x4F)")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(10, 6, 10, 6)

        self._scan_btn = QPushButton("Scan Addresses")
        self._scan_btn.setFixedWidth(140)
        self._scan_btn.setEnabled(False)
        # Style applied in _apply_theme()
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
        # Style applied in _apply_theme()
        layout.addWidget(self._scan_result_label)
        layout.addStretch()
        return group

    def _create_io_hold_panel(self) -> QGroupBox:
        group = QGroupBox("GPIO Hold (D4-D7)")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        self._hold_btns: dict[int, QPushButton] = {}
        self._hold_leds: dict[int, QLabel] = {}
        self._hold_bars: dict[int, QFrame] = {}
        row = QHBoxLayout()
        row.setSpacing(6)
        for bit in range(4, 8):
            btn = QPushButton(f"D{bit}: OFF")
            btn.setCheckable(True)
            btn.setMinimumWidth(80)
            btn.setMinimumHeight(28)
            # Style applied in _apply_theme()
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
            # Style applied in _apply_theme()
            self._hold_leds[bit] = led
            led_row.addWidget(tag)
            led_row.addWidget(led)
            bar_bg = QFrame()
            bar_bg.setFixedSize(56, 8)
            bar_bg.setObjectName("holdBarBg")
            bar_fill = QFrame(bar_bg)
            bar_fill.setGeometry(1, 1, 6, 6)
            # Style applied in _apply_theme()
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

    def _apply_io_hold(self) -> None:
        if not self._ftdi.is_connected:
            return
        if not self._ftdi.supports_mpsse(self._ftdi.channel):
            return
        if self._saved_hold is None:
            self._saved_hold = self._ftdi.get_i2c_hold()
        self._ftdi.set_i2c_hold(self._io_hold_mask, self._io_hold_value)
        self._refresh_hold_status()

    def _refresh_hold_status(self, sync_buttons: bool = False) -> None:
        """Refresh GPIO Hold LED/bar from actual hardware state.

        Args:
            sync_buttons: if True, also update button checked/text to match hardware.
        """
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
        """Left control panel."""
        group = QGroupBox("ADC Settings")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setSpacing(6)

        # ADC Range
        grid.addWidget(QLabel("ADC range:"), 0, 0)
        self._adc_range_combo = QComboBox()
        for k, v in ADC_RANGE_OPTIONS.items():
            self._adc_range_combo.addItem(v, k)
        self._adc_range_combo.setCurrentIndex(1)
        grid.addWidget(self._adc_range_combo, 0, 1)

        # Averaging
        grid.addWidget(QLabel("AVG samples:"), 1, 0)
        self._avg_combo = QComboBox()
        for k, v in AVG_COUNT_OPTIONS.items():
            self._avg_combo.addItem(v, k)
        self._avg_combo.setCurrentIndex(2)   # AVG=16
        grid.addWidget(self._avg_combo, 1, 1)

        # VBUSCT
        grid.addWidget(QLabel("VBUS conversion time:"), 2, 0)
        self._vbusct_combo = QComboBox()
        for k, v in CONV_TIME_OPTIONS.items():
            self._vbusct_combo.addItem(v, k)
        self._vbusct_combo.setCurrentIndex(4)  # 540us
        grid.addWidget(self._vbusct_combo, 2, 1)

        # VSHCT
        grid.addWidget(QLabel("Shunt conversion time:"), 3, 0)
        self._vshct_combo = QComboBox()
        for k, v in CONV_TIME_OPTIONS.items():
            self._vshct_combo.addItem(v, k)
        self._vshct_combo.setCurrentIndex(4)  # 540us
        grid.addWidget(self._vshct_combo, 3, 1)

        # Shunt Resistor
        grid.addWidget(QLabel("Shunt resistor (Ohm):"), 4, 0)
        self._shunt_spinbox = QDoubleSpinBox()
        self._shunt_spinbox.setDecimals(4)
        self._shunt_spinbox.setRange(0.0001, 100.0)
        self._shunt_spinbox.setSingleStep(0.001)
        self._shunt_spinbox.setValue(0.01)
        grid.addWidget(self._shunt_spinbox, 4, 1)

        # Polling interval
        grid.addWidget(QLabel("Polling interval (ms):"), 5, 0)
        self._interval_spinbox = QSpinBox()
        self._interval_spinbox.setRange(50, 10000)
        self._interval_spinbox.setValue(100)
        self._interval_spinbox.setSingleStep(50)
        grid.addWidget(self._interval_spinbox, 5, 1)

        # Data window (seconds)
        grid.addWidget(QLabel("Window (s):"), 6, 0)
        self._window_spinbox = QSpinBox()
        self._window_spinbox.setRange(10, 600)
        self._window_spinbox.setValue(60)
        self._window_spinbox.setSingleStep(10)
        self._window_spinbox.valueChanged.connect(self._on_window_seconds_changed)
        grid.addWidget(self._window_spinbox, 6, 1)

        # Auto range toggle (prominent)
        grid.addWidget(QLabel("Auto Range:"), 7, 0)
        self._auto_range_btn = QPushButton("AUTO RANGE: ON")
        self._auto_range_btn.setCheckable(True)
        self._auto_range_btn.setChecked(True)
        self._auto_range_btn.setMinimumHeight(32)
        # Style applied in _apply_theme()
        self._auto_range_btn.toggled.connect(self._on_auto_range_toggled)
        grid.addWidget(self._auto_range_btn, 7, 1)

        layout.addLayout(grid)

        # Divider
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        # Style applied in _apply_theme()
        self._ctrl_sep = sep
        layout.addWidget(sep)

        # Start / Stop buttons
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Monitoring")
        self._start_btn.setObjectName("startBtn")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start_btn_clicked)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop Monitoring")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self.stop_communication)
        btn_row.addWidget(self._stop_btn)
        layout.addLayout(btn_row)

        # Register map refresh button
        self._refresh_reg_btn = QPushButton("Refresh Register Map")
        self._refresh_reg_btn.clicked.connect(self._refresh_register_map)
        layout.addWidget(self._refresh_reg_btn)

        layout.addStretch()
        return group

    def _create_visualizer_panel(self) -> QGroupBox:
        """Right visualization panel (pyqtgraph + metric labels)."""
        group = QGroupBox("Real-Time Monitoring")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 6, 6, 6)

        # Metrics row
        metrics_layout = QHBoxLayout()
        metrics_layout.setSpacing(20)
        self._metric_containers: List[QWidget] = []

        def _make_metric(title: str, unit: str, color: str) -> QLabel:
            container = QWidget()
            self._metric_containers.append(container)
            vl = QVBoxLayout(container)
            vl.setContentsMargins(8, 4, 8, 4)
            vl.setSpacing(2)
            t = QLabel(title)
            t.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: bold;")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.addWidget(t)
            val = QLabel("-")
            val.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold;")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.addWidget(val)
            container.setObjectName("metricContainer")
            metrics_layout.addWidget(container)
            return val

        self._vbus_label    = _make_metric("VBUS",    "V",  "#00d2ff")
        self._vshunt_label  = _make_metric("VSHUNT",  "mV", "#88d8ff")
        self._current_label = _make_metric("Current",    "mA", "#ff64b4")
        self._power_label   = _make_metric("Power",    "mW", "#ffcc44")
        self._temp_label    = _make_metric("Temp",    "C", "#88cc88")

        layout.addLayout(metrics_layout)

        # pyqtgraph dual charts
        self._visualizer = PowerVisualizer(show_toolbar=False)
        layout.addWidget(self._visualizer, 1)

        return group

    def _create_bottom_panel(self) -> QTabWidget:
        """Bottom register map + I2C log (tab widget)."""
        tabs = QTabWidget()

        # -- Register map tab --
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
            addr_item.setForeground(QColor(136, 192, 255))
            self._reg_table.setItem(row, 0, addr_item)

            name_item = QTableWidgetItem(REGISTER_NAMES.get(reg, "?"))
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item.setForeground(QColor(200, 210, 255))
            self._reg_table.setItem(row, 1, name_item)

            desc_item = QTableWidgetItem(REGISTER_DESCRIPTIONS.get(reg, ""))
            desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._reg_table.setItem(row, 2, desc_item)

            val_item = QTableWidgetItem("-")
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            val_item.setFont(QFont("Consolas", 10))
            val_item.setForeground(QColor(220, 255, 200))
            self._reg_table.setItem(row, 3, val_item)

        reg_layout.addWidget(self._reg_table)
        tabs.addTab(reg_tab, "Register Map")

        # -- I2C log tab --
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(6, 6, 6, 6)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("I2C Packet Log"))
        log_header.addStretch()
        clear_btn = QPushButton("Clear Log")
        clear_btn.setFixedWidth(120)
        clear_btn.clicked.connect(lambda: self._log_text.clear())
        log_header.addWidget(clear_btn)
        log_layout.addLayout(log_header)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFont(QFont("Consolas", 10))
        log_layout.addWidget(self._log_text, 1)

        tabs.addTab(log_tab, "I2C Log")

        return tabs

    # -- Slots --

    @Slot()
    def _on_scan_addresses(self) -> None:
        """Scan I2C addresses in 0x40~0x4F range."""
        if not self._ftdi.is_connected:
            return
        if not self._ftdi.supports_mpsse(self._ftdi.channel):
            self._show_mpsse_warning(self._ftdi.channel)
            return

        self._scan_result_label.setText("Scanning...")
        self._addr_combo.clear()

        found = self._ftdi.i2c_scan(self.INA228_SCAN_START, self.INA228_SCAN_END)

        if not found:
            self._scan_result_label.setText("INA228 device not found")
            self._start_btn.setEnabled(False)
            return

        for addr in found:
            self._addr_combo.addItem(f"0x{addr:02X}", addr)

        self._scan_result_label.setText(f"{len(found)} device(s) found")
        self._slave_addr = found[0]
        self._addr_combo.setCurrentIndex(0)
        self._start_btn.setEnabled(True)

    @Slot(int)
    def _on_addr_changed(self, index: int) -> None:
        if index >= 0:
            data = self._addr_combo.itemData(index)
            if data is not None:
                self._slave_addr = int(data)

    @Slot(object)
    def _on_measurement(self, m: INA228Measurement) -> None:
        """Receive worker measurements -> update charts/labels."""
        if not all(
            math.isfinite(x)
            for x in (m.vbus_v, m.vshunt_mv, m.current_ma, m.power_mw, m.die_temp_c)
        ):
            return
        elapsed = m.timestamp - self._start_time
        self._time_data.append(elapsed)
        self._voltage_data.append(m.vbus_v)
        self._current_data.append(m.current_ma)

        window_s = self._window_seconds
        while self._time_data and (elapsed - self._time_data[0]) > window_s:
            self._time_data.popleft()
            self._voltage_data.popleft()
            self._current_data.popleft()

        self._visualizer.update_data(
            list(self._time_data),
            list(self._voltage_data),
            list(self._current_data),
        )

        self._vbus_label.setText(f"{m.vbus_v:.4f} V")
        self._vshunt_label.setText(f"{m.vshunt_mv:.4f} mV")
        self._current_label.setText(f"{m.current_ma:.4f} mA")
        self._power_label.setText(f"{m.power_mw:.4f} mW")
        self._temp_label.setText(f"{m.die_temp_c:.2f} C")

    @Slot(int)
    def _on_window_seconds_changed(self, value: int) -> None:
        self._window_seconds = max(1, int(value))

    @Slot(bool)
    def _on_auto_range_toggled(self, checked: bool) -> None:
        if hasattr(self, "_visualizer"):
            self._visualizer.set_auto_range(checked)
        if hasattr(self, "_auto_range_btn"):
            self._auto_range_btn.setText("AUTO RANGE: ON" if checked else "AUTO RANGE: OFF")

    @Slot(str)
    def _on_worker_error(self, msg: str) -> None:
        self._append_log(f"[INA228 ERROR] {msg}")

    @Slot(str)
    def _on_worker_log(self, msg: str) -> None:
        self._append_log(msg)

    _MAX_LOG_BLOCKS = 3000

    def _append_log(self, message: str) -> None:
        """Append a message to the I2C log tab (color-coded)."""
        if not hasattr(self, "_log_text"):
            return
        if "[ERROR]" in message or "ERROR" in message:
            color = "#ff6666"
        elif "TX ->" in message:
            color = "#66ccff"
        elif "RX <-" in message:
            color = "#66ff99"
        elif "[WARN]" in message or "WARN" in message:
            color = "#ffcc44"
        else:
            color = "#8899aa"

        html = f'<span style="color:{color};">{message}</span>'
        self._log_text.append(html)

        doc = self._log_text.document()
        while doc.blockCount() > self._MAX_LOG_BLOCKS:
            cursor = self._log_text.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def _refresh_register_map(self) -> None:
        """Refresh register map table (direct read in UI thread when idle)."""
        if not self._ftdi.is_connected:
            return

        self._reg_table.blockSignals(True)
        for row, reg in enumerate(DISPLAY_REGISTERS):
            size = REGISTER_SIZE.get(reg, 2)
            raw = self._ftdi.i2c_read(self._slave_addr, bytes([reg.value]), size)
            if raw is not None and len(raw) >= size:
                if size >= 3:
                    val = ((raw[0] << 16) | (raw[1] << 8) | raw[2]) >> 4
                else:
                    val = (raw[0] << 8) | raw[1]
                hex_str = f"0x{val:04X}" if size == 2 else f"0x{val:05X}"
            else:
                hex_str = "ERROR"

            val_item = self._reg_table.item(row, 3)
            if val_item:
                val_item.setText(hex_str)
        self._reg_table.blockSignals(False)

    @Slot(int, int)
    def _on_reg_cell_changed(self, row: int, col: int) -> None:
        """Directly edit a value from the register map table."""
        if col != 3:
            return
        if not self._ftdi.is_connected or self._is_monitoring:
            return

        item = self._reg_table.item(row, 3)
        if item is None:
            return

        text = item.text().strip()
        try:
            if text.startswith(("0x", "0X")):
                value = int(text, 16)
            else:
                value = int(text, 16)

            reg = DISPLAY_REGISTERS[row]
            data = bytes([reg.value, (value >> 8) & 0xFF, value & 0xFF])
            self._ftdi.i2c_write(self._slave_addr, data)
        except ValueError:
            self._refresh_register_map()
