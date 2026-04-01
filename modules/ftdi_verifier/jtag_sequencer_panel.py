"""
JTAG Sequencer Right Panel \u2014 TAP State Diagram + Pin Status + TDO Logger + Mapping.

JTAG \ubaa8\ub4dc \uc120\ud0dd \uc2dc \ud540\uc544\uc6c3 \uc704\uce58\uc5d0 \ud45c\uc2dc\ub418\ub294 \uc6b0\uce21 \ud328\ub110.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QGroupBox, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
    QTextEdit, QSizePolicy, QFrame,
)

from core.theme_manager import ThemeManager
from modules.ftdi_verifier.jtag_tap_diagram import TapStateDiagram


class _PinLed(QLabel):
    """Compact pin status LED indicator."""

    def __init__(self, name: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._name = name
        self._state = False
        self.setFixedSize(14, 14)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_style()

    @property
    def state(self) -> bool:
        return self._state

    def set_state(self, high: bool) -> None:
        if self._state != high:
            self._state = high
            self._update_style()

    def _update_style(self) -> None:
        tm = ThemeManager.instance()
        if self._state:
            bg = "#4A90E2"
        else:
            bg = tm.color("jtag_tap_state") if tm.is_dark() else "#D0D4DC"
        self.setStyleSheet(
            f"background: {bg}; border-radius: 7px; border: 1px solid #3A3F50;"
        )


def _make_section_header(title: str) -> QWidget:
    """Create a compact section header: title label + horizontal line."""
    w = QWidget()
    w.setFixedHeight(20)
    lay = QHBoxLayout(w)
    lay.setContentsMargins(4, 0, 4, 0)
    lay.setSpacing(6)
    lbl = QLabel(title)
    lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
    lbl.setProperty("_section_title", True)
    lay.addWidget(lbl)
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setProperty("_section_line", True)
    lay.addWidget(line, 1)
    return w


def _make_hsep() -> QFrame:
    """Thin horizontal separator line."""
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFixedHeight(1)
    return sep


class JtagSequencerPanel(QWidget):
    """JTAG \ubaa8\ub4dc \uc6b0\uce21 \ud328\ub110 \u2014 TAP Diagram + Pin Status + TDO Logger + \ub9e4\ud551."""

    # Quick operation button signals (connected by parent module)
    read_idcode_clicked = Signal()
    reset_tap_clicked = Signal()
    bypass_test_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._init_ui()

        tm = ThemeManager.instance()
        tm.theme_changed.connect(self._apply_theme)
        self._apply_theme()

    # \u2500\u2500 UI \uad6c\uc131 \u2500\u2500

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # \u2500\u2500 TOP: [Device Chain] | [TAP Diagram] | [Pin Status + Exec] \u2500\u2500
        tap_container = QWidget()
        tap_main = QVBoxLayout(tap_container)
        tap_main.setContentsMargins(0, 0, 0, 0)
        tap_main.setSpacing(0)

        self._tap_header = _make_section_header("TAP State Machine")
        tap_main.addWidget(self._tap_header)

        tap_body = QHBoxLayout()
        tap_body.setContentsMargins(2, 2, 2, 2)
        tap_body.setSpacing(4)

        # \u2500\u2500 LEFT column: Device Chain Info + Quick Ops \u2500\u2500
        left_panel = QWidget()
        left_panel.setFixedWidth(130)
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(4, 0, 4, 4)
        left_lay.setSpacing(4)

        chain_title = QLabel("Device Chain")
        chain_title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        chain_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_lay.addWidget(chain_title)

        self._chain_sep = _make_hsep()
        left_lay.addWidget(self._chain_sep)

        # Chain info labels
        info_font = QFont("Consolas", 8)
        self._chain_labels = {}
        for key, default in [
            ("Devices", "- "),
            ("IDCODE", "- "),
            ("Mfr", "- "),
            ("Part", "- "),
            ("IR Len", "- "),
        ]:
            row = QHBoxLayout()
            row.setSpacing(2)
            k_lbl = QLabel(f"{key}:")
            k_lbl.setFont(QFont("Segoe UI", 8))
            k_lbl.setFixedWidth(42)
            v_lbl = QLabel(default)
            v_lbl.setFont(info_font)
            v_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(k_lbl)
            row.addWidget(v_lbl, 1)
            left_lay.addLayout(row)
            self._chain_labels[key] = (k_lbl, v_lbl)

        left_lay.addStretch()

        # Quick Operations
        ops_title = QLabel("Quick Ops")
        ops_title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        ops_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_lay.addWidget(ops_title)

        self._ops_sep = _make_hsep()
        left_lay.addWidget(self._ops_sep)

        self._btn_read_idcode = QPushButton("Read IDCODE")
        self._btn_read_idcode.setFixedHeight(26)
        self._btn_read_idcode.clicked.connect(self.read_idcode_clicked)
        left_lay.addWidget(self._btn_read_idcode)

        self._btn_reset_tap = QPushButton("Reset TAP")
        self._btn_reset_tap.setFixedHeight(26)
        self._btn_reset_tap.clicked.connect(self.reset_tap_clicked)
        left_lay.addWidget(self._btn_reset_tap)

        self._btn_bypass_test = QPushButton("Bypass Test")
        self._btn_bypass_test.setFixedHeight(26)
        self._btn_bypass_test.clicked.connect(self.bypass_test_clicked)
        left_lay.addWidget(self._btn_bypass_test)

        self._left_panel = left_panel
        self._chain_title = chain_title
        self._ops_title = ops_title
        tap_body.addWidget(left_panel)

        # \u2500\u2500 CENTER: TAP Diagram \u2500\u2500
        self._tap_diagram = TapStateDiagram()
        self._tap_diagram.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        tap_body.addWidget(self._tap_diagram, 1)

        # \u2500\u2500 RIGHT column: Pin Status + Execution Counters \u2500\u2500
        right_panel = QWidget()
        right_panel.setFixedWidth(120)
        right_lay = QVBoxLayout(right_panel)
        right_lay.setContentsMargins(4, 0, 4, 4)
        right_lay.setSpacing(4)

        pin_title = QLabel("Pin Status")
        pin_title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        pin_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_lay.addWidget(pin_title)

        self._pin_sep = _make_hsep()
        right_lay.addWidget(self._pin_sep)

        self._pin_leds = {}
        for pin_name in ("TCK", "TMS", "TDI", "TDO", "RST"):
            row = QHBoxLayout()
            row.setSpacing(4)
            led = _PinLed(pin_name)
            lbl = QLabel(pin_name)
            lbl.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
            lbl.setFixedWidth(32)
            state_lbl = QLabel("LOW")
            state_lbl.setFont(QFont("Consolas", 8))
            state_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(led)
            row.addWidget(lbl)
            row.addWidget(state_lbl, 1)
            right_lay.addLayout(row)
            self._pin_leds[pin_name] = (led, lbl, state_lbl)

        # TAP state display
        self._state_sep = _make_hsep()
        right_lay.addWidget(self._state_sep)

        state_title = QLabel("Current State")
        state_title.setFont(QFont("Segoe UI", 8))
        state_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_lay.addWidget(state_title)

        self._state_label = QLabel("TLR")
        self._state_label.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        self._state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state_label.setFixedHeight(24)
        right_lay.addWidget(self._state_label)

        # Execution counters
        self._exec_sep = _make_hsep()
        right_lay.addWidget(self._exec_sep)

        exec_title = QLabel("Execution")
        exec_title.setFont(QFont("Segoe UI", 8))
        exec_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_lay.addWidget(exec_title)

        counter_font = QFont("Consolas", 9)
        self._exec_labels = {}
        for key, default in [("Cycles", "0 / 0"), ("Pass", "0"), ("Fail", "0")]:
            row = QHBoxLayout()
            row.setSpacing(2)
            k_lbl = QLabel(f"{key}:")
            k_lbl.setFont(QFont("Segoe UI", 8))
            v_lbl = QLabel(default)
            v_lbl.setFont(counter_font)
            v_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(k_lbl)
            row.addWidget(v_lbl, 1)
            right_lay.addLayout(row)
            self._exec_labels[key] = (k_lbl, v_lbl)

        right_lay.addStretch()

        self._right_panel = right_panel
        self._pin_title = pin_title
        self._state_title = state_title
        self._exec_title = exec_title
        tap_body.addWidget(right_panel)

        tap_main.addLayout(tap_body, 1)

        # \u2500\u2500 MIDDLE: Mapping Management \u2500\u2500
        mapping_container = QWidget()
        mapping_main = QVBoxLayout(mapping_container)
        mapping_main.setContentsMargins(0, 0, 0, 0)
        mapping_main.setSpacing(0)

        self._mapping_header = _make_section_header("Mapping Management")
        mapping_main.addWidget(self._mapping_header)

        mapping_body = QVBoxLayout()
        mapping_body.setContentsMargins(2, 4, 2, 2)
        mapping_body.setSpacing(4)

        header_row = QHBoxLayout()
        self._target_file_lbl = QLabel("Target File:")
        header_row.addWidget(self._target_file_lbl)
        self._mapping_file_label = QLabel("-")
        self._mapping_file_label.setFont(QFont("Consolas", 9))
        header_row.addWidget(self._mapping_file_label, 1)
        self._mapping_import_btn = QPushButton("CSV Import")
        self._mapping_import_btn.setFixedHeight(24)
        self._mapping_export_btn = QPushButton("CSV Export")
        self._mapping_export_btn.setFixedHeight(24)
        header_row.addWidget(self._mapping_import_btn)
        header_row.addWidget(self._mapping_export_btn)
        mapping_body.addLayout(header_row)

        self._mapping_table = QTableWidget(0, 5)
        self._mapping_table.setHorizontalHeaderLabels(
            ["Signal", "Type", "Bits", "Dir", "Mapped Pin"]
        )
        h_hdr = self._mapping_table.horizontalHeader()
        h_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4):
            h_hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        self._mapping_table.setColumnWidth(1, 55)
        self._mapping_table.setColumnWidth(2, 60)
        self._mapping_table.setColumnWidth(3, 50)
        self._mapping_table.setColumnWidth(4, 90)
        self._mapping_table.verticalHeader().setVisible(False)
        self._mapping_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._mapping_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        mapping_body.addWidget(self._mapping_table, 1)

        self._mapping_status = QLabel("Mapping Status: -")
        self._mapping_status.setFont(QFont("Segoe UI", 8))
        mapping_body.addWidget(self._mapping_status)

        mapping_main.addLayout(mapping_body, 1)

        # \u2500\u2500 BOTTOM: TDO Data Logger \u2500\u2500
        tdo_container = QWidget()
        tdo_main = QVBoxLayout(tdo_container)
        tdo_main.setContentsMargins(0, 0, 0, 0)
        tdo_main.setSpacing(0)

        self._tdo_header = _make_section_header("TDO Data Logger")
        tdo_main.addWidget(self._tdo_header)

        tdo_body = QVBoxLayout()
        tdo_body.setContentsMargins(2, 4, 2, 2)
        tdo_body.setSpacing(2)

        tdo_btn_row = QHBoxLayout()
        self._tdo_clear_btn = QPushButton("Clear")
        self._tdo_clear_btn.setFixedHeight(24)
        self._tdo_clear_btn.clicked.connect(self._on_tdo_clear)
        self._tdo_export_btn = QPushButton("Export")
        self._tdo_export_btn.setFixedHeight(24)
        tdo_btn_row.addStretch()
        tdo_btn_row.addWidget(self._tdo_clear_btn)
        tdo_btn_row.addWidget(self._tdo_export_btn)
        tdo_body.addLayout(tdo_btn_row)

        self._tdo_log = QTextEdit()
        self._tdo_log.setReadOnly(True)
        self._tdo_log.setFont(QFont("Consolas", 10))
        self._tdo_log.setPlaceholderText("TDO raw data will appear here...")
        tdo_body.addWidget(self._tdo_log, 1)

        tdo_main.addLayout(tdo_body, 1)

        # 3.5 : 4.5 : 2 ratio \u2014 TAP 35%, Mapping 45%, TDO 20%
        splitter.addWidget(tap_container)
        splitter.addWidget(mapping_container)
        splitter.addWidget(tdo_container)
        splitter.setStretchFactor(0, 35)
        splitter.setStretchFactor(1, 45)
        splitter.setStretchFactor(2, 20)
        splitter.setSizes([350, 420, 130])
        splitter.setChildrenCollapsible(False)

        layout.addWidget(splitter)

        self._tap_container = tap_container
        self._mapping_container = mapping_container
        self._tdo_container = tdo_container

    # \u2500\u2500 public API \u2500\u2500

    def set_mapping_file(self, name: str) -> None:
        self._mapping_file_label.setText(name)

    def clear_mapping(self) -> None:
        self._mapping_table.setRowCount(0)
        self._mapping_file_label.setText("-")
        self._mapping_status.setText("Mapping Status: -")

    def set_current_state(self, state: str) -> None:
        self._tap_diagram.set_current_state(state)
        short = state.replace("Test-Logic-Reset", "TLR") \
                     .replace("Run-Test/Idle", "RTI") \
                     .replace("Select-DR-Scan", "Sel-DR") \
                     .replace("Select-IR-Scan", "Sel-IR")
        self._state_label.setText(short)

    def set_pin_state(self, pin: str, high: bool) -> None:
        """Update a JTAG pin LED. pin: 'TCK'|'TMS'|'TDI'|'TDO'."""
        if pin in self._pin_leds:
            led, lbl, state_lbl = self._pin_leds[pin]
            led.set_state(high)
            state_lbl.setText("HIGH" if high else "LOW")

    def set_chain_info(
        self,
        devices: str = "- ",
        idcode: str = "- ",
        mfr: str = "- ",
        part: str = "- ",
        ir_len: str = "- ",
    ) -> None:
        """Update Device Chain info labels."""
        mapping = {
            "Devices": devices, "IDCODE": idcode,
            "Mfr": mfr, "Part": part, "IR Len": ir_len,
        }
        for key, val in mapping.items():
            if key in self._chain_labels:
                self._chain_labels[key][1].setText(val)

    def set_exec_counters(
        self, cycles: str = "0 / 0", passed: str = "0", failed: str = "0"
    ) -> None:
        """Update execution counter labels."""
        self._exec_labels["Cycles"][1].setText(cycles)
        self._exec_labels["Pass"][1].setText(passed)
        self._exec_labels["Fail"][1].setText(failed)

    def append_tdo_data(self, data: str) -> None:
        self._tdo_log.append(data)

    def clear_tdo_log(self) -> None:
        self._tdo_log.clear()

    # \u2500\u2500 slots \u2500\u2500

    def _on_tdo_clear(self) -> None:
        self._tdo_log.clear()

    # \u2500\u2500 \ud14c\ub9c8 \u2500\u2500

    def _apply_theme(self) -> None:
        tm = ThemeManager.instance()

        mapping_bg = tm.color("jtag_mapping_bg")
        text = tm.color("jtag_tap_text")
        border = tm.color("jtag_btn_border")
        surface = tm.color("jtag_tap_state")
        highlight = tm.color("jtag_tap_state_active")
        panel_bg = tm.color("jtag_tap_bg")

        # Section headers: title label + horizontal line
        header_style = (
            f"QLabel[_section_title=\"true\"] {{ color: {text}; }}"
            f"QFrame[_section_line=\"true\"] {{ background: {border}; }}"
        )
        self._tap_header.setStyleSheet(header_style)
        self._mapping_header.setStyleSheet(header_style)
        self._tdo_header.setStyleSheet(header_style)

        # Container backgrounds
        container_bg = f"background: {panel_bg};"
        self._tap_container.setStyleSheet(
            f"QWidget {{ {container_bg} }}"
        )

        self._mapping_table.setStyleSheet(
            f"QTableWidget {{ background: {mapping_bg}; color: {text}; "
            f"gridline-color: {border}; border: 1px solid {border}; }}"
            f"QHeaderView::section {{ background: {surface}; "
            f"color: {text}; border: 1px solid {border}; padding: 2px; }}"
        )

        btn_style = (
            f"QPushButton {{ background: {tm.color('jtag_btn_bg')}; "
            f"color: {tm.color('jtag_btn_text')}; border: 1px solid {border}; "
            f"border-radius: 2px; padding: 2px 8px; }}"
            f"QPushButton:hover {{ background: {tm.color('jtag_btn_hover')}; }}"
        )
        self._mapping_import_btn.setStyleSheet(btn_style)
        self._mapping_export_btn.setStyleSheet(btn_style)
        self._tdo_clear_btn.setStyleSheet(btn_style)
        self._tdo_export_btn.setStyleSheet(btn_style)
        self._btn_read_idcode.setStyleSheet(btn_style)
        self._btn_reset_tap.setStyleSheet(btn_style)
        self._btn_bypass_test.setStyleSheet(btn_style)

        self._tdo_log.setStyleSheet(
            f"QTextEdit {{ background: {tm.color('jtag_preview_bg')}; "
            f"color: {tm.color('jtag_preview_text')}; "
            f"border: 1px solid {border}; }}"
        )

        self._mapping_file_label.setStyleSheet(f"color: {text};")
        self._target_file_lbl.setStyleSheet(f"color: {text};")
        self._mapping_status.setStyleSheet(
            f"color: {tm.color('jtag_status_text')};"
        )

        # Pin status panel (right)
        self._pin_title.setStyleSheet(f"color: {text};")
        self._state_title.setStyleSheet(f"color: {tm.color('jtag_status_text')};")
        self._state_label.setStyleSheet(
            f"color: {highlight}; background: {surface}; "
            f"border: 1px solid {border}; border-radius: 3px;"
        )
        self._pin_sep.setStyleSheet(f"background: {border};")
        self._state_sep.setStyleSheet(f"background: {border};")
        self._exec_sep.setStyleSheet(f"background: {border};")
        self._exec_title.setStyleSheet(f"color: {tm.color('jtag_status_text')};")
        for pin_name, (led, lbl, state_lbl) in self._pin_leds.items():
            lbl.setStyleSheet(f"color: {text};")
            state_lbl.setStyleSheet(f"color: {tm.color('jtag_status_text')};")
            led._update_style()
        for key, (k_lbl, v_lbl) in self._exec_labels.items():
            k_lbl.setStyleSheet(f"color: {tm.color('jtag_status_text')};")
            v_lbl.setStyleSheet(
                f"color: {'#E74C3C' if key == 'Fail' else highlight};"
            )

        # Device Chain panel (left)
        self._chain_title.setStyleSheet(f"color: {text};")
        self._ops_title.setStyleSheet(f"color: {text};")
        self._chain_sep.setStyleSheet(f"background: {border};")
        self._ops_sep.setStyleSheet(f"background: {border};")
        for key, (k_lbl, v_lbl) in self._chain_labels.items():
            k_lbl.setStyleSheet(f"color: {tm.color('jtag_status_text')};")
            v_lbl.setStyleSheet(f"color: {text};")
