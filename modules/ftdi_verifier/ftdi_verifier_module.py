"""
FTDI Hardware Verifier 모듈 - Universal Device Studio 플러그인

연결된 FTDI 칩의 하드웨어 리소스를 검증하고,
각 채널·핀의 기능을 실시간으로 테스트합니다.

Layout:
  좌측: Control Center (칩 선택, 모드, 프로토콜 테스트, GPIO 제어)
  우측: Interactive Pinout View (CubeIDE 스타일)
  하단: Communication Log
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Slot, QThread, QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QComboBox, QSpinBox,
    QSplitter, QTabWidget, QFrame, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QCheckBox,
)

from core.ftdi_manager import FtdiManager
from modules.base_module import BaseModule
from modules.ftdi_verifier.ftdi_chip_specs import (
    CHIP_SPECS, ChipSpec, PinSpec, PinFunction, PinDirection,
    ProtocolMode, ChannelSpec, PIN_COLORS, PROTOCOL_COLORS,
    get_chip_spec, get_channel_protocols,
)
from modules.ftdi_verifier.pinout_widget import PinoutWidget
from modules.ftdi_verifier.verifier_worker import (
    VerifierWorker, I2CScanResult, ProtocolTestResult,
)


class FtdiVerifierModule(BaseModule):
    """FTDI Hardware Verifier 디바이스 모듈

    칩 모델별 핀맵 시각화, I2C/SPI ACK 체크, GPIO 제어를 제공합니다.
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
        self._gpio_poll_pin: int = -1
        self._gpio_poll_blink = None
        super().__init__(ftdi_manager, parent)

    # ── BaseModule 구현 ──

    def init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 상단: FTDI 정보 패널 (연결 탭에 연동)
        layout.addWidget(self._create_device_info_panel())

        # 메인 영역 (좌·우 + 하단)
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        v_splitter.setHandleWidth(3)

        # 좌측(제어) + 우측(핀맵)
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.setHandleWidth(3)
        h_splitter.addWidget(self._create_control_panel())
        h_splitter.addWidget(self._create_pinout_panel())
        h_splitter.setStretchFactor(0, 3)
        h_splitter.setStretchFactor(1, 4)

        v_splitter.addWidget(h_splitter)
        v_splitter.addWidget(self._create_log_panel())
        v_splitter.setStretchFactor(0, 4)
        v_splitter.setStretchFactor(1, 2)

        layout.addWidget(v_splitter, 1)

        # 기본 칩 로드
        self._apply_chip_and_channel("FT232H", "A")
        if self._uart_read_timer is None:
            self._uart_read_timer = QTimer(self)
            self._uart_read_timer.setInterval(50)
            self._uart_read_timer.timeout.connect(self._poll_uart)
        if self._gpio_poll_blink is None:
            self._gpio_poll_blink = QTimer(self)
            self._gpio_poll_blink.setInterval(450)
            self._gpio_poll_blink.timeout.connect(self._on_gpio_poll_blink)

    def on_device_connected(self) -> None:
        self._i2c_scan_btn.setEnabled(True)
        self._i2c_test_btn.setEnabled(True)
        self._spi_test_btn.setEnabled(True)
        if hasattr(self, "_gpio_mask_apply"):
            self._gpio_mask_apply.setEnabled(True)
        self._gpio_poll_btn.setEnabled(True)
        info = self._ftdi.get_device_info()
        chip = info.get("device_type", "FT232H")
        ch = info.get("channel", "A")
        self._apply_chip_and_channel(chip, ch)
        self._update_protocol_availability()
        self.status_message.emit("FTDI Verifier: 장치 연결됨")

    def on_device_disconnected(self) -> None:
        self.stop_communication()
        self._i2c_scan_btn.setEnabled(False)
        self._i2c_test_btn.setEnabled(False)
        self._spi_test_btn.setEnabled(False)
        self._gpio_poll_btn.setEnabled(False)
        if hasattr(self, "_chip_label"):
            self._chip_label.setText("-")
        if hasattr(self, "_channel_label"):
            self._channel_label.setText("-")
        self.status_message.emit("FTDI Verifier: 장치 해제됨")

    def on_channel_changed(self, channel: str) -> None:
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

    def start_communication(self) -> None:
        pass  # GPIO 폴링은 별도 버튼으로 시작

    def stop_communication(self) -> None:
        self._stop_worker()
        self._close_uart()

    def update_data(self) -> None:
        pass

    # ── UI 빌더 ──

    def _create_device_info_panel(self) -> QGroupBox:
        """상단: FTDI 연결 정보 (연결 탭 연동)"""
        group = QGroupBox("FTDI 장치 정보")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(10, 6, 10, 6)

        layout.addWidget(QLabel("칩 모델:"))
        self._chip_label = QLabel("-")
        self._chip_label.setStyleSheet("color: #c8d2f0; font-weight: 600;")
        layout.addWidget(self._chip_label)

        layout.addSpacing(20)
        layout.addWidget(QLabel("채널:"))
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
        """좌측 제어 패널"""
        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(6)

        # ── 모드 선택 (Exclusive) ──
        mode_group = QGroupBox("프로토콜 모드")
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

        # ── 프로토콜 탭 ──
        self._proto_tabs = QTabWidget()

        # I2C 테스트
        self._i2c_group = QGroupBox("I2C 테스트")
        i2c_layout = QVBoxLayout(self._i2c_group)
        i2c_layout.setSpacing(4)
        i2c_layout.setContentsMargins(8, 6, 8, 6)

        self._i2c_scan_btn = QPushButton("I2C 버스 스캔")
        self._i2c_scan_btn.setEnabled(False)
        self._i2c_scan_btn.setMinimumHeight(36)
        self._i2c_scan_btn.setStyleSheet(
            "QPushButton { background: #1f2430; color: #e9eefc; font-weight: 700; border-radius: 8px; "
            "border: 1px solid #3b4458; }"
            "QPushButton:hover { background: #2a3142; }"
            "QPushButton:pressed { background: #141923; border: 1px solid #58627a; }"
            "QPushButton:disabled { background: #2a303b; color: #9aa4b8; border: 1px solid #3b4458; }"
        )
        self._i2c_scan_btn.clicked.connect(self._on_i2c_scan)
        i2c_layout.addWidget(self._i2c_scan_btn)

        self._i2c_ack_led = QLabel("●  ACK: N/A")
        self._i2c_ack_led.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._i2c_ack_led.setToolTip("I2C ACK 상태")
        self._i2c_ack_led.setStyleSheet(
            "background: #6f7a8e; color: #ffffff; border-radius: 8px; padding: 4px 10px;"
            "font-weight: 700; letter-spacing: 0.5px;"
        )
        self._i2c_ack_led.setFixedHeight(32)
        i2c_layout.addWidget(self._i2c_ack_led)

        addr_row = QHBoxLayout()
        addr_row.addWidget(QLabel("주소:"))
        self._i2c_addr_combo = QComboBox()
        self._i2c_addr_combo.setEditable(True)
        self._i2c_addr_combo.setMinimumWidth(100)
        self._i2c_addr_combo.addItem("0x40")
        addr_row.addWidget(self._i2c_addr_combo)
        self._i2c_test_btn = QPushButton("ACK TEST")
        self._i2c_test_btn.setEnabled(False)
        self._i2c_test_btn.setMinimumHeight(34)
        self._i2c_test_btn.setMinimumWidth(110)
        self._i2c_test_btn.setStyleSheet(
            "QPushButton { background: #2f343f; color: #f3d28b; font-weight: 700; border-radius: 8px; "
            "border: 1px solid #5a513f; }"
            "QPushButton:hover { background: #3a404d; }"
            "QPushButton:disabled { background: #2a303b; color: #9aa4b8; border: 1px solid #3b4458; }"
        )
        self._i2c_test_btn.clicked.connect(self._on_i2c_test)
        addr_row.addWidget(self._i2c_test_btn)
        i2c_layout.addLayout(addr_row)

        # 스캔 결과 테이블
        self._i2c_result_table = QTableWidget(0, 2)
        self._i2c_result_table.setHorizontalHeaderLabels(["주소", "상태"])
        self._i2c_result_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self._i2c_result_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._i2c_result_table.setColumnWidth(0, 70)
        self._i2c_result_table.verticalHeader().setDefaultSectionSize(24)
        self._i2c_result_table.verticalHeader().setVisible(False)
        self._i2c_result_table.setMaximumHeight(220)
        i2c_layout.addWidget(self._i2c_result_table)

        self._proto_tabs.addTab(self._i2c_group, "I2C")

        # SPI 테스트
        self._spi_group = QGroupBox("SPI 테스트")
        spi_layout = QVBoxLayout(self._spi_group)
        self._spi_test_btn = QPushButton("SPI 루프백 테스트")
        self._spi_test_btn.setEnabled(False)
        self._spi_test_btn.clicked.connect(self._on_spi_test)
        spi_layout.addWidget(self._spi_test_btn)
        self._spi_result_label = QLabel("—")
        self._spi_result_label.setStyleSheet("color: #8899bb;")
        spi_layout.addWidget(self._spi_result_label)
        self._proto_tabs.addTab(self._spi_group, "SPI")

        # JTAG 테스트 (GUI 전용)
        self._jtag_group = QGroupBox("JTAG 테스트")
        jtag_layout = QVBoxLayout(self._jtag_group)
        jtag_layout.addWidget(QLabel("JTAG Read/Write 테스트"))
        self._jtag_test_btn = QPushButton("JTAG 테스트 실행")
        self._jtag_test_btn.setEnabled(False)
        jtag_layout.addWidget(self._jtag_test_btn)
        self._proto_tabs.addTab(self._jtag_group, "JTAG")

        # UART 테스트 (GUI 전용)
        self._uart_group = QGroupBox("UART 테스트")
        uart_layout = QVBoxLayout(self._uart_group)
        uart_layout.setSpacing(6)
        uart_layout.setContentsMargins(8, 6, 8, 6)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("COM Port:"))
        self._uart_port_combo = QComboBox()
        self._uart_port_combo.addItem("Auto-Detect")
        port_row.addWidget(self._uart_port_combo)
        uart_layout.addLayout(port_row)

        baud_row = QHBoxLayout()
        baud_row.addWidget(QLabel("Baudrate:"))
        self._uart_baud_combo = QComboBox()
        self._uart_baud_combo.addItems(["9600", "115200", "230400", "460800", "921600"])
        baud_row.addWidget(self._uart_baud_combo)
        uart_layout.addLayout(baud_row)

        cfg_row = QHBoxLayout()
        cfg_row.addWidget(QLabel("Data Bits:"))
        self._uart_data_bits = QComboBox()
        self._uart_data_bits.addItems(["7", "8"])
        cfg_row.addWidget(self._uart_data_bits)
        cfg_row.addWidget(QLabel("Parity:"))
        self._uart_parity = QComboBox()
        self._uart_parity.addItems(["None", "Even", "Odd"])
        cfg_row.addWidget(self._uart_parity)
        uart_layout.addLayout(cfg_row)

        cfg_row2 = QHBoxLayout()
        cfg_row2.addWidget(QLabel("Stop Bits:"))
        self._uart_stop_bits = QComboBox()
        self._uart_stop_bits.addItems(["1", "1.5", "2"])
        cfg_row2.addWidget(self._uart_stop_bits)
        cfg_row2.addWidget(QLabel("Flow:"))
        self._uart_flow = QComboBox()
        self._uart_flow.addItems(["None", "RTS/CTS", "XON/XOFF"])
        cfg_row2.addWidget(self._uart_flow)
        uart_layout.addLayout(cfg_row2)

        self._uart_open_btn = QPushButton("OPEN")
        self._uart_open_btn.setEnabled(False)
        self._uart_open_btn.clicked.connect(self._on_uart_open_clicked)
        uart_layout.addWidget(self._uart_open_btn)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("RX Format:"))
        self._uart_rx_format = QComboBox()
        self._uart_rx_format.addItems(["ASCII", "HEX"])
        fmt_row.addWidget(self._uart_rx_format)
        self._uart_autoscroll = QCheckBox("Auto Scroll")
        self._uart_autoscroll.setChecked(True)
        fmt_row.addWidget(self._uart_autoscroll)
        self._uart_timestamp = QCheckBox("Timestamp")
        self._uart_timestamp.setChecked(False)
        fmt_row.addWidget(self._uart_timestamp)
        fmt_row.addStretch()
        uart_layout.addLayout(fmt_row)

        self._uart_console = QTextEdit()
        self._uart_console.setReadOnly(False)
        self._uart_console.setFont(QFont("Consolas", 10))
        self._uart_console.setPlaceholderText("UART Console")
        self._uart_console.setMinimumHeight(160)
        uart_layout.addWidget(self._uart_console)

        send_row = QHBoxLayout()
        self._uart_input = QLineEdit()
        self._uart_input.setPlaceholderText("입력 후 전송")
        self._uart_input.setStyleSheet("color: #111111;")
        send_row.addWidget(self._uart_input)
        self._uart_crlf = QComboBox()
        self._uart_crlf.addItems(["No EOL", "CR", "LF", "CRLF"])
        send_row.addWidget(self._uart_crlf)
        self._uart_send_btn = QPushButton("SEND")
        self._uart_send_btn.setEnabled(False)
        self._uart_send_btn.clicked.connect(self._on_uart_send_clicked)
        send_row.addWidget(self._uart_send_btn)
        uart_layout.addLayout(send_row)
        self._proto_tabs.addTab(self._uart_group, "UART")

        # GPIO 제어
        self._gpio_group = QGroupBox("GPIO 제어")
        gpio_layout = QVBoxLayout(self._gpio_group)
        gpio_layout.setSpacing(4)

        poll_row = QHBoxLayout()
        poll_row.addWidget(QLabel("폴링 주기(ms):"))
        self._gpio_poll_interval = QSpinBox()
        self._gpio_poll_interval.setRange(50, 2000)
        self._gpio_poll_interval.setValue(200)
        self._gpio_poll_interval.setSingleStep(50)
        self._gpio_poll_interval.setMinimumWidth(110)
        self._gpio_poll_interval.valueChanged.connect(self._on_gpio_poll_interval_changed)
        poll_row.addWidget(self._gpio_poll_interval)
        poll_row.addStretch()

        self._gpio_poll_btn = QPushButton("GPIO 폴링 시작 (전체)")
        self._gpio_poll_btn.setCheckable(True)
        self._gpio_poll_btn.setEnabled(False)
        self._gpio_poll_btn.setMinimumHeight(32)
        self._gpio_poll_btn.setStyleSheet(
            "QPushButton { background: #1d2433; color: #c8d2f0; font-weight: 700; border-radius: 6px; }"
            "QPushButton:checked { background: #2ecc71; color: #0b1a10; }"
            "QPushButton:disabled { background: #2a303b; color: #9aa4b8; }"
        )
        self._gpio_poll_btn.toggled.connect(self._on_gpio_poll_toggled)
        poll_row.addWidget(self._gpio_poll_btn)
        gpio_layout.addLayout(poll_row)

        self._gpio_poll_status = QLabel("● 전역 폴링 중")
        self._gpio_poll_status.setStyleSheet(
            "color: #2ecc71; font-weight: 700; font-size: 11px;"
        )
        self._gpio_poll_status.setVisible(False)
        gpio_layout.addWidget(self._gpio_poll_status)

        mask_row = QHBoxLayout()
        mask_row.addWidget(QLabel("Bitbang Mask (HEX):"))
        self._gpio_mask_edit = QLineEdit("FF")
        self._gpio_mask_edit.setMaximumWidth(80)
        self._gpio_mask_edit.setStyleSheet("color: #111111;")
        mask_row.addWidget(self._gpio_mask_edit)
        self._gpio_mask_apply = QPushButton("APPLY")
        self._gpio_mask_apply.setEnabled(False)
        self._gpio_mask_apply.clicked.connect(self._on_gpio_mask_apply)
        mask_row.addWidget(self._gpio_mask_apply)
        mask_row.addStretch()
        gpio_layout.addLayout(mask_row)

        # 선택된 핀 정보
        self._pin_info_group = QGroupBox("선택된 핀")
        pin_info_layout = QGridLayout(self._pin_info_group)
        pin_info_layout.setSpacing(4)

        pin_info_layout.addWidget(QLabel("핀:"), 0, 0)
        self._pin_name_label = QLabel("—")
        self._pin_name_label.setStyleSheet("font-weight: bold; color: #00d2ff;")
        pin_info_layout.addWidget(self._pin_name_label, 0, 1)

        pin_info_layout.addWidget(QLabel("기능:"), 1, 0)
        self._pin_func_label = QLabel("—")
        pin_info_layout.addWidget(self._pin_func_label, 1, 1)

        pin_info_layout.addWidget(QLabel("채널:"), 2, 0)
        self._pin_ch_label = QLabel("—")
        pin_info_layout.addWidget(self._pin_ch_label, 2, 1)

        pin_info_layout.addWidget(QLabel("설명:"), 3, 0)
        self._pin_desc_label = QLabel("—")
        self._pin_desc_label.setWordWrap(True)
        pin_info_layout.addWidget(self._pin_desc_label, 3, 1)

        # GPIO 토글 버튼
        self._gpio_toggle_btn = QPushButton("GPIO: LOW")
        self._gpio_toggle_btn.setEnabled(False)
        self._gpio_toggle_btn.setCheckable(True)
        self._gpio_toggle_btn.setMinimumHeight(32)
        self._gpio_toggle_btn.setStyleSheet(
            "QPushButton { font-weight: bold; border-radius: 6px; "
            "background: #1d2433; color: #c8d2f0; }"
            "QPushButton:checked { background: #2ecc71; color: #0b1a10; }"
            "QPushButton:disabled { background: #2a303b; color: #9aa4b8; }"
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
        """우측: 인터랙티브 핀맵"""
        group = QGroupBox("Pinout View")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 6, 6, 6)

        # 범례 행
        legend = QHBoxLayout()
        legend.setSpacing(12)
        protocols = [
            ("I2C", "#00d2ff"), ("SPI", "#ff9933"), ("JTAG", "#cc66ff"),
            ("UART", "#66ff66"), ("GPIO", "#ffcc44"), ("PWR", "#ff4444"),
            ("GND", "#666666"),
        ]
        for name, color in protocols:
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 10px;")
            dot.setFixedWidth(12)
            lbl = QLabel(name)
            lbl.setStyleSheet("color: #8899bb; font-size: 10px;")
            legend.addWidget(dot)
            legend.addWidget(lbl)
        legend.addStretch()
        layout.addLayout(legend)

        # 핀맵 위젯
        self._pinout = PinoutWidget()
        self._pinout.pin_clicked.connect(self._on_pin_clicked)
        self._pinout.pin_hovered.connect(self._on_pin_hovered)
        layout.addWidget(self._pinout, 1)

        return group

    def _create_log_panel(self) -> QTabWidget:
        """하단: Communication Log"""
        tabs = QTabWidget()

        # 통신 로그 탭
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(6, 6, 6, 6)

        header = QHBoxLayout()
        header.addWidget(QLabel("Communication Log"))
        header.addStretch()
        clear_btn = QPushButton("로그 지우기")
        clear_btn.setFixedWidth(120)
        clear_btn.clicked.connect(lambda: self._log_text.clear())
        header.addWidget(clear_btn)
        log_layout.addLayout(header)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFont(QFont("Consolas", 10))
        log_layout.addWidget(self._log_text, 1)

        tabs.addTab(log_tab, "Communication Log")

        return tabs

    # ── 칩/채널 동기화 (FTDI 연결 탭 연동) ──

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
            self._gpio_poll_btn.setText("GPIO 폴링 시작 (전체)")
            self._gpio_poll_btn.blockSignals(False)
        if hasattr(self, "_gpio_poll_status"):
            self._gpio_poll_status.setVisible(False)
        if hasattr(self, "_pinout"):
            self._pinout.set_polling_active(False)
        self._gpio_poll_pin = -1
        self._gpio_states = {}

        # 채널 적용 — 연결된 실제 채널 우선, 칩 스펙에 없으면 첫 번째 채널로 매핑
        ch = channel or "A"
        if ch not in spec.channels:
            # 단일채널 칩(FT232H)이면 스펙 키는 "A"뿐이지만 표시는 실제 채널 사용
            spec_ch = list(spec.channels.keys())[0] if spec.channels else "A"
            self._current_channel = spec_ch
            self._display_channel = ch  # 실제 연결 채널 (UI 표시용)
        else:
            self._current_channel = ch
            self._display_channel = ch
        self._pinout.set_channel_filter(self._current_channel)

        # 칩 정보
        self._chip_info_label.setText(
            f"{spec.description}  |  {len(spec.pins)}핀  |  "
            f"{len(spec.channels)}채널"
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
            self._gpio_table.setItem(row, 2, QTableWidgetItem(pin.default_function.name))
            self._gpio_table.setItem(row, 3, QTableWidgetItem(pin.direction.name))
            level = self._gpio_states.get(pin.number, False)
            self._gpio_table.setItem(row, 4, QTableWidgetItem("1" if level else "0"))

    def _update_protocol_availability(self) -> None:
        """현재 칩+채널의 프로토콜 지원 목록으로 모드 콤보 갱신"""
        if self._current_chip is None:
            return

        ch_spec = self._current_chip.channels.get(self._current_channel)
        if ch_spec is None:
            return

        current = self._proto_mode_combo.currentText()
        self._proto_mode_combo.blockSignals(True)
        self._proto_mode_combo.clear()
        for name in ["I2C", "SPI", "JTAG", "UART", "GPIO"]:
            self._proto_mode_combo.addItem(name)
        self._proto_mode_combo.blockSignals(False)

        if self._proto_mode_combo.count() > 0:
            if current in [self._proto_mode_combo.itemText(i) for i in range(self._proto_mode_combo.count())]:
                self._proto_mode_combo.setCurrentText(current)
            else:
                self._proto_mode_combo.setCurrentIndex(0)
            self._on_protocol_mode_changed(self._proto_mode_combo.currentText())

        # SPI/I2C/GPIO 버튼은 MPSSE 채널에서만 활성
        has_mpsse = ch_spec.supports_mpsse
        connected = self._ftdi.is_connected
        ch_match = (not connected) or (self._ftdi.channel == self._current_channel)
        self._i2c_scan_btn.setEnabled(has_mpsse and connected)
        self._i2c_test_btn.setEnabled(has_mpsse and connected)
        self._spi_test_btn.setEnabled(has_mpsse and connected)
        if not (has_mpsse and connected and ch_match):
            self._pin_name_label.setText("사용 불가")
            self._pin_func_label.setText("MPSSE 미지원 채널")
            if not ch_match and connected:
                self._pin_ch_label.setText(f"{self._display_channel} (연결: {self._ftdi.channel})")
                self._pin_desc_label.setText("연결된 채널과 선택 채널이 다릅니다.")
            else:
                self._pin_ch_label.setText(self._display_channel)
                self._pin_desc_label.setText("채널 A/B에서만 GPIO 제어가 가능합니다.")

        # 채널 제약 안내
        self._update_mode_desc(self._proto_mode_combo.currentText())

        # Protocol mode UI enable/disable
        self._apply_protocol_mode(self._proto_mode_combo.currentText())
        self._refresh_gpio_controls()

    def _apply_protocol_mode(self, mode: str) -> None:
        required = ("_i2c_group", "_spi_group", "_jtag_group", "_uart_group", "_gpio_group")
        if not all(hasattr(self, name) for name in required):
            return
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
            self._append_log("GPIO 모드: Bit-bang 모드로 전환됩니다.")
            self.status_message.emit("GPIO 모드: Bit-bang 모드로 전환")

        # Update pinout mapping based on mode
        self._on_mode_changed(mode)
        self._update_mode_desc(mode)
        if self._ftdi.is_connected:
            self._ftdi.set_protocol_mode(mode)

        if mode == "UART":
            self._refresh_uart_ports()
        else:
            self._close_uart()
            if hasattr(self, "_uart_open_btn"):
                self._uart_open_btn.setEnabled(False)

    @Slot(str)
    def _on_protocol_mode_changed(self, text: str) -> None:
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
            self._apply_protocol_mode(names[index])

    @Slot(str)
    def _on_mode_changed(self, text: str) -> None:
        """프로토콜 모드 변경 시 핀맵 기능 업데이트"""
        if self._current_chip is None:
            return

        mode_map = {
            "I2C": {
                0: PinFunction.I2C_SCL,
                1: PinFunction.I2C_SDA_OUT,
                2: PinFunction.I2C_SDA_IN,
            },
            "SPI": {
                0: PinFunction.SPI_SCK,
                1: PinFunction.SPI_MOSI,
                2: PinFunction.SPI_MISO,
                3: PinFunction.SPI_CS,
            },
            "JTAG": {
                0: PinFunction.JTAG_TCK,
                1: PinFunction.JTAG_TDI,
                2: PinFunction.JTAG_TDO,
                3: PinFunction.JTAG_TMS,
            },
            "UART": {
                0: PinFunction.UART_TX,
                1: PinFunction.UART_RX,
                2: PinFunction.UART_RTS,
                3: PinFunction.UART_CTS,
                4: PinFunction.UART_DTR,
                5: PinFunction.UART_DSR,
                6: PinFunction.UART_DCD,
                7: PinFunction.UART_RI,
            },
        }

        func_map = mode_map.get(text, {})

        ch_spec = self._current_chip.channels.get(self._current_channel)
        supports_mpsse = bool(ch_spec and ch_spec.supports_mpsse)
        force_gpio = (not supports_mpsse) and text in ("I2C", "SPI", "JTAG")

        for num, pin in self._current_chip.pins.items():
            if pin.channel != self._current_channel:
                continue

            if text == "GPIO" or force_gpio:
                if PinFunction.GPIO_OUT in pin.functions:
                    self._pinout.set_pin_function(num, PinFunction.GPIO_OUT)
                elif PinFunction.GPIO_IN in pin.functions:
                    self._pinout.set_pin_function(num, PinFunction.GPIO_IN)
                elif pin.direction == PinDirection.INPUT:
                    self._pinout.set_pin_function(num, PinFunction.GPIO_IN)
                elif pin.direction in (PinDirection.OUTPUT, PinDirection.BIDIRECTIONAL):
                    self._pinout.set_pin_function(num, PinFunction.GPIO_OUT)
                continue

            assigned = func_map.get(pin.mpsse_bit)
            if assigned and assigned in pin.functions:
                self._pinout.set_pin_function(num, assigned)
            elif PinFunction.GPIO_OUT in pin.functions:
                self._pinout.set_pin_function(num, PinFunction.GPIO_OUT)
            elif PinFunction.GPIO_IN in pin.functions:
                self._pinout.set_pin_function(num, PinFunction.GPIO_IN)
            else:
                self._pinout.set_pin_function(num, pin.default_function)

    @Slot()
    def _on_pin_clicked(self, pin_number: int) -> None:
        if self._current_chip is None or pin_number < 0:
            self._pin_name_label.setText("—")
            self._pin_func_label.setText("—")
            self._pin_ch_label.setText("—")
            self._pin_desc_label.setText("—")
            self._refresh_gpio_controls(pin_selected=False, is_gpio=False)
            return

        ch_spec = self._current_chip.channels.get(self._current_channel)
        if ch_spec is None or not ch_spec.supports_mpsse or not self._ftdi.is_connected or (self._ftdi.channel != self._current_channel):
            self._pin_name_label.setText("사용 불가")
            self._pin_func_label.setText("MPSSE 미지원 채널")
            if self._ftdi.is_connected and self._ftdi.channel != self._current_channel:
                self._pin_ch_label.setText(f"{self._display_channel} (연결: {self._ftdi.channel})")
                self._pin_desc_label.setText("연결된 채널과 선택 채널이 다릅니다.")
            else:
                self._pin_ch_label.setText(self._display_channel)
                self._pin_desc_label.setText("채널 A/B에서만 GPIO 제어가 가능합니다.")
            self._refresh_gpio_controls(pin_selected=True, is_gpio=False)
            return

        pin = self._current_chip.pins.get(pin_number)
        if pin is None:
            return

        self._pin_name_label.setText(f"Pin {pin.number}: {pin.name}")
        active = self._pinout._pin_active_funcs.get(pin.number, pin.default_function)
        self._pin_func_label.setText(active.name)
        self._pin_ch_label.setText(pin.channel or "N/A")
        self._pin_desc_label.setText(pin.description)

        # GPIO 핀이면 토글 버튼 활성화
        is_gpio = active in (PinFunction.GPIO_OUT, PinFunction.GPIO_IN)
        self._refresh_gpio_controls(pin_selected=True, is_gpio=is_gpio)

        state = self._gpio_states.get(pin_number, self._pinout._pin_states.get(pin_number, False))
        self._gpio_toggle_btn.blockSignals(True)
        self._gpio_toggle_btn.setChecked(state)
        self._gpio_toggle_btn.setText("GPIO: HIGH" if state else "GPIO: LOW")
        self._gpio_toggle_btn.blockSignals(False)
        self._pin_desc_label.setText(
            (pin.description or "") + f"\n현재 상태: {'HIGH' if state else 'LOW'}"
        )

    @Slot(int)
    def _on_pin_hovered(self, pin_number: int) -> None:
        pass  # PinoutWidget 내부에서 처리

    # ── I2C ──

    @Slot()
    def _on_i2c_scan(self) -> None:
        if not self._ftdi.is_connected:
            return

        self._append_log("[I2C] 버스 스캔 시작 (0x08 ~ 0x77)...")
        self._i2c_result_table.setRowCount(0)
        self._i2c_ack_led.setText("●  ACK: N/A")
        self._i2c_ack_led.setStyleSheet(
            "background: #6f7a8e; color: #ffffff; border-radius: 8px; padding: 4px 10px;"
            "font-weight: 700; letter-spacing: 0.5px;"
        )

        worker = VerifierWorker(self._ftdi)
        worker.i2c_scan_done.connect(self._on_i2c_scan_result)
        worker.log_message.connect(self._append_log)
        worker.error_occurred.connect(self._append_log)

        # 동기 실행 (UI 스레드에서 직접 — 짧은 작업이므로)
        worker.run_i2c_scan(0x08, 0x77)

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

            status_item = QTableWidgetItem("ACK ✓")
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

    @Slot()
    def _on_i2c_test(self) -> None:
        if not self._ftdi.is_connected:
            return
        text = self._i2c_addr_combo.currentText().strip()
        try:
            addr = int(text, 16)
        except ValueError:
            self._append_log(f"[I2C] 잘못된 주소: {text}")
            return
        if not (0x08 <= addr <= 0x77):
            self._append_log(f"[I2C] 주소 범위 오류: 0x{addr:02X}")
            return
        self._append_log(f"[I2C] 단일 주소 테스트: 0x{addr:02X}")

        worker = VerifierWorker(self._ftdi)
        worker.protocol_test_done.connect(self._on_protocol_result)
        worker.log_message.connect(self._append_log)
        worker.error_occurred.connect(self._append_log)
        worker.test_i2c_address(addr)

    # ── SPI ──

    @Slot()
    def _on_spi_test(self) -> None:
        if not self._ftdi.is_connected:
            return
        worker = VerifierWorker(self._ftdi)
        worker.protocol_test_done.connect(self._on_protocol_result)
        worker.log_message.connect(self._append_log)
        worker.run_i2c_scan = lambda: None  # unused
        worker.test_spi_loopback()

    @Slot(object)
    def _on_protocol_result(self, result: ProtocolTestResult) -> None:
        color = "#33cc33" if result.success else "#ff6666"
        self._append_log(
            f'<span style="color:{color};">[{result.protocol}] {result.message}</span>'
        )
        if result.protocol == "I2C":
            if result.success:
                self._i2c_ack_led.setText("●  ACK")
                self._i2c_ack_led.setStyleSheet(
                    "background: #2ecc71; color: #0b1a10; border-radius: 8px; padding: 4px 10px;"
                    "font-weight: 800; letter-spacing: 0.5px;"
                )
            else:
                self._i2c_ack_led.setText("●  NACK")
                self._i2c_ack_led.setStyleSheet(
                    "background: #ff5b5b; color: #1a0b0b; border-radius: 8px; padding: 4px 10px;"
                    "font-weight: 800; letter-spacing: 0.5px;"
                )
        if result.protocol == "SPI":
            self._spi_result_label.setText(result.message)
            self._spi_result_label.setStyleSheet(f"color: {color};")

    # ── GPIO ──

    @Slot(bool)
    def _on_gpio_poll_toggled(self, checked: bool) -> None:
        if self._ftdi.is_connected and self._ftdi.channel != self._current_channel:
            self._gpio_poll_btn.setChecked(False)
            # "채널 불일치: 연결된 채널에서만 GPIO 제어가 가능합니다."
            self._append_log("\ucc44\ub110 \ubd88\uc77c\uce58: \uc5f0\uacb0\ub41c \ucc44\ub110\uc5d0\uc11c\ub9cc GPIO \uc81c\uc5b4\uac00 \uac00\ub2a5\ud569\ub2c8\ub2e4.")
            return
        if checked:
            # "GPIO 폴링 중지 (실행)"
            self._gpio_poll_btn.setText("GPIO \ud3f4\ub9c1 \uc911\uc9c0 (\uc2e4\ud589)")
            self._refresh_gpio_controls()
            interval = self._gpio_poll_interval.value()
            self._start_worker(interval)
            if hasattr(self, "_gpio_poll_status"):
                self._gpio_poll_status.setVisible(True)
            if hasattr(self, "_pinout"):
                self._pinout.set_polling_active(True)
            if self._gpio_poll_blink is not None and not self._gpio_poll_blink.isActive():
                self._gpio_poll_blink.start()
        else:
            # "GPIO 폴링 시작 (대기)"
            self._gpio_poll_btn.setText("GPIO \ud3f4\ub9c1 \uc2dc\uc791 (\ub300\uae30)")
            self._stop_worker()
            self._refresh_gpio_controls()
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
        if self._worker is not None and self._gpio_poll_btn.isChecked():
            try:
                self._worker.start_gpio_polling(value)
                self._append_log(f"[GPIO] 폴링 주기 변경: {value} ms")
            except Exception:
                pass

    @Slot(bool)
    def _on_gpio_toggle(self, high: bool) -> None:
        if self._ftdi.is_connected and self._ftdi.channel != self._current_channel:
            self._gpio_toggle_btn.setChecked(False)
            self._append_log("채널 불일치: 연결된 채널에서만 GPIO 제어가 가능합니다.")
            return
        pin_num = self._pinout.get_selected_pin()
        if pin_num < 0:
            return
        if self._gpio_poll_btn.isChecked():
            self._gpio_poll_btn.setChecked(False)
        self._pinout.set_pin_state(pin_num, high)
        self._gpio_states[pin_num] = high
        self._gpio_toggle_btn.setText("GPIO: HIGH" if high else "GPIO: LOW")
        self._refresh_gpio_controls(pin_selected=True, is_gpio=True)
        self._pin_desc_label.setText(
            (self._pin_desc_label.text().split("\n")[0]) + f"\n현재 상태: {'HIGH' if high else 'LOW'}"
        )
        state_str = "HIGH" if high else "LOW"
        pin = self._current_chip.pins.get(pin_num) if self._current_chip else None
        name = pin.name if pin else f"#{pin_num}"
        self._append_log(f"[GPIO] {name} → {state_str}")
        self._refresh_gpio_table()

    @Slot(object)
    def _on_gpio_updated(self, state: object) -> None:
        if self._gpio_table is None or self._current_chip is None:
            return
        # Prefer hardware read-back if provided
        pin_states = getattr(state, "pin_states", None) or {}
        # Map MPSSE bit -> pin number if mapping exists
        mapped = {}
        if hasattr(self, "_gpio_bit_to_pin"):
            for bit, val in pin_states.items():
                pin_num = self._gpio_bit_to_pin.get(bit)
                if pin_num is not None:
                    mapped[pin_num] = val
        for row in range(self._gpio_table.rowCount()):
            pin_num_item = self._gpio_table.item(row, 0)
            if pin_num_item is None:
                continue
            try:
                pin_num = int(pin_num_item.text().lstrip("D"))
            except ValueError:
                continue
            level = mapped.get(pin_num, self._gpio_states.get(pin_num, False))
            level_item = self._gpio_table.item(row, 4)
            if level_item:
                level_item.setText("1" if level else "0")
                if self._gpio_poll_btn.isChecked():
                    level_item.setForeground(QColor("#66ff99"))
                else:
                    level_item.setForeground(QColor("#c8d2f0"))
            if pin_num in mapped:
                self._pinout.set_pin_state(pin_num, mapped[pin_num])
                self._gpio_states[pin_num] = mapped[pin_num]
                if pin_num == self._pinout.get_selected_pin():
                    self._gpio_toggle_btn.blockSignals(True)
                    self._gpio_toggle_btn.setChecked(mapped[pin_num])
                    self._gpio_toggle_btn.setText("GPIO: HIGH" if mapped[pin_num] else "GPIO: LOW")
                    self._gpio_toggle_btn.blockSignals(False)

    def _on_gpio_mask_apply(self) -> None:
        text = (self._gpio_mask_edit.text() or "").strip().lower()
        if text.startswith("0x"):
            text = text[2:]
        if not text:
            return
        try:
            mask = int(text, 16) & 0xFF
        except ValueError:
            self._append_log("[GPIO] Bitbang mask 형식 오류 (예: FF)")
            return
        self._gpio_mask_edit.setText(f"{mask:02X}")
        self._ftdi.set_bitbang_mask(mask)
        self._append_log(f"[GPIO] Bitbang mask 적용: 0x{mask:02X}")

    def _on_uart_open_clicked(self) -> None:
        if self._uart_serial is not None:
            self._close_uart()
            return
        try:
            import serial

            port = self._uart_port_combo.currentData()
            if not port:
                label = self._uart_port_combo.currentText()
                if label.startswith("COM"):
                    port = label.split()[0]
            if not port:
                self._append_log("[UART] 사용 가능한 포트가 없습니다.")
                return

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
            self._uart_send_btn.setEnabled(True)
            self._uart_read_timer.start()
            self._append_log(f"[UART] OPEN: {port} @ {baud}")
        except Exception as e:
            self._append_log(f"[UART] OPEN 실패: {e}")
            self._uart_serial = None

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
            self._append_uart_console(f"TX> {data}")
        except Exception as e:
            self._append_log(f"[UART] 전송 실패: {e}")

    def _poll_uart(self) -> None:
        if self._uart_serial is None:
            return
        try:
            n = self._uart_serial.in_waiting
            if n:
                data = self._uart_serial.read(n)
                if data:
                    if self._uart_rx_format.currentText() == "HEX":
                        text = " ".join(f"{b:02X}" for b in data)
                    else:
                        text = data.decode("utf-8", errors="ignore")
                    if text:
                        self._append_uart_console(text)
        except Exception as e:
            self._append_log(f"[UART] 수신 오류: {e}")
            self._close_uart()

    def _on_gpio_poll_blink(self) -> None:
        if not hasattr(self, "_gpio_poll_status"):
            return
        # simple blink by toggling opacity
        if self._gpio_poll_status.isVisible():
            cur = self._gpio_poll_status.styleSheet()
            if "opacity: 0.35" in cur:
                self._gpio_poll_status.setStyleSheet(
                    "color: #2ecc71; font-weight: 700; font-size: 11px; opacity: 1.0;"
                )
            else:
                self._gpio_poll_status.setStyleSheet(
                    "color: #2ecc71; font-weight: 700; font-size: 11px; opacity: 0.35;"
                )


    def _append_uart_console(self, text: str) -> None:
        if hasattr(self, "_uart_timestamp") and self._uart_timestamp.isChecked():
            ts = time.strftime("%H:%M:%S")
            text = f"[{ts}] {text}"
        self._uart_console.append(text)
        if hasattr(self, "_uart_autoscroll") and self._uart_autoscroll.isChecked():
            cursor = self._uart_console.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self._uart_console.setTextCursor(cursor)

    def _close_uart(self) -> None:
        if self._uart_read_timer is not None and self._uart_read_timer.isActive():
            self._uart_read_timer.stop()
        if self._uart_serial is not None:
            try:
                self._uart_serial.close()
            except Exception:
                pass
        self._uart_serial = None
        self._uart_open_btn.setText("OPEN")
        self._uart_send_btn.setEnabled(False)

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
            self._append_log(f"[UART] 포트 스캔 실패: {e}")
        finally:
            self._uart_port_combo.blockSignals(False)
            has_ports = any(
                self._uart_port_combo.itemData(i) for i in range(self._uart_port_combo.count())
            )
            self._uart_open_btn.setEnabled(has_ports and self._proto_mode_combo.currentText() == "UART")

    # ── Worker 관리 ──

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
        if self._current_chip is None:
            self._gpio_toggle_btn.setEnabled(False)
            self._gpio_poll_btn.setEnabled(False)
            return

        ch_spec = self._current_chip.channels.get(self._current_channel)
        has_mpsse = bool(ch_spec and ch_spec.supports_mpsse)
        connected = self._ftdi.is_connected
        ch_match = (not connected) or (self._ftdi.channel == self._current_channel)

        if pin_selected is None:
            pin_selected = self._pinout.get_selected_pin() >= 0

        if is_gpio is None:
            is_gpio = False
            if pin_selected:
                pin_num = self._pinout.get_selected_pin()
                if self._current_chip and pin_num in self._current_chip.pins:
                    pin = self._current_chip.pins[pin_num]
                    active = self._pinout._pin_active_funcs.get(pin.number, pin.default_function)
                    is_gpio = active in (PinFunction.GPIO_OUT, PinFunction.GPIO_IN)

        mode = self._proto_mode_combo.currentText() if hasattr(self, "_proto_mode_combo") else ""
        base_ok = connected and ch_match and (has_mpsse or mode == "GPIO")
        poll_checked = self._gpio_poll_btn.isChecked()

        if not base_ok:
            if poll_checked:
                self._gpio_poll_btn.blockSignals(True)
                self._gpio_poll_btn.setChecked(False)
                self._gpio_poll_btn.blockSignals(False)
                self._gpio_poll_btn.setText("GPIO \ud3f4\ub9c1 \uc2dc\uc791")
                self._stop_worker()
            self._gpio_toggle_btn.setEnabled(False)
            self._gpio_poll_btn.setEnabled(False)
            return

        if poll_checked:
            # Global polling active: keep poll enabled, disable toggle to avoid conflict.
            self._gpio_poll_btn.setEnabled(True)
            self._gpio_toggle_btn.setEnabled(False)
            return

        allow = pin_selected and (is_gpio or mode == "GPIO")
        self._gpio_toggle_btn.setEnabled(allow)
        # Global polling can start without selecting a pin.
        self._gpio_poll_btn.setEnabled(base_ok)

    # ── 로그 ──

    _MAX_LOG_BLOCKS = 3000

    def _update_mode_desc(self, mode: str) -> None:
        if self._current_chip is None:
            self._mode_desc_label.setText("")
            return

        ch_spec = self._current_chip.channels.get(self._current_channel)
        if ch_spec is None:
            self._mode_desc_label.setText("")
            return

        # --- mode descriptions (Korean) ---
        dch = self._display_channel
        green_css = "color: #88cc88; font-size: 11px; font-family: 'Malgun Gothic';"
        warn_css = "color: #ffcc44; font-size: 11px; font-family: 'Malgun Gothic';"

        _DESC = {
            # (supports_mpsse=False, mode)
            (False, "GPIO"): (
                "\ucc44\ub110 {ch}: GPIO\ub294 Bit-bang \ubaa8\ub4dc\ub85c \uc9c0\uc6d0\ub429\ub2c8\ub2e4. \ub514\uc9c0\ud138 IO \uc81c\uc5b4\uac00 \uac00\ub2a5\ud569\ub2c8\ub2e4.",
                green_css,
            ),
            (False, "UART"): (
                "\ucc44\ub110 {ch}: UART \ubaa8\ub4dc\ub85c \ub3d9\uc791\ud569\ub2c8\ub2e4.",
                green_css,
            ),
            (False, "_else"): (
                "\ucc44\ub110 {ch}: UART/GPIO\ub9cc \uc9c0\uc6d0\ub429\ub2c8\ub2e4. MPSSE(I2C/SPI/JTAG)\ub294 \uc0ac\uc6a9\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.",
                warn_css,
            ),
            # (supports_mpsse=True, mode)
            (True, "GPIO"): (
                "\ucc44\ub110 {ch}: GPIO\ub294 Bit-bang \ubaa8\ub4dc\uc785\ub2c8\ub2e4. I2C/SPI/JTAG\ub294 MPSSE, UART\ub294 \ubcc4\ub3c4 \ubaa8\ub4dc\ub85c \uc9c0\uc6d0\ub429\ub2c8\ub2e4.",
                green_css,
            ),
            (True, "UART"): (
                "\ucc44\ub110 {ch}: UART \ubaa8\ub4dc (MPSSE \uc544\ub2d8). \ubcc4\ub3c4 \uc2dc\ub9ac\uc5bc \ud1b5\uc2e0\uc73c\ub85c \ub3d9\uc791\ud569\ub2c8\ub2e4.",
                green_css,
            ),
            (True, "_else"): (
                "\ucc44\ub110 {ch}: MPSSE \ubaa8\ub4dc - I2C/SPI/JTAG \uc0ac\uc6a9 \uac00\ub2a5",
                green_css,
            ),
        }

        mpsse = ch_spec.supports_mpsse
        key = (mpsse, mode) if (mpsse, mode) in _DESC else (mpsse, "_else")
        text_tmpl, css = _DESC[key]
        self._mode_desc_label.setText(text_tmpl.format(ch=dch))
        self._mode_desc_label.setStyleSheet(css)

    def _append_log(self, message: str) -> None:
        if not hasattr(self, "_log_text"):
            return

        if "<span" in message:
            html = message
        elif "오류" in message or "FAIL" in message or "ERROR" in message:
            html = f'<span style="color:#ff6666;">{message}</span>'
        elif "ACK" in message and "NACK" not in message:
            html = f'<span style="color:#33cc33;">{message}</span>'
        elif "NACK" in message:
            html = f'<span style="color:#ff6666;">{message}</span>'
        elif "TX ->" in message:
            html = f'<span style="color:#66ccff;">{message}</span>'
        elif "RX <-" in message:
            html = f'<span style="color:#66ff99;">{message}</span>'
        elif "경고" in message or "⚠" in message:
            html = f'<span style="color:#ffcc44;">{message}</span>'
        else:
            html = f'<span style="color:#8899aa;">{message}</span>'

        self._log_text.append(html)

        doc = self._log_text.document()
        while doc.blockCount() > self._MAX_LOG_BLOCKS:
            cursor = self._log_text.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()
