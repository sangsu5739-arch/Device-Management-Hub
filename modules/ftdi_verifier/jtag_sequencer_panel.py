"""
JTAG Sequencer Right Panel \u2014 TAP State Diagram + Mapping Management.

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
)

from core.theme_manager import ThemeManager
from modules.ftdi_verifier.jtag_tap_diagram import TapStateDiagram


class JtagSequencerPanel(QWidget):
    """JTAG \ubaa8\ub4dc \uc6b0\uce21 \ud328\ub110 \u2014 TAP Diagram + \ub9e4\ud551 \uad00\ub9ac."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._init_ui()

        tm = ThemeManager.instance()
        tm.theme_changed.connect(self._apply_theme)
        self._apply_theme()

    # ── UI \uad6c\uc131 ──

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── TOP: TAP State Diagram ──
        tap_group = QGroupBox("TAP State Diagram")
        tap_layout = QVBoxLayout(tap_group)
        tap_layout.setContentsMargins(4, 4, 4, 4)
        self._tap_diagram = TapStateDiagram()
        tap_layout.addWidget(self._tap_diagram)

        # ── BOTTOM: \ub9e4\ud551 \uad00\ub9ac ──
        mapping_group = QGroupBox("Mapping Management")
        mapping_layout = QVBoxLayout(mapping_group)
        mapping_layout.setContentsMargins(6, 6, 6, 6)
        mapping_layout.setSpacing(4)

        # Header
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("Target File:"))
        self._mapping_file_label = QLabel("-")
        self._mapping_file_label.setFont(QFont("Consolas", 9))
        header_row.addWidget(self._mapping_file_label, 1)
        self._mapping_import_btn = QPushButton("CSV Import")
        self._mapping_import_btn.setFixedHeight(26)
        self._mapping_export_btn = QPushButton("CSV Export")
        self._mapping_export_btn.setFixedHeight(26)
        header_row.addWidget(self._mapping_import_btn)
        header_row.addWidget(self._mapping_export_btn)
        mapping_layout.addLayout(header_row)

        # Mapping Table
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

        # \ub9e4\ud551 \uc0c1\ud0dc
        self._mapping_status = QLabel("Mapping Status: -")
        self._mapping_status.setFont(QFont("Segoe UI", 8))
        mapping_layout.addWidget(self._mapping_status)

        splitter.addWidget(tap_group)
        splitter.addWidget(mapping_group)
        splitter.setSizes([300, 200])

        layout.addWidget(splitter)

        # \uc704\uc82f \ucc38\uc870 \ubcf4\uc874
        self._tap_group = tap_group
        self._mapping_group = mapping_group

    # ── public API ──

    def set_mapping_file(self, name: str) -> None:
        self._mapping_file_label.setText(name)

    def clear_mapping(self) -> None:
        self._mapping_table.setRowCount(0)
        self._mapping_file_label.setText("-")
        self._mapping_status.setText("Mapping Status: -")

    def set_current_state(self, state: str) -> None:
        self._tap_diagram.set_current_state(state)

    # ── \ud14c\ub9c8 ──

    def _apply_theme(self) -> None:
        tm = ThemeManager.instance()

        # TAP \ub2e4\uc774\uc5b4\uadf8\ub7a8 \ubc30\uacbd\uc740 TapStateDiagram \ub0b4\ubd80\uc5d0\uc11c \ucc98\ub9ac

        mapping_bg = tm.color("jtag_mapping_bg")
        text = tm.color("jtag_tap_text")
        border = tm.color("jtag_btn_border")

        for grp in (self._tap_group, self._mapping_group):
            grp.setStyleSheet(
                f"QGroupBox {{ color: {text}; border: 1px solid {border}; "
                f"border-radius: 4px; margin-top: 8px; padding-top: 12px; }}"
                f"QGroupBox::title {{ subcontrol-origin: margin; left: 8px; "
                f"padding: 0 4px; color: {text}; }}"
            )

        self._mapping_table.setStyleSheet(
            f"QTableWidget {{ background: {mapping_bg}; color: {text}; "
            f"gridline-color: {border}; border: 1px solid {border}; }}"
            f"QHeaderView::section {{ background: {tm.color('jtag_tap_state')}; "
            f"color: {text}; border: 1px solid {border}; padding: 2px; }}"
        )

        btn_style = (
            f"QPushButton {{ background: {tm.color('jtag_btn_bg')}; "
            f"color: {tm.color('jtag_btn_text')}; border: 1px solid {border}; "
            f"border-radius: 4px; padding: 2px 8px; }}"
            f"QPushButton:hover {{ background: {tm.color('jtag_btn_hover')}; }}"
        )
        self._mapping_import_btn.setStyleSheet(btn_style)
        self._mapping_export_btn.setStyleSheet(btn_style)

        self._mapping_file_label.setStyleSheet(f"color: {text};")
        self._mapping_status.setStyleSheet(
            f"color: {tm.color('jtag_status_text')};"
        )

        self._tap_diagram.update()
