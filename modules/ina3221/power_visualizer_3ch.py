"""
INA3221 power visualization - 3-channel dual chart widget
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
from PySide6.QtCore import Qt

import pyqtgraph as pg

from core.theme_manager import ThemeManager

pg.setConfigOptions(antialias=True)

class PowerVisualizer3CH(QWidget):
    """INA3221 real-time power visualization widget for 3 channels.

    Top: Bus voltage (V) time series chart (3 lines)
    Bottom: Current (mA) time series chart (3 lines)
    """

    COLOR_CH1 = "#00d2ff"   # Cyan
    COLOR_CH2 = "#ff64b4"   # Pink
    COLOR_CH3 = "#ffd000"   # Yellow
    COLOR_TEXT = "#88a0cc"

    def __init__(self, parent: Optional[QWidget] = None, show_toolbar: bool = False) -> None:
        super().__init__(parent)
        self._auto_range: bool = True
        self._auto_range_every: int = 5
        self._auto_range_counter: int = 0
        self._init_plots()
        if show_toolbar:
            self._init_toolbar()
        self._apply_theme()
        ThemeManager.instance().theme_changed.connect(self._apply_theme)

    def _init_plots(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # -- Voltage chart (top) --
        self._voltage_plot = pg.PlotWidget()
        self._voltage_plot.setLabel("left", "Voltage", units="V", color=self.COLOR_TEXT)
        self._voltage_plot.setLabel("bottom", "Time", units="s", color=self.COLOR_TEXT)
        self._voltage_plot.showGrid(x=True, y=True, alpha=0.3)
        self._voltage_plot.getAxis("left").setWidth(50)
        self._voltage_plot.addLegend(offset=(10, 10))
        
        title_style = {"color": self.COLOR_TEXT, "size": "12pt", "bold": True}
        self._voltage_plot.setTitle("Bus Voltage", **title_style)

        self._v_curves = [
            self._voltage_plot.plot(pen=pg.mkPen(color=c, width=2), name=f"CH{i+1}")
            for i, c in enumerate([self.COLOR_CH1, self.COLOR_CH2, self.COLOR_CH3])
        ]
        layout.addWidget(self._voltage_plot, 1)

        # -- Current chart (bottom) --
        self._current_plot = pg.PlotWidget()
        self._current_plot.setLabel("left", "Current", units="mA", color=self.COLOR_TEXT)
        self._current_plot.setLabel("bottom", "Time", units="s", color=self.COLOR_TEXT)
        self._current_plot.showGrid(x=True, y=True, alpha=0.3)
        self._current_plot.getAxis("left").setWidth(50)
        self._current_plot.addLegend(offset=(10, 10))

        self._current_plot.setTitle("Current", **title_style)

        self._i_curves = [
            self._current_plot.plot(pen=pg.mkPen(color=c, width=2), name=f"CH{i+1}")
            for i, c in enumerate([self.COLOR_CH1, self.COLOR_CH2, self.COLOR_CH3])
        ]
        layout.addWidget(self._current_plot, 1)

        self._current_plot.setXLink(self._voltage_plot)

    def _init_toolbar(self) -> None:
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(4, 2, 4, 2)

        self._auto_range_btn = QPushButton("Autorange: ON")
        self._auto_range_btn.setFixedWidth(140)
        self._auto_range_btn.setCheckable(True)
        self._auto_range_btn.setChecked(True)
        self._auto_range_btn.clicked.connect(self._on_auto_range_toggled)
        toolbar_layout.addWidget(self._auto_range_btn)
        toolbar_layout.addStretch()

        main_layout = self.layout()
        main_layout.insertLayout(0, toolbar_layout)

    def _on_auto_range_toggled(self, checked: bool) -> None:
        self.set_auto_range(checked)
        self._auto_range_counter = 0

    def update_data(
        self,
        time_data: List[float],
        voltage_data: List[List[float]],
        current_data: List[List[float]],
    ) -> None:
        for i in range(3):
            self._v_curves[i].setData(time_data, voltage_data[i])
            self._i_curves[i].setData(time_data, current_data[i])

        if self._auto_range:
            self._auto_range_counter += 1
            if self._auto_range_counter % self._auto_range_every == 0:
                self._voltage_plot.enableAutoRange()
                self._current_plot.enableAutoRange()

    def clear(self) -> None:
        for i in range(3):
            self._v_curves[i].setData([], [])
            self._i_curves[i].setData([], [])
        self._auto_range_counter = 0

    def set_auto_range(self, enabled: bool) -> None:
        self._auto_range = enabled
        self._voltage_plot.enableAutoRange(enable=enabled)
        self._current_plot.enableAutoRange(enable=enabled)

    def _apply_theme(self) -> None:
        tm = ThemeManager.instance()
        bg = tm.color("graph_bg")
        fg = tm.color("graph_fg")
        axis_color = tm.color("graph_axis_text")
        for plot in (self._voltage_plot, self._current_plot):
            plot.setBackground(bg)
            for axis_name in ("left", "bottom"):
                plot.getAxis(axis_name).setPen(pg.mkPen(axis_color))
                plot.getAxis(axis_name).setTextPen(pg.mkPen(fg))
