"""
INA228 전력 모니터 모듈 - Universal Device Studio 플러그인

I2C 자동 탐지, 실시간 전압/전류 모니터링, 레지스터 맵 시각화를 제공합니다.
"""

from __future__ import annotations

import time
from collections import deque
import math
from typing import Optional, List

from PySide6.QtCore import Qt, Slot, QThread
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QComboBox,
    QDoubleSpinBox, QSpinBox, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QTextEdit,
)

from core.ftdi_manager import FtdiManager
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
    """INA228 전력 모니터 디바이스 모듈

    Layout:
    - 상단: I2C 주소 자동 탐지 패널
    - 좌측: 제어 패널 (ADC RANGE, AVG, 변환시간, Shunt 저항, Start/Stop)
    - 우측: pyqtgraph 듀얼 차트 + 실시간 수치 라벨
    - 하단: INA228 레지스터 맵 테이블
    """

    MODULE_NAME = "INA228"
    MODULE_ICON = ""
    MODULE_VERSION = "1.0.0"

    MAX_DATA_POINTS = 2000
    INA228_SCAN_START = 0x40
    INA228_SCAN_END   = 0x4F

    def __init__(self, ftdi_manager: FtdiManager, parent: Optional[QWidget] = None) -> None:
        self._worker: Optional[INA228Worker] = None
        self._worker_thread: Optional[QThread] = None
        self._slave_addr: int = 0x40
        self._is_monitoring: bool = False
        self._window_seconds: int = 60

        # 데이터 버퍼 (sliding window)
        self._time_data: deque = deque(maxlen=self.MAX_DATA_POINTS)
        self._voltage_data: deque = deque(maxlen=self.MAX_DATA_POINTS)
        self._current_data: deque = deque(maxlen=self.MAX_DATA_POINTS)
        self._start_time: float = 0.0
        super().__init__(ftdi_manager, parent)

    # ── BaseModule 추상 메서드 구현 ──

    def init_ui(self) -> None:
        """모듈 UI 초기화"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 상단: 주소 탐지 패널
        layout.addWidget(self._create_address_panel())

        # 중앙+하단 Splitter
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        v_splitter.setHandleWidth(3)

        # 좌측(제어) + 우측(시각화) Splitter
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

    def on_device_connected(self) -> None:
        self._scan_btn.setEnabled(True)
        if self._addr_combo.count() == 0:
            self._addr_combo.addItem(f"0x{self._slave_addr:02X}", self._slave_addr)
        self._start_btn.setEnabled(True)
        self.status_message.emit("INA228: FTDI 연결됨 - 주소 스캔 가능")

    def on_device_disconnected(self) -> None:
        self.stop_communication()
        self._scan_btn.setEnabled(False)
        self._start_btn.setEnabled(False)
        self.status_message.emit("INA228: FTDI 해제됨")

    def start_communication(self) -> None:
        """Worker 스레드 시작 (모니터링 ON)"""
        if self._is_monitoring:
            return
        if not self._ftdi.is_connected:
            return

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

        # FTDI 통신 로그를 I2C 로그 탭에 연결
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
        """Worker 스레드 중지 (모니터링 OFF)"""
        if not self._is_monitoring:
            return

        # FTDI 로그 시그널 해제
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
            self._adc_range_combo.setEnabled(True)
            self._avg_combo.setEnabled(True)
            self._vbusct_combo.setEnabled(True)
            self._vshct_combo.setEnabled(True)
            self._shunt_spinbox.setEnabled(True)

    def update_data(self) -> None:
        """레지스터 맵 1회 갱신"""
        self._refresh_register_map()

    # ── UI 빌더 ──

    def _create_address_panel(self) -> QGroupBox:
        """I2C 주소 자동 탐지 패널"""
        group = QGroupBox("I2C 주소 탐지 (0x40 ~ 0x4F)")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(10, 6, 10, 6)

        self._scan_btn = QPushButton("주소 스캔")
        self._scan_btn.setFixedWidth(100)
        self._scan_btn.setEnabled(False)
        self._scan_btn.clicked.connect(self._on_scan_addresses)
        layout.addWidget(self._scan_btn)

        layout.addWidget(QLabel("발견된 장치:"))
        self._addr_combo = QComboBox()
        self._addr_combo.setMinimumWidth(180)
        self._addr_combo.setPlaceholderText("스캔을 실행하세요")
        self._addr_combo.currentIndexChanged.connect(self._on_addr_changed)
        layout.addWidget(self._addr_combo)

        layout.addSpacing(20)
        self._scan_result_label = QLabel("—")
        self._scan_result_label.setStyleSheet("color: #88a0cc; font-style: italic;")
        layout.addWidget(self._scan_result_label)
        layout.addStretch()
        return group

    def _create_control_panel(self) -> QGroupBox:
        """좌측 제어 패널"""
        group = QGroupBox("ADC 설정")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setSpacing(6)

        # ADC Range
        grid.addWidget(QLabel("ADC 범위:"), 0, 0)
        self._adc_range_combo = QComboBox()
        for k, v in ADC_RANGE_OPTIONS.items():
            self._adc_range_combo.addItem(v, k)
        self._adc_range_combo.setCurrentIndex(1)
        grid.addWidget(self._adc_range_combo, 0, 1)

        # Averaging
        grid.addWidget(QLabel("평균 샘플:"), 1, 0)
        self._avg_combo = QComboBox()
        for k, v in AVG_COUNT_OPTIONS.items():
            self._avg_combo.addItem(v, k)
        self._avg_combo.setCurrentIndex(2)   # AVG=16
        grid.addWidget(self._avg_combo, 1, 1)

        # VBUSCT
        grid.addWidget(QLabel("버스 전압 변환시간:"), 2, 0)
        self._vbusct_combo = QComboBox()
        for k, v in CONV_TIME_OPTIONS.items():
            self._vbusct_combo.addItem(v, k)
        self._vbusct_combo.setCurrentIndex(4)  # 540us
        grid.addWidget(self._vbusct_combo, 2, 1)

        # VSHCT
        grid.addWidget(QLabel("Shunt 전압 변환시간:"), 3, 0)
        self._vshct_combo = QComboBox()
        for k, v in CONV_TIME_OPTIONS.items():
            self._vshct_combo.addItem(v, k)
        self._vshct_combo.setCurrentIndex(4)  # 540us
        grid.addWidget(self._vshct_combo, 3, 1)

        # Shunt Resistor
        grid.addWidget(QLabel("Shunt 저항 (Ω):"), 4, 0)
        self._shunt_spinbox = QDoubleSpinBox()
        self._shunt_spinbox.setDecimals(4)
        self._shunt_spinbox.setRange(0.0001, 100.0)
        self._shunt_spinbox.setSingleStep(0.001)
        self._shunt_spinbox.setValue(0.01)
        grid.addWidget(self._shunt_spinbox, 4, 1)

        # 폴링 간격
        grid.addWidget(QLabel("폴링 간격 (ms):"), 5, 0)
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
        self._auto_range_btn.setStyleSheet(
            "QPushButton {"
            "  font-weight: bold;"
            "  font-size: 12px;"
            "  padding: 6px 10px;"
            "  border-radius: 6px;"
            "  background: #1d2433;"
            "  color: #c8d2f0;"
            "}"
            "QPushButton:checked {"
            "  background: #1f5eff;"
            "  color: #ffffff;"
            "}"
        )
        self._auto_range_btn.toggled.connect(self._on_auto_range_toggled)
        grid.addWidget(self._auto_range_btn, 7, 1)

        layout.addLayout(grid)

        # 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #3a3f50;")
        layout.addWidget(sep)

        # Start / Stop 버튼
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("모니터링 시작")
        self._start_btn.setObjectName("startBtn")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self.start_communication)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("모니터링 중지")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self.stop_communication)
        btn_row.addWidget(self._stop_btn)
        layout.addLayout(btn_row)

        # 레지스터 맵 갱신 버튼
        self._refresh_reg_btn = QPushButton("레지스터 맵 갱신")
        self._refresh_reg_btn.clicked.connect(self._refresh_register_map)
        layout.addWidget(self._refresh_reg_btn)

        layout.addStretch()
        return group

    def _create_visualizer_panel(self) -> QGroupBox:
        """우측 시각화 패널 (pyqtgraph + 수치 라벨)"""
        group = QGroupBox("실시간 모니터링")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 6, 6, 6)

        # 수치 라벨 행
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
            val = QLabel("—")
            val.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold;")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            vl.addWidget(val)
            container.setStyleSheet("background-color: #22242e; border-radius: 6px;")
            metrics_layout.addWidget(container)
            return val

        self._vbus_label    = _make_metric("VBUS",    "V",  "#00d2ff")
        self._vshunt_label  = _make_metric("VSHUNT",  "mV", "#88d8ff")
        self._current_label = _make_metric("전류",    "mA", "#ff64b4")
        self._power_label   = _make_metric("전력",    "mW", "#ffcc44")
        self._temp_label    = _make_metric("온도",    "°C", "#88cc88")

        layout.addLayout(metrics_layout)

        # pyqtgraph 듀얼 차트
        self._visualizer = PowerVisualizer(show_toolbar=False)
        layout.addWidget(self._visualizer, 1)

        return group

    def _create_bottom_panel(self) -> QTabWidget:
        """하단 레지스터 맵 + I2C 로그 (탭 위젯)"""
        tabs = QTabWidget()

        # ── 레지스터 맵 탭 ──
        reg_tab = QWidget()
        reg_layout = QVBoxLayout(reg_tab)
        reg_layout.setContentsMargins(6, 6, 6, 6)

        self._reg_table = QTableWidget(len(DISPLAY_REGISTERS), 4)
        self._reg_table.setHorizontalHeaderLabels(["주소", "이름", "설명", "값 (Hex)"])
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

            val_item = QTableWidgetItem("—")
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            val_item.setFont(QFont("Consolas", 10))
            val_item.setForeground(QColor(220, 255, 200))
            self._reg_table.setItem(row, 3, val_item)

        reg_layout.addWidget(self._reg_table)
        tabs.addTab(reg_tab, "레지스터 맵")

        # ── I2C 로그 탭 ──
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(6, 6, 6, 6)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("I2C 패킷 로그"))
        log_header.addStretch()
        clear_btn = QPushButton("로그 지우기")
        clear_btn.setFixedWidth(120)
        clear_btn.clicked.connect(lambda: self._log_text.clear())
        log_header.addWidget(clear_btn)
        log_layout.addLayout(log_header)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFont(QFont("Consolas", 10))
        log_layout.addWidget(self._log_text, 1)

        tabs.addTab(log_tab, "I2C 로그")

        return tabs

    # ── 슬롯 ──

    @Slot()
    def _on_scan_addresses(self) -> None:
        """0x40~0x4F 범위 I2C 스캔"""
        if not self._ftdi.is_connected:
            return

        self._scan_result_label.setText("스캔 중...")
        self._addr_combo.clear()

        found = self._ftdi.i2c_scan(self.INA228_SCAN_START, self.INA228_SCAN_END)

        if not found:
            self._scan_result_label.setText("INA228 장치를 찾지 못했습니다")
            self._start_btn.setEnabled(False)
            return

        for addr in found:
            self._addr_combo.addItem(f"0x{addr:02X}", addr)

        self._scan_result_label.setText(f"{len(found)}개 장치 발견")
        self._slave_addr = found[0]
        self._start_btn.setEnabled(True)

    @Slot(int)
    def _on_addr_changed(self, index: int) -> None:
        if index >= 0:
            data = self._addr_combo.itemData(index)
            if data is not None:
                self._slave_addr = int(data)

    @Slot(object)
    def _on_measurement(self, m: INA228Measurement) -> None:
        """Worker에서 측정 결과 수신 → 차트/라벨 업데이트"""
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
        self._temp_label.setText(f"{m.die_temp_c:.2f} °C")

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
        self._append_log(f"[INA228 오류] {msg}")

    @Slot(str)
    def _on_worker_log(self, msg: str) -> None:
        self._append_log(msg)

    _MAX_LOG_BLOCKS = 3000

    def _append_log(self, message: str) -> None:
        """I2C 로그 탭에 메시지 추가 (색상 코딩)"""
        if not hasattr(self, "_log_text"):
            return
        if "[오류]" in message or "오류" in message:
            color = "#ff6666"
        elif "TX ->" in message:
            color = "#66ccff"
        elif "RX <-" in message:
            color = "#66ff99"
        elif "[경고]" in message:
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
        """레지스터 맵 테이블 갱신 (모니터링 중이 아닐 때 UI 스레드에서 직접 읽기)"""
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
                hex_str = "오류"

            val_item = self._reg_table.item(row, 3)
            if val_item:
                val_item.setText(hex_str)
        self._reg_table.blockSignals(False)

    @Slot(int, int)
    def _on_reg_cell_changed(self, row: int, col: int) -> None:
        """레지스터 맵 테이블에서 값 직접 수정"""
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
