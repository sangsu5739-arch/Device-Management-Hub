"""
JTAG Sequencer left panel — scenario step list + controls.

Phase 3: Enhanced 8-column step table, context menu, execution controls,
scenario save/load, dynamic mode auto-detection.
"""

from __future__ import annotations

import os
import uuid
from typing import List, Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont, QAction, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QTextEdit, QFrame, QAbstractItemView,
    QFileDialog, QMenu, QStackedWidget, QFormLayout,
)

from core.theme_manager import ThemeManager
from modules.ftdi_verifier.scenario_model import (
    Scenario, ScenarioStep, StepType, StepStatus,
    DynamicMode, DynamicSourceConfig,
)
from modules.ftdi_verifier.atp_parser import AtpParser


# ── Column indices ─────────────────────────────────────────────────

COL_ENABLE = 0
COL_TYPE = 1
COL_NAME = 2
COL_FILENAME = 3
COL_DYNAMIC = 4
COL_SOURCE = 5
COL_STATUS = 6
COL_RESULT = 7
_COL_COUNT = 8
_COL_HEADERS = ["On", "T", "Name", "File", "Mode", "S", "St", "Result"]

# Step type display
_TYPE_LABELS = {
    StepType.ATP_PATTERN: "A",
    StepType.GPIO_SEQUENCE: "G",
    StepType.DELAY: "D",
}

# Status display
_STATUS_ICONS = {
    StepStatus.PENDING: "-",
    StepStatus.RUNNING: ">>",
    StepStatus.PASSED: "OK",
    StepStatus.FAILED: "NG",
    StepStatus.SKIPPED: "--",
    StepStatus.ERROR: "E!",
}
_STATUS_COLORS = {
    StepStatus.PENDING: "#6A7080",
    StepStatus.RUNNING: "#4A90E2",
    StepStatus.PASSED: "#27AE60",
    StepStatus.FAILED: "#E74C3C",
    StepStatus.SKIPPED: "#95A5A6",
    StepStatus.ERROR: "#E67E22",
}


