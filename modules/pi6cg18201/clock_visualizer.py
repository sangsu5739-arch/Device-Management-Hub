"""
PI6CG18201 클럭 제너레이터 - 파형 시각화 모듈

QPainter 기반으로 클럭 파형을 렌더링합니다.
PI6CG18201은 Q0(Q0+/Q0-), Q1(Q1+/Q1-) 총 2개의 차동 출력 쌍을 가집니다.
Amplitude, Slew Rate, OE 상태에 따라 동적으로 파형이 변경됩니다.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QFont, QLinearGradient,
    QPainterPath, QBrush,
)
from PySide6.QtWidgets import QWidget


# ──────────────────────────────────────────────
# 색상 팔레트
# ──────────────────────────────────────────────

COLOR_BG = QColor(24, 26, 32)
COLOR_GRID = QColor(50, 55, 70)
COLOR_LABEL = QColor(160, 170, 190)

# Q0: 시안 계열 (+: 밝은, -: 어두운)
COLOR_Q0_POS = QColor(0, 210, 255)
COLOR_Q0_NEG = QColor(0, 140, 180)

# Q1: 핑크/마젠타 계열
COLOR_Q1_POS = QColor(255, 100, 180)
COLOR_Q1_NEG = QColor(180, 60, 130)

COLOR_DISABLED = QColor(70, 75, 85)

# 채널 정의: (이름, +색상, -색상)
CHANNELS = [
    ("Q0", COLOR_Q0_POS, COLOR_Q0_NEG),
    ("Q1", COLOR_Q1_POS, COLOR_Q1_NEG),
]


class ClockVisualizer(QWidget):
    """클럭 파형 시각화 위젯

    QPainter를 사용하여 PI6CG18201의 클럭 출력 파형을 렌더링합니다.
    Q0(Q0+/Q0-), Q1(Q1+/Q1-) 총 2개 차동 출력 쌍을 시각화합니다.

    Attributes:
        _amplitude_v: 출력 진폭 (V) - Y축 높이에 반영
        _slew_rate_level: Slew Rate 레벨 (0~15) - Edge 기울기에 반영
        _oe_states: [Q0, Q1] 활성화 상태
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(400, 250)
        self._amplitude_v: float = 0.8
        self._slew_rate_level: int = 8
        self._q_slew_bits: list[int] = [1, 1]  # [Q0, Q1], 0=slow, 1=fast
        self._oe_states: list = [True, True]

    def update_parameters(
        self,
        amplitude_v: float,
        slew_rate_level: int,
        oe_states: list,
        q_slew_bits: Optional[Sequence[int]] = None,
    ) -> None:
        """파형 파라미터 업데이트 및 재렌더링

        Args:
            amplitude_v: 출력 진폭 (0.6~0.9V)
            slew_rate_level: Slew Rate 결합 레벨 (0~15)
            oe_states: [Q0, Q1] 활성화 상태
        """
        self._amplitude_v = amplitude_v
        self._slew_rate_level = slew_rate_level
        self._oe_states = oe_states[:2]
        if q_slew_bits is not None and len(q_slew_bits) >= 2:
            self._q_slew_bits = [1 if bool(q_slew_bits[0]) else 0, 1 if bool(q_slew_bits[1]) else 0]
        self.update()

    def paintEvent(self, event) -> None:
        """위젯 페인트 이벤트 - 파형 렌더링"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # 배경
        self._draw_background(painter, w, h)

        # 그리드
        self._draw_grid(painter, w, h)

        # 각 채널 파형 (Q0, Q1 각각 + / - 쌍으로 렌더링)
        # 화면을 2등분: Q0 영역, Q1 영역
        margin_top = 45
        margin_bottom = 30
        usable_h = h - margin_top - margin_bottom
        channel_height = usable_h / 2

        for ch_idx in range(2):
            y_center = margin_top + channel_height * ch_idx + channel_height / 2
            self._draw_differential_waveform(
                painter, w, y_center, channel_height * 0.8,
                ch_idx, self._oe_states[ch_idx],
            )

        # 범례
        self._draw_legend(painter, w, h)

        # 파라미터 정보
        self._draw_info(painter, w, h)

        painter.end()

    def _draw_background(self, painter: QPainter, w: int, h: int) -> None:
        """배경 그라디언트"""
        gradient = QLinearGradient(0, 0, 0, h)
        gradient.setColorAt(0.0, QColor(18, 20, 28))
        gradient.setColorAt(1.0, QColor(28, 32, 42))
        painter.fillRect(0, 0, w, h, gradient)

    def _draw_grid(self, painter: QPainter, w: int, h: int) -> None:
        """그리드 라인"""
        pen = QPen(COLOR_GRID, 1, Qt.PenStyle.DotLine)
        painter.setPen(pen)

        x_step = max(w // 16, 30)
        for x in range(0, w, x_step):
            painter.drawLine(x, 0, x, h)

        y_step = max(h // 8, 25)
        for y in range(0, h, y_step):
            painter.drawLine(0, y, w, y)

    def _draw_differential_waveform(
        self,
        painter: QPainter,
        width: int,
        y_center: float,
        max_height: float,
        channel_idx: int,
        is_active: bool,
    ) -> None:
        """단일 채널의 차동 클럭 파형 그리기 (+ / -)

        Args:
            painter: QPainter 객체
            width: 위젯 너비
            y_center: 채널의 Y 중심 좌표
            max_height: 채널에 할당된 최대 높이
            channel_idx: 채널 인덱스 (0=Q0, 1=Q1)
            is_active: OE 활성화 여부
        """
        ch_name, color_pos, color_neg = CHANNELS[channel_idx]

        # 채널 이름 라벨
        font = QFont("Consolas", 12, QFont.Weight.Bold)
        painter.setFont(font)
        label_color = color_pos if is_active else COLOR_DISABLED
        painter.setPen(QPen(label_color, 1))
        painter.drawText(8, int(y_center - max_height / 2 + 16), ch_name)

        if not is_active:
            # 비활성: 점선 중앙 라인 + DISABLED
            pen = QPen(COLOR_DISABLED, 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(50, int(y_center), width - 15, int(y_center))

            painter.setPen(QPen(QColor(90, 95, 105), 1))
            small_font = QFont("Consolas", 10)
            painter.setFont(small_font)
            painter.drawText(width // 2 - 30, int(y_center - 3), "DISABLED")
            return

        # ── 활성 차동 파형 렌더링 ──
        amp_scale = self._amplitude_v / 0.9
        wave_height = max_height * amp_scale * 0.22  # 각 신호의 반 높이

        # 채널별 Slew(Q0/Q1)에 따른 Edge 폭
        # 0=Slow, 1=Fast (데이터시트 Byte2[1]/[2])
        q_slew = self._q_slew_bits[channel_idx] if channel_idx < len(self._q_slew_bits) else 1
        edge_width = 16.0 if q_slew == 0 else 4.0

        # Q+ 파형 (위쪽 영역)
        path_pos = self._build_clock_path(
            x_start=50, x_end=width - 15,
            y_center=y_center - wave_height * 0.8,
            wave_height=wave_height,
            edge_width=edge_width,
            inverted=False,
        )

        # Q- 파형 (아래쪽 영역, 반전)
        path_neg = self._build_clock_path(
            x_start=50, x_end=width - 15,
            y_center=y_center + wave_height * 0.8,
            wave_height=wave_height,
            edge_width=edge_width,
            inverted=True,
        )

        # 렌더링 — Q+ (글로우 + 메인)
        self._render_path(painter, path_pos, color_pos)

        # 렌더링 — Q- (글로우 + 메인, 살짝 투명)
        self._render_path(painter, path_neg, color_neg)

        # 레이블 (+/-)
        label_font = QFont("Consolas", 10, QFont.Weight.Medium)
        painter.setFont(label_font)
        painter.setPen(QPen(color_pos, 1))
        painter.drawText(30, int(y_center - wave_height * 0.8 - wave_height + 4), f"{ch_name}+")
        painter.setPen(QPen(color_neg, 1))
        painter.drawText(30, int(y_center + wave_height * 0.8 + wave_height + 6), f"{ch_name}−")

        # 전압 표기
        value_font = QFont("Consolas", 10, QFont.Weight.Medium)
        painter.setFont(value_font)
        painter.setPen(QPen(COLOR_LABEL, 1))
        painter.drawText(
            width - 50, int(y_center - max_height / 2 + 14),
            f"±{self._amplitude_v:.1f}V"
        )

    def _build_clock_path(
        self,
        x_start: float,
        x_end: float,
        y_center: float,
        wave_height: float,
        edge_width: float,
        inverted: bool,
    ) -> QPainterPath:
        """클럭 사각파 경로 생성

        Args:
            x_start: 시작 X
            x_end: 끝 X
            y_center: 중심 Y
            wave_height: 파형 반 높이
            edge_width: Edge 전환 폭 (Slew Rate)
            inverted: 반전 여부 (Q- 용)
        """
        path = QPainterPath()
        period = 80
        flat_len = max((period / 2.0) - edge_width, 5)

        x = float(x_start)
        state_high = not inverted  # Q+는 High 시작, Q-는 Low 시작
        first = True

        while x < x_end:
            y_high = y_center - wave_height
            y_low = y_center + wave_height

            if first:
                path.moveTo(x, y_high if state_high else y_low)
                first = False

            # 수평 구간
            y_current = y_high if state_high else y_low
            x_next = min(x + flat_len, x_end)
            path.lineTo(x_next, y_current)
            x = x_next

            if x >= x_end:
                break

            # Edge 전환
            y_target = y_low if state_high else y_high
            x_edge_end = min(x + edge_width, x_end)
            path.lineTo(x_edge_end, y_target)
            x = x_edge_end
            state_high = not state_high

        return path

    def _render_path(self, painter: QPainter, path: QPainterPath, color: QColor) -> None:
        """파형 경로 렌더링 (글로우 + 메인 라인)"""
        # 글로우
        glow_color = QColor(color)
        glow_color.setAlpha(35)
        glow_pen = QPen(glow_color, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(glow_pen)
        painter.drawPath(path)

        # 메인 라인
        main_pen = QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(main_pen)
        painter.drawPath(path)

    def _draw_legend(self, painter: QPainter, w: int, h: int) -> None:
        """범례 (상단)"""
        font = QFont("Segoe UI", 8)
        painter.setFont(font)

        x_pos = 10
        y_pos = 14

        for idx, (ch_name, color_pos, color_neg) in enumerate(CHANNELS):
            is_on = self._oe_states[idx] if idx < len(self._oe_states) else False
            status = "ON" if is_on else "OFF"
            color = color_pos if is_on else COLOR_DISABLED

            # 색상 샘플
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(x_pos, y_pos - 8, 10, 10, 2, 2)

            # 텍스트
            painter.setPen(QPen(color, 1))
            painter.drawText(x_pos + 14, y_pos, f"{ch_name}(±):{status}")
            x_pos += 110

    def _draw_info(self, painter: QPainter, w: int, h: int) -> None:
        """파라미터 정보 (하단)"""
        font = QFont("Consolas", 8)
        painter.setFont(font)
        painter.setPen(QPen(COLOR_LABEL, 1))

        active = sum(1 for s in self._oe_states if s)
        info_text = (
            f"Amplitude: {self._amplitude_v:.1f}V  │  "
            f"Slew Rate: Lv.{self._slew_rate_level}/15  │  "
            f"Q0:{'Fast' if self._q_slew_bits[0] else 'Slow'} / "
            f"Q1:{'Fast' if self._q_slew_bits[1] else 'Slow'}  │  "
            f"100 MHz HCSL  │  "
            f"Active: {active}/2"
        )
        painter.drawText(10, h - 8, info_text)
