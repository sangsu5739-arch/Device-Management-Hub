"""
FTDI Hardware Verifier module - Universal Device Studio plugin

Validates connected FTDI chip hardware resources,
Tests each channel/pin function in real time.

Layout:
  Left: Control Center (chip select, mode, protocol tests, GPIO control)
  Right: Interactive Pinout View (CubeIDE style)
  Bottom: Communication Log
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Slot, QThread, QTimer
from PySide6.QtGui import QFont, QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QComboBox, QSpinBox,
    QSplitter, QTabWidget, QFrame, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QCheckBox, QFileDialog, QStyle, QAbstractItemView,
)

from core.ftdi_manager import FtdiManager
from modules.base_module import BaseModule
from modules.ftdi_verifier.ftdi_chip_specs import (
    CHIP_SPECS, ChipSpec, PinSpec, PinFunction,
    ProtocolMode, ChannelSpec, PIN_COLORS, PROTOCOL_COLORS,
    get_chip_spec, get_channel_protocols,
)
from modules.ftdi_verifier.pinout_widget import PinoutWidget
from modules.ftdi_verifier.pinmap_controller import PinmapController
from modules.ftdi_verifier.gpio_controller import GpioController
from modules.ftdi_verifier.verifier_worker import (
    VerifierWorker, I2CScanResult, ProtocolTestResult,
)


class FtdiVerifierModule(BaseModule):
    """FTDI Hardware Verifier device module

    Provides chip pinmap view, I2C/SPI ACK check, and GPIO control.
    """

    MODULE_NAME = "FTDI Verifier"
    MODULE_ICON = ""
    MODULE_VERSION = "1.0.0"

    def __init__(self, ftdi_manager: FtdiManager, parent: Optional[QWidget] = None) -> None:
        self._worker: Optional[VerifierWorker] = None
        self._worker_thread: Optional[QThread] = None
        self._current_chip: Optional[ChipSpec] = None
        self._current_channel: str = "A"
        self._display_channel: str = "A"
        self._gpio_states: dict[int, bool] = {}
        self._uart_serial = None
        self._uart_read_timer = None
        self._last_proto_mode: str = "I2C"
        self._last_non_uart_mode: str = "I2C"
        self._uart_prev_connected: bool = False
        self._uart_prev_serial: str = ""
        self._uart_prev_channel: str = "A"
        self._uart_restore_in_progress: bool = False
        self._suppress_protocol_sync: bool = False
        self._suppress_protocol_until_ts: float = 0.0
        self._gpio_poll_pin: int = -1
        self._gpio_poll_blink = None
        self._uart_poll_skip_count: int = 0
        self._gpio_poll_was_active: bool = False
        self._gpio_poll_restore_interval: int = 500
        self._bitbang_mask: int = 0xFF
        self._bitbang_btns: list[QPushButton] = []
        self._last_i2c_scan_t0: float | None = None
        self._last_i2c_scan_range: tuple[int, int] = (0x08, 0x77)
        self._gpio_poll_interval_value: int = 500
        self._poll_blink_state: bool = False
        self._gpio_backend: str = "BITBANG"
        self._pinmap = PinmapController(self)
        self._gpio = GpioController(self)
        super().__init__(ftdi_manager, parent)

    # -- BaseModule implementation --

    def init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Top: FTDI info panel (linked to connection tab)
        layout.addWidget(self._create_device_info_panel())

        # Main area (left/right + bottom)
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        v_splitter.setHandleWidth(3)

        # Left (control) + right (pinmap)
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.setHandleWidth(3)
        control_panel = self._create_control_panel()
        pinout_panel = self._create_pinout_panel()
        control_panel.setMinimumWidth(260)
        pinout_panel.setMinimumWidth(520)
        h_splitter.addWidget(control_panel)
        h_splitter.addWidget(pinout_panel)
        h_splitter.setStretchFactor(0, 2)
        h_splitter.setStretchFactor(1, 5)
        h_splitter.setSizes([260, 520])

        v_splitter.addWidget(h_splitter)
        v_splitter.addWidget(self._create_log_panel())
        v_splitter.setStretchFactor(0, 4)
        v_splitter.setStretchFactor(1, 2)

        layout.addWidget(v_splitter, 1)

        # Load default chip
        self._apply_chip_and_channel("FT232H", "A")
        QTimer.singleShot(0, lambda: h_splitter.setSizes([260, 520]))
        if self._uart_read_timer is None:
            self._uart_read_timer = QTimer(self)
            self._uart_read_timer.setInterval(50)
            self._uart_read_timer.timeout.connect(self._poll_uart)
        if self._gpio_poll_blink is None:
            self._gpio_poll_blink = QTimer(self)
            self._gpio_poll_blink.setInterval(450)
            self._gpio_poll_blink.timeout.connect(self._on_gpio_poll_blink)

    def on_device_connected(self) -> None:
        # Skip if UART mode switching triggered this signal.
        if self.is_uart_switching:
            return
        self._i2c_scan_btn.setEnabled(True)
        self._i2c_test_btn.setEnabled(True)
        self._spi_loopback_btn.setEnabled(True)
        self._spi_id_btn.setEnabled(True)
        self._set_bitbang_controls_enabled(True)
        self._apply_bitbang_mask(self._bitbang_mask, push=True)
        self._gpio_poll_btn.setEnabled(True)
        info = self._ftdi.get_device_info()
        chip = info.get("device_type", "FT232H")
        ch = info.get("channel", "A")
        self._apply_chip_and_channel(chip, ch)
        self._update_protocol_availability()
        self.status_message.emit("FTDI Verifier: device connected")

    def on_device_disconnected(self) -> None:
        # Skip if UART mode switching triggered this signal.
        if self.is_uart_switching:
            return
        # If UART (VCP) is active, avoid protocol/tabs reset.
        if self._uart_serial is not None:
            return
        self.stop_communication()
        self._i2c_scan_btn.setEnabled(False)
        self._i2c_test_btn.setEnabled(False)
        self._spi_loopback_btn.setEnabled(False)
        self._spi_id_btn.setEnabled(False)
        self._set_bitbang_controls_enabled(False)
        self._gpio_poll_btn.setEnabled(False)
        if hasattr(self, "_chip_label"):
            self._chip_label.setText("-")
        if hasattr(self, "_channel_label"):
            self._channel_label.setText("-")
        self.status_message.emit("FTDI Verifier: device disconnected")

    def on_channel_changed(self, channel: str) -> None:
        if self.is_uart_switching:
            return
        info = self._ftdi.get_device_info()
        chip = info.get("device_type", "FT232H")
        self._apply_chip_and_channel(chip, channel)

    def on_tab_activated(self) -> None:
        super().on_tab_activated()
        if hasattr(self, "_proto_mode_combo"):
            mode = self._last_proto_mode or self._proto_mode_combo.currentText() or "I2C"
            self._proto_mode_combo.blockSignals(True)
            self._proto_mode_combo.setCurrentText(mode)
            self._proto_mode_combo.blockSignals(False)
            self._apply_protocol_mode(mode)

    def on_tab_deactivated(self) -> None:
        super().on_tab_deactivated()
        if hasattr(self, "_proto_mode_combo"):
            self._last_proto_mode = self._proto_mode_combo.currentText()
        # If UART is open, close and restore FTDI connection on tab leave.
        # _close_uart already calls set_vcp_mode(False), so no separate call needed.
        if self._uart_serial is not None:
            self._append_log("[UART] Auto-closed (left Verifier tab)")
            self.status_message.emit("UART auto-closed (left Verifier tab)")
            self._close_uart()
        else:
            # Only clear VCP indicator if UART was not open (no _close_uart to do it).
            main_win = self.window()
            if hasattr(main_win, "set_vcp_mode"):
                main_win.set_vcp_mode(False)

    def _show_mpsse_warning(self, channel: str) -> None:
        # FTDI Verifier mixes MPSSE and Bitbang intentionally - suppress popup.
        pass

    def start_communication(self) -> None:
        pass  # GPIO polling starts from a separate button

    def stop_communication(self) -> None:
        self._stop_worker()
        # On global stop (disconnect or channel switch), do not restore FTDI from UART.
        self._close_uart(restore=False)

    def update_data(self) -> None:
        pass

    # -- UI builders --

    def _create_device_info_panel(self) -> QGroupBox:
        """Top: FTDI connection info (linked to connection tab)."""
        group = QGroupBox("FTDI Device Info")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(10, 6, 10, 6)

        layout.addWidget(QLabel("Chip model:"))
        self._chip_label = QLabel("-")
        self._chip_label.setStyleSheet("color: #c8d2f0; font-weight: 600;")
        layout.addWidget(self._chip_label)

        layout.addSpacing(20)
        layout.addWidget(QLabel("Channel:"))
        self._channel_label = QLabel("-")
        self._channel_label.setStyleSheet("color: #c8d2f0; font-weight: 600;")
        layout.addWidget(self._channel_label)

        layout.addSpacing(20)
        self._chip_info_label = QLabel("")
        self._chip_info_label.setStyleSheet("color: #88a0cc; font-style: italic;")
        layout.addWidget(self._chip_info_label)

        layout.addStretch()
        return group

    def _create_control_panel(self) -> QWidget:
        """Left control panel."""
        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(6)

        # -- Mode selection (exclusive) --
        mode_group = QGroupBox("Protocol Mode")
        mode_group.setFont(QFont("Malgun Gothic", 10))
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setSpacing(4)

        self._proto_mode_combo = QComboBox()
        self._proto_mode_combo.setFont(QFont("Malgun Gothic", 10))
        self._proto_mode_combo.addItems(["I2C", "SPI", "JTAG", "UART", "GPIO"])
        self._proto_mode_combo.currentTextChanged.connect(self._on_protocol_mode_changed)
        mode_layout.addWidget(self._proto_mode_combo)

        self._mode_desc_label = QLabel("")
        self._mode_desc_label.setFont(QFont("Malgun Gothic", 9))
        self._mode_desc_label.setWordWrap(True)
        self._mode_desc_label.setStyleSheet("color: #8899bb; font-size: 11px;")
        mode_layout.addWidget(self._mode_desc_label)

        main_layout.addWidget(mode_group)

        # -- Protocol tabs --
        self._proto_tabs = QTabWidget()

        # I2C Test
        self._i2c_group = QGroupBox("I2C Test")
        i2c_layout = QVBoxLayout(self._i2c_group)
        i2c_layout.setSpacing(3)
        i2c_layout.setContentsMargins(6, 4, 6, 4)

        self._i2c_scan_btn = QPushButton("I2C Bus Scan")
        self._i2c_scan_btn.setEnabled(False)
        self._i2c_scan_btn.setMinimumHeight(30)
        self._i2c_scan_btn.setStyleSheet(
            "QPushButton { background: #2a2510; color: #d4a84b; font-weight: 700; border-radius: 6px; "
            "border: 1px solid #5a4820; letter-spacing: 0.4px; }"
            "QPushButton:hover { background: #342e18; color: #e8c06a; border-color: #7a6030; }"
            "QPushButton:pressed { background: #1e1b0c; border-color: #8a7040; }"
            "QPushButton:disabled { background: #1e2028; color: #4a5068; border: 1px solid #2a2e3a; }"
        )
        self._i2c_scan_btn.clicked.connect(self._on_i2c_scan)
        i2c_layout.addWidget(self._i2c_scan_btn)

        addr_row = QHBoxLayout()
        addr_row.setSpacing(6)
        addr_row.addWidget(QLabel("Scanned address:"))
        self._i2c_addr_combo = QComboBox()
        self._i2c_addr_combo.setEditable(True)
        self._i2c_addr_combo.setMinimumWidth(100)
        self._i2c_addr_combo.addItem("0x40")
        addr_row.addWidget(self._i2c_addr_combo)
        addr_row.addStretch()
        i2c_layout.addLayout(addr_row)

        ack_row = QHBoxLayout()
        ack_row.setSpacing(6)
        self._i2c_test_btn = QPushButton("ACK TEST")
        self._i2c_test_btn.setEnabled(False)
        self._i2c_test_btn.setMinimumHeight(30)
        self._i2c_test_btn.setMinimumWidth(110)
        self._i2c_test_btn.setStyleSheet(
            "QPushButton { background: #2a2510; color: #d4a84b; font-weight: 700; border-radius: 6px; "
            "border: 1px solid #5a4820; }"
            "QPushButton:hover { background: #342e18; color: #e8c06a; border-color: #7a6030; }"
            "QPushButton:pressed { background: #1e1b0c; border-color: #8a7040; }"
            "QPushButton:disabled { background: #1e2028; color: #4a5068; border: 1px solid #2a2e3a; }"
        )
        self._i2c_test_btn.clicked.connect(self._on_i2c_test)
        ack_row.addWidget(self._i2c_test_btn)

        self._i2c_ack_led = QLabel("  ACK: N/A")
        self._i2c_ack_led.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._i2c_ack_led.setToolTip("I2C ACK Status")
        self._i2c_ack_led.setStyleSheet(
            "background: #6f7a8e; color: #ffffff; border-radius: 8px; padding: 4px 10px;"
            "font-weight: 800; letter-spacing: 0.6px; font-size: 12px;"
        )
        self._i2c_ack_led.setFixedHeight(30)
        ack_row.addWidget(self._i2c_ack_led, 1)
        i2c_layout.addLayout(ack_row)

        self._i2c_scan_start = 0x08
        self._i2c_scan_end = 0x77

        # Scan result table
        self._i2c_result_table = QTableWidget(0, 2)
        self._i2c_result_table.setHorizontalHeaderLabels(["Addr", "Status"])
        self._i2c_result_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self._i2c_result_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._i2c_result_table.setColumnWidth(0, 60)
        self._i2c_result_table.verticalHeader().setDefaultSectionSize(24)
        self._i2c_result_table.verticalHeader().setVisible(False)
        self._i2c_result_table.setMaximumHeight(170)
        i2c_layout.addWidget(self._i2c_result_table)

        # Scan history table
        self._i2c_history_table = QTableWidget(0, 4)
        self._i2c_history_table.setHorizontalHeaderLabels(["Time", "Scan Range", "Found", "Duration"])
        self._i2c_history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._i2c_history_table.verticalHeader().setVisible(False)
        self._i2c_history_table.setMaximumHeight(130)
        i2c_layout.addWidget(self._i2c_history_table)

        self._proto_tabs.addTab(self._i2c_group, "I2C")

        # SPI Control (GUI only)
        self._spi_group = QGroupBox("SPI Control")
        spi_layout = QVBoxLayout(self._spi_group)
        spi_layout.setSpacing(6)
        spi_layout.setContentsMargins(8, 8, 8, 8)

        # -- SPI Configuration --
        cfg_panel = QGroupBox("Configuration")
        cfg_layout = QGridLayout(cfg_panel)
        cfg_layout.setSpacing(6)
        cfg_layout.setContentsMargins(8, 8, 8, 8)

        cfg_layout.addWidget(QLabel("SPI Mode:"), 0, 0)
        self._spi_mode_combo = QComboBox()
        self._spi_mode_combo.addItems([
            "Mode 0 (CPOL=0, CPHA=0)",
            "Mode 1 (CPOL=0, CPHA=1)",
            "Mode 2 (CPOL=1, CPHA=0)",
            "Mode 3 (CPOL=1, CPHA=1)",
        ])
        self._spi_mode_combo.setCurrentIndex(0)
        cfg_layout.addWidget(self._spi_mode_combo, 0, 1, 1, 2)

        self._spi_waveform = QLabel()
        self._spi_waveform.setStyleSheet(
            "background: #0e1018; color: #7fd9ff; border: 1px solid #223344; "
            "border-radius: 6px; padding: 10px 12px; font-family: Consolas; font-size: 11px;"
        )
        self._spi_waveform.setMinimumHeight(160)
        cfg_layout.addWidget(self._spi_waveform, 1, 0, 1, 3)
        self._spi_mode_combo.currentIndexChanged.connect(self._update_spi_waveform)
        self._update_spi_waveform(0)

        cfg_layout.addWidget(QLabel("Clock:"), 2, 0)
        self._spi_clock_combo = QComboBox()
        self._spi_clock_combo.addItems([
            "100 kHz", "250 kHz", "500 kHz", "1 MHz",
            "2 MHz", "4 MHz", "8 MHz", "12 MHz",
        ])
        self._spi_clock_combo.setCurrentText("1 MHz")
        cfg_layout.addWidget(self._spi_clock_combo, 2, 1, 1, 2)

        spi_layout.addWidget(cfg_panel)

        # -- Loopback Test --
        _SPI_BTN_STYLE = (
            "QPushButton { background: #18242b; color: #b5d7ff; border-radius: 6px; "
            "border: 1px solid #2a4a5a; font-weight: 700; }"
            "QPushButton:hover { background: #1f313a; }"
            "QPushButton:disabled { background: #1e2028; color: #4a5068; border: 1px solid #2a2e3a; }"
        )
        _SPI_RESULT_IDLE = (
            "background: #6f7a8e; color: #ffffff; border-radius: 8px; padding: 4px 10px;"
            "font-weight: 800; letter-spacing: 0.6px; font-size: 12px;"
        )

        lb_group = QGroupBox("Loopback Test")
        lb_layout = QVBoxLayout(lb_group)
        lb_layout.setSpacing(6)
        lb_layout.setContentsMargins(8, 8, 8, 8)
        lb_row = QHBoxLayout()
        lb_row.setSpacing(8)
        self._spi_loopback_btn = QPushButton("\u25b6  Loopback Test")
        self._spi_loopback_btn.setEnabled(False)
        self._spi_loopback_btn.setMinimumHeight(30)
        self._spi_loopback_btn.setStyleSheet(_SPI_BTN_STYLE)
        self._spi_loopback_btn.clicked.connect(self._on_spi_loopback)
        lb_row.addWidget(self._spi_loopback_btn)
        self._spi_loopback_result = QLabel("  Idle")
        self._spi_loopback_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spi_loopback_result.setFixedHeight(30)
        self._spi_loopback_result.setStyleSheet(_SPI_RESULT_IDLE)
        lb_row.addWidget(self._spi_loopback_result, 1)
        lb_layout.addLayout(lb_row)
        spi_layout.addWidget(lb_group)

        # -- Device ID Verification --
        id_group = QGroupBox("Device ID Verification")
        id_layout = QVBoxLayout(id_group)
        id_layout.setSpacing(6)
        id_layout.setContentsMargins(8, 8, 8, 8)

        id_cfg_row = QHBoxLayout()
        id_cfg_row.setSpacing(6)
        id_cfg_row.addWidget(QLabel("Register:"))
        self._spi_id_addr = QLineEdit()
        self._spi_id_addr.setText("0x9F")
        self._spi_id_addr.setPlaceholderText("e.g. 0x9F")
        self._spi_id_addr.setMinimumWidth(80)
        self._spi_id_addr.setStyleSheet(
            "background: #1a2030; color: #e7eef9; border: 1px solid #3a4560; "
            "border-radius: 4px; padding: 2px 6px;"
        )
        id_cfg_row.addWidget(self._spi_id_addr)
        id_cfg_row.addWidget(QLabel("Bytes:"))
        self._spi_id_bytes = QComboBox()
        self._spi_id_bytes.addItems(["1", "2", "3", "4"])
        self._spi_id_bytes.setCurrentText("2")
        self._spi_id_bytes.setFixedWidth(50)
        id_cfg_row.addWidget(self._spi_id_bytes)
        id_layout.addLayout(id_cfg_row)

        id_expect_row = QHBoxLayout()
        id_expect_row.setSpacing(6)
        id_expect_row.addWidget(QLabel("Expected:"))
        self._spi_id_expect = QLineEdit()
        self._spi_id_expect.setPlaceholderText("e.g. 0xEF4018 (optional)")
        self._spi_id_expect.setMinimumHeight(28)
        self._spi_id_expect.setStyleSheet(
            "background: #1a2030; color: #e7eef9; border: 1px solid #3a4560; "
            "border-radius: 4px; padding: 2px 8px;"
        )
        id_expect_row.addWidget(self._spi_id_expect)
        id_layout.addLayout(id_expect_row)

        id_btn_row = QHBoxLayout()
        id_btn_row.setSpacing(8)
        self._spi_id_btn = QPushButton("\u25b6  Read ID")
        self._spi_id_btn.setEnabled(False)
        self._spi_id_btn.setMinimumHeight(30)
        self._spi_id_btn.setStyleSheet(_SPI_BTN_STYLE)
        self._spi_id_btn.clicked.connect(self._on_spi_read_id)
        id_btn_row.addWidget(self._spi_id_btn)
        self._spi_id_result = QLabel("  Idle")
        self._spi_id_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spi_id_result.setFixedHeight(30)
        self._spi_id_result.setStyleSheet(_SPI_RESULT_IDLE)
        id_btn_row.addWidget(self._spi_id_result, 1)
        id_layout.addLayout(id_btn_row)
        spi_layout.addWidget(id_group)

        spi_layout.addStretch()
        self._proto_tabs.addTab(self._spi_group, "SPI")

        # JTAG Test (GUI only)
        self._jtag_group = QGroupBox("JTAG Pattern Test")
        jtag_layout = QVBoxLayout(self._jtag_group)
        jtag_layout.setSpacing(4)
        jtag_layout.setContentsMargins(6, 4, 6, 4)
        jtag_layout.addWidget(QLabel("TDI/TDO Pattern Transmit & Verify"))
        self._jtag_test_btn = QPushButton("Run Pattern Test")
        self._jtag_test_btn.setEnabled(False)
        self._jtag_test_btn.setMinimumHeight(30)
        self._jtag_test_btn.setStyleSheet(
            "QPushButton { background: #2a2510; color: #d4a84b; font-weight: 700; border-radius: 6px; "
            "border: 1px solid #5a4820; }"
            "QPushButton:hover { background: #342e18; color: #e8c06a; border-color: #7a6030; }"
            "QPushButton:pressed { background: #1e1b0c; border-color: #8a7040; }"
            "QPushButton:disabled { background: #1e2028; color: #4a5068; border: 1px solid #2a2e3a; }"
        )
        jtag_layout.addWidget(self._jtag_test_btn)
        self._proto_tabs.addTab(self._jtag_group, "JTAG")

        # UART Test (GUI only)
        self._uart_group = QGroupBox("UART Test")
        uart_layout = QVBoxLayout(self._uart_group)
        uart_layout.setSpacing(6)
        uart_layout.setContentsMargins(8, 8, 8, 8)

        # -- Port, Baud, Data, Parity, Stop, Flow (Grid Layout) --
        cfg_layout = QGridLayout()
        cfg_layout.setSpacing(6)

        # Row 0: Port & Refresh, Baudrate
        cfg_layout.addWidget(QLabel("COM Port:"), 0, 0)
        port_hbox = QHBoxLayout()
        port_hbox.setSpacing(4)
        self._uart_port_combo = QComboBox()
        self._uart_port_combo.setSizePolicy(
            self._uart_port_combo.sizePolicy().horizontalPolicy(),
            self._uart_port_combo.sizePolicy().verticalPolicy()
        )
        port_hbox.addWidget(self._uart_port_combo, 1)
        self._uart_refresh_btn = QPushButton("\u21ba")
        self._uart_refresh_btn.setToolTip("Refresh port list")
        self._uart_refresh_btn.setFixedSize(28, 28)
        self._uart_refresh_btn.setStyleSheet(
            "QPushButton { background: #2a303b; color: #c8d2f0; border-radius: 5px; font-size: 14px; }"
            "QPushButton:hover { background: #3a4050; }"
        )
        self._uart_refresh_btn.clicked.connect(self._refresh_uart_ports)
        port_hbox.addWidget(self._uart_refresh_btn)
        cfg_layout.addLayout(port_hbox, 0, 1)

        cfg_layout.addWidget(QLabel("Baudrate:"), 0, 2)
        self._uart_baud_combo = QComboBox()
        self._uart_baud_combo.addItems(["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"])
        self._uart_baud_combo.setCurrentText("115200")
        cfg_layout.addWidget(self._uart_baud_combo, 0, 3)

        # Row 1: Data, Parity
        cfg_layout.addWidget(QLabel("Data:"), 1, 0)
        self._uart_data_bits = QComboBox()
        self._uart_data_bits.addItems(["7", "8"])
        self._uart_data_bits.setCurrentText("8")
        cfg_layout.addWidget(self._uart_data_bits, 1, 1)

        cfg_layout.addWidget(QLabel("Parity:"), 1, 2)
        self._uart_parity = QComboBox()
        self._uart_parity.addItems(["None", "Even", "Odd", "Mark", "Space"])
        cfg_layout.addWidget(self._uart_parity, 1, 3)

        # Row 2: Stop, Flow
        cfg_layout.addWidget(QLabel("Stop:"), 2, 0)
        self._uart_stop_bits = QComboBox()
        self._uart_stop_bits.addItems(["1", "1.5", "2"])
        cfg_layout.addWidget(self._uart_stop_bits, 2, 1)

        cfg_layout.addWidget(QLabel("Flow:"), 2, 2)
        self._uart_flow = QComboBox()
        self._uart_flow.addItems(["None", "RTS/CTS", "XON/XOFF"])
        cfg_layout.addWidget(self._uart_flow, 2, 3)

        uart_layout.addLayout(cfg_layout)

        # -- OPEN / CLOSE button (full width) --
        self._uart_open_btn = QPushButton("OPEN")
        self._uart_open_btn.setEnabled(False)
        self._uart_open_btn.setMinimumHeight(34)
        self._uart_open_btn.clicked.connect(self._on_uart_open_clicked)
        self._apply_uart_open_style(opened=False)
        uart_layout.addWidget(self._uart_open_btn)

        # -- Console toolbar (RX Format / options / Clear / Save) --
        console_toolbar = QHBoxLayout()
        console_toolbar.setSpacing(8)
        console_toolbar.addWidget(QLabel("RX:"))
        self._uart_rx_format = QComboBox()
        self._uart_rx_format.addItems(["ASCII", "HEX"])
        self._uart_rx_format.setFixedWidth(45)
        console_toolbar.addWidget(self._uart_rx_format)
        console_toolbar.addSpacing(6)
        self._uart_autoscroll = QCheckBox("AutoScroll")
        self._uart_autoscroll.setChecked(True)
        console_toolbar.addWidget(self._uart_autoscroll)
        self._uart_timestamp = QCheckBox("Timestamp")
        self._uart_timestamp.setChecked(False)
        self._uart_timestamp.toggled.connect(self._on_uart_timestamp_toggled)
        console_toolbar.addWidget(self._uart_timestamp)
        console_toolbar.addStretch()
        self._uart_clear_btn = QPushButton()
        self._uart_clear_btn.setToolTip("Clear console")
        self._uart_clear_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self._uart_clear_btn.setFixedSize(26, 26)
        self._uart_clear_btn.setFlat(True)
        self._uart_clear_btn.clicked.connect(self._on_uart_clear_clicked)
        console_toolbar.addWidget(self._uart_clear_btn)
        self._uart_save_btn = QPushButton()
        self._uart_save_btn.setToolTip("Save log")
        self._uart_save_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self._uart_save_btn.setFixedSize(26, 26)
        self._uart_save_btn.setFlat(True)
        self._uart_save_btn.clicked.connect(self._on_uart_save_clicked)
        console_toolbar.addWidget(self._uart_save_btn)
        uart_layout.addLayout(console_toolbar)

        # -- UART console --
        self._uart_console = QTableWidget(0, 3)
        self._uart_console.setHorizontalHeaderLabels(["Time", "Dir", "Data"])
        self._uart_console.verticalHeader().setVisible(False)
        self._uart_console.verticalHeader().setDefaultSectionSize(22)
        self._uart_console.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._uart_console.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self._uart_console.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._uart_console.setFont(QFont("Consolas", 10))
        self._uart_console.setMinimumHeight(230)
        self._uart_console.horizontalHeader().setStretchLastSection(True)
        self._uart_console.setColumnWidth(0, 72)
        self._uart_console.setColumnWidth(1, 52)
        self._uart_console.setColumnHidden(0, True)
        self._uart_console.setWordWrap(False)
        self._uart_console.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._uart_console.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._uart_console.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._uart_console.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._uart_console.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._uart_console.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._uart_console.setStyleSheet(
            "QTableWidget { background: #0b0f14; color: #e7eef9; border: 1px solid #273043; "
            "border-radius: 6px; gridline-color: transparent; }"
            "QHeaderView::section { background: #141a22; color: #708090; padding: 2px 6px;"
            " border: 0px; font-size: 10px; }"
            "QTableWidget::item { padding: 1px 4px; border-bottom: 1px solid #161c26; }"
            "QTableWidget::item:selected { background: #1e3050; }"
        )
        uart_layout.addWidget(self._uart_console)

        # -- Send row --
        send_row = QHBoxLayout()
        send_row.setSpacing(6)
        self._uart_input = QLineEdit()
        self._uart_input.setPlaceholderText("Send data...")
        self._uart_input.setMinimumHeight(32)
        self._uart_input.setStyleSheet(
            "background: #1a2030; color: #e7eef9; border: 1px solid #3a4560; "
            "border-radius: 4px; padding: 2px 8px;"
        )
        self._uart_input.returnPressed.connect(self._on_uart_send_clicked)
        send_row.addWidget(self._uart_input, 1)
        self._uart_crlf = QComboBox()
        self._uart_crlf.addItems(["No EOL", "CR", "LF", "CRLF"])
        self._uart_crlf.setCurrentText("CRLF")
        self._uart_crlf.setFixedWidth(60)
        send_row.addWidget(self._uart_crlf)
        self._uart_send_btn = QPushButton("SEND")
        self._uart_send_btn.setEnabled(False)
        self._uart_send_btn.setFixedWidth(76)
        self._uart_send_btn.setMinimumHeight(32)
        self._uart_send_btn.setStyleSheet(
            "QPushButton { background: #3a5a8a; color: #e7eef9; font-weight: 700; border-radius: 4px; }"
            "QPushButton:hover { background: #4a6a9a; }"
            "QPushButton:disabled { background: #2a303b; color: #6a7488; }"
        )
        self._uart_send_btn.clicked.connect(self._on_uart_send_clicked)
        send_row.addWidget(self._uart_send_btn)
        uart_layout.addLayout(send_row)
        self._proto_tabs.addTab(self._uart_group, "UART")

        # GPIO Control
        self._gpio_group = QGroupBox("GPIO Control")
        gpio_layout = QVBoxLayout(self._gpio_group)
        gpio_layout.setSpacing(4)

        poll_row = QHBoxLayout()
        poll_row.addWidget(QLabel("Polling interval (ms):"))
        self._gpio_poll_interval = QSpinBox()
        self._gpio_poll_interval.setRange(50, 10000)
        self._gpio_poll_interval.setValue(500)
        self._gpio_poll_interval.setSingleStep(100)
        self._gpio_poll_interval.setMinimumWidth(110)
        self._gpio_poll_interval.valueChanged.connect(self._on_gpio_poll_interval_changed)
        self._gpio_poll_interval_value = self._gpio_poll_interval.value()
        poll_row.addWidget(self._gpio_poll_interval)
        poll_row.addStretch()

        self._gpio_poll_btn = QPushButton("Start Polling")
        self._gpio_poll_btn.setCheckable(True)
        self._gpio_poll_btn.setEnabled(False)
        self._gpio_poll_btn.setMinimumHeight(32)
        self._gpio_poll_btn.setStyleSheet(
            "QPushButton { background: #1d2d3a; color: #70b8d0; font-weight: 700; border-radius: 6px; "
            "border: 1px solid #2a5068; }"
            "QPushButton:hover { background: #243548; color: #90d0e8; border-color: #3a6880; }"
            "QPushButton:checked { background: #1a2d20; color: #80c890; border: 1px solid #2a5a38; }"
            "QPushButton:checked:hover { background: #203828; color: #a0e0a8; border-color: #3a7048; }"
            "QPushButton:disabled { background: #1e2028; color: #4a5068; border: 1px solid #2a2e3a; }"
        )
        self._gpio_poll_btn.toggled.connect(self._on_gpio_poll_toggled)
        poll_row.addWidget(self._gpio_poll_btn)
        gpio_layout.addLayout(poll_row)

        # GPIO backend status
        self._gpio_backend_label = QLabel("GPIO Backend: BITBANG")
        self._gpio_backend_label.setStyleSheet(
            "color: #d4a84b; font-weight: 700; font-size: 11px;"
        )
        gpio_layout.addWidget(self._gpio_backend_label)

        self._gpio_poll_status = QLabel(" Global polling active")
        self._gpio_poll_status.setStyleSheet(
            "color: #80c890; font-weight: 800; font-size: 12px;"
        )
        self._gpio_poll_status.setVisible(False)
        gpio_layout.addWidget(self._gpio_poll_status)

        # Selected pin info
        self._pin_info_group = QGroupBox("Selected Pin")
        pin_info_layout = QGridLayout(self._pin_info_group)
        pin_info_layout.setSpacing(4)
        pin_info_layout.setHorizontalSpacing(6)
        pin_info_layout.setColumnMinimumWidth(0, 100)
        pin_info_layout.setColumnStretch(0, 0)
        pin_info_layout.setColumnStretch(1, 1)

        pin_info_layout.addWidget(QLabel("Pin:"), 0, 0)
        self._pin_name_label = QLabel("-")
        self._pin_name_label.setStyleSheet("font-weight: bold; color: #00d2ff;")
        pin_info_layout.addWidget(self._pin_name_label, 0, 1)

        pin_info_layout.addWidget(QLabel("Function:"), 1, 0)
        self._pin_func_label = QLabel("-")
        pin_info_layout.addWidget(self._pin_func_label, 1, 1)

        pin_info_layout.addWidget(QLabel("Channel:"), 2, 0)
        self._pin_ch_label = QLabel("-")
        pin_info_layout.addWidget(self._pin_ch_label, 2, 1)

        pin_info_layout.addWidget(QLabel("Desc:"), 3, 0)
        self._pin_desc_label = QLabel("-")
        self._pin_desc_label.setWordWrap(True)
        self._pin_desc_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        pin_info_layout.addWidget(self._pin_desc_label, 3, 1)

        # GPIO toggle buttons
        self._gpio_toggle_btn = QPushButton("GPIO: LOW")
        self._gpio_toggle_btn.setEnabled(False)
        self._gpio_toggle_btn.setCheckable(True)
        self._gpio_toggle_btn.setMinimumHeight(32)
        self._gpio_toggle_btn.setStyleSheet(
            "QPushButton { font-weight: bold; border-radius: 6px; "
            "background: #1d2d3a; color: #70b8d0; border: 1px solid #2a5068; }"
            "QPushButton:hover { background: #243548; color: #90d0e8; border-color: #3a6880; }"
            "QPushButton:checked { background: #1a2d20; color: #80c890; border: 1px solid #2a5a38; }"
            "QPushButton:checked:hover { background: #203828; color: #a0e0a8; border-color: #3a7048; }"
            "QPushButton:disabled { background: #1e2028; color: #4a5068; border: 1px solid #2a2e3a; }"
        )
        self._gpio_toggle_btn.toggled.connect(self._on_gpio_toggle)
        pin_info_layout.addWidget(self._gpio_toggle_btn, 4, 0, 1, 2)

        gpio_layout.addWidget(self._pin_info_group)

        # All-Pin Status Table
        self._gpio_table = QTableWidget(0, 5)
        self._gpio_table.setHorizontalHeaderLabels(
            ["Pin", "Name", "Mode", "Direction", "Level"]
        )
        self._gpio_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._gpio_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._gpio_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._gpio_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._gpio_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._gpio_table.setColumnWidth(0, 50)
        self._gpio_table.setColumnWidth(2, 90)
        self._gpio_table.setColumnWidth(3, 90)
        self._gpio_table.setColumnWidth(4, 60)
        self._gpio_table.verticalHeader().setVisible(False)
        self._gpio_table.setMaximumHeight(220)
        gpio_layout.addWidget(self._gpio_table)

        self._proto_tabs.addTab(self._gpio_group, "GPIO")

        self._proto_tabs.currentChanged.connect(self._on_proto_tab_changed)

        main_layout.addWidget(self._proto_tabs)

        main_layout.addStretch()
        return container

    def _create_pinout_panel(self) -> QGroupBox:
        """Right: interactive pinmap."""
        group = QGroupBox("Pinout View")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 6, 6, 6)

        # Legend row
        legend = QHBoxLayout()
        legend.setSpacing(12)
        protocols = [
            ("I2C", "#00d2ff"), ("SPI", "#ff9933"), ("JTAG", "#cc66ff"),
            ("UART", "#66ff66"), ("GPIO", "#ffcc44"), ("PWR", "#ff4444"),
            ("GND", "#666666"),
        ]
        for name, color in protocols:
            dot = QLabel("o")
            dot.setStyleSheet(f"color: {color}; font-size: 10px;")
            dot.setFixedWidth(12)
            lbl = QLabel(name)
            lbl.setStyleSheet("color: #8899bb; font-size: 10px;")
            legend.addWidget(dot)
            legend.addWidget(lbl)
        legend.addStretch()
        layout.addLayout(legend)

        # Pinmap widget
        self._pinout = PinoutWidget()
        self._pinout.pin_clicked.connect(self._on_pin_clicked)
        self._pinout.pin_double_clicked.connect(self._on_pin_double_clicked)
        self._pinout.pin_hovered.connect(self._on_pin_hovered)
        layout.addWidget(self._pinout, 1)

        return group

    def _create_log_panel(self) -> QTabWidget:
        """Bottom: Communication Log"""
        tabs = QTabWidget()

        # Communication log tab
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(6, 6, 6, 6)

        header = QHBoxLayout()
        header.addWidget(QLabel("Communication Log"))
        header.addStretch()
        clear_btn = QPushButton("Clear Log")
        clear_btn.setFixedWidth(120)
        clear_btn.clicked.connect(lambda: self._log_text.clear())
        header.addWidget(clear_btn)
        log_layout.addLayout(header)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFont(QFont("Consolas", 10))
        self._log_text.document().setMaximumBlockCount(3000)
        log_layout.addWidget(self._log_text, 1)

        tabs.addTab(log_tab, "Communication Log")

        return tabs

    # -- Chip/channel sync (linked to FTDI connection tab) --

    def _apply_chip_and_channel(self, chip_name: str, channel: str) -> None:
        spec = get_chip_spec(chip_name)
        if spec is None:
            return

        self._current_chip = spec
        self._pinout.set_chip(spec)
        # Reset GPIO polling/state on channel change
        try:
            self._stop_worker()
        except Exception:
            pass
        if hasattr(self, "_gpio_poll_btn"):
            self._gpio_poll_btn.blockSignals(True)
            self._gpio_poll_btn.setChecked(False)
            self._gpio_poll_btn.setText("Start Polling")
            self._gpio_poll_btn.blockSignals(False)
        if hasattr(self, "_gpio_poll_status"):
            self._gpio_poll_status.setVisible(False)
        if hasattr(self, "_pinout"):
            self._pinout.set_polling_active(False)
        self._gpio_poll_pin = -1
        self._gpio_states = {}

        # # Channel apply: prefer connected channel; if absent in spec, map to first
        ch = channel or "A"
        if ch not in spec.channels:
            # Single-channel chip (FT232H) has spec key 'A' but displays actual channel
            spec_ch = list(spec.channels.keys())[0] if spec.channels else "A"
            self._current_channel = spec_ch
            self._display_channel = ch  # actual connected channel (UI display)
        else:
            self._current_channel = ch
            self._display_channel = ch
        self._pinout.set_channel_filter(self._current_channel)

        # Chip info
        self._chip_info_label.setText(
            f"{spec.description}  |  {len(spec.pins)} pins  |  "
            f"{len(spec.channels)} channels"
        )

        if hasattr(self, "_chip_label"):
            self._chip_label.setText(chip_name)
        if hasattr(self, "_channel_label"):
            self._channel_label.setText(self._display_channel)

        self._update_protocol_availability()
        self._refresh_gpio_table()

    def _refresh_gpio_table(self) -> None:
        if self._gpio_table is None or self._current_chip is None:
            return
        pins = list(self._current_chip.pins.values())
        pins.sort(key=lambda p: p.number)
        self._gpio_bit_to_pin: Dict[int, int] = {}
        self._gpio_table.setRowCount(len(pins))
        for row, pin in enumerate(pins):
            if pin.channel == self._current_channel and pin.mpsse_bit is not None:
                self._gpio_bit_to_pin[pin.mpsse_bit] = pin.number
            self._gpio_table.setItem(row, 0, QTableWidgetItem(f"D{pin.number}"))
            self._gpio_table.setItem(row, 1, QTableWidgetItem(pin.name))
            active_func = self._pinout._pin_active_funcs.get(pin.number, pin.default_function)
            self._gpio_table.setItem(row, 2, QTableWidgetItem(active_func.name))
            self._gpio_table.setItem(row, 3, QTableWidgetItem(pin.direction.name))
            level = self._gpio_states.get(pin.number, False)
            self._gpio_table.setItem(row, 4, QTableWidgetItem("1" if level else "0"))

    def _update_protocol_availability(self) -> None:
        """Update mode combo using supported protocols for current chip/channel."""
        if self._current_chip is None:
            return
        if self._uart_serial is not None:
            # UART/VCP active - do not force protocol/tabs reset.
            return

        ch_spec = self._current_chip.channels.get(self._current_channel)
        if ch_spec is None:
            return

        # Capture and clear suppress flag once used throughout this function.
        suppressed = self._suppress_protocol_sync
        self._suppress_protocol_sync = False
        # Also suppress if within the time-based suppression window.
        if time.time() < self._suppress_protocol_until_ts:
            suppressed = True

        current = self._proto_mode_combo.currentText()
        self._proto_mode_combo.blockSignals(True)
        self._proto_mode_combo.clear()
        for name in ["I2C", "SPI", "JTAG", "UART", "GPIO"]:
            self._proto_mode_combo.addItem(name)
        self._proto_mode_combo.blockSignals(False)

        if self._proto_mode_combo.count() > 0 and not suppressed:
            items = [self._proto_mode_combo.itemText(i) for i in range(self._proto_mode_combo.count())]
            desired = current
            if (
                desired == "UART"
                and self._uart_serial is None
                and self._last_proto_mode != "UART"
                and self._last_non_uart_mode in items
            ):
                desired = self._last_non_uart_mode
            if self._last_proto_mode in items:
                desired = self._last_proto_mode
            if desired in items:
                self._proto_mode_combo.setCurrentText(desired)
            else:
                self._proto_mode_combo.setCurrentIndex(0)
            self._on_protocol_mode_changed(self._proto_mode_combo.currentText())

        # SPI/I2C/GPIO buttons only enabled on MPSSE channels
        has_mpsse = ch_spec.supports_mpsse
        connected = self._ftdi.is_connected
        ch_match = (not connected) or (self._ftdi.channel == self._current_channel)
        self._i2c_scan_btn.setEnabled(has_mpsse and connected)
        self._i2c_test_btn.setEnabled(has_mpsse and connected)
        self._spi_loopback_btn.setEnabled(has_mpsse and connected)
        self._spi_id_btn.setEnabled(has_mpsse and connected)
        if not (has_mpsse and connected and ch_match):
            self._pin_name_label.setText("Unavailable")
            self._pin_func_label.setText("MPSSE not supported on this channel")
            if not ch_match and connected:
                self._pin_ch_label.setText(f"{self._display_channel} (Connected: {self._ftdi.channel})")
                self._pin_desc_label.setText("Connected channel and selected channel differ.")
            else:
                self._pin_ch_label.setText(self._display_channel)
                self._pin_desc_label.setText("GPIO control is only available on channels A/B.")

        # Channel constraints
        self._update_mode_desc(self._proto_mode_combo.currentText())

        # Protocol mode UI enable/disable
        if not suppressed:
            self._apply_protocol_mode(self._proto_mode_combo.currentText())
        self._gpio.refresh_controls()

    def _apply_protocol_mode(self, mode: str) -> None:
        required = ("_i2c_group", "_spi_group", "_jtag_group", "_uart_group", "_gpio_group")
        if not all(hasattr(self, name) for name in required):
            return

        # If UART VCP is open and user switches away, auto-close first.
        if self._uart_serial is not None and mode != "UART":
            self._append_log(f"[UART] Auto-closed (switched to {mode})")
            self.status_message.emit(f"UART auto-closed (switched to {mode})")
            self._close_uart()

        groups = {
            "I2C": self._i2c_group,
            "SPI": self._spi_group,
            "JTAG": self._jtag_group,
            "UART": self._uart_group,
            "GPIO": self._gpio_group,
        }
        ch_spec = self._current_chip.channels.get(self._current_channel) if self._current_chip else None
        supported = set(p.value for p in ch_spec.supported_protocols) if ch_spec else set()
        for name, grp in groups.items():
            grp.setEnabled(name == mode and name in supported)
        if hasattr(self, "_proto_tabs"):
            idx = list(groups.keys()).index(mode) if mode in groups else 0
            self._proto_tabs.setCurrentIndex(idx)

        if mode == "GPIO":
            self._append_log("GPIO mode: auto-switch Bitbang/MPSSE for GPIO control.")
            self.status_message.emit("GPIO mode: auto Bitbang/MPSSE for GPIO")

        # Update pinout mapping based on mode
        self._pinmap.apply_mode(mode)
        self._update_mode_desc(mode)
        if mode != "UART":
            self._last_non_uart_mode = mode
        if self._ftdi.is_connected:
            self._ftdi.set_protocol_mode(mode)
            if mode == "GPIO":
                self._gpio.set_all_low()

        if mode == "UART":
            self._refresh_uart_ports()
        else:
            if hasattr(self, "_uart_open_btn"):
                self._uart_open_btn.setEnabled(False)

    @Slot(str)
    def _on_protocol_mode_changed(self, text: str) -> None:
        self._last_proto_mode = text
        self._apply_protocol_mode(text)

    @Slot(int)
    def _on_proto_tab_changed(self, index: int) -> None:
        required = ("_i2c_group", "_spi_group", "_jtag_group", "_uart_group", "_gpio_group")
        if not all(hasattr(self, name) for name in required):
            return
        names = ["I2C", "SPI", "JTAG", "UART", "GPIO"]
        if 0 <= index < len(names):
            self._proto_mode_combo.blockSignals(True)
            self._proto_mode_combo.setCurrentText(names[index])
            self._proto_mode_combo.blockSignals(False)
            self._last_proto_mode = names[index]
            self._apply_protocol_mode(names[index])

    @Slot(str)
    def _on_mode_changed(self, text: str) -> None:
        self._pinmap.apply_mode(text)

    @Slot()
    def _on_pin_clicked(self, pin_number: int) -> None:
        if self._current_chip is None or pin_number < 0:
            self._pin_name_label.setText("-")
            self._pin_func_label.setText("-")
            self._pin_ch_label.setText("-")
            self._pin_desc_label.setText("-")
            self._gpio.refresh_controls(pin_selected=False, is_gpio=False)
            return

        ch_spec = self._current_chip.channels.get(self._current_channel)
        mode = self._proto_mode_combo.currentText() if hasattr(self, "_proto_mode_combo") else ""
        connected = self._ftdi.is_connected
        ch_match = (not connected) or (self._ftdi.channel == self._current_channel)
        supports_mpsse = bool(ch_spec and ch_spec.supports_mpsse)

        # Allow GPIO (Bitbang) on any channel; require MPSSE for I2C/SPI/JTAG.
        if ch_spec is None or (not connected) or (not ch_match) or ((mode in ("I2C", "SPI", "JTAG")) and not supports_mpsse):
            self._pin_name_label.setText("Unavailable")
            if not connected:
                self._pin_func_label.setText("Device not connected")
            elif not ch_match:
                self._pin_func_label.setText("Connected channel differs")
            else:
                self._pin_func_label.setText("MPSSE not supported on this channel")
            if connected and not ch_match:
                self._pin_ch_label.setText(f"{self._display_channel} (Connected: {self._ftdi.channel})")
                self._pin_desc_label.setText("Connected channel and selected channel differ.")
            else:
                self._pin_ch_label.setText(self._display_channel)
                if mode == "GPIO":
                    self._pin_desc_label.setText("GPIO (Auto Bitbang/MPSSE)")
                else:
                    self._pin_desc_label.setText("GPIO uses auto Bitbang/MPSSE in GPIO mode.")
            self._gpio.refresh_controls(pin_selected=True, is_gpio=False)
            return

        pin = self._current_chip.pins.get(pin_number)
        if pin is None:
            return

        self._pin_name_label.setText(f"Pin {pin.number}: {pin.name}")
        active = self._pinout._pin_active_funcs.get(pin.number, pin.default_function)
        self._pin_func_label.setText(active.name)
        self._pin_ch_label.setText(pin.channel or "N/A")

        force_gpio = False
        if mode in ("I2C", "SPI", "JTAG"):
            ch_spec = self._current_chip.channels.get(self._current_channel) if self._current_chip else None
            supports_mpsse = bool(ch_spec and ch_spec.supports_mpsse)
            force_gpio = not supports_mpsse
        if mode == "GPIO" or force_gpio:
            self._pin_desc_label.setText("GPIO (Auto Bitbang/MPSSE)")
        else:
            self._pin_desc_label.setText(pin.description)

        # Enable toggle button for GPIO pins
        is_gpio = active in (PinFunction.GPIO_OUT, PinFunction.GPIO_IN)
        self._gpio.refresh_controls(pin_selected=True, is_gpio=is_gpio)

        state = self._gpio_states.get(pin_number, self._pinout._pin_states.get(pin_number, False))
        self._gpio_toggle_btn.blockSignals(True)
        self._gpio_toggle_btn.setChecked(state)
        self._gpio_toggle_btn.setText("GPIO: HIGH" if state else "GPIO: LOW")
        self._gpio_toggle_btn.blockSignals(False)
        self._pin_desc_label.setText(
            (pin.description or "") + f"\nCurrent status: {'HIGH' if state else 'LOW'}"
        )

    @Slot(int)
    def _on_pin_hovered(self, pin_number: int) -> None:
        pass  # Handled inside PinoutWidget

    @Slot(int)
    def _on_pin_double_clicked(self, pin_number: int) -> None:
        if pin_number < 0:
            return
        # Ensure selection context is updated
        self._on_pin_clicked(pin_number)
        if not self._gpio_toggle_btn.isEnabled():
            return
        current = self._gpio_states.get(pin_number, False)
        self._gpio_toggle_btn.blockSignals(True)
        self._gpio_toggle_btn.setChecked(not current)
        self._gpio_toggle_btn.setText("GPIO: HIGH" if not current else "GPIO: LOW")
        self._gpio_toggle_btn.blockSignals(False)
        self._on_gpio_toggle(not current)

    # -- I2C --

    @Slot()
    def _on_i2c_scan(self) -> None:
        if not self._ftdi.is_connected:
            return

        start = getattr(self, "_i2c_scan_start", 0x08)
        end = getattr(self, "_i2c_scan_end", 0x77)
        self._last_i2c_scan_t0 = time.time()
        self._last_i2c_scan_range = (start, end)
        self._append_log(f"[I2C] Bus scan start (0x{start:02X} ~ 0x{end:02X})...")
        self._i2c_result_table.setRowCount(0)
        self._i2c_ack_led.setText("  ACK: N/A")
        self._i2c_ack_led.setStyleSheet(
            "background: #6f7a8e; color: #ffffff; border-radius: 8px; padding: 4px 10px;"
            "font-weight: 700; letter-spacing: 0.5px;"
        )

        worker = VerifierWorker(self._ftdi)
        worker.i2c_scan_done.connect(self._on_i2c_scan_result)
        worker.log_message.connect(self._append_log)
        worker.error_occurred.connect(self._append_log)

        self._i2c_scan_thread = QThread()
        worker.moveToThread(self._i2c_scan_thread)
        self._i2c_scan_thread.started.connect(lambda: worker.run_i2c_scan(start, end))
        worker.i2c_scan_done.connect(self._i2c_scan_thread.quit)
        worker.i2c_scan_done.connect(worker.deleteLater)
        self._i2c_scan_thread.finished.connect(self._i2c_scan_thread.deleteLater)
        self._i2c_scan_thread.start()

    @Slot(object)
    def _on_i2c_scan_result(self, result: I2CScanResult) -> None:
        self._i2c_result_table.setRowCount(len(result.found_addresses))
        self._i2c_addr_combo.blockSignals(True)
        self._i2c_addr_combo.clear()
        for row, addr in enumerate(result.found_addresses):
            addr_item = QTableWidgetItem(f"0x{addr:02X}")
            addr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            addr_item.setForeground(QColor("#00d2ff"))
            self._i2c_result_table.setItem(row, 0, addr_item)

            status_item = QTableWidgetItem("ACK OK")
            status_item.setForeground(QColor("#33cc33"))
            self._i2c_result_table.setItem(row, 1, status_item)

            self._i2c_addr_combo.addItem(f"0x{addr:02X}", addr)
        if self._i2c_addr_combo.count() == 0:
            self._i2c_addr_combo.addItem("0x40")
            self._i2c_addr_combo.setCurrentIndex(0)
        else:
            first_addr = result.found_addresses[0]
            self._i2c_addr_combo.setCurrentText(f"0x{first_addr:02X}")
        self._i2c_addr_combo.blockSignals(False)

        # Update scan history table
        t0 = self._last_i2c_scan_t0 or time.time()
        elapsed = time.time() - t0
        start, end = self._last_i2c_scan_range
        row = 0
        self._i2c_history_table.insertRow(row)
        time_item = QTableWidgetItem(time.strftime("%H:%M:%S"))
        range_item = QTableWidgetItem(f"0x{start:02X}-0x{end:02X}")
        found_item = QTableWidgetItem(str(len(result.found_addresses)))
        dur_item = QTableWidgetItem(f"{elapsed:.2f}s")
        for col, item in enumerate([time_item, range_item, found_item, dur_item]):
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._i2c_history_table.setItem(row, col, item)
        max_rows = 5
        while self._i2c_history_table.rowCount() > max_rows:
            self._i2c_history_table.removeRow(self._i2c_history_table.rowCount() - 1)

    @Slot(str)
    def _on_i2c_scan_preset_changed(self, text: str) -> None:
        if "0x40" in text:
            self._i2c_scan_start = 0x40
            self._i2c_scan_end = 0x4F
        else:
            self._i2c_scan_start = 0x08
            self._i2c_scan_end = 0x77

    @Slot()
    def _on_i2c_test(self) -> None:
        if not self._ftdi.is_connected:
            return
        text = self._i2c_addr_combo.currentText().strip()
        try:
            addr = int(text, 16)
        except ValueError:
            self._append_log(f"[I2C] Invalid address: {text}")
            return
        if not (0x08 <= addr <= 0x77):
            self._append_log(f"[I2C] Address out of range: 0x{addr:02X}")
            return
        self._append_log(f"[I2C] Single address test: 0x{addr:02X}")

        worker = VerifierWorker(self._ftdi)
        worker.protocol_test_done.connect(self._on_protocol_result)
        worker.log_message.connect(self._append_log)
        worker.error_occurred.connect(self._append_log)

        self._i2c_test_thread = QThread()
        worker.moveToThread(self._i2c_test_thread)
        self._i2c_test_thread.started.connect(lambda: worker.test_i2c_address(addr))
        worker.protocol_test_done.connect(self._i2c_test_thread.quit)
        worker.protocol_test_done.connect(worker.deleteLater)
        self._i2c_test_thread.finished.connect(self._i2c_test_thread.deleteLater)
        self._i2c_test_thread.start()

    # -- SPI --

    @Slot()
    @Slot()
    def _on_spi_loopback(self) -> None:
        """SPI loopback test (placeholder — SPI API not yet implemented)."""
        if not self._ftdi.is_connected:
            return
        self._spi_loopback_result.setText("  Testing...")
        self._spi_loopback_result.setStyleSheet(
            "background: #3a3520; color: #e8c06a; border-radius: 8px; padding: 4px 10px;"
            "font-weight: 800; letter-spacing: 0.5px; border: 1px solid #5a4820;"
        )
        worker = VerifierWorker(self._ftdi)
        worker.protocol_test_done.connect(self._on_protocol_result)
        worker.log_message.connect(self._append_log)

        self._spi_test_thread = QThread()
        worker.moveToThread(self._spi_test_thread)
        self._spi_test_thread.started.connect(worker.test_spi_loopback)
        worker.protocol_test_done.connect(self._spi_test_thread.quit)
        worker.protocol_test_done.connect(worker.deleteLater)
        self._spi_test_thread.finished.connect(self._spi_test_thread.deleteLater)
        self._spi_test_thread.start()

    @Slot()
    def _on_spi_read_id(self) -> None:
        """SPI device ID read (placeholder — SPI API not yet implemented)."""
        if not self._ftdi.is_connected:
            return
        self._spi_id_result.setText("  Reading...")
        self._spi_id_result.setStyleSheet(
            "background: #3a3520; color: #e8c06a; border-radius: 8px; padding: 4px 10px;"
            "font-weight: 800; letter-spacing: 0.5px; border: 1px solid #5a4820;"
        )
        self._append_log("[SPI] Device ID read: SPI API not yet implemented")

    @Slot(object)
    def _on_protocol_result(self, result: ProtocolTestResult) -> None:
        color = "#33cc33" if result.success else "#ff6666"
        self._append_log(
            f'<span style="color:{color};">[{result.protocol}] {result.message}</span>'
        )
        if result.protocol == "I2C":
            if result.success:
                self._i2c_ack_led.setText("  ACK")
                self._i2c_ack_led.setStyleSheet(
                    "background: #1e4a2a; color: #80c890; border-radius: 8px; padding: 4px 10px;"
                    "font-weight: 800; letter-spacing: 0.5px; border: 1px solid #2a7040;"
                )
            else:
                self._i2c_ack_led.setText("  NACK")
                self._i2c_ack_led.setStyleSheet(
                    "background: #ff5b5b; color: #1a0b0b; border-radius: 8px; padding: 4px 10px;"
                    "font-weight: 800; letter-spacing: 0.5px;"
                )
        if result.protocol == "SPI":
            # Determine which result badge to update based on message content
            if "loopback" in result.message.lower():
                target = self._spi_loopback_result
            else:
                target = self._spi_id_result
            if result.success:
                target.setText(f"  {result.message}")
                target.setStyleSheet(
                    "background: #1e4a2a; color: #80c890; border-radius: 8px; padding: 4px 10px;"
                    "font-weight: 800; letter-spacing: 0.5px; border: 1px solid #2a7040;"
                )
            else:
                target.setText(f"  {result.message}")
                target.setStyleSheet(
                    "background: #5a1a1a; color: #ff8888; border-radius: 8px; padding: 4px 10px;"
                    "font-weight: 800; letter-spacing: 0.5px; border: 1px solid #8a3030;"
                )

    # -- GPIO --

    @Slot(bool)
    def _on_gpio_poll_toggled(self, checked: bool) -> None:
        if self._ftdi.is_connected and self._ftdi.channel != self._current_channel:
            self._gpio_poll_btn.setChecked(False)
            # "Channel mismatch: GPIO control is only available on the connected channel."
            self._append_log("Channel mismatch: GPIO control is only available on the connected channel.")
            return
        if checked:
            # "GPIO polling stop (active)"
            self._gpio_poll_btn.setText("Polling ON")
            if hasattr(self, "_gpio_poll_interval"):
                self._gpio_poll_interval.setEnabled(False)
                self._gpio_poll_interval_value = self._gpio_poll_interval.value()
            self._gpio.refresh_controls()
            interval = self._gpio_poll_interval.value()
            if self._gpio_poll_blink is not None:
                self._gpio_poll_blink.setInterval(max(50, interval))
            self._start_worker(interval)
            # Polling start: clear all LED states (show OFF)
            for pin_num in list(self._gpio_states.keys()):
                self._gpio_states[pin_num] = False
                self._pinout.set_pin_state(pin_num, False)
            self._refresh_gpio_table()
            if hasattr(self, "_gpio_poll_status"):
                self._gpio_poll_status.setVisible(True)
            if hasattr(self, "_pinout"):
                self._pinout.set_polling_active(True)
            if self._gpio_poll_blink is not None and not self._gpio_poll_blink.isActive():
                self._gpio_poll_blink.start()
        else:
            # "GPIO polling start (idle)"
            self._gpio_poll_btn.setText("Polling OFF")
            self._stop_worker()
            self._gpio.refresh_controls()
            if hasattr(self, "_gpio_poll_interval"):
                self._gpio_poll_interval.setEnabled(True)
            if hasattr(self, "_gpio_poll_status"):
                self._gpio_poll_status.setVisible(False)
            if hasattr(self, "_pinout"):
                self._pinout.set_polling_active(False)
            if self._gpio_poll_blink is not None and self._gpio_poll_blink.isActive():
                self._gpio_poll_blink.stop()
            # Reset all pin states to LOW on poll stop
            for pin_num in list(self._gpio_states.keys()):
                self._gpio_states[pin_num] = False
                self._pinout.set_pin_state(pin_num, False)
            self._refresh_gpio_table()

    @Slot(int)
    def _on_gpio_poll_interval_changed(self, value: int) -> None:
        if self._gpio_poll_btn.isChecked():
            # Prevent changing interval while polling is active.
            if hasattr(self, "_gpio_poll_interval"):
                self._gpio_poll_interval.blockSignals(True)
                self._gpio_poll_interval.setValue(self._gpio_poll_interval_value)
                self._gpio_poll_interval.blockSignals(False)
            return
        self._gpio_poll_interval_value = value
        if self._worker is not None and self._gpio_poll_btn.isChecked():
            try:
                self._worker.start_gpio_polling(value)
                self._append_log(f"[GPIO] Poll interval changed: {value} ms")
            except Exception:
                pass

    @Slot(bool)
    def _on_gpio_toggle(self, high: bool) -> None:
        self._gpio.toggle_selected(high)

    @Slot(object)
    def _on_gpio_updated(self, state: object) -> None:
        self._gpio.on_gpio_updated(state)

    
    def _set_bitbang_controls_enabled(self, enabled: bool) -> None:
        if hasattr(self, "_bitbang_all_out"):
            self._bitbang_all_out.setEnabled(enabled)
        if hasattr(self, "_bitbang_all_in"):
            self._bitbang_all_in.setEnabled(enabled)
        if hasattr(self, "_bitbang_btns"):
            for btn in self._bitbang_btns:
                btn.setEnabled(enabled)

    def _update_bitbang_mask_label(self) -> None:
        if hasattr(self, "_bitbang_mask_label"):
            self._bitbang_mask_label.setText(f"MASK: 0x{self._bitbang_mask:02X}")

    def _apply_bitbang_mask(self, mask: int, push: bool = True) -> None:
        self._bitbang_mask = mask & 0xFF
        for i, btn in enumerate(self._bitbang_btns):
            checked = bool(self._bitbang_mask & (1 << i))
            btn.blockSignals(True)
            btn.setChecked(checked)
            btn.setText(f"D{i} {'OUT' if checked else 'IN'}")
            btn.blockSignals(False)
        self._update_bitbang_mask_label()
        if push:
            self._ftdi.set_bitbang_mask(self._bitbang_mask)

    def _on_bitbang_toggle(self, checked: bool) -> None:
        mask = 0
        for i, btn in enumerate(self._bitbang_btns):
            if btn.isChecked():
                mask |= (1 << i)
            btn.setText(f"D{i} {'OUT' if btn.isChecked() else 'IN'}")
        self._apply_bitbang_mask(mask, push=True)

    def _on_bitbang_all_output(self) -> None:
        self._apply_bitbang_mask(0xFF, push=True)

    def _on_bitbang_all_input(self) -> None:
        self._apply_bitbang_mask(0x00, push=True)

    def _on_uart_open_clicked(self) -> None:
        if self._uart_serial is not None:
            self._close_uart()
            return

        # Save GPIO polling state so we can restore after UART close.
        if hasattr(self, "_gpio_poll_btn") and self._gpio_poll_btn.isChecked():
            self._gpio_poll_was_active = True
            self._gpio_poll_restore_interval = self._gpio_poll_interval_value
        else:
            self._gpio_poll_was_active = False
        try:
            import serial

            port = self._uart_port_combo.currentData()
            if not port:
                label = self._uart_port_combo.currentText()
                if label.startswith("COM"):
                    port = label.split()[0]
            if not port:
                self._append_log("[UART] No available ports.")
                return

            # If FTDI is connected via D2XX, close it before opening VCP.
            # Guard with _uart_switching to block signal cascade from close_device().
            if self._ftdi.is_connected:
                self._uart_prev_connected = True
                self._uart_prev_serial = self._ftdi.serial_number
                self._uart_prev_channel = self._ftdi.channel
                self.is_uart_switching = True
                try:
                    self._ftdi.close_device()
                finally:
                    self.is_uart_switching = False
                self._append_log("[UART] Switched to VCP - D2XX connection closed.")
            else:
                self._uart_prev_connected = False

            baud = int(self._uart_baud_combo.currentText())
            data_bits = int(self._uart_data_bits.currentText())
            parity_map = {
                "None": serial.PARITY_NONE,
                "Even": serial.PARITY_EVEN,
                "Odd": serial.PARITY_ODD,
            }
            stop_map = {
                "1": serial.STOPBITS_ONE,
                "1.5": serial.STOPBITS_ONE_POINT_FIVE,
                "2": serial.STOPBITS_TWO,
            }
            flow = self._uart_flow.currentText()

            self._uart_serial = serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=data_bits,
                parity=parity_map.get(self._uart_parity.currentText(), serial.PARITY_NONE),
                stopbits=stop_map.get(self._uart_stop_bits.currentText(), serial.STOPBITS_ONE),
                timeout=0,
                rtscts=(flow == "RTS/CTS"),
                xonxoff=(flow == "XON/XOFF"),
            )
            self._uart_open_btn.setText("CLOSE")
            self._apply_uart_open_style(opened=True)
            self._uart_send_btn.setEnabled(True)
            self._uart_read_timer.start()
            self._append_log(f"[UART] OPEN: {port} @ {baud}")
            # Notify MainWindow to show VCP mode in toolbar.
            main_win = self.window()
            if hasattr(main_win, "set_vcp_mode"):
                main_win.set_vcp_mode(True, port)
        except Exception as e:
            self._append_log(f"[UART] OPEN failed: {e}")
            self._uart_serial = None
            self._apply_uart_open_style(opened=False)

    def _on_uart_send_clicked(self) -> None:
        if self._uart_serial is None:
            return
        data = self._uart_input.text()
        if not data:
            return
        eol = ""
        if self._uart_crlf.currentText() == "CR":
            eol = "\r"
        elif self._uart_crlf.currentText() == "LF":
            eol = "\n"
        elif self._uart_crlf.currentText() == "CRLF":
            eol = "\r\n"
        try:
            payload = (data + eol).encode("utf-8")
            self._uart_serial.write(payload)
            self._uart_input.clear()
            self._append_uart_console(data, kind="TX")
        except Exception as e:
            self._append_log(f"[UART] Send failed: {e}")

    def _poll_uart(self) -> None:
        if self._uart_serial is None:
            return
        # Skip first N polls after UART restore to discard stale buffer data.
        if self._uart_poll_skip_count > 0:
            self._uart_poll_skip_count -= 1
            return
        try:
            n = self._uart_serial.in_waiting
            if n:
                data = self._uart_serial.read(n)
                if data:
                    if self._uart_rx_format.currentText() == "HEX":
                        text = " ".join(f"{b:02X}" for b in data)
                    else:
                        text = data.decode("utf-8", errors="ignore").rstrip("\r\n")
                    if text:
                        self._append_uart_console(text, kind="RX")
        except Exception as e:
            self._append_log(f"[UART] Receive error: {e}")
            self._close_uart()

    def _on_uart_clear_clicked(self) -> None:
        if hasattr(self, "_uart_console"):
            self._uart_console.setRowCount(0)

    def _on_uart_save_clicked(self) -> None:
        if not hasattr(self, "_uart_console"):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save UART Log", "uart_log.txt", "Text Files (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for row in range(self._uart_console.rowCount()):
                    time_item = self._uart_console.item(row, 0)
                    dir_item = self._uart_console.item(row, 1)
                    data_item = self._uart_console.item(row, 2)
                    time_val = time_item.text() if time_item else ""
                    dir_val = dir_item.text() if dir_item else ""
                    data_val = data_item.text() if data_item else ""
                    f.write(f"{time_val}\t{dir_val}\t{data_val}\n")
            self._append_log(f"[UART] Log saved: {path}")
        except Exception as e:
            self._append_log(f"[UART] Save failed: {e}")

    def _update_spi_waveform(self, index: int) -> None:
        if not hasattr(self, "_spi_waveform"):
            return
        waves = [
            # Mode 0: CPOL=0 CPHA=0 -- idle LOW, sample on every RISING edge
            (
                "Mode 0   CPOL=0  CPHA=0   [sample: RISING edge]\n"
                "\n"
                "        _________          _________          _________\n"
                "       |         |        |         |        |         |\n"
                "SCLK: _+         +________+         +________+         +____\n"
                "\n"
                "DATA: =X==  D7  ==========X==  D6  ==========X==  D5  ==X\n"
                "\n"
                "Smpl:  ^                  ^                  ^"
            ),
            # Mode 1: CPOL=0 CPHA=1 -- idle LOW, sample on every FALLING edge
            (
                "Mode 1   CPOL=0  CPHA=1   [sample: FALLING edge]\n"
                "\n"
                "        _________          _________          _________\n"
                "       |         |        |         |        |         |\n"
                "SCLK: _+         +________+         +________+         +____\n"
                "\n"
                "DATA: =====X==  D7  ==========X==  D6  ==========X========\n"
                "\n"
                "Smpl:           v                  v                  v"
            ),
            # Mode 2: CPOL=1 CPHA=0 -- idle HIGH, sample on every FALLING edge
            (
                "Mode 2   CPOL=1  CPHA=0   [sample: FALLING edge]\n"
                "\n"
                "SCLK: __          ________          ________          ______\n"
                "         |       |        |        |        |        |\n"
                "         +_______+        +________+        +________+\n"
                "\n"
                "DATA: ==X==  D7  ==========X==  D6  ==========X==  D5  ==X\n"
                "\n"
                "Smpl:   v                 v                v"
            ),
            # Mode 3: CPOL=1 CPHA=1 -- idle HIGH, sample on every RISING edge
            (
                "Mode 3   CPOL=1  CPHA=1   [sample: RISING edge]\n"
                "\n"
                "SCLK: __          ________          ________          ______\n"
                "         |       |        |        |        |        |\n"
                "         +_______+        +________+        +________+\n"
                "\n"
                "DATA: ======X==  D7  ==========X==  D6  ==========X========\n"
                "\n"
                "Smpl:            ^                 ^                ^"
            ),
        ]
        idx = index if 0 <= index < len(waves) else 0
        self._spi_waveform.setText(waves[idx])
    def _on_uart_timestamp_toggled(self, checked: bool) -> None:
        if not hasattr(self, "_uart_console"):
            return
        if checked:
            self._uart_console.setColumnHidden(0, False)
            self._uart_console.setColumnWidth(0, 72)
        else:
            self._uart_console.setColumnHidden(0, True)

    def _on_gpio_poll_blink(self) -> None:
        if not hasattr(self, "_gpio_poll_status"):
            return
        # simple blink by toggling opacity
        if self._gpio_poll_status.isVisible():
            cur = self._gpio_poll_status.styleSheet()
            if "opacity: 0.35" in cur:
                self._gpio_poll_status.setStyleSheet(
                    "color: #80c890; font-weight: 700; font-size: 11px; opacity: 1.0;"
                )
            else:
                self._gpio_poll_status.setStyleSheet(
                    "color: #80c890; font-weight: 700; font-size: 11px; opacity: 0.35;"
                )
        # Blink all GPIO pins visually while polling is active.
        if not self._gpio_poll_btn.isChecked():
            return
        if self._current_chip is None or not hasattr(self, "_pinout"):
            return
        self._poll_blink_state = not self._poll_blink_state
        pin_states: dict[int, bool] = {}
        low_mask = 0
        high_mask = 0
        high_value = 0
        low_value = 0
        for pin_num, pin in self._current_chip.pins.items():
            active = self._pinout._pin_active_funcs.get(pin.number, pin.default_function)
            if active in (PinFunction.GPIO_OUT, PinFunction.GPIO_IN):
                pin_states[pin.number] = self._poll_blink_state
                if pin.name.startswith(("AC", "BC")):
                    if pin.mpsse_bit is not None:
                        bit = 1 << pin.mpsse_bit
                        high_mask |= bit
                        if self._poll_blink_state:
                            high_value |= bit
                else:
                    if pin.mpsse_bit is not None:
                        bit = 1 << pin.mpsse_bit
                        low_mask |= bit
                        if self._poll_blink_state:
                            low_value |= bit
        if pin_states:
            # Apply hardware toggle first
            try:
                # Bitbang controls low byte only; MPSSE required for high byte.
                if low_mask:
                    self._ftdi.set_gpio_backend("bitbang")
                    self._set_gpio_backend_label("BITBANG")
                    self._ftdi.set_gpio_masked(low_mask, low_value)
                if high_mask:
                    if self._ftdi.set_gpio_backend("mpsse"):
                        self._set_gpio_backend_label("MPSSE")
                        self._ftdi.set_gpio_high_masked(high_mask, high_value)
                    else:
                        self._append_log("GPIO toggle: MPSSE not available on this channel (high byte skipped).")
            except Exception:
                pass
            self._pinout.set_pin_states_bulk(pin_states)
            self._gpio_states.update(pin_states)
            self._refresh_gpio_table()


    def _append_uart_console(self, text: str, kind: str = "RX") -> None:
        if not hasattr(self, "_uart_console"):
            return
        ts = time.strftime("%H:%M:%S") if (hasattr(self, "_uart_timestamp") and self._uart_timestamp.isChecked()) else ""
        is_rx = (kind == "RX")
        direction = "\u25c0 RX" if is_rx else "TX \u25b6"  # ◀ RX / TX ▶
        dir_color = QColor("#66ff99") if is_rx else QColor("#66ccff")
        bg_color = QColor("#0d1a10") if is_rx else QColor("#0d1420")  # subtle row tint

        row = self._uart_console.rowCount()
        self._uart_console.insertRow(row)

        # Time column
        time_item = QTableWidgetItem(ts)
        time_item.setForeground(QBrush(QColor("#607080")))
        time_item.setBackground(QBrush(bg_color))
        self._uart_console.setItem(row, 0, time_item)

        # Direction column
        dir_item = QTableWidgetItem(direction)
        dir_item.setForeground(QBrush(dir_color))
        dir_font = dir_item.font()
        dir_font.setBold(True)
        dir_item.setFont(dir_font)
        dir_item.setBackground(QBrush(bg_color))
        dir_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._uart_console.setItem(row, 1, dir_item)

        # Data column
        data_item = QTableWidgetItem(text)
        data_item.setForeground(QBrush(QColor("#d0d8e4")))
        data_item.setBackground(QBrush(bg_color))
        self._uart_console.setItem(row, 2, data_item)

        # Enforce max rows
        max_rows = 3000
        if self._uart_console.rowCount() > max_rows:
            self._uart_console.removeRow(0)

        if hasattr(self, "_uart_autoscroll") and self._uart_autoscroll.isChecked():
            self._uart_console.scrollToBottom()

    def _close_uart(self, restore: bool = True) -> None:
        if self._uart_read_timer is not None and self._uart_read_timer.isActive():
            self._uart_read_timer.stop()
        if self._uart_serial is not None:
            try:
                self._uart_serial.close()
            except Exception:
                pass
        self._uart_serial = None
        self._uart_open_btn.setText("OPEN")
        self._apply_uart_open_style(opened=False)
        self._uart_send_btn.setEnabled(False)
        # Clear VCP mode in MainWindow toolbar.
        main_win = self.window()
        if hasattr(main_win, "set_vcp_mode"):
            main_win.set_vcp_mode(False)
        # Restore FTDI connection if we closed it for UART.
        if not restore or self._uart_restore_in_progress:
            return
        if self._uart_prev_connected and self._uart_prev_serial:
            if self._ftdi.is_connected:
                self._uart_prev_connected = False
                return
            self._uart_restore_in_progress = True
            self.is_uart_switching = True
            restored = False
            try:
                # Avoid protocol tab switching during restore.
                self._suppress_protocol_sync = True
                self._suppress_protocol_until_ts = time.time() + 1.2
                if self._ftdi.open_device(self._uart_prev_serial, self._uart_prev_channel):
                    mode = self._last_non_uart_mode or "I2C"
                    self._ftdi.set_protocol_mode(mode)
                    self._append_log(f"[UART] Restored FTDI: {self._uart_prev_serial} CH-{self._uart_prev_channel}")
                    restored = True
            except Exception as e:
                self._append_log(f"[UART] Restore failed: {e}")
            finally:
                self._uart_prev_connected = False
                self._uart_restore_in_progress = False
                # Keep is_uart_switching True during signal re-emission to prevent
                # on_device_connected from triggering _update_protocol_availability
                # which would cause a tab jump.
            # Re-emit signals with is_uart_switching still True,
            # so MainWindow and other modules update their state
            # without this module's handlers re-triggering.
            if restored and self._ftdi.is_connected:
                info = f"Connected: SN={self._ftdi.serial_number}, CH={self._ftdi.channel}"
                self._ftdi.device_connected.emit(info)
                info_dict = self._ftdi.get_device_info()
                if info_dict:
                    self._ftdi.device_info_changed.emit(info_dict)
            # Now clear the flag after signals are processed.
            self.is_uart_switching = False

            if restored and self._ftdi.is_connected:
                self._uart_poll_skip_count = 2
                # Restore GPIO polling state if it was active before UART opened.
                if self._gpio_poll_was_active and hasattr(self, "_gpio_poll_btn"):
                    try:
                        self._gpio_poll_interval.blockSignals(True)
                        self._gpio_poll_interval.setValue(self._gpio_poll_restore_interval)
                        self._gpio_poll_interval.blockSignals(False)
                        self._gpio_poll_btn.blockSignals(True)
                        self._gpio_poll_btn.setChecked(True)
                        self._gpio_poll_btn.blockSignals(False)
                        self._on_gpio_poll_toggled(True)
                    except Exception:
                        pass
                self._gpio_poll_was_active = False

    def _apply_uart_open_style(self, opened: bool) -> None:
        if not hasattr(self, "_uart_open_btn"):
            return
        if opened:
            # CLOSE: dark background with muted red border + text
            self._uart_open_btn.setStyleSheet(
                "QPushButton { background: #2d1e20; color: #e07070; font-weight: 700; "
                "border: 1px solid #6a3030; border-radius: 6px; padding: 4px 10px; }"
                "QPushButton:hover { background: #3a2225; color: #f09090; border-color: #8a4040; }"
            )
        else:
            # OPEN: muted teal matching app palette
            self._uart_open_btn.setStyleSheet(
                "QPushButton { background: #1d2d3a; color: #70b8d0; font-weight: 700; "
                "border: 1px solid #2a5068; border-radius: 6px; padding: 4px 10px; }"
                "QPushButton:hover { background: #243548; color: #90d0e8; border-color: #3a6880; }"
                "QPushButton:disabled { background: #1e2028; color: #4a5068; border: 1px solid #2a2e3a; }"
            )

    def _refresh_uart_ports(self) -> None:
        if not hasattr(self, "_uart_port_combo"):
            return
        self._uart_port_combo.blockSignals(True)
        self._uart_port_combo.clear()
        try:
            from serial.tools import list_ports

            ports = list(list_ports.comports())
            if not ports:
                self._uart_port_combo.addItem("No Ports")
            else:
                for p in ports:
                    label = f"{p.device} ({p.description})"
                    self._uart_port_combo.addItem(label, p.device)
        except Exception as e:
            self._uart_port_combo.addItem("pyserial not available")
            self._append_log(f"[UART] Port scan failed: {e}")
        finally:
            self._uart_port_combo.blockSignals(False)
            has_ports = any(
                self._uart_port_combo.itemData(i) for i in range(self._uart_port_combo.count())
            )
            self._uart_open_btn.setEnabled(has_ports and self._proto_mode_combo.currentText() == "UART")

    # -- Worker management --

    def _start_worker(self, interval_ms: int = 200) -> None:
        if self._worker_thread is not None:
            return

        self._worker = VerifierWorker(self._ftdi)
        self._worker.start_gpio_polling(interval_ms)
        self._worker.gpio_updated.connect(self._on_gpio_updated)
        self._worker.log_message.connect(self._append_log)
        self._worker.error_occurred.connect(self._append_log)

        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker_thread.start()

    def _stop_worker(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
            self._worker_thread.deleteLater()
            self._worker_thread = None
        self._worker = None

    def _refresh_gpio_controls(self, pin_selected: bool | None = None, is_gpio: bool | None = None) -> None:
        self._gpio.refresh_controls(pin_selected, is_gpio)

    # -- Logs --

    # Log block limit is handled by setMaximumBlockCount(3000) on the QTextEdit.

    def _update_mode_desc(self, mode: str) -> None:
        if self._current_chip is None:
            self._mode_desc_label.setText("")
            return

        ch_spec = self._current_chip.channels.get(self._current_channel)
        if ch_spec is None:
            self._mode_desc_label.setText("")
            return

        # --- mode descriptions ---
        dch = self._display_channel
        green_css = "color: #88cc88; font-size: 11px; font-family: 'Malgun Gothic';"
        warn_css = "color: #ffcc44; font-size: 11px; font-family: 'Malgun Gothic';"

        _DESC = {
            # (supports_mpsse=False, mode)
            (False, "GPIO"): (
                "Channel {ch}: GPIO uses Bitbang mode (low byte only).",
                green_css,
            ),
            (False, "UART"): (
                "Channel {ch}: UART mode.",
                green_css,
            ),
            (False, "_else"): (
                "Channel {ch}: Only UART/GPIO are supported. MPSSE (I2C/SPI/JTAG) is unavailable.",
                warn_css,
            ),
            # (supports_mpsse=True, mode)
            (True, "GPIO"): (
                "Channel {ch}: GPIO auto-switches Bitbang (low) / MPSSE (high).",
                green_css,
            ),
            (True, "UART"): (
                "Channel {ch}: UART mode (not MPSSE). Operates via separate serial interface.",
                green_css,
            ),
            (True, "_else"): (
                "Channel {ch}: MPSSE mode - I2C/SPI/JTAG available.",
                green_css,
            ),
        }

        mpsse = ch_spec.supports_mpsse
        key = (mpsse, mode) if (mpsse, mode) in _DESC else (mpsse, "_else")
        text_tmpl, css = _DESC[key]
        self._mode_desc_label.setText(text_tmpl.format(ch=dch))
        self._mode_desc_label.setStyleSheet(css)

    def _set_gpio_backend_label(self, backend: str) -> None:
        self._gpio_backend = backend
        if not hasattr(self, "_gpio_backend_label"):
            return
        if backend == "MPSSE":
            css = "color: #70b8d0; font-weight: 700; font-size: 11px;"
        else:
            css = "color: #d4a84b; font-weight: 700; font-size: 11px;"
        self._gpio_backend_label.setText(f"GPIO Backend: {backend}")
        self._gpio_backend_label.setStyleSheet(css)

    def _append_log(self, message: str) -> None:
        if not hasattr(self, "_log_text"):
            return

        if "<span" in message:
            html = message
        elif "ERROR" in message or "FAIL" in message:
            html = f'<span style="color:#ff6666;">{message}</span>'
        elif "ACK" in message and "NACK" not in message:
            html = f'<span style="color:#33cc33;">{message}</span>'
        elif "NACK" in message:
            html = f'<span style="color:#ff6666;">{message}</span>'
        elif "TX ->" in message:
            html = f'<span style="color:#66ccff;">{message}</span>'
        elif "RX <-" in message:
            html = f'<span style="color:#66ff99;">{message}</span>'
        elif "WARN" in message or "!" in message:
            html = f'<span style="color:#ffcc44;">{message}</span>'
        else:
            html = f'<span style="color:#8899aa;">{message}</span>'

        self._log_text.append(html)