class JtagLeftPanel(QWidget):
    """JTAG Sequencer left panel with scenario management."""

    # ── Signals ────────────────────────────────────────────────────

    folder_changed = Signal(str)
    csv_changed = Signal(str)
    register_map_changed = Signal(str)
    tck_changed = Signal(int)
    step_selected = Signal(int, str)       # row, filepath
    scenario_changed = Signal(object)      # Scenario

    # Execution signals
    run_all_requested = Signal()
    run_selected_requested = Signal(list)
    run_from_here_requested = Signal(str)  # step_id
    dry_run_requested = Signal()
    stop_requested = Signal()
    retry_failed_requested = Signal()
    scenario_save_requested = Signal()
    scenario_load_requested = Signal()

    # Legacy (Phase 2 compat)
    run_requested = Signal()

    _TCK_FREQ_MAP = [100_000, 500_000, 1_000_000, 2_000_000, 3_000_000, 6_000_000]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._scenario: Scenario = Scenario()
        self._atp_folder: str = ""
        self._atp_files: list[str] = []
        self._csv_path: str = ""

        self._init_ui()

        tm = ThemeManager.instance()
        tm.theme_changed.connect(self._apply_theme)
        self._apply_theme()

    # ── UI construction ────────────────────────────────────────────

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(2)
        layout.setContentsMargins(4, 2, 4, 2)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_step_list(), 1)
        layout.addWidget(self._build_preview_panel())
        layout.addWidget(self._build_execution_controls())
        layout.addWidget(self._build_status_bar())

    def _build_header(self) -> QGroupBox:
        """Settings header — compact vertical layout for narrow panels."""
        grp = QGroupBox("Settings")
        lay = QVBoxLayout(grp)
        lay.setSpacing(2)
        lay.setContentsMargins(4, 2, 4, 2)

        # Row 0: TCK frequency (label + combo side by side)
        tck_row = QHBoxLayout()
        tck_row.setSpacing(4)
        tck_lbl = QLabel("TCK:")
        tck_lbl.setFixedWidth(30)
        self._tck_combo = QComboBox()
        self._tck_combo.addItems([
            "100kHz", "500kHz", "1MHz", "2MHz", "3MHz", "6MHz",
        ])
        self._tck_combo.setCurrentIndex(2)
        self._tck_combo.setFixedHeight(24)
        self._tck_combo.currentIndexChanged.connect(self._on_tck_changed)
        tck_row.addWidget(tck_lbl)
        tck_row.addWidget(self._tck_combo, 1)
        # Scenario Save/Load (same row, right side)
        self._save_btn = QPushButton("Save")
        self._save_btn.setFixedSize(42, 24)
        self._save_btn.clicked.connect(self.scenario_save_requested)
        self._load_btn = QPushButton("Load")
        self._load_btn.setFixedSize(42, 24)
        self._load_btn.clicked.connect(self.scenario_load_requested)
        tck_row.addWidget(self._save_btn)
        tck_row.addWidget(self._load_btn)
        lay.addLayout(tck_row)

        # Row 1: Pattern Folder + Register Map (equal split)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(3)
        self._folder_btn = QPushButton("\U0001f4c2 Folder")
        self._folder_btn.setFixedHeight(26)
        self._folder_btn.setToolTip("Select ATP pattern folder")
        self._folder_btn.clicked.connect(self._on_select_folder)
        self._regmap_btn = QPushButton("\U0001f4c4 RegMap")
        self._regmap_btn.setFixedHeight(26)
        self._regmap_btn.setToolTip("Select Register Map CSV")
        self._regmap_btn.clicked.connect(self._on_select_register_map)
        btn_row.addWidget(self._folder_btn, 1)
        btn_row.addWidget(self._regmap_btn, 1)
        lay.addLayout(btn_row)

        # Row 2: Path label (hidden until folder selected)
        self._folder_label = QLabel("")
        self._folder_label.setFont(QFont("Consolas", 7))
        self._folder_label.setWordWrap(True)
        self._folder_label.setMaximumHeight(20)
        self._folder_label.setVisible(False)
        lay.addWidget(self._folder_label)

        return grp

    def _build_step_list(self) -> QGroupBox:
        """Enhanced 8-column step table with context menu."""
        grp = QGroupBox("Step List")
        layout = QVBoxLayout(grp)
        layout.setContentsMargins(2, 2, 2, 2)

        self._step_table = QTableWidget(0, _COL_COUNT)
        self._step_table.setHorizontalHeaderLabels(_COL_HEADERS)
        # Header tooltips
        _header_tips = {
            COL_ENABLE: "Enable/Disable step",
            COL_TYPE: "Step type: A=ATP, G=GPIO, D=Delay",
            COL_NAME: "Step name (editable)",
            COL_FILENAME: "ATP pattern filename",
            COL_DYNAMIC: "Dynamic value mode:\nNone / Auto / CSV / Manual / Script",
            COL_SOURCE: "Source status:\nR=RegisterMap, C=CSV, M=Manual, S=Script",
            COL_STATUS: "Execution status:\nPending / Running / Passed / Failed",
            COL_RESULT: "Execution result",
        }
        for col, tip in _header_tips.items():
            item = self._step_table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(tip)
        h = self._step_table.horizontalHeader()
        h.setSectionResizeMode(COL_ENABLE, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_FILENAME, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(COL_DYNAMIC, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(COL_SOURCE, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Fixed)
        h.setSectionResizeMode(COL_RESULT, QHeaderView.ResizeMode.Fixed)
        self._step_table.setColumnWidth(COL_ENABLE, 22)
        self._step_table.setColumnWidth(COL_TYPE, 22)
        self._step_table.setColumnWidth(COL_DYNAMIC, 52)
        self._step_table.setColumnWidth(COL_SOURCE, 22)
        self._step_table.setColumnWidth(COL_STATUS, 24)
        self._step_table.setColumnWidth(COL_RESULT, 56)
        self._step_table.verticalHeader().setVisible(False)
        self._step_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._step_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._step_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._step_table.customContextMenuRequested.connect(self._on_context_menu)
        self._step_table.cellClicked.connect(self._on_step_clicked)

        layout.addWidget(self._step_table)
        return grp

    def _build_preview_panel(self) -> QGroupBox:
        """Stacked preview: raw text / pattern summary."""
        grp = QGroupBox("Preview")
        layout = QVBoxLayout(grp)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)

        # Toggle row
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(4)
        self._preview_raw_btn = QPushButton("Raw")
        self._preview_raw_btn.setFixedHeight(22)
        self._preview_raw_btn.setCheckable(True)
        self._preview_raw_btn.setChecked(True)
        self._preview_raw_btn.clicked.connect(lambda: self._set_preview_page(0))
        self._preview_summary_btn = QPushButton("Summary")
        self._preview_summary_btn.setFixedHeight(22)
        self._preview_summary_btn.setCheckable(True)
        self._preview_summary_btn.clicked.connect(lambda: self._set_preview_page(1))
        toggle_row.addWidget(self._preview_raw_btn)
        toggle_row.addWidget(self._preview_summary_btn)
        toggle_row.addStretch()
        layout.addLayout(toggle_row)

        self._preview_stack = QStackedWidget()

        # Page 0: raw text
        self._file_preview = QTextEdit()
        self._file_preview.setReadOnly(True)
        self._file_preview.setFont(QFont("Consolas", 9))
        self._file_preview.setMaximumHeight(80)
        self._file_preview.setPlaceholderText(
            "Select a step to preview its contents."
        )
        self._preview_stack.addWidget(self._file_preview)

        # Page 1: summary
        summary_w = QWidget()
        summary_lay = QFormLayout(summary_w)
        summary_lay.setContentsMargins(4, 4, 4, 4)
        summary_lay.setSpacing(2)
        self._summary_vectors = QLabel("-")
        self._summary_dynamic = QLabel("-")
        self._summary_readback = QLabel("-")
        self._summary_signals = QLabel("-")
        self._summary_signals.setWordWrap(True)
        for lbl in (self._summary_vectors, self._summary_dynamic,
                     self._summary_readback, self._summary_signals):
            lbl.setFont(QFont("Consolas", 9))
        summary_lay.addRow("Vectors:", self._summary_vectors)
        summary_lay.addRow("Dynamic:", self._summary_dynamic)
        summary_lay.addRow("Readback:", self._summary_readback)
        summary_lay.addRow("Signals:", self._summary_signals)
        self._preview_stack.addWidget(summary_w)
        self._preview_stack.setMaximumHeight(80)

        layout.addWidget(self._preview_stack)
        return grp

    def _build_execution_controls(self) -> QFrame:
        """Execution buttons: Run All / Selected / From Here / Dry Run / Stop / Retry."""
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(frame)
        layout.setSpacing(2)
        layout.setContentsMargins(0, 2, 0, 0)

        row1 = QHBoxLayout()
        row1.setSpacing(3)
        self._btn_run_all = QPushButton("\u25b6 All")
        self._btn_run_sel = QPushButton("\u25b6 Sel")
        self._btn_run_from = QPushButton("\u25b6 From")
        self._btn_run_all.setToolTip("Run all enabled steps")
        self._btn_run_sel.setToolTip("Run selected steps")
        self._btn_run_from.setToolTip("Run from selected step")
        for btn in (self._btn_run_all, self._btn_run_sel, self._btn_run_from):
            btn.setFixedHeight(26)
            btn.setEnabled(False)
        self._btn_run_all.clicked.connect(self.run_all_requested)
        self._btn_run_sel.clicked.connect(self._on_run_selected)
        self._btn_run_from.clicked.connect(self._on_run_from_here)
        row1.addWidget(self._btn_run_all)
        row1.addWidget(self._btn_run_sel)
        row1.addWidget(self._btn_run_from)

        row2 = QHBoxLayout()
        row2.setSpacing(3)
        self._btn_dry_run = QPushButton("Dry Run")
        self._btn_stop = QPushButton("Stop")
        self._btn_retry = QPushButton("Retry")
        self._btn_dry_run.setToolTip("Validate without hardware")
        self._btn_stop.setToolTip("Stop execution")
        self._btn_retry.setToolTip("Retry failed steps")
        for btn in (self._btn_dry_run, self._btn_stop, self._btn_retry):
            btn.setFixedHeight(24)
        self._btn_dry_run.setEnabled(False)
        self._btn_stop.setEnabled(False)
        self._btn_retry.setEnabled(False)
        self._btn_dry_run.clicked.connect(self.dry_run_requested)
        self._btn_stop.clicked.connect(self.stop_requested)
        self._btn_retry.clicked.connect(self.retry_failed_requested)
        row2.addWidget(self._btn_dry_run)
        row2.addWidget(self._btn_stop)
        row2.addWidget(self._btn_retry)

        layout.addLayout(row1)
        layout.addLayout(row2)
        return frame

    def _build_status_bar(self) -> QFrame:
        """Bottom status — 2 rows, compact."""
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.NoFrame)
        g = QGridLayout(frame)
        g.setSpacing(1)
        g.setContentsMargins(4, 2, 4, 0)
        lbl_font = QFont("Segoe UI", 7)
        val_font = QFont("Consolas", 7)

        # Row 0: Progress + Pass/Fail + Pattern (all in one line)
        g.addWidget(self._make_status_lbl("Step:", lbl_font), 0, 0)
        self._progress_label = QLabel("0/0")
        self._progress_label.setFont(val_font)
        g.addWidget(self._progress_label, 0, 1)

        g.addWidget(self._make_status_lbl("P/F:", lbl_font), 0, 2)
        self._pass_fail_label = QLabel("0/0")
        self._pass_fail_label.setFont(val_font)
        g.addWidget(self._pass_fail_label, 0, 3)

        g.addWidget(self._make_status_lbl("Pat:", lbl_font), 0, 4)
        self._current_pattern = QLabel("-")
        self._current_pattern.setFont(val_font)
        g.addWidget(self._current_pattern, 0, 5)

        # Row 1: Output path (full width)
        g.addWidget(self._make_status_lbl("Out:", lbl_font), 1, 0)
        self._tdo_path_label = QLabel("-")
        self._tdo_path_label.setFont(val_font)
        g.addWidget(self._tdo_path_label, 1, 1, 1, 5)

        return frame

    # ── Public API ─────────────────────────────────────────────────

    @property
    def scenario(self) -> Scenario:
        return self._scenario

    @property
    def atp_folder(self) -> str:
        return self._atp_folder

    @property
    def atp_files(self) -> list[str]:
        return list(self._atp_files)

    @property
    def csv_path(self) -> str:
        return self._csv_path

    def set_scenario(self, scenario: Scenario) -> None:
        """Load a Scenario into the table."""
        self._scenario = scenario
        if scenario.pattern_folder:
            self._atp_folder = scenario.pattern_folder
            self._folder_label.setText(scenario.pattern_folder)
        self._rebuild_table()
        self._update_exec_buttons()
        self.scenario_changed.emit(scenario)

    def update_step_status(self, step_id: str, status: StepStatus, message: str = "") -> None:
        """Update a step's Status + Result columns from execution engine."""
        for row in range(self._step_table.rowCount()):
            if self._get_step_id_at_row(row) == step_id:
                self._set_status_cell(row, status)
                result_item = self._step_table.item(row, COL_RESULT)
                if result_item:
                    text = message if message else status.value
                    result_item.setText(text)
                    result_item.setToolTip(text)
                break
        # Also update model
        step = self._scenario.get_step_by_id(step_id)
        if step:
            step.status = status
            step.result_message = message

    def update_progress(self, current: int, total: int) -> None:
        self._progress_label.setText(f"{current} / {total}")

    def update_pass_fail(self, passed: int, failed: int) -> None:
        self._pass_fail_label.setText(f"{passed} / {failed}")

    def update_current_pattern(self, name: str) -> None:
        self._current_pattern.setText(name)

    def update_tdo_path(self, path: str) -> None:
        self._tdo_path_label.setText(path)

    def set_execution_running(self, running: bool) -> None:
        """Toggle UI for execution in progress."""
        self._btn_run_all.setEnabled(not running and len(self._scenario.steps) > 0)
        self._btn_run_sel.setEnabled(not running)
        self._btn_run_from.setEnabled(not running)
        self._btn_dry_run.setEnabled(not running and len(self._scenario.steps) > 0)
        self._btn_stop.setEnabled(running)
        self._btn_retry.setEnabled(not running)
        self._folder_btn.setEnabled(not running)
        self._regmap_btn.setEnabled(not running)
        self._save_btn.setEnabled(not running)
        self._load_btn.setEnabled(not running)

    def set_run_enabled(self, enabled: bool) -> None:
        """Legacy compat."""
        self._btn_run_all.setEnabled(enabled)
        self._btn_dry_run.setEnabled(enabled)

    def get_selected_step_ids(self) -> List[str]:
        rows = set(idx.row() for idx in self._step_table.selectedIndexes())
        ids = []
        for r in sorted(rows):
            sid = self._get_step_id_at_row(r)
            if sid:
                ids.append(sid)
        return ids

    # ── Internal: table management ─────────────────────────────────

    def _rebuild_table(self) -> None:
        """Rebuild step table from scenario model."""
        self._step_table.setRowCount(0)
        for step in self._scenario.steps:
            self._add_step_row(step)

    def _add_step_row(self, step: ScenarioStep) -> int:
        """Add a single step to the table. Returns row index."""
        row = self._step_table.rowCount()
        self._step_table.insertRow(row)

        # COL_ENABLE: checkbox via item checkstate
        enable_item = QTableWidgetItem()
        enable_item.setFlags(enable_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        enable_item.setCheckState(
            Qt.CheckState.Checked if step.enabled else Qt.CheckState.Unchecked
        )
        enable_item.setData(Qt.ItemDataRole.UserRole, step.step_id)
        self._step_table.setItem(row, COL_ENABLE, enable_item)

        # COL_TYPE
        _TYPE_TOOLTIPS = {
            StepType.ATP_PATTERN: "ATP Pattern",
            StepType.GPIO_SEQUENCE: "GPIO Sequence",
            StepType.DELAY: "Delay",
        }
        type_item = QTableWidgetItem(_TYPE_LABELS.get(step.step_type, "?"))
        type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        type_item.setFont(QFont("Consolas", 8))
        type_item.setToolTip(_TYPE_TOOLTIPS.get(step.step_type, "Unknown"))
        self._step_table.setItem(row, COL_TYPE, type_item)

        # COL_NAME (editable)
        name_item = QTableWidgetItem(step.name)
        name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self._step_table.setItem(row, COL_NAME, name_item)

        # COL_FILENAME
        fn_item = QTableWidgetItem(step.filename)
        fn_item.setToolTip(step.filename)
        self._step_table.setItem(row, COL_FILENAME, fn_item)

        # COL_DYNAMIC: combo box
        dyn_combo = QComboBox()
        dyn_combo.setFixedHeight(20)
        dyn_combo.setStyleSheet("QComboBox { font-size: 10px; padding: 0 2px; }")
        dyn_combo.addItems(["None", "Auto", "CSV", "Manual", "Script"])
        dyn_combo.setCurrentText(self._dynamic_mode_display(step.dynamic_source.mode))
        dyn_combo.currentTextChanged.connect(
            lambda text, r=row: self._on_dynamic_combo_changed(r, text)
        )
        # Wrap in container to respect column width
        dyn_w = QWidget()
        dyn_lay = QHBoxLayout(dyn_w)
        dyn_lay.setContentsMargins(1, 0, 1, 0)
        dyn_lay.addWidget(dyn_combo)
        self._step_table.setCellWidget(row, COL_DYNAMIC, dyn_w)

        # COL_SOURCE
        source_item = QTableWidgetItem(self._source_display(step))
        source_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        source_item.setFont(QFont("Consolas", 8))
        source_item.setToolTip(self._source_tooltip(step))
        self._step_table.setItem(row, COL_SOURCE, source_item)

        # COL_STATUS
        self._set_status_cell(row, step.status)

        # COL_RESULT
        result_text = step.result_message or "-"
        result_item = QTableWidgetItem(result_text)
        result_item.setFont(QFont("Consolas", 8))
        result_item.setToolTip(result_text)
        self._step_table.setItem(row, COL_RESULT, result_item)

        return row

    def _set_status_cell(self, row: int, status: StepStatus) -> None:
        icon = _STATUS_ICONS.get(status, "?")
        color = _STATUS_COLORS.get(status, "#6A7080")
        item = self._step_table.item(row, COL_STATUS)
        if item is None:
            item = QTableWidgetItem()
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._step_table.setItem(row, COL_STATUS, item)
        item.setText(icon)
        item.setToolTip(status.value.capitalize())
        item.setForeground(QColor(color))
        item.setFont(QFont("Consolas", 8, QFont.Weight.Bold))

    def _get_step_id_at_row(self, row: int) -> Optional[str]:
        item = self._step_table.item(row, COL_ENABLE)
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _update_exec_buttons(self) -> None:
        has_steps = len(self._scenario.steps) > 0
        self._btn_run_all.setEnabled(has_steps)
        self._btn_dry_run.setEnabled(has_steps)

    @staticmethod
    def _dynamic_mode_display(mode: DynamicMode) -> str:
        return {
            DynamicMode.NONE: "None",
            DynamicMode.AUTO: "Auto",
            DynamicMode.CSV: "CSV",
            DynamicMode.MANUAL: "Manual",
            DynamicMode.SCRIPT: "Script",
        }.get(mode, "None")

    @staticmethod
    def _dynamic_mode_from_display(text: str) -> DynamicMode:
        return {
            "None": DynamicMode.NONE,
            "Auto": DynamicMode.AUTO,
            "CSV": DynamicMode.CSV,
            "Manual": DynamicMode.MANUAL,
            "Script": DynamicMode.SCRIPT,
        }.get(text, DynamicMode.NONE)

    @staticmethod
    def _source_display(step: ScenarioStep) -> str:
        mode = step.dynamic_source.mode
        if mode == DynamicMode.NONE:
            return "-"
        if mode == DynamicMode.AUTO:
            return "R"    # Register map
        if mode == DynamicMode.CSV:
            return "C" if step.dynamic_source.csv_path else "!"
        if mode == DynamicMode.MANUAL:
            return "M" if step.dynamic_source.manual_values else "!"
        if mode == DynamicMode.SCRIPT:
            return "S" if step.dynamic_source.script_expr else "!"
        return "-"

    @staticmethod
    def _source_tooltip(step: ScenarioStep) -> str:
        mode = step.dynamic_source.mode
        if mode == DynamicMode.NONE:
            return "No dynamic source"
        if mode == DynamicMode.AUTO:
            cap = step.dynamic_source.capture_step_id[:8] if step.dynamic_source.capture_step_id else "none"
            return f"Auto: Register Map + Capture (ref: {cap})"
        if mode == DynamicMode.CSV:
            p = step.dynamic_source.csv_path or "not set"
            return f"CSV: {p}"
        if mode == DynamicMode.MANUAL:
            n = len(step.dynamic_source.manual_values)
            return f"Manual: {n} value(s) entered"
        if mode == DynamicMode.SCRIPT:
            return f"Script: {step.dynamic_source.script_expr or 'not set'}"
        return ""

    @staticmethod
    def _make_status_lbl(text: str, font) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(font)
        lbl.setFixedWidth(26)
        return lbl

    def _set_preview_page(self, page: int) -> None:
        self._preview_stack.setCurrentIndex(page)
        self._preview_raw_btn.setChecked(page == 0)
        self._preview_summary_btn.setChecked(page == 1)

    def _update_summary(self, filepath: str) -> None:
        """Update pattern summary labels from ATP file scan."""
        try:
            summary = AtpParser.scan_summary(filepath, max_bytes=512_000)
        except Exception:
            self._summary_vectors.setText("-")
            self._summary_dynamic.setText("-")
            self._summary_readback.setText("-")
            self._summary_signals.setText("-")
            return
        self._summary_vectors.setText(
            f"{summary.total_vectors} (expanded: {summary.repeat_expanded_count})"
        )
        self._summary_dynamic.setText(
            f"{summary.dynamic_field_count} fields, {summary.total_dynamic_bits} bits"
            if summary.dynamic_field_count > 0 else "None"
        )
        self._summary_readback.setText(
            "Yes (TDO 'V')" if summary.has_readback else "No"
        )
        self._summary_signals.setText(
            ", ".join(summary.signal_names) if summary.signal_names else "-"
        )

    # ── Slots ──────────────────────────────────────────────────────

    @Slot()
    def _on_tck_changed(self) -> None:
        idx = self._tck_combo.currentIndex()
        if 0 <= idx < len(self._TCK_FREQ_MAP):
            hz = self._TCK_FREQ_MAP[idx]
            self._scenario.tck_frequency_hz = hz
            self.tck_changed.emit(hz)

    @Slot()
    def _on_select_folder(self) -> None:
        """Pattern folder → auto-build scenario from ATP files."""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Pattern Folder", ""
        )
        if not folder:
            return
        self._atp_folder = folder
        self._folder_label.setText(folder)
        self._folder_label.setVisible(True)

        atp_files = sorted(
            f for f in os.listdir(folder)
            if f.lower().endswith((".atp", ".txt", ".pat"))
        )
        self._atp_files = atp_files

        # Build scenario from folder scan
        scenario = Scenario(
            name=os.path.basename(folder),
            pattern_folder=folder,
            tck_frequency_hz=self._scenario.tck_frequency_hz,
            register_map_path=self._scenario.register_map_path,
        )
        for fname in atp_files:
            filepath = os.path.join(folder, fname)
            has_dyn = AtpParser.has_dynamic_fields(filepath)
            name = os.path.splitext(fname)[0]
            step = ScenarioStep(
                name=name,
                filename=fname,
                dynamic_source=DynamicSourceConfig(
                    mode=DynamicMode.AUTO if has_dyn else DynamicMode.NONE,
                ),
            )
            scenario.steps.append(step)

        self.set_scenario(scenario)
        self.folder_changed.emit(folder)

    @Slot()
    def _on_select_register_map(self) -> None:
        """Register Map CSV file selection."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Register Map CSV", "",
            "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return
        self._scenario.register_map_path = path
        self.register_map_changed.emit(path)

    @Slot(int, int)
    def _on_step_clicked(self, row: int, col: int) -> None:
        """Step row clicked → file preview + summary."""
        step_id = self._get_step_id_at_row(row)
        if not step_id:
            return
        step = self._scenario.get_step_by_id(step_id)
        if not step or step.step_type != StepType.ATP_PATTERN:
            self._file_preview.setPlainText("")
            return

        filepath = os.path.join(self._atp_folder, step.filename) if self._atp_folder else step.filename
        if not os.path.isfile(filepath):
            self._file_preview.setPlainText(f"(File not found: {filepath})")
            return

        # Raw preview
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(8192)
            self._file_preview.setPlainText(content)
        except Exception as e:
            self._file_preview.setPlainText(f"(Failed to read: {e})")

        # Summary
        self._update_summary(filepath)
        self.step_selected.emit(row, filepath)

    def _on_dynamic_combo_changed(self, row: int, text: str) -> None:
        """Dynamic mode combo changed → update model + source column.

        For AUTO mode, auto-link capture_step_id to the nearest preceding
        ATP step that has TDO readback ('V').
        """
        step_id = self._get_step_id_at_row(row)
        if not step_id:
            return
        step = self._scenario.get_step_by_id(step_id)
        if not step:
            return
        step.dynamic_source.mode = self._dynamic_mode_from_display(text)

        # AUTO: auto-set capture_step_id to nearest preceding readback step
        if step.dynamic_source.mode == DynamicMode.AUTO:
            step.dynamic_source.capture_step_id = self._find_capture_step(step_id)
            step.dynamic_source.register_map_path = self._scenario.register_map_path

        # Update Source column
        source_item = self._step_table.item(row, COL_SOURCE)
        if source_item:
            source_item.setText(self._source_display(step))

    def _find_capture_step(self, current_step_id: str) -> str:
        """Find the nearest preceding ATP step with TDO readback."""
        found_current = False
        candidates = []
        for s in reversed(self._scenario.steps):
            if s.step_id == current_step_id:
                found_current = True
                continue
            if found_current and s.step_type == StepType.ATP_PATTERN and s.filename:
                filepath = os.path.join(self._atp_folder, s.filename) if self._atp_folder else s.filename
                if os.path.isfile(filepath):
                    summary = AtpParser.scan_summary(filepath, max_bytes=64_000)
                    if summary.has_readback:
                        return s.step_id
        return ""

    @Slot()
    def _on_run_selected(self) -> None:
        ids = self.get_selected_step_ids()
        if ids:
            self.run_selected_requested.emit(ids)

    @Slot()
    def _on_run_from_here(self) -> None:
        ids = self.get_selected_step_ids()
        if ids:
            self.run_from_here_requested.emit(ids[0])

    # ── Context menu ───────────────────────────────────────────────

    @Slot()
    def _on_context_menu(self, pos) -> None:
        menu = QMenu(self)
        row = self._step_table.rowAt(pos.y())

        act_add_atp = QAction("Add ATP Step", self)
        act_add_atp.triggered.connect(lambda: self._add_new_step(StepType.ATP_PATTERN))
        menu.addAction(act_add_atp)

        act_add_delay = QAction("Add Delay Step", self)
        act_add_delay.triggered.connect(lambda: self._add_new_step(StepType.DELAY))
        menu.addAction(act_add_delay)

        act_add_gpio = QAction("Add GPIO Step", self)
        act_add_gpio.triggered.connect(lambda: self._add_new_step(StepType.GPIO_SEQUENCE))
        menu.addAction(act_add_gpio)

        menu.addSeparator()

        if row >= 0:
            act_del = QAction("Delete Step", self)
            act_del.triggered.connect(lambda: self._delete_step(row))
            menu.addAction(act_del)

            act_up = QAction("Move Up", self)
            act_up.setEnabled(row > 0)
            act_up.triggered.connect(lambda: self._move_step(row, -1))
            menu.addAction(act_up)

            act_down = QAction("Move Down", self)
            act_down.setEnabled(row < self._step_table.rowCount() - 1)
            act_down.triggered.connect(lambda: self._move_step(row, 1))
            menu.addAction(act_down)

        menu.exec(self._step_table.viewport().mapToGlobal(pos))

    def _add_new_step(self, step_type: StepType) -> None:
        """Add a new step at the end."""
        name = {
            StepType.ATP_PATTERN: "New ATP Step",
            StepType.DELAY: "Delay",
            StepType.GPIO_SEQUENCE: "GPIO Sequence",
        }.get(step_type, "New Step")
        step = ScenarioStep(
            step_type=step_type,
            name=name,
            delay_ms=100 if step_type == StepType.DELAY else 0,
        )
        self._scenario.steps.append(step)
        self._add_step_row(step)
        self._update_exec_buttons()

    def _delete_step(self, row: int) -> None:
        step_id = self._get_step_id_at_row(row)
        if not step_id:
            return
        self._scenario.steps = [s for s in self._scenario.steps if s.step_id != step_id]
        self._step_table.removeRow(row)
        self._update_exec_buttons()

    def _move_step(self, row: int, direction: int) -> None:
        """Move step up (-1) or down (+1)."""
        new_row = row + direction
        if new_row < 0 or new_row >= len(self._scenario.steps):
            return
        steps = self._scenario.steps
        steps[row], steps[new_row] = steps[new_row], steps[row]
        self._rebuild_table()
        self._step_table.selectRow(new_row)

    # ── Theme ──────────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        tm = ThemeManager.instance()
        border = tm.color("jtag_btn_border")
        text = tm.color("jtag_tap_text")
        is_dark = tm.is_dark()

        # Neutral tool buttons (Folder, RegMap, Save, Load, Raw, Summary)
        tool_btn_style = (
            f"QPushButton {{ background: {tm.color('jtag_btn_bg')}; "
            f"color: {tm.color('jtag_btn_text')}; "
            f"border: 1px solid {border}; border-radius: 3px; padding: 2px 6px; }}"
            f"QPushButton:hover {{ background: {tm.color('jtag_btn_hover')}; }}"
        )
        for btn in (self._folder_btn, self._regmap_btn,
                     self._save_btn, self._load_btn,
                     self._preview_raw_btn, self._preview_summary_btn):
            btn.setStyleSheet(tool_btn_style)

        # Run buttons — accent blue (matching I2C scan button tone)
        run_bg = "#2a5070" if is_dark else "#d0e4f8"
        run_text = "#c0e0ff" if is_dark else "#1e4878"
        run_border = "#3a6888" if is_dark else "#78a8d8"
        run_hover = "#345e80" if is_dark else "#b8d4f0"
        run_disabled_bg = "#1e2830" if is_dark else "#e8ecf0"
        run_disabled_text = "#4a5a6a" if is_dark else "#a0a8b0"
        run_style = (
            f"QPushButton {{ background: {run_bg}; color: {run_text}; "
            f"border: 1px solid {run_border}; border-radius: 3px; "
            f"padding: 3px 6px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {run_hover}; }}"
            f"QPushButton:disabled {{ background: {run_disabled_bg}; "
            f"color: {run_disabled_text}; border: 1px solid {border}; }}"
        )
        for btn in (self._btn_run_all, self._btn_run_sel, self._btn_run_from):
            btn.setStyleSheet(run_style)

        # Dry Run — muted teal
        dry_bg = "#1e3838" if is_dark else "#daf0ee"
        dry_text = "#80c0b8" if is_dark else "#1a5050"
        dry_style = (
            f"QPushButton {{ background: {dry_bg}; color: {dry_text}; "
            f"border: 1px solid {border}; border-radius: 3px; padding: 3px 6px; }}"
            f"QPushButton:hover {{ background: {run_hover}; }}"
            f"QPushButton:disabled {{ background: {run_disabled_bg}; "
            f"color: {run_disabled_text}; border: 1px solid {border}; }}"
        )
        self._btn_dry_run.setStyleSheet(dry_style)

        # Stop — red accent
        stop_bg = "#4a1e1e" if is_dark else "#f8e0e0"
        stop_text = "#e08080" if is_dark else "#8a2020"
        stop_hover = "#5a2828" if is_dark else "#f0c8c8"
        stop_style = (
            f"QPushButton {{ background: {stop_bg}; color: {stop_text}; "
            f"border: 1px solid {border}; border-radius: 3px; padding: 3px 6px; }}"
            f"QPushButton:hover {{ background: {stop_hover}; }}"
            f"QPushButton:disabled {{ background: {run_disabled_bg}; "
            f"color: {run_disabled_text}; border: 1px solid {border}; }}"
        )
        self._btn_stop.setStyleSheet(stop_style)

        # Retry — amber accent
        retry_bg = "#3a2e1a" if is_dark else "#f8f0d8"
        retry_text = "#d0a830" if is_dark else "#6a5010"
        retry_hover = "#4a3a22" if is_dark else "#f0e4c0"
        retry_style = (
            f"QPushButton {{ background: {retry_bg}; color: {retry_text}; "
            f"border: 1px solid {border}; border-radius: 3px; padding: 3px 6px; }}"
            f"QPushButton:hover {{ background: {retry_hover}; }}"
            f"QPushButton:disabled {{ background: {run_disabled_bg}; "
            f"color: {run_disabled_text}; border: 1px solid {border}; }}"
        )
        self._btn_retry.setStyleSheet(retry_style)

        # Table — clean white/neutral background
        table_style = (
            f"QTableWidget {{ background: {tm.color('jtag_seq_table_bg')}; color: {text}; "
            f"gridline-color: {border}; border: 1px solid {border}; }}"
            f"QHeaderView::section {{ background: {tm.color('jtag_tap_state')}; "
            f"color: {text}; border: 1px solid {border}; padding: 2px; "
            f"font-size: 11px; }}"
            f"QTableWidget::item:selected {{ background: {tm.color('jtag_seq_selected')}; }}"
        )
        self._step_table.setStyleSheet(table_style)

        # Preview
        preview_style = (
            f"QTextEdit {{ background: {tm.color('jtag_preview_bg')}; "
            f"color: {tm.color('jtag_preview_text')}; "
            f"border: 1px solid {border}; border-radius: 2px; }}"
        )
        self._file_preview.setStyleSheet(preview_style)

        # Status bar labels
        status_css = f"color: {tm.color('jtag_status_text')};"
        self._progress_label.setStyleSheet(status_css)
        self._pass_fail_label.setStyleSheet(status_css)
        self._current_pattern.setStyleSheet(status_css)
        self._tdo_path_label.setStyleSheet(status_css)
        self._folder_label.setStyleSheet(f"color: {tm.color('jtag_status_text')};")

        summary_css = f"color: {tm.color('jtag_preview_text')};"
        for lbl in (self._summary_vectors, self._summary_dynamic,
                     self._summary_readback, self._summary_signals):
            lbl.setStyleSheet(summary_css)
