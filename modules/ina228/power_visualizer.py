"""
INA228 전력 시각화 - pyqtgraph 듀얼 차트 위젯

전압(V)과 전류(A)를 상하 듀얼 차트로 표시합니다.
마우스 휠 줌, 오토레인지, X축 링크를 지원합니다.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
from PySide6.QtCore import Qt

import pyqtgraph as pg


# pyqtgraph 전역 설정 (다크 테마)
pg.setConfigOptions(antialias=True, background="#1a1c24", foreground="#c8cdd8")


class PowerVisualizer(QWidget):
    """INA228 전력 실시간 시각화 위젯

    상단: 버스 전압 (V) 시계열 차트
    하단: 전류 (mA) 시계열 차트

    Features:
        - X축 공유 (시간 동기화)
        - 마우스 휠 줌 (pyqtgraph 내장)
        - 오토레인지 토글 버튼
        - 다크 테마 (PI6CG 팔레트 통일)
    """

    # 색상 (PI6CG 팔레트 통일)
    COLOR_VOLTAGE = "#00d2ff"   # 시안 (Q0 계열)
    COLOR_CURRENT = "#ff64b4"   # 핑크 (Q1 계열)
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
        """pyqtgraph PlotWidget 생성 및 설정"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ── 전압 차트 (상단) ──
        self._voltage_plot = pg.PlotWidget()
        self._voltage_plot.setLabel("left", "전압", units="V", color=self.COLOR_TEXT)
        self._voltage_plot.setLabel("bottom", "시간", units="s", color=self.COLOR_TEXT)
        self._voltage_plot.showGrid(x=True, y=True, alpha=0.3)
        self._voltage_plot.getAxis("left").setPen(pg.mkPen(self.COLOR_TEXT))
        self._voltage_plot.getAxis("bottom").setPen(pg.mkPen(self.COLOR_TEXT))

        title_style = {"color": self.COLOR_VOLTAGE, "size": "12pt", "bold": True}
        self._voltage_plot.setTitle("버스 전압 (Bus Voltage)", **title_style)

        self._voltage_curve = self._voltage_plot.plot(
            pen=pg.mkPen(color=self.COLOR_VOLTAGE, width=2),
            name="VBUS",
        )
        layout.addWidget(self._voltage_plot, 1)

        # ── 전류 차트 (하단) ──
        self._current_plot = pg.PlotWidget()
        self._current_plot.setLabel("left", "전류", units="mA", color=self.COLOR_TEXT)
        self._current_plot.setLabel("bottom", "시간", units="s", color=self.COLOR_TEXT)
        self._current_plot.showGrid(x=True, y=True, alpha=0.3)
        self._current_plot.getAxis("left").setPen(pg.mkPen(self.COLOR_TEXT))
        self._current_plot.getAxis("bottom").setPen(pg.mkPen(self.COLOR_TEXT))

        title_style_c = {"color": self.COLOR_CURRENT, "size": "12pt", "bold": True}
        self._current_plot.setTitle("전류 (Current)", **title_style_c)

        self._current_curve = self._current_plot.plot(
            pen=pg.mkPen(color=self.COLOR_CURRENT, width=2),
            name="Current",
        )
        layout.addWidget(self._current_plot, 1)

        # X축 링크
        self._current_plot.setXLink(self._voltage_plot)

    def _init_toolbar(self) -> None:
        """오토레인지 토글 버튼 툴바"""
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(4, 2, 4, 2)

        self._auto_range_btn = QPushButton("오토 레인지: ON")
        self._auto_range_btn.setFixedWidth(140)
        self._auto_range_btn.setCheckable(True)
        self._auto_range_btn.setChecked(True)
        self._auto_range_btn.clicked.connect(self._on_auto_range_toggled)
        toolbar_layout.addWidget(self._auto_range_btn)

        toolbar_layout.addStretch()

        hint = QLabel("마우스 휠: 줌  |  우클릭: 메뉴")
        hint.setStyleSheet("color: #6a7088; font-size: 10px;")
        toolbar_layout.addWidget(hint)

        # 레이아웃에 추가 (상단 차트 위)
        main_layout = self.layout()
        main_layout.insertLayout(0, toolbar_layout)

    def _on_auto_range_toggled(self, checked: bool) -> None:
        self._auto_range = checked
        self._auto_range_btn.setText(f"오토 레인지: {'ON' if checked else 'OFF'}")
        self.set_auto_range(checked)
        self._auto_range_counter = 0

    def update_data(
        self,
        time_data: List[float],
        voltage_data: List[float],
        current_data: List[float],
    ) -> None:
        """양쪽 차트 데이터 업데이트

        Args:
            time_data: X축 시간 배열 (초)
            voltage_data: 버스 전압 배열 (V)
            current_data: 전류 배열 (mA)
        """
        self._voltage_curve.setData(time_data, voltage_data)
        self._current_curve.setData(time_data, current_data)

        if self._auto_range:
            self._auto_range_counter += 1
            if self._auto_range_counter % self._auto_range_every == 0:
                self._voltage_plot.enableAutoRange()
                self._current_plot.enableAutoRange()

    def clear(self) -> None:
        """차트 데이터 초기화"""
        self._voltage_curve.setData([], [])
        self._current_curve.setData([], [])
        self._auto_range_counter = 0

    def set_auto_range(self, enabled: bool) -> None:
        """오토레인지 활성화/비활성화

        Args:
            enabled: True=오토레인지, False=수동
        """
        self._auto_range = enabled
        self._voltage_plot.enableAutoRange(enable=enabled)
        self._current_plot.enableAutoRange(enable=enabled)
