"""
JTAG Sequencer Right Panel \u2014 TAP State Diagram + TDO Logger + Mapping.

JTAG \ubaa8\ub4dc \uc120\ud0dd \uc2dc \ud540\uc544\uc6c3 \uc704\uce58\uc5d0 \ud45c\uc2dc\ub418\ub294 \uc6b0\uce21 \ud328\ub110.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QGroupBox, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
    QTextEdit,
)

from core.theme_manager import ThemeManager
from modules.ftdi_verifier.jtag_tap_diagram import TapStateDiagram


class JtagSequencerPanel(QWidget):
    """JTAG \ubaa8\ub4dc \uc6b0\uce21 \ud328\ub110 \u2014 TAP Diagram + TDO Logger + \ub9e4\ud551."""

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

        # \u2500\u2500 TOP: TAP State Machine (zero padding, fill width) \u2500\u2500
        tap_group = QGroupBox("TAP State Machine")
        tap_group.setContentsMargins(0, 0, 0, 0)
        tap_layout = QVBoxLayout(tap_group)
        tap_layout.setContentsMargins(0, 0, 0, 0)
        tap_layout.setSpacing(0)
        self._tap_diagram = TapStateDiagram()
        tap_layout.addWidget(self._tap_diagram)

        # \u2500\u2500 MIDDLE: TDO Data Logger \u2500\u2500
        tdo_group = QGroupBox("TDO Data Logger")
        tdo_layout = QVBoxLayout(tdo_group)
        tdo_layout.setContentsMargins(2, 2, 2, 2)
        tdo_layout.setSpacing(2)

        tdo_header = QHBoxLayout()
        self._tdo_clear_btn = QPushButton("Clear")
        self._tdo_clear_btn.setFixedHeight(24)
        self._tdo_clear_btn.clicked.connect(self._on_tdo_clear)
        self._tdo_export_btn = QPushButton("Export")
        self._tdo_export_btn.setFixedHeight(24)
        tdo_header.addStretch()
        tdo_header.addWidget(self._tdo_clear_btn)
        tdo_header.addWidget(self._tdo_export_btn)
        tdo_layout.addLayout(tdo_header)

        self._tdo_log = QTextEdit()
        self._tdo_log.setReadOnly(True)
        self._tdo_log.setFont(QFont("Consolas", 10))
        self._tdo_log.setPlaceholderText("TDO raw data will appear here...")
        tdo_layout.addWidget(self._tdo_log, 1)

        # \u2500\u2500 BOTTOM: Mapping Management \u2500\u2500
        mapping_group = QGroupBox("Mapping Management")
        mapping_layout = QVBoxLayout(mapping_group)
        mapping_layout.setContentsMargins(2, 2, 2, 2)
        mapping_layout.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("Target File:"))
        self._mapping_file_label = QLabel("-")
        self._mapping_file_label.setFont(QFont("Consolas", 9))
        header_row.addWidget(self._mapping_file_label, 1)
        self._mapping_import_btn = QPushButton("CSV Import")
        self._mapping_import_btn.setFixedHeight(24)
        self._mapping_export_btn = QPushButton("CSV Export")
        self._mapping_export_btn.setFixedHeight(24)
        header_row.addWidget(self._mapping_import_btn)
        header_row.addWidget(self._mapping_export_btn)
        mapping_layout.addLayout(header_row)

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
        mapping_layout.addWidget(self._mapping_table, 1)

        self._mapping_status = QLabel("Mapping Status: -")
        self._mapping_status.setFont(QFont("Segoe UI", 8))
        mapping_layout.addWidget(self._mapping_status)

        splitter.addWidget(tap_group)
        splitter.addWidget(mapping_group)
        splitter.addWidget(tdo_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([300, 370, 220])
        splitter.setChildrenCollapsible(False)

        layout.addWidget(splitter)

        self._tap_group = tap_group
        self._tdo_group = tdo_group
        self._mapping_group = mapping_group

    # \u2500\u2500 public API \u2500\u2500

    def set_mapping_file(self, name: str) -> None:
        self._mapping_file_label.setText(name)

    def clear_mapping(self) -> None:
        self._mapping_table.setRowCount(0)
        self._mapping_file_label.setText("-")
        self._mapping_status.setText("Mapping Status: -")

    def set_current_state(self, state: str) -> None:
        self._tap_diagram.set_current_state(state)

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

        # TAP group: enforce clear title/content breathing room
        self._tap_group.setStyleSheet(
            f"QGroupBox {{ color: {text}; border: 1px solid #333333; "
            f"border-radius: 0px; margin-top: 8px; padding: 0; padding-top: 22px; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 6px; "
            f"padding: 0 4px; color: {text}; font-size: 10pt; "
            f"font-weight: bold; }}"
        )
        # TDO & Mapping: same title/content breathing room
        data_style = (
            f"QGroupBox {{ color: {text}; border: 1px solid {border}; "
            f"border-radius: 0px; margin-top: 8px; padding-top: 22px; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin; left: 4px; "
            f"padding: 0 2px; color: {text}; }}"
        )
        self._tdo_group.setStyleSheet(data_style)
        self._mapping_group.setStyleSheet(data_style)

        self._mapping_table.setStyleSheet(
            f"QTableWidget {{ background: {mapping_bg}; color: {text}; "
            f"gridline-color: {border}; border: 1px solid {border}; }}"
            f"QTableWidget::item {{ border-bottom: 1px solid {tm.color('border_subtle')}; }}"
            f"QHeaderView::section {{ background: {tm.color('jtag_tap_state')}; "
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

        self._tdo_log.setStyleSheet(
            f"QTextEdit {{ background: {tm.color('jtag_preview_bg')}; "
            f"color: {tm.color('jtag_preview_text')}; "
            f"border: 1px solid {border}; }}"
        )

        self._mapping_file_label.setStyleSheet(f"color: {text};")
        self._mapping_status.setStyleSheet(
            f"color: {tm.color('jtag_status_text')};"
        )
