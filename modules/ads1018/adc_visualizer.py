"""
ADS1018 4-Channel Real-time ADC Visualizer

pyqtgraph-based plotter with per-channel visibility,
auto-range, and configurable time window.
4 channels are displayed in separate subplots for independent Y ranges.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Tuple

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QComboBox, QLabel
from PySide6.QtCore import Qt

import pyqtgraph as pg

from core.theme_manager import ThemeManager


# Channel colors
CH_COLORS = [
    "#5090e0",  # CH0 blue
    "#50c878",  # CH1 green
    "#e89050",  # CH2 orange
    "#e06070",  # CH3 red
]

CH_LABELS = ["CH0", "CH1", "CH2", "CH3"]


class ADCVisualizer(QWidget):
    """4-channel real-time ADC plot widget with separated subplots."""

    def __init__(self, parent: Optional[QWidget] = None, show_toolbar: bool = True) -> None:
        super().__init__(parent)
        self._window_seconds: int = 30
        self._auto_range: bool = True
        self._max_points: int = 6000
        self._show_toolbar = show_toolbar

        # Data buffers: deque of (time, value) per channel
        self._times: deque = deque(maxlen=self._max_points)
        self._data: List[deque] = [
            deque(maxlen=self._max_points) for _ in range(4)
        ]
        self._ch_units: List[str] = ["V", "V", "V", "V"]
        self._start_time: Optional[float] = None

        self._build_ui()
        self._apply_theme()
        ThemeManager.instance().theme_changed.connect(self._apply_theme)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # pyqtgraph layout widget
        self._gw = pg.GraphicsLayoutWidget()
        layout.addWidget(self._gw, 1)

        self._plots: List[pg.PlotItem] = []
        self._curves: List[pg.PlotDataItem] = []

        # Create 4 vertically stacked plots
        for i in range(4):
            plot = self._gw.addPlot(row=i, col=0)
            plot.showGrid(x=True, y=True, alpha=0.15)
            # Only show bottom axis label for the last visible plot, but we'll just set it for all and hide axes
            if i == 3:
                plot.setLabel("bottom", "Time", units="s")
            
            # Use smaller font for left axis to save space
            left_axis = plot.getAxis("left")
            left_axis.setWidth(45)
            plot.setLabel("left", CH_LABELS[i], units=self._ch_units[i])

            # Axis pens set in _apply_theme()

            # Link X axes so zooming/panning one zooms all
            if i > 0:
                plot.setXLink(self._plots[0])

            pen = pg.mkPen(color=CH_COLORS[i], width=2)
            curve = plot.plot([], [], pen=pen, name=CH_LABELS[i])
            
            self._plots.append(plot)
            self._curves.append(curve)

        # Group toolbar container
        self._toolbar_container = QWidget()
        toolbar_layout = QHBoxLayout(self._toolbar_container)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(8)

        # Channel checkboxes
        self._ch_checks: List[QCheckBox] = []
        for i in range(4):
            cb = QCheckBox(CH_LABELS[i])
            cb.setChecked(True)
            cb.setObjectName("chCheckbox")
            cb.setProperty("chColor", CH_COLORS[i])
            cb.toggled.connect(lambda checked, idx=i: self._on_ch_toggle(idx, checked))
            self._ch_checks.append(cb)
            toolbar_layout.addWidget(cb)

        toolbar_layout.addStretch()

        # Auto-range checkbox
        self._auto_range_cb = QCheckBox("Auto Range")
        self._auto_range_cb.setChecked(True)
        self._auto_range_cb.toggled.connect(self._on_auto_range)
        toolbar_layout.addWidget(self._auto_range_cb)

        # Time window selector
        self._win_lbl = QLabel("Window:")
        toolbar_layout.addWidget(self._win_lbl)

        self._window_combo = QComboBox()
        self._window_combo.addItems(["10s", "30s", "60s", "120s"])
        self._window_combo.setCurrentIndex(1)
        self._window_combo.setFixedWidth(70)
        self._window_combo.currentIndexChanged.connect(self._on_window_changed)
        toolbar_layout.addWidget(self._window_combo)

        layout.addWidget(self._toolbar_container)
        if not self._show_toolbar:
            self._toolbar_container.setVisible(False)

    # ── Public API ────────────────────────────────────────────────────

    def add_measurement(self, timestamp: float, values: List[float],
                        units: List[str]) -> None:
        """Append a new measurement and update the plot."""
        if self._start_time is None:
            self._start_time = timestamp

        t = timestamp - self._start_time
        self._times.append(t)

        for i in range(4):
            self._data[i].append(values[i] if i < len(values) else 0.0)

        self._ch_units = list(units) if units else self._ch_units
        self._update_plot()

    def clear(self) -> None:
        """Clear all data."""
        self._times.clear()
        for d in self._data:
            d.clear()
        self._start_time = None
        for curve in self._curves:
            curve.setData([], [])

    def set_window_seconds(self, seconds: int) -> None:
        self._window_seconds = max(5, seconds)
        if hasattr(self, "_window_combo"):
            # Update combobox to match if needed, though we will hide toolbar
            pass

    def set_channel_unit(self, ch_idx: int, unit: str) -> None:
        if 0 <= ch_idx < len(self._ch_units):
            self._ch_units[ch_idx] = unit
            self._plots[ch_idx].setLabel("left", CH_LABELS[ch_idx], units=unit)

    def set_auto_range(self, enabled: bool) -> None:
        if hasattr(self, "_auto_range_cb"):
            self._auto_range_cb.setChecked(enabled)
            
        self._auto_range = enabled
        for p in self._plots:
            if enabled:
                p.enableAutoRange(axis="y")
            else:
                p.disableAutoRange(axis="y")

    # ── Internal ──────────────────────────────────────────────────────

    def _update_plot(self) -> None:
        if not self._times:
            return

        t_list = list(self._times)
        t_max = t_list[-1]
        t_min = max(0, t_max - self._window_seconds)

        for i in range(4):
            if not self._ch_checks[i].isChecked():
                # Avoid plotting hidden curves
                continue

            y_list = list(self._data[i])
            # Filter to window
            pairs = [(t, y) for t, y in zip(t_list, y_list) if t >= t_min]
            if pairs:
                ts, ys = zip(*pairs)
                self._curves[i].setData(list(ts), list(ys))
            else:
                self._curves[i].setData([], [])
                
            # Outline y-axis unit dynamically
            if self._ch_units[i]:
                self._plots[i].setLabel("left", CH_LABELS[i], units=self._ch_units[i])

        # Apply X range to the first plot (all are linked)
        self._plots[0].setXRange(t_min, t_max, padding=0.02)

        if self._auto_range:
            for p in self._plots:
                p.enableAutoRange(axis="y")
        else:
            for p in self._plots:
                p.disableAutoRange(axis="y")

    def _on_ch_toggle(self, idx: int, checked: bool) -> None:
        if not checked:
            self._curves[idx].setData([], [])
            # Hide the plot entirely to save vertical space
            self._plots[idx].hide()
        else:
            self._plots[idx].show()
            self._update_plot()
            
        # Ensure the bottom-most visible plot has the Time label
        visible_plots = [p for p in self._plots if p.isVisible()]
        for p in self._plots:
            p.showLabel("bottom", False)
        if visible_plots:
            visible_plots[-1].showLabel("bottom", True)

    def _on_auto_range(self, checked: bool) -> None:
        self._auto_range = checked
        if checked:
            for p in self._plots:
                p.enableAutoRange(axis="y")

    def _on_window_changed(self, index: int) -> None:
        windows = [10, 30, 60, 120]
        if 0 <= index < len(windows):
            self._window_seconds = windows[index]

    # ── Theme ─────────────────────────────────────────────────────────

    def _apply_theme(self) -> None:
        tm = ThemeManager.instance()
        bg = tm.color("graph_bg")
        axis_pen = tm.color("graph_axis_text")
        text_pen = tm.color("graph_fg")
        muted = tm.color("text_muted")

        self._gw.setBackground(bg)
        for plot in self._plots:
            for ax_name in ("left", "bottom"):
                plot.getAxis(ax_name).setPen(pg.mkPen(axis_pen))
                plot.getAxis(ax_name).setTextPen(pg.mkPen(text_pen))

        # Channel checkboxes — keep channel color
        for cb in self._ch_checks:
            ch_color = cb.property("chColor")
            cb.setStyleSheet(
                f"QCheckBox {{ color: {ch_color}; font-weight: 700;"
                f" font-size: 11px; background: transparent; }}"
                f"QCheckBox::indicator {{ width: 14px; height: 14px; }}"
            )

        self._auto_range_cb.setStyleSheet(
            f"QCheckBox {{ color: {muted}; font-size: 11px; background: transparent; }}"
        )
        self._win_lbl.setStyleSheet(
            f"color: {muted}; font-size: 11px; background: transparent;"
        )
