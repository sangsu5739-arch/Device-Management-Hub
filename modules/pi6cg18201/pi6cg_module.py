"""
PI6CG18201 클럭 제너레이터 모듈 - Universal Device Studio 플러그인

기존 단일 윈도우 앱의 제어/시각화/레지스터 UI를 BaseModule 위젯으로 마이그레이션.
연결 패널은 MainWindow에서 관리하므로 제외합니다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, Slot, QSettings
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QComboBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QTextEdit,
    QSplitter, QTabWidget, QHeaderView, QFrame,
)

from core.ftdi_manager import FtdiManager
from modules.base_module import BaseModule
from modules.pi6cg18201.register_map import (
    RegisterMap,
    REGISTER_FIELDS,
    EDITABLE_FIELDS,
    FIELD_BY_NAME,
    TOTAL_BYTES,
    BitField,
    SLAVE_ADDRESS_7BIT_SADR_LOW,
    SLAVE_ADDRESS_7BIT_SADR_HIGH,
)
from modules.pi6cg18201.clock_visualizer import ClockVisualizer


class PI6CGModule(BaseModule):
    """PI6CG18201 클럭 제너레이터 디바이스 모듈

    Layout:
    - 상단: 슬레이브 주소 선택
    - 좌측: 제어 센터 (OE, Amplitude, Spread Spectrum, Slew Rate)
    - 우측: 클럭 파형 시각화
    - 하단: 레지스터 맵 + I2C 로그
    """

    MODULE_NAME = "PI6CG18201"
    MODULE_ICON = ""
    MODULE_VERSION = "1.0.0"

    VALID_SLAVE_ADDRESSES = (SLAVE_ADDRESS_7BIT_SADR_LOW, SLAVE_ADDRESS_7BIT_SADR_HIGH)

    def __init__(self, ftdi_manager: FtdiManager, parent: Optional[QWidget] = None) -> None:
        self._reg_map = RegisterMap()
        self._slave_address: int = SLAVE_ADDRESS_7BIT_SADR_LOW
        self._live_mode: bool = False
        self._advanced_mode: bool = False
        self._advanced_hint_labels: list[QLabel] = []
        self._settings = QSettings("UniversalDeviceStudio", "PI6CGModule")
        super().__init__(ftdi_manager, parent)

    def init_ui(self) -> None:
        """모듈 UI 초기화"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 주소 선택
        addr_row = QHBoxLayout()
        addr_row.addWidget(QLabel("슬레이브 주소:"))
        self._addr_combo = QComboBox()
        self._addr_combo.addItem(
            f"0x{SLAVE_ADDRESS_7BIT_SADR_LOW:02X} (SADR=L)", SLAVE_ADDRESS_7BIT_SADR_LOW
        )
        self._addr_combo.addItem(
            f"0x{SLAVE_ADDRESS_7BIT_SADR_HIGH:02X} (SADR=H)", SLAVE_ADDRESS_7BIT_SADR_HIGH
        )
        self._addr_combo.setFixedWidth(160)
        self._addr_combo.currentIndexChanged.connect(self._on_addr_changed)
        addr_row.addWidget(self._addr_combo)
        addr_row.addStretch()
        layout.addLayout(addr_row)

        # 중앙+하단 Splitter
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        v_splitter.setHandleWidth(3)
        self._v_splitter = v_splitter

        # 좌측(제어) + 우측(시각화) Splitter
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.setHandleWidth(3)
        self._h_splitter = h_splitter

        left_panel = self._create_control_panel()
        right_panel = self._create_visualizer_panel()
        h_splitter.addWidget(left_panel)
        h_splitter.addWidget(right_panel)
        h_splitter.setStretchFactor(0, 2)
        h_splitter.setStretchFactor(1, 3)

        v_splitter.addWidget(h_splitter)

        # 하단: 레지스터 맵 + 로그
        bottom_panel = self._create_bottom_panel()
        v_splitter.addWidget(bottom_panel)
        v_splitter.setStretchFactor(0, 2)
        v_splitter.setStretchFactor(1, 3)

        layout.addWidget(v_splitter, 1)

        # 시그널 연결
        self._reg_map.register_changed.connect(self._on_register_changed)
        self._reg_map.full_map_changed.connect(self._on_full_map_changed)

        # 초기 갱신
        self._update_visualizer()
        self._refresh_register_table()
        self._load_ui_settings()

    def on_device_connected(self) -> None:
        self.status_message.emit(f"PI6CG18201 준비 (0x{self._slave_address:02X})")

    def on_device_disconnected(self) -> None:
        self.stop_communication()

    def start_communication(self) -> None:
        pass  # PI6CG18201은 사용자 트리거 방식 (라이브 모드는 제어 변경 시 자동 전송)

    def stop_communication(self) -> None:
        pass

    def update_data(self) -> None:
        """하드웨어에서 레지스터 읽기"""
        self._on_read_registers()

    # ── UI 빌더 ──

    def _create_control_panel(self) -> QGroupBox:
        """좌측 제어 패널"""
        group = QGroupBox("제어 센터")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        # 고급 모드
        mode_row = QHBoxLayout()
        self._advanced_mode_cb = QCheckBox("고급 모드")
        self._advanced_mode_cb.setToolTip("ON: Byte/Bit 매핑 정보를 표시합니다.")
        self._advanced_mode_cb.stateChanged.connect(self._on_advanced_mode_changed)
        mode_row.addWidget(self._advanced_mode_cb)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # 출력 활성화 (OE)
        oe_label = QLabel("출력 활성화 (Output Enable)")
        oe_label.setStyleSheet("color: #88c0ff; font-weight: bold; font-size: 12px;")
        layout.addWidget(oe_label)

        oe_frame = QFrame()
        oe_layout = QGridLayout(oe_frame)
        oe_layout.setSpacing(8)

        self._oe_checks: list[QCheckBox] = []
        ch_colors = ['#00d2ff', '#ff64b4']
        ch_labels = ['Q0 (Q0+/Q0-)', 'Q1 (Q1+/Q1-)']
        for i in range(2):
            cb = QCheckBox(f" {ch_labels[i]}")
            cb.setChecked(True)
            cb.setStyleSheet(f"""
                QCheckBox {{ font-size: 13px; font-weight: bold; }}
                QCheckBox::indicator:checked {{ background-color: {ch_colors[i]}; }}
            """)
            cb.stateChanged.connect(self._on_control_changed)
            self._oe_checks.append(cb)
            oe_layout.addWidget(cb, 0, i)
        layout.addWidget(oe_frame)

        self._oe_hint = QLabel("고급: OE_Q0=Byte0[1], OE_Q1=Byte0[2]")
        self._oe_hint.setStyleSheet("color: #7f8aa4; font-size: 11px;")
        self._oe_hint.setVisible(False)
        self._advanced_hint_labels.append(self._oe_hint)
        layout.addWidget(self._oe_hint)

        # 진폭 (Amplitude)
        amp_label = QLabel("출력 진폭 (Amplitude)")
        amp_label.setStyleSheet("color: #88c0ff; font-weight: bold; font-size: 12px;")
        layout.addWidget(amp_label)

        amp_frame = QFrame()
        amp_layout = QHBoxLayout(amp_frame)
        amp_layout.setContentsMargins(4, 2, 4, 2)
        amp_layout.addWidget(QLabel("출력 전압 레벨"))
        self._amplitude_combo = QComboBox()
        self._amplitude_combo.addItems(["0.6 V", "0.7 V", "0.8 V", "0.9 V"])
        self._amplitude_combo.setCurrentIndex(2)
        self._amplitude_combo.currentIndexChanged.connect(self._on_control_changed)
        amp_layout.addWidget(self._amplitude_combo)
        amp_layout.addStretch()
        self._amp_indicator = QLabel("● 0.8V")
        self._amp_indicator.setStyleSheet("color: #ffcc44; font-weight: bold; font-size: 14px;")
        amp_layout.addWidget(self._amp_indicator)
        layout.addWidget(amp_frame)

        self._amp_hint = QLabel("고급: AMPLITUDE=Byte1[1:0]")
        self._amp_hint.setStyleSheet("color: #7f8aa4; font-size: 11px;")
        self._amp_hint.setVisible(False)
        self._advanced_hint_labels.append(self._amp_hint)
        layout.addWidget(self._amp_hint)

        # 스프레드 스펙트럼
        ss_label = QLabel("스프레드 스펙트럼 (Spread Spectrum)")
        ss_label.setStyleSheet("color: #88c0ff; font-weight: bold; font-size: 12px;")
        layout.addWidget(ss_label)

        ss_frame = QFrame()
        ss_layout = QHBoxLayout(ss_frame)
        ss_layout.setContentsMargins(4, 2, 4, 2)
        ss_layout.addWidget(QLabel("동작 모드"))
        self._ss_combo = QComboBox()
        self._ss_combo.addItems([
            "자동 (하드웨어 핀 설정 사용)",
            "끄기", "약하게 (-0.25%)", "강하게 (-0.5%)",
        ])
        self._ss_combo.currentIndexChanged.connect(self._on_control_changed)
        ss_layout.addWidget(self._ss_combo)
        ss_layout.addStretch()
        self._ss_readback_badge = QLabel("현재: -")
        self._ss_readback_badge.setStyleSheet(
            "color: #9ac6ff; font-weight: bold; background-color: #26374f; "
            "padding: 2px 8px; border-radius: 8px;"
        )
        self._ss_readback_badge.setVisible(False)
        ss_layout.addWidget(self._ss_readback_badge)
        layout.addWidget(ss_frame)

        self._ss_hint = QLabel("고급: SS_SW_CTRL=Byte1[5], SS_MODE=Byte1[4:3]")
        self._ss_hint.setStyleSheet("color: #7f8aa4; font-size: 11px;")
        self._ss_hint.setVisible(False)
        self._advanced_hint_labels.append(self._ss_hint)
        layout.addWidget(self._ss_hint)

        # Slew Rate
        slew_label = QLabel("Slew Rate 조절")
        slew_label.setStyleSheet("color: #88c0ff; font-weight: bold; font-size: 12px;")
        layout.addWidget(slew_label)

        slew_frame = QFrame()
        slew_grid = QGridLayout(slew_frame)
        slew_grid.setSpacing(6)

        slew_grid.addWidget(QLabel("출력 엣지 속도 (Q0/Q1):"), 0, 0)
        self._slew_coarse_combo = QComboBox()
        self._slew_coarse_combo.addItems([
            "둘 다 느리게", "Q0 빠르게 / Q1 느리게",
            "Q0 느리게 / Q1 빠르게", "둘 다 빠르게",
        ])
        self._slew_coarse_combo.setCurrentIndex(2)
        self._slew_coarse_combo.currentIndexChanged.connect(self._on_control_changed)
        slew_grid.addWidget(self._slew_coarse_combo, 0, 1)

        slew_grid.addWidget(QLabel("기준클럭(REF) 엣지 속도:"), 1, 0)
        self._slew_fine_combo = QComboBox()
        self._slew_fine_combo.addItems(["느림", "보통", "빠름", "매우 빠름"])
        self._slew_fine_combo.currentIndexChanged.connect(self._on_control_changed)
        slew_grid.addWidget(self._slew_fine_combo, 1, 1)

        self._slew_indicator = QLabel("Combined: Lv.8/15")
        self._slew_indicator.setStyleSheet("color: #66ccaa; font-weight: bold;")
        slew_grid.addWidget(self._slew_indicator, 2, 0, 1, 2)
        layout.addWidget(slew_frame)

        self._slew_hint = QLabel("고급: SLEW_Q1=Byte2[2], SLEW_Q0=Byte2[1], REF_SLEW=Byte3[7:6]")
        self._slew_hint.setStyleSheet("color: #7f8aa4; font-size: 11px;")
        self._slew_hint.setVisible(False)
        self._advanced_hint_labels.append(self._slew_hint)
        layout.addWidget(self._slew_hint)

        # 라이브 모드
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("color: #3a3f50;")
        layout.addWidget(separator)

        live_frame = QFrame()
        live_layout = QHBoxLayout(live_frame)
        live_layout.setContentsMargins(4, 2, 4, 2)
        self._live_mode_cb = QCheckBox(" 라이브 모드 (실시간 I2C 전송)")
        self._live_mode_cb.setStyleSheet("""
            QCheckBox { font-weight: bold; color: #ff8888; font-size: 12px; }
            QCheckBox::indicator:checked { background-color: #cc3344; border-color: #ff4455; }
        """)
        self._live_mode_cb.stateChanged.connect(self._on_live_mode_changed)
        live_layout.addWidget(self._live_mode_cb)
        layout.addWidget(live_frame)

        # 수동 전송 버튼
        btn_frame = QFrame()
        btn_layout = QHBoxLayout(btn_frame)
        btn_layout.setContentsMargins(4, 2, 4, 2)
        self._write_btn = QPushButton("Write")
        self._write_btn.setFixedWidth(100)
        self._write_btn.clicked.connect(self._on_write_registers)
        btn_layout.addWidget(self._write_btn)
        self._read_btn = QPushButton("Read")
        self._read_btn.setFixedWidth(100)
        self._read_btn.clicked.connect(self._on_read_registers)
        btn_layout.addWidget(self._read_btn)
        btn_layout.addStretch()
        layout.addWidget(btn_frame)

        self._set_advanced_mode(False)
        layout.addStretch()
        return group

    def _create_visualizer_panel(self) -> QGroupBox:
        """우측 파형 시각화 패널"""
        group = QGroupBox("파형 시각화 (Waveform Visualizer)")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 6, 6, 6)
        self._visualizer = ClockVisualizer()
        layout.addWidget(self._visualizer, 1)
        return group

    def _create_bottom_panel(self) -> QTabWidget:
        """하단 레지스터 맵 + 로그"""
        tabs = QTabWidget()

        # 레지스터 맵 탭
        reg_tab = QWidget()
        reg_layout = QVBoxLayout(reg_tab)
        reg_layout.setContentsMargins(6, 6, 6, 6)

        # Overview
        overview_label = QLabel("레지스터 맵 (8-Byte Overview)")
        overview_label.setStyleSheet("color: #88c0ff; font-weight: bold; font-size: 12px;")
        reg_layout.addWidget(overview_label)

        self._reg_overview_table = QTableWidget(1, 8)
        self._reg_overview_table.setHorizontalHeaderLabels([f"Byte {i}" for i in range(8)])
        self._reg_overview_table.setVerticalHeaderLabels(["Hex"])
        self._reg_overview_table.verticalHeader().setDefaultSectionSize(30)
        self._reg_overview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._reg_overview_table.setMaximumHeight(70)
        self._reg_overview_table.setAlternatingRowColors(True)
        self._reg_overview_table.cellChanged.connect(self._on_overview_cell_changed)
        reg_layout.addWidget(self._reg_overview_table)

        # Detail
        detail_label = QLabel("비트 필드 상세 (Bit Field Detail)")
        detail_label.setStyleSheet("color: #88c0ff; font-weight: bold; font-size: 12px;")
        detail_header = QHBoxLayout()
        detail_header.addWidget(detail_label)
        detail_header.addStretch()
        self._show_advanced_columns_cb = QCheckBox("Byte/비트 컬럼 표시")
        self._show_advanced_columns_cb.setChecked(True)
        self._show_advanced_columns_cb.stateChanged.connect(self._on_advanced_columns_changed)
        detail_header.addWidget(self._show_advanced_columns_cb)
        reg_layout.addLayout(detail_header)

        self._reg_detail_table = QTableWidget(len(REGISTER_FIELDS), 7)
        self._reg_detail_table.setHorizontalHeaderLabels([
            "필드명", "설명", "Byte", "비트 범위", "값", "Hex", "설정"
        ])
        self._reg_detail_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._reg_detail_table.horizontalHeader().setStretchLastSection(True)
        self._reg_detail_table.setAlternatingRowColors(True)
        self._reg_detail_table.verticalHeader().setDefaultSectionSize(28)
        self._reg_detail_table.setColumnWidth(0, 120)
        self._reg_detail_table.setColumnWidth(1, 280)
        self._reg_detail_table.setColumnWidth(2, 50)
        self._reg_detail_table.setColumnWidth(3, 80)
        self._reg_detail_table.setColumnWidth(4, 60)
        self._reg_detail_table.setColumnWidth(5, 60)

        for row, bf in enumerate(REGISTER_FIELDS):
            name_item = QTableWidgetItem(bf.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if bf.read_only:
                name_item.setForeground(QColor(100, 105, 120))
            else:
                name_item.setForeground(QColor(100, 200, 255))
            self._reg_detail_table.setItem(row, 0, name_item)

            desc_item = QTableWidgetItem(bf.description)
            desc_item.setFlags(desc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._reg_detail_table.setItem(row, 1, desc_item)

            byte_item = QTableWidgetItem(str(bf.byte_index))
            byte_item.setFlags(byte_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            byte_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._reg_detail_table.setItem(row, 2, byte_item)

            range_item = QTableWidgetItem(bf.bit_range_str)
            range_item.setFlags(range_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            range_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._reg_detail_table.setItem(row, 3, range_item)

            val_item = QTableWidgetItem("0")
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if bf.read_only:
                val_item.setFlags(val_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                val_item.setForeground(QColor(100, 105, 120))
            else:
                val_item.setForeground(QColor(220, 255, 200))
            self._reg_detail_table.setItem(row, 4, val_item)

            hex_item = QTableWidgetItem("0x00")
            hex_item.setFlags(hex_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            hex_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._reg_detail_table.setItem(row, 5, hex_item)

            if bf.options:
                opt_text = bf.options.get(0, "—")
                opt_item = QTableWidgetItem(opt_text)
                opt_item.setFlags(opt_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                opt_item.setForeground(QColor(180, 200, 140))
            else:
                opt_item = QTableWidgetItem("—")
                opt_item.setFlags(opt_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                opt_item.setForeground(QColor(80, 85, 100))
            self._reg_detail_table.setItem(row, 6, opt_item)

        self._reg_detail_table.cellChanged.connect(self._on_detail_cell_changed)
        reg_layout.addWidget(self._reg_detail_table, 1)

        tabs.addTab(reg_tab, "레지스터 맵")

        # 로그 탭
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        log_layout.setContentsMargins(6, 6, 6, 6)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("I2C 패킷 로그"))
        self._clear_log_btn = QPushButton("로그 지우기")
        self._clear_log_btn.setFixedWidth(120)
        self._clear_log_btn.clicked.connect(lambda: self._log_text.clear())
        log_header.addStretch()
        log_header.addWidget(self._clear_log_btn)
        log_layout.addLayout(log_header)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setFont(QFont("Consolas", 10))
        log_layout.addWidget(self._log_text, 1)

        tabs.addTab(log_tab, "I2C 로그")
        return tabs

    # ── 핸들러 ──

    @Slot()
    def _on_addr_changed(self) -> None:
        self._slave_address = int(self._addr_combo.currentData())

    @Slot()
    def _on_control_changed(self) -> None:
        """제어 패널 값 변경 → RegisterMap 업데이트"""
        for i, cb in enumerate(self._oe_checks):
            self._reg_map.set_field(f"OE_Q{i}", int(cb.isChecked()), emit=False)
        self._reg_map.set_field("AMPLITUDE", self._amplitude_combo.currentIndex(), emit=False)
        self._apply_ss_combo_to_regmap(self._ss_combo.currentIndex())
        self._reg_map.slew_rate_coarse = self._slew_coarse_combo.currentIndex()
        self._reg_map.slew_rate_fine = self._slew_fine_combo.currentIndex()

        amp_v = self._reg_map.amplitude_voltage
        self._amp_indicator.setText(f"● {amp_v:.1f}V")
        combined = self._reg_map.slew_rate_combined
        self._slew_indicator.setText(f"Combined: Lv.{combined}/15")
        self._update_ss_readback_badge()
        self._reg_map.full_map_changed.emit()

        if self._live_mode and self._ftdi.is_connected:
            self._ftdi.smbus_block_write(self._slave_address, 0x00, self._reg_map.get_all_bytes())

    @Slot(int)
    def _on_advanced_mode_changed(self, state: int) -> None:
        self._set_advanced_mode(state == Qt.CheckState.Checked.value)
        self._save_ui_settings()

    @Slot(int)
    def _on_advanced_columns_changed(self, _state: int) -> None:
        self._update_detail_advanced_columns()
        self._save_ui_settings()

    def _set_advanced_mode(self, enabled: bool) -> None:
        self._advanced_mode = enabled
        for label in self._advanced_hint_labels:
            label.setVisible(enabled)
        self._update_detail_advanced_columns()

    def _update_detail_advanced_columns(self) -> None:
        show = self._show_advanced_columns_cb.isChecked() if hasattr(self, "_show_advanced_columns_cb") else False
        if hasattr(self, "_reg_detail_table"):
            self._reg_detail_table.setColumnHidden(2, not show)
            self._reg_detail_table.setColumnHidden(3, not show)

    def _update_ss_readback_badge(self) -> None:
        is_auto = self._ss_combo.currentIndex() == 0
        self._ss_readback_badge.setVisible(is_auto)
        if not is_auto:
            return
        readback = self._reg_map.get_field("SS_READBACK")
        text_map = {0: "SS Off", 1: "-0.25%", 3: "-0.5%"}
        self._ss_readback_badge.setText(f"현재: {text_map.get(readback, f'예약({readback})')}")

    def _apply_ss_combo_to_regmap(self, index: int) -> None:
        if index == 0:
            self._reg_map.set_field("SS_SW_CTRL", 0, emit=False)
            return
        self._reg_map.set_field("SS_SW_CTRL", 1, emit=False)
        mode_map = {1: 0, 2: 1, 3: 3}
        self._reg_map.set_field("SS_MODE", mode_map.get(index, 0), emit=False)

    def _get_ss_combo_index_from_regmap(self) -> int:
        if self._reg_map.get_field("SS_SW_CTRL") == 0:
            return 0
        mode = self._reg_map.get_field("SS_MODE")
        return {0: 1, 1: 2, 3: 3}.get(mode, 0)

    @Slot(int)
    def _on_live_mode_changed(self, state: int) -> None:
        self._live_mode = state == Qt.CheckState.Checked.value

    @Slot()
    def _on_write_registers(self) -> None:
        if not self._ftdi.is_connected:
            return
        data = self._reg_map.get_all_bytes()
        self._ftdi.smbus_block_write(self._slave_address, 0x00, data)

    @Slot()
    def _on_read_registers(self) -> None:
        if not self._ftdi.is_connected:
            return
        data = self._ftdi.smbus_block_read(self._slave_address, 0x00, TOTAL_BYTES)
        if data:
            self._reg_map.set_all_bytes(data)
            self._sync_controls_from_regmap()

    @Slot(int, int)
    def _on_register_changed(self, byte_index: int, new_value: int) -> None:
        self._refresh_register_table()
        self._update_visualizer()

    @Slot()
    def _on_full_map_changed(self) -> None:
        self._refresh_register_table()
        self._update_visualizer()

    def _refresh_register_table(self) -> None:
        self._reg_overview_table.blockSignals(True)
        for col in range(TOTAL_BYTES):
            hex_val = self._reg_map.get_hex_string(col)
            item = self._reg_overview_table.item(0, col)
            if item is None:
                item = QTableWidgetItem(hex_val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
                self._reg_overview_table.setItem(0, col, item)
            else:
                item.setText(hex_val)
        self._reg_overview_table.blockSignals(False)

        self._reg_detail_table.blockSignals(True)
        for row, bf in enumerate(REGISTER_FIELDS):
            value = self._reg_map.get_field(bf.name)
            byte_val = self._reg_map.get_byte(bf.byte_index)
            val_item = self._reg_detail_table.item(row, 4)
            if val_item:
                val_item.setText(str(value))
            hex_item = self._reg_detail_table.item(row, 5)
            if hex_item:
                hex_item.setText(f"0x{byte_val:02X}")
            opt_item = self._reg_detail_table.item(row, 6)
            if opt_item and bf.options:
                opt_item.setText(bf.options.get(value, f"Unknown({value})"))
        self._reg_detail_table.blockSignals(False)

    @Slot(int, int)
    def _on_overview_cell_changed(self, row: int, col: int) -> None:
        item = self._reg_overview_table.item(row, col)
        if item is None:
            return
        text = item.text().strip()
        try:
            if text.startswith(("0x", "0X")):
                value = int(text, 16)
            elif text.isdigit():
                value = int(text, 10)
            else:
                value = int(text, 16)
            if 0 <= value <= 0xFF:
                self._reg_map.set_byte(col, value)
                self._sync_controls_from_regmap()
        except ValueError:
            self._refresh_register_table()

    @Slot(int, int)
    def _on_detail_cell_changed(self, row: int, col: int) -> None:
        if col != 4:
            return
        bf = REGISTER_FIELDS[row]
        if bf.read_only:
            return
        item = self._reg_detail_table.item(row, col)
        if item is None:
            return
        try:
            value = int(item.text().strip())
            max_val = (1 << bf.width) - 1
            if 0 <= value <= max_val:
                self._reg_map.set_field(bf.name, value)
                self._sync_controls_from_regmap()
            else:
                self._refresh_register_table()
        except ValueError:
            self._refresh_register_table()

    def _sync_controls_from_regmap(self) -> None:
        for i, cb in enumerate(self._oe_checks):
            cb.blockSignals(True)
            cb.setChecked(self._reg_map.get_field(f"OE_Q{i}") == 1)
            cb.blockSignals(False)

        self._amplitude_combo.blockSignals(True)
        self._amplitude_combo.setCurrentIndex(self._reg_map.amplitude)
        self._amplitude_combo.blockSignals(False)

        self._ss_combo.blockSignals(True)
        self._ss_combo.setCurrentIndex(self._get_ss_combo_index_from_regmap())
        self._ss_combo.blockSignals(False)

        self._slew_coarse_combo.blockSignals(True)
        self._slew_coarse_combo.setCurrentIndex(self._reg_map.slew_rate_coarse)
        self._slew_coarse_combo.blockSignals(False)

        self._slew_fine_combo.blockSignals(True)
        self._slew_fine_combo.setCurrentIndex(self._reg_map.slew_rate_fine)
        self._slew_fine_combo.blockSignals(False)

        self._amp_indicator.setText(f"● {self._reg_map.amplitude_voltage:.1f}V")
        self._slew_indicator.setText(f"Combined: Lv.{self._reg_map.slew_rate_combined}/15")
        self._update_ss_readback_badge()
        self._update_visualizer()

    def _update_visualizer(self) -> None:
        oe_states = [self._reg_map.oe_q0, self._reg_map.oe_q1]
        q_slew_bits = [
            self._reg_map.get_field("SLEW_Q0"),
            self._reg_map.get_field("SLEW_Q1"),
        ]
        self._visualizer.update_parameters(
            amplitude_v=self._reg_map.amplitude_voltage,
            slew_rate_level=self._reg_map.slew_rate_combined,
            oe_states=oe_states,
            q_slew_bits=q_slew_bits,
        )

    def _load_ui_settings(self) -> None:
        advanced = self._settings.value("pi6cg/advanced_mode", False, type=bool)
        self._advanced_mode_cb.blockSignals(True)
        self._advanced_mode_cb.setChecked(advanced)
        self._advanced_mode_cb.blockSignals(False)
        self._set_advanced_mode(advanced)

    def _save_ui_settings(self) -> None:
        self._settings.setValue("pi6cg/advanced_mode", self._advanced_mode)
