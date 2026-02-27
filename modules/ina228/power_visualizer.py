"""
INA228 power visualization - pyqtgraph dual chart widget

Displays voltage (V) and current (A) as stacked dual charts.
Supports mouse-wheel zoom, autorange, and linked X-axis.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
from PySide6.QtCore import Qt

import pyqtgraph as pg


# pyqtgraph global settings (dark theme)
pg.setConfigOptions(antialias=True, background="#1a1c24", foreground="#c8cdd8")


class PowerVisualizer(QWidget):
    """INA228 real-time power visualization widget.

    Top: Bus voltage (V) time series chart
    Bottom: Current (mA) time series chart

    Features:
        - Shared X axis (time sync)
        - Mouse wheel zoom (pyqtgraph built-in)
        - Autorange toggle button
        - Dark theme (PI6CG palette)
    """

    # Colors (PI6CG palette)
    COLOR_VOLTAGE = "#00d2ff"   # Cyan (Q0 family)
    COLOR_CURRENT = "#ff64b4"   # Pink (Q1 family)
    COLOR_GRID    = "#3a3f50"
    COLOR_TEXT    = "#88a0cc"

    def __init__(self, parent: Optional[QWidget] = None, show_toolbar: bool = False) -> None:
        super().__init__(parent)
        self._auto_range: bool = True
        self._auto_range_every: int = 5
        self._auto_range_counter: int = 0
        self._init_plots()
        if show_toolbar:
            self._init_toolbar()

    def _init_plots(self) -> None:
        """Create and configure pyqtgraph PlotWidget."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # -- Voltage chart (top) --
        self._voltage_plot = pg.PlotWidget()
        self._voltage_plot.setLabel("left", "Voltage", units="V", color=self.COLOR_TEXT)
        self._voltage_plot.setLabel("bottom", "Time", units="s", color=self.COLOR_TEXT)
        self._voltage_plot.showGrid(x=True, y=True, alpha=0.3)
        self._voltage_plot.getAxis("left").setPen(pg.mkPen(self.COLOR_TEXT))
        self._voltage_plot.getAxis("bottom").setPen(pg.mkPen(self.COLOR_TEXT))

        title_style = {"color": self.COLOR_VOLTAGE, "size": "12pt", "bold": True}
        self._voltage_plot.setTitle("Bus Voltage", **title_style)

        self._voltage_curve = self._voltage_plot.plot(
            pen=pg.mkPen(color=self.COLOR_VOLTAGE, width=2),
            name="VBUS",
        )
        layout.addWidget(self._voltage_plot, 1)

        # -- Current chart (bottom) --
        self._current_plot = pg.PlotWidget()
        self._current_plot.setLabel("left", "Current", units="mA", color=self.COLOR_TEXT)
        self._current_plot.setLabel("bottom", "Time", units="s", color=self.COLOR_TEXT)
        self._current_plot.showGrid(x=True, y=True, alpha=0.3)
        self._current_plot.getAxis("left").setPen(pg.mkPen(self.COLOR_TEXT))
        self._current_plot.getAxis("bottom").setPen(pg.mkPen(self.COLOR_TEXT))

        title_style_c = {"color": self.COLOR_CURRENT, "size": "12pt", "bold": True}
        self._current_plot.setTitle("Current (Current)", **title_style_c)

        self._current_curve = self._current_plot.plot(
            pen=pg.mkPen(color=self.COLOR_CURRENT, width=2),
            name="Current",
        )
        layout.addWidget(self._current_plot, 1)

        # X-axis link
        self._current_plot.setXLink(self._voltage_plot)

    def _init_toolbar(self) -> None:
        """Autorange toggle toolbar."""
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(4, 2, 4, 2)

        self._auto_range_btn = QPushButton("Autorange: ON")
        self._auto_range_btn.setFixedWidth(140)
        self._auto_range_btn.setCheckable(True)
        self._auto_range_btn.setChecked(True)
        self._auto_range_btn.clicked.connect(self._on_auto_range_toggled)
        toolbar_layout.addWidget(self._auto_range_btn)

        toolbar_layout.addStretch()

        hint = QLabel("Mouse wheel: zoom  |  Right click: menu")
        hint.setStyleSheet("color: #6a7088; font-size: 10px;")
        toolbar_layout.addWidget(hint)

        # Add to layout (above top chart)
        main_layout = self.layout()
        main_layout.insertLayout(0, toolbar_layout)

    def _on_auto_range_toggled(self, checked: bool) -> None:
        self._auto_range = checked
        self._auto_range_btn.setText(f"Autorange: {'ON' if checked else 'OFF'}")
        self.set_auto_range(checked)
        self._auto_range_counter = 0

    def update_data(
        self,
        time_data: List[float],
        voltage_data: List[float],
        current_data: List[float],
    ) -> None:
        """Update both charts with data.

        Args:
            time_data: X-axis time array (s)
            voltage_data: Bus voltage array (V)
            current_data: Current array (mA)
        """
        self._voltage_curve.setData(time_data, voltage_data)
        self._current_curve.setData(time_data, current_data)

        if self._auto_range:
            self._auto_range_counter += 1
            if self._auto_range_counter % self._auto_range_every == 0:
                self._voltage_plot.enableAutoRange()
                self._current_plot.enableAutoRange()

    def clear(self) -> None:
        """Clear chart data."""
        self._voltage_curve.setData([], [])
        self._current_curve.setData([], [])
        self._auto_range_counter = 0

    def set_auto_range(self, enabled: bool) -> None:
        """Enable/disable autorange.

        Args:
            enabled: True=autorange, False=manual
        """
        self._auto_range = enabled
        self._voltage_plot.enableAutoRange(enable=enabled)
        self._current_plot.enableAutoRange(enable=enabled)
