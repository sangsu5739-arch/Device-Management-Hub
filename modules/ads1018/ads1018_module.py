"""
ADS1018 SPI ADC Module — Universal Device Studio plugin

4-channel 12-bit ADC monitoring via SPI.
Provides per-channel V/I mode, real-time pyqtgraph visualization,
and dynamic control panel.
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QCheckBox,
    QGroupBox, QLineEdit, QRadioButton, QButtonGroup,
    QSplitter, QFrame, QSizePolicy, QTextEdit,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtGui import QColor, QFont

from core.ftdi_manager import FtdiManager
from core.theme_manager import ThemeManager
from modules.base_module import BaseModule
from modules.ads1018.ads1018_driver import (
    PGA, DataRate, ChannelMode, ChannelConfig, ADS1018Config,
)
from modules.ads1018.ads1018_worker import ADS1018Worker, ADS1018Measurement
from modules.ads1018.adc_visualizer import ADCVisualizer, CH_COLORS


class ADS1018Module(BaseModule):
    """ADS1018 4-channel SPI ADC monitor module."""

    MODULE_NAME = "ADS1018 Monitor"
    MODULE_ICON = "📈"
    MODULE_VERSION = "1.0.0"
    MODULE_ORDER = 30
    REQUIRED_MODE = "SPI"

    def __init__(self, ftdi_manager: FtdiManager,
                 parent: Optional[QWidget] = None) -> None:
        self._worker: Optional[ADS1018Worker] = None
        self._worker_thread: Optional[QThread] = None
        self._running = False
        self._config = ADS1018Config()

        # Per-channel UI references (must be set before super().__init__ calls init_ui)
        self._ch_radio_groups: list = []
        self._ch_shunt_edits: list = []
        self._ch_gain_edits: list = []
        self._ch_current_frames: list = []
        self._ch_value_labels: list = []
        self._ch_unit_labels: list = []
        super().__init__(ftdi_manager, parent)

    def init_ui(self) -> None:
        """Build the module UI (INA228 style layout)."""
        self._io_hold_value = 0
        self._io_hold_mask = 0xF0  # D4-D7

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Top: GPIO hold (right aligned)
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        top_row.addStretch(1)
        top_row.addWidget(self._create_gpio_panel(), 0)
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
        v_splitter.setStretchFactor(0, 3)
        v_splitter.setStretchFactor(1, 1)

        layout.addWidget(v_splitter, 1)

        # Theme support
        self._apply_theme()
        ThemeManager.instance().theme_changed.connect(self._apply_theme)

    def _apply_theme(self) -> None:
        tm = ThemeManager.instance()
        # Hold LEDs
        for w in self.findChildren(QLabel, "holdLed"):
            w.setStyleSheet(f"background: {tm.color('ads_led_off_bg')}; border-radius: 7px;"
                            f" border: 1px solid {tm.color('ads_led_off_border')};")
        # Hold buttons
        for w in self.findChildren(QPushButton, "holdBtn"):
            w.setStyleSheet(
                f"QPushButton {{ background: {tm.color('btn_hold_bg')}; color: {tm.color('btn_hold_text')};"
                f" font-weight: bold; border-radius: 4px; border: 1px solid {tm.color('btn_hold_border')};"
                f" font-size: 11px; }}"
                f"QPushButton:hover {{ background: {tm.color('btn_hold_hover')}; }}"
                f"QPushButton:checked {{ background: {tm.color('btn_hold_checked_bg')};"
                f" color: {tm.color('btn_hold_checked_text')};"
                f" border: 1px solid {tm.color('btn_hold_checked_border')}; }}"
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
        # Section title
        for w in self.findChildren(QLabel, "sectionTitle"):
            w.setStyleSheet(f"color: {tm.color('text_accent')}; font-weight: bold; font-size: 11px;")
        # Channel frames
        for w in self.findChildren(QFrame, "chFrame"):
            w.setStyleSheet(
                f"QFrame {{ background: {tm.color('ads_ch_frame_bg')};"
                f" border: 1px solid {tm.color('ads_ch_frame_border')}; border-radius: 4px; }}"
            )
        # Current frames
        for w in self.findChildren(QFrame, "currentFrame"):
            w.setStyleSheet("border: none; background: transparent;")
        # Param labels
        for w in self.findChildren(QLabel, "paramLbl"):
            w.setStyleSheet(f"color: {tm.color('text_tag')}; font-size: 10px;")
        # Param edits
        for w in self.findChildren(QLineEdit, "paramEdit"):
            w.setStyleSheet(
                f"QLineEdit {{ background: {tm.color('ads_vi_btn_bg')}; color: {tm.color('text_primary')};"
                f" border: 1px solid {tm.color('ads_vi_btn_border')}; border-radius: 3px;"
                f" padding: 1px 4px; font-size: 10px; max-width: 50px; }}"
            )
        # Metric containers
        for w in self.findChildren(QWidget, "metricContainer"):
            w.setStyleSheet(f"background-color: {tm.color('metric_bg')}; border-radius: 6px;")
        # V/I toggle buttons per channel
        vi_base = (f"QPushButton {{ font-size: 11px; font-weight: bold; border-radius: 4px;"
                   f" background: {tm.color('ads_vi_btn_bg')}; color: {tm.color('ads_vi_btn_text')};"
                   f" border: 1px solid {tm.color('ads_vi_btn_border')}; }}")
        v_checked = (f"QPushButton:checked {{ background: {tm.color('ads_vi_v_checked_bg')};"
                     f" color: {tm.color('ads_vi_v_checked_text')};"
                     f" border: 1px solid {tm.color('ads_vi_v_checked_border')}; }}")
        i_checked = (f"QPushButton:checked {{ background: {tm.color('ads_vi_i_checked_bg')};"
                     f" color: {tm.color('ads_vi_i_checked_text')};"
                     f" border: 1px solid {tm.color('ads_vi_i_checked_border')}; }}")
        from modules.ads1018.ads1018_driver import ChannelMode as _CM
        for grp in self._ch_radio_groups:
            btn_v = grp.button(_CM.VOLTAGE)
            btn_i = grp.button(_CM.CURRENT)
            if btn_v:
                btn_v.setStyleSheet(vi_base + v_checked)
            if btn_i:
                btn_i.setStyleSheet(vi_base + i_checked)
        # Console views
        for w in self.findChildren(QTextEdit, "themedConsole"):
            w.setStyleSheet(
                f"QTextEdit {{ background: {tm.color('ads_config_bg')}; color: {tm.color('ads_config_text')};"
                f" border: 1px solid {tm.color('ads_config_border')}; border-radius: 4px;"
                f" font-family: 'Consolas', monospace; font-size: 11px; }}"
            )

    # ── UI Builders ──────────────────────────────────────────────────

    def _create_gpio_panel(self) -> QGroupBox:
        group = QGroupBox("GPIO Hold (D4-D7)")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        layout.setSpacing(16)

        self._hold_btns = {}
        self._hold_leds = {}
        
        for bit in range(4, 8):
            pair_layout = QHBoxLayout()
            pair_layout.setSpacing(6)
            
            led = QLabel()
            led.setFixedSize(14, 14)
            led.setObjectName("holdLed")
            self._hold_leds[bit] = led
            pair_layout.addWidget(led)
            
            btn = QPushButton(f"D{bit}: OFF")
            btn.setCheckable(True)
            btn.setMinimumWidth(60)
            btn.setMinimumHeight(24)
            btn.setObjectName("holdBtn")
            btn.toggled.connect(lambda checked, b=bit: self._on_io_hold_toggled(b, checked))
            self._hold_btns[bit] = btn
            pair_layout.addWidget(btn)
            
            layout.addLayout(pair_layout)

        layout.addStretch()
        return group

    def _create_control_panel(self) -> QGroupBox:
        group = QGroupBox("ADC Settings")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        grid = QGridLayout()
        grid.setSpacing(6)
        
        # Row 0: PGA
        grid.addWidget(QLabel("PGA:"), 0, 0)
        self._pga_combo = QComboBox()
        for k, v in sorted(PGA.LABELS.items()):
            self._pga_combo.addItem(v, k)
        self._pga_combo.setCurrentIndex(1)
        grid.addWidget(self._pga_combo, 0, 1)

        # Row 1: Data Rate
        grid.addWidget(QLabel("Data Rate:"), 1, 0)
        self._rate_combo = QComboBox()
        for k, v in sorted(DataRate.LABELS.items()):
            self._rate_combo.addItem(v, k)
        self._rate_combo.setCurrentIndex(6)
        grid.addWidget(self._rate_combo, 1, 1)

        # Row 2: Operating Mode
        grid.addWidget(QLabel("Op. Mode:"), 2, 0)
        self._op_mode_combo = QComboBox()
        self._op_mode_combo.addItem("Continuous", 1)  # maps to 0 in config reg later (or handled by driver)
        self._op_mode_combo.addItem("Single-shot", 0) # maps to 1
        self._op_mode_combo.setCurrentIndex(1)
        grid.addWidget(self._op_mode_combo, 2, 1)

        # Row 3: Sensor
        grid.addWidget(QLabel("Sensor:"), 3, 0)
        self._ts_mode_combo = QComboBox()
        self._ts_mode_combo.addItem("ADC", 0)
        self._ts_mode_combo.addItem("Temperature", 1)
        self._ts_mode_combo.setCurrentIndex(0)
        grid.addWidget(self._ts_mode_combo, 3, 1)

        # Row 4: CS Pin
        grid.addWidget(QLabel("CS Pin:"), 4, 0)
        self._cs_combo = QComboBox()
        self._cs_combo.addItem("ADBUS3", 0x08)
        self._cs_combo.addItem("ADBUS4", 0x10)
        self._cs_combo.addItem("ADBUS5", 0x20)
        self._cs_combo.addItem("ADBUS6", 0x40)
        self._cs_combo.addItem("ADBUS7", 0x80)
        grid.addWidget(self._cs_combo, 4, 1)

        # Row 5: Pull-up
        grid.addWidget(QLabel("Pull-up:"), 5, 0)
        self._pullup_cb = QCheckBox("Enable")
        self._pullup_cb.setChecked(True)
        grid.addWidget(self._pullup_cb, 5, 1)

        # Row 6: Window (s)
        grid.addWidget(QLabel("Window (s):"), 6, 0)
        from PySide6.QtWidgets import QSpinBox
        self._window_spinbox = QSpinBox()
        self._window_spinbox.setRange(10, 600)
        self._window_spinbox.setValue(60)
        self._window_spinbox.setSingleStep(10)
        self._window_spinbox.valueChanged.connect(lambda v: self._visualizer.set_window_seconds(v))
        grid.addWidget(self._window_spinbox, 6, 1)
        
        # Row 7: Auto Range
        grid.addWidget(QLabel("Auto Range:"), 7, 0)
        self._auto_range_btn = QPushButton("AUTO RANGE: ON")
        self._auto_range_btn.setCheckable(True)
        self._auto_range_btn.setChecked(True)
        self._auto_range_btn.setMinimumHeight(32)
        self._auto_range_btn.setObjectName("autoRangeBtn")
        self._auto_range_btn.toggled.connect(lambda checked: self._visualizer.set_auto_range(checked))
        grid.addWidget(self._auto_range_btn, 7, 1)

        # Connect to live-update register map (note: we do this after all are initialized)
        self._pga_combo.currentIndexChanged.connect(self._update_register_map)
        self._rate_combo.currentIndexChanged.connect(self._update_register_map)
        self._op_mode_combo.currentIndexChanged.connect(self._update_register_map)
        self._ts_mode_combo.currentIndexChanged.connect(self._update_register_map)
        self._ts_mode_combo.currentIndexChanged.connect(lambda _: self._update_units_display())
        self._pullup_cb.stateChanged.connect(self._update_register_map)

        layout.addLayout(grid)

        # Separator for Channels
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setObjectName("themeSep")
        layout.addWidget(sep1)
        
        ch_title = QLabel("Channel Config:")
        ch_title.setObjectName("sectionTitle")
        layout.addWidget(ch_title)

        # Channels
        # Styles set dynamically in _apply_theme()
        edit_style = ""
        lbl_style = ""

        ch_vbox = QVBoxLayout()
        ch_vbox.setSpacing(4)
        for i in range(4):
            ch_frame = QFrame()
            ch_frame.setObjectName("chFrame")
            ch_layout = QVBoxLayout(ch_frame)
            ch_layout.setContentsMargins(4, 4, 4, 4)
            ch_layout.setSpacing(4)
            
            top_h = QHBoxLayout()
            ch_lbl = QLabel(f"CH{i}")
            ch_lbl.setStyleSheet(f"color: {CH_COLORS[i]}; font-weight: bold; font-size: 11px; border: none;")  # data color
            top_h.addWidget(ch_lbl)
            
            btn_group = QButtonGroup(self)
            btn_v = QPushButton("V")
            btn_v.setCheckable(True)
            btn_v.setChecked(True)
            btn_v.setFixedSize(50, 20)
            btn_v.setStyleSheet("")  # styled in _apply_theme
            btn_i = QPushButton("I")
            btn_i.setCheckable(True)
            btn_i.setFixedSize(50, 20)
            btn_i.setStyleSheet("")  # styled in _apply_theme
            
            btn_group.addButton(btn_v, ChannelMode.VOLTAGE)
            btn_group.addButton(btn_i, ChannelMode.CURRENT)
            self._ch_radio_groups.append(btn_group)
            
            top_h.addWidget(btn_v)
            top_h.addWidget(btn_i)
            top_h.addStretch()
            ch_layout.addLayout(top_h)

            current_frame = QFrame()
            current_frame.setObjectName("currentFrame")
            cf_layout = QHBoxLayout(current_frame)
            cf_layout.setContentsMargins(0, 0, 0, 0)
            cf_layout.setSpacing(2)
            s_lbl = QLabel("R:")
            s_lbl.setObjectName("paramLbl")
            cf_layout.addWidget(s_lbl)
            shunt_edit = QLineEdit("0.02")
            shunt_edit.setObjectName("paramEdit")
            cf_layout.addWidget(shunt_edit)
            g_lbl = QLabel(" G:")
            g_lbl.setObjectName("paramLbl")
            cf_layout.addWidget(g_lbl)
            gain_edit = QLineEdit("100")
            gain_edit.setObjectName("paramEdit")
            cf_layout.addWidget(gain_edit)
            current_frame.setVisible(False)
            ch_layout.addWidget(current_frame)

            self._ch_shunt_edits.append(shunt_edit)
            self._ch_gain_edits.append(gain_edit)
            self._ch_current_frames.append(current_frame)
            btn_group.idToggled.connect(lambda id, checked, idx=i: self._on_mode_changed(idx, id) if checked else None)
            
            ch_vbox.addWidget(ch_frame)

        layout.addLayout(ch_vbox)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setObjectName("themeSep")
        layout.addWidget(sep2)

        # Start / Stop buttons
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Monitoring")
        self._start_btn.setMinimumHeight(28)
        self._start_btn.clicked.connect(self._on_start_clicked)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop Monitoring")
        self._stop_btn.setMinimumHeight(28)
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
        group = QGroupBox("Real-Time Monitoring")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 6, 6, 6)
        
        # Metrics row
        metrics_layout = QHBoxLayout()
        metrics_layout.setSpacing(20)

        def _make_metric(title: str, color: str) -> QLabel:
            container = QWidget()
            vl = QVBoxLayout(container)
            vl.setContentsMargins(8, 4, 8, 4)
            vl.setSpacing(2)
            t = QLabel(title)
            t.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.addWidget(t)
            val = QLabel("-")
            val.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold; font-family: 'Consolas', monospace;")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.addWidget(val)
            container.setObjectName("metricContainer")
            metrics_layout.addWidget(container)
            return val

        for i in range(4):
            val_lbl = _make_metric(f"CH{i}", CH_COLORS[i])
            self._ch_value_labels.append(val_lbl)

        layout.addLayout(metrics_layout)

        self._visualizer = ADCVisualizer(show_toolbar=False)
        layout.addWidget(self._visualizer, 1)

        return group

    def _create_bottom_panel(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.setMaximumHeight(200)

        # Register map tab
        reg_tab = QWidget()
        reg_layout = QVBoxLayout(reg_tab)
        reg_layout.setContentsMargins(6, 6, 6, 6)
        
        self._reg_table = QTableWidget(1, 4)
        self._reg_table.setHorizontalHeaderLabels(["Addr", "Name", "Desc", "Value (Hex)"])
        self._reg_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._reg_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._reg_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._reg_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._reg_table.setColumnWidth(0, 80)
        self._reg_table.setColumnWidth(1, 180)
        self._reg_table.setColumnWidth(3, 110)
        self._reg_table.setAlternatingRowColors(True)
        self._reg_table.verticalHeader().setVisible(False)
        
        addr_item = QTableWidgetItem("0x01")
        addr_item.setFlags(addr_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        addr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._reg_table.setItem(0, 0, addr_item)
        
        name_item = QTableWidgetItem("CONFIG")
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._reg_table.setItem(0, 1, name_item)
        
        desc_item = QTableWidgetItem("Configuration Register (OS, MUX, PGA, MODE, DR, TS_MODE, PULL_UP, NOP)")
        desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._reg_table.setItem(0, 2, desc_item)
        
        val_item = QTableWidgetItem("-")
        val_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        val_item.setFont(QFont("Consolas", 10))
        self._reg_table.setItem(0, 3, val_item)
        
        reg_layout.addWidget(self._reg_table)
        tabs.addTab(reg_tab, "Register Map")

        # Config Summary tab
        summary_tab = QWidget()
        summary_layout = QVBoxLayout(summary_tab)
        summary_layout.setContentsMargins(6, 6, 6, 6)
        self._config_summary = QTextEdit()
        self._config_summary.setReadOnly(True)
        self._config_summary.setObjectName("themedConsole")
        summary_layout.addWidget(self._config_summary)
        tabs.addTab(summary_tab, "Config Summary")

        # SPI Log tab
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(6, 6, 6, 6)
        
        clear_btn = QPushButton("Clear Log")
        clear_btn.setFixedWidth(100)
        clear_btn.clicked.connect(lambda: self._log_view.clear())
        log_layout.addWidget(clear_btn, 0, Qt.AlignmentFlag.AlignRight)
        
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setObjectName("themedConsole")
        log_layout.addWidget(self._log_view)
        tabs.addTab(log_tab, "SPI Log")

        return tabs

    def _update_register_map(self) -> None:
        """Update register map table dynamically based on UI selections."""
        if not hasattr(self, '_pga_combo'):
            return

        pga = self._pga_combo.currentData() or 0
        rate = self._rate_combo.currentData() or 0
        is_continuous = self._op_mode_combo.currentData() == 1
        is_temp = self._ts_mode_combo.currentData() == 1
        pullup = self._pullup_cb.isChecked()
        
        # 16-bit Config Register mapping
        # Bit 15: OS (1 for start single-shot)
        # Bits 14:12: MUX (010 for default AIN0-GND, although we change this per channel during read)
        # Bits 11:9: PGA
        # Bit 8: MODE (0=Continuous, 1=Single-shot)
        # Bits 7:5: DR
        # Bit 4: TS_MODE
        # Bit 3: PULL_UP_EN
        # Bits 2:1: NOP (01 = valid data)
        # Bit 0: Reserved (1)
        
        val = 0x8000 # OS=1
        val |= (4 << 12) # MUX=4 (AIN0-GND) as idle representation
        val |= (pga << 9)
        val |= ((0 if is_continuous else 1) << 8)
        val |= (rate << 5)
        val |= ((1 if is_temp else 0) << 4)
        val |= ((1 if pullup else 0) << 3)
        val |= (1 << 1) # NOP = 01
        val |= 1 # Reserved

        val_item = self._reg_table.item(0, 3)
        if val_item:
            val_item.setText(f"0x{val:04X}")

    def _refresh_register_map(self) -> None:
        """User clicked refresh register map button. Emulates reading."""
        self._append_log("[INFO] Refreshed Register Map.")
        self._update_register_map()

    # ── BaseModule Overrides ──────────────────────────────────────────

    def on_device_connected(self) -> None:
        self._start_btn.setEnabled(True)
        self._set_hold_controls_enabled(True)
        self._append_log("[INFO] FTDI device connected.")

    def on_device_disconnected(self) -> None:
        self.stop_communication()
        self._start_btn.setEnabled(False)
        self._set_hold_controls_enabled(False)
        self._append_log("[INFO] FTDI device disconnected.")

    def on_tab_activated(self) -> None:
        connected = self._ftdi.is_connected
        self._set_hold_controls_enabled(connected)
        if connected:
            self._ftdi.set_protocol_mode("SPI")
            self._append_log("[INFO] Protocol mode: SPI")
            self._apply_io_hold()
            self._refresh_hold_status(sync_buttons=True)

    def on_tab_deactivated(self) -> None:
        if self._running:
            self.stop_communication()

    def on_channel_changed(self, channel: str) -> None:
        if self._running:
            self.stop_communication()

    def start_communication(self) -> None:
        if self._running:
            return
        if not self._ftdi.is_connected:
            self._append_log("[WARN] FTDI not connected.")
            return

        # Gather config from UI
        self._config.pga = self._pga_combo.currentData()
        self._config.data_rate = self._rate_combo.currentData()
        self._config.continuous = (self._op_mode_combo.currentData() == 1)
        self._config.ts_mode = (self._ts_mode_combo.currentData() == 1)
        self._config.pullup_enable = self._pullup_cb.isChecked()
        self._config.cs_pin = self._cs_combo.currentData()

        channels = []
        for i in range(4):
            mode = self._ch_radio_groups[i].checkedId()
            try:
                shunt = float(self._ch_shunt_edits[i].text())
            except ValueError:
                shunt = 0.02
            try:
                gain = float(self._ch_gain_edits[i].text())
            except ValueError:
                gain = 100.0
            channels.append(ChannelConfig(
                mode=mode, shunt_resistor=shunt, gain=gain, enabled=True,
            ))
        self._config.channels = channels

        # Create worker
        self._worker = ADS1018Worker(self._ftdi)
        self._worker.configure(
            pga=self._config.pga,
            data_rate=self._config.data_rate,
            pullup=self._config.pullup_enable,
            continuous=self._config.continuous,
            ts_mode=self._config.ts_mode,
            cs_pin=self._config.cs_pin,
            channel_configs=channels,
        )

        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.measurement.connect(self._on_measurement)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.log_message.connect(self._append_log)

        self._worker_thread.start()
        self._running = True
        self._visualizer.clear()

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._pga_combo.setEnabled(False)
        self._rate_combo.setEnabled(False)
        self._cs_combo.setEnabled(False)
        self._pullup_cb.setEnabled(False)
        for edit in self._ch_shunt_edits: edit.setEnabled(False)
        for edit in self._ch_gain_edits: edit.setEnabled(False)
        for group in self._ch_radio_groups:
            for btn in group.buttons():
                btn.setEnabled(False)

        self._append_log("[INFO] Monitoring started.")

        # Update Config Summary tab
        summary = f"PGA: {self._pga_combo.currentText()}\n"
        summary += f"Data Rate: {self._rate_combo.currentText()}\n"
        summary += f"Op. Mode: {self._op_mode_combo.currentText()}\n"
        summary += f"Sensor: {self._ts_mode_combo.currentText()}\n"
        summary += f"Pull-up: {'Enabled' if self._config.pullup_enable else 'Disabled'}\n"
        summary += f"CS Pin: {self._cs_combo.currentText()}\n\n"
        for i, ch in enumerate(channels):
            if ch.mode == ChannelMode.VOLTAGE:
                mode_str = "VOLTAGE"
                ch_conf = ""
            else:
                mode_str = "CURRENT"
                ch_conf = f" | R={ch.shunt_resistor}Ω, G={ch.gain}"
            summary += f"CH{i}: {mode_str}{ch_conf}\n"
        if hasattr(self, '_config_summary'):
            self._config_summary.setText(summary)

    def stop_communication(self) -> None:
        if not self._running:
            return
        self._running = False

        if self._worker:
            self._worker.stop()
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait(2000)
            self._worker_thread = None
        self._worker = None

        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._pga_combo.setEnabled(True)
        self._rate_combo.setEnabled(True)
        self._cs_combo.setEnabled(True)
        self._pullup_cb.setEnabled(True)
        for edit in self._ch_shunt_edits: edit.setEnabled(True)
        for edit in self._ch_gain_edits: edit.setEnabled(True)
        for group in self._ch_radio_groups:
            for btn in group.buttons():
                btn.setEnabled(True)

        self._append_log("[INFO] Monitoring stopped.")

    def update_data(self) -> None:
        pass

    # ── Slots ─────────────────────────────────────────────────────────

    @Slot()
    def _on_start_clicked(self) -> None:
        if self._running:
            self.stop_communication()
        else:
            self.start_communication()

    @Slot(object)
    def _on_measurement(self, m: ADS1018Measurement) -> None:
        """Receive worker measurement → update visualizer + labels."""
        self._visualizer.add_measurement(m.timestamp, m.values, m.units)

        for i in range(4):
            val = m.values[i]
            unit = m.units[i]
            if val is not None:
                self._ch_value_labels[i].setText(f"{val:.3f} {unit}")
            else:
                self._ch_value_labels[i].setText("---")

    @Slot(str)
    def _on_worker_error(self, msg: str) -> None:
        self._append_log(f"[ERROR] {msg}")
        self.status_message.emit(f"ADS1018: {msg}")

    def _update_units_display(self) -> None:
        """Update graph units explicitly based on Sensor mode and V/I toggles."""
        if not hasattr(self, '_visualizer') or not hasattr(self, '_ts_mode_combo'):
            return
        is_temp = self._ts_mode_combo.currentData() == 1
        for idx in range(4):
            if idx < len(self._ch_radio_groups):
                mode_id = self._ch_radio_groups[idx].checkedId()
                if is_temp:
                    unit = "°C"
                else:
                    unit = "mA" if mode_id == ChannelMode.CURRENT else "V"
                if hasattr(self._visualizer, 'set_channel_unit'):
                    self._visualizer.set_channel_unit(idx, unit)

    def _on_mode_changed(self, ch_idx: int, mode_id: int) -> None:
        """Show/hide current-mode fields and update plot axis."""
        show_current = (mode_id == ChannelMode.CURRENT)
        self._ch_current_frames[ch_idx].setVisible(show_current)
        self._update_units_display()

    def _on_io_hold_toggled(self, bit: int, checked: bool) -> None:
        btn = self._hold_btns.get(bit)
        if btn:
            btn.setText(f"D{bit}: {'ON' if checked else 'OFF'}")
        if checked:
            self._io_hold_value |= (1 << bit)
        else:
            self._io_hold_value &= ~(1 << bit)
        if not self._running:
            self._apply_io_hold()

    def _set_hold_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable GPIO hold buttons and reset LEDs when disabled."""
        for btn in self._hold_btns.values():
            btn.setEnabled(enabled)
        if not enabled:
            tm = ThemeManager.instance()
            for led in self._hold_leds.values():
                led.setStyleSheet(
                    f"background: {tm.color('ads_led_off_bg')}; border-radius: 7px;"
                    f" border: 1px solid {tm.color('ads_led_off_border')};"
                )

    def _apply_io_hold(self) -> None:
        if not self._ftdi.is_connected:
            return
        # Delegate GPIO mapping to ftdi_manager using masked write
        self._ftdi.set_gpio_masked(self._io_hold_mask, self._io_hold_value)
        self._refresh_hold_status()

    def _refresh_hold_status(self, sync_buttons: bool = False) -> None:
        """Refresh inline GPIO LEDs from actual hardware state."""
        if not hasattr(self, "_hold_leds"):
            return
        if not self._ftdi.is_connected:
            return
            
        value = self._ftdi.read_gpio_low()
        if value is None:
            return
            
        for bit in range(4, 8):
            high = bool(value & (1 << bit))
            
            led = self._hold_leds.get(bit)
            if led:
                if high:
                    # glowing green LED
                    led.setStyleSheet("background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #a0e0a8, stop:1 #408858); border-radius: 7px; border: 1px solid #2a5a38;")  # semantic on
                else:
                    tm = ThemeManager.instance()
                    led.setStyleSheet(f"background: {tm.color('ads_led_off_bg')}; border-radius: 7px; border: 1px solid {tm.color('ads_led_off_border')};")
                    
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

    # ── Helpers ───────────────────────────────────────────────────────

    def _append_log(self, message: str) -> None:
        if not hasattr(self, "_log_view"):
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
        self._log_view.append(f'<span style="color:{color}">{message}</span>')
        sb = self._log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
