"""
PI6CG18201 clock generator - waveform visualization module

Renders clock waveforms using QPainter.
PI6CG18201 has two differential output pairs: Q0(Q0+/Q0-) and Q1(Q1+/Q1-).
Waveform updates dynamically based on amplitude, slew rate, and OE state.
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


# ----------------------------------------------
# Color palette
# ----------------------------------------------

COLOR_BG = QColor(24, 26, 32)
COLOR_GRID = QColor(50, 55, 70)
COLOR_LABEL = QColor(160, 170, 190)

# Q0: cyan family (+: bright, -: dark)
COLOR_Q0_POS = QColor(0, 210, 255)
COLOR_Q0_NEG = QColor(0, 140, 180)

# Q1: pink/magenta family
COLOR_Q1_POS = QColor(255, 100, 180)
COLOR_Q1_NEG = QColor(180, 60, 130)

COLOR_DISABLED = QColor(70, 75, 85)

# Channel definition: (name, +color, -color)
CHANNELS = [
    ("Q0", COLOR_Q0_POS, COLOR_Q0_NEG),
    ("Q1", COLOR_Q1_POS, COLOR_Q1_NEG),
]


class ClockVisualizer(QWidget):
    """Clock waveform visualization widget.

    Uses QPainter to render PI6CG18201 clock output waveforms.
    Visualizes two differential output pairs: Q0(Q0+/Q0-) and Q1(Q1+/Q1-).

    Attributes:
        _amplitude_v: Output amplitude (V) - affects Y scale
        _slew_rate_level: Slew rate level (0~15) - affects edge slope
        _oe_states: [Q0, Q1] enable state
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
        """Update waveform parameters and repaint.

        Args:
            amplitude_v: Output amplitude (0.6~0.9V)
            slew_rate_level: Slew rate combined level (0~15)
            oe_states: [Q0, Q1] enable state
        """
        self._amplitude_v = amplitude_v
        self._slew_rate_level = slew_rate_level
        self._oe_states = oe_states[:2]
        if q_slew_bits is not None and len(q_slew_bits) >= 2:
            self._q_slew_bits = [1 if bool(q_slew_bits[0]) else 0, 1 if bool(q_slew_bits[1]) else 0]
        self.update()

    def paintEvent(self, event) -> None:
        """Widget paint event - render waveform."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Background
        self._draw_background(painter, w, h)

        # Grid
        self._draw_grid(painter, w, h)

        # Channel waveforms (Q0, Q1 each as +/- pair)
        # Split area in half: Q0 region, Q1 region
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

        # Legend
        self._draw_legend(painter, w, h)

        # Parameter info
        self._draw_info(painter, w, h)

        painter.end()

    def _draw_background(self, painter: QPainter, w: int, h: int) -> None:
        """Background gradient."""
        gradient = QLinearGradient(0, 0, 0, h)
        gradient.setColorAt(0.0, QColor(18, 20, 28))
        gradient.setColorAt(1.0, QColor(28, 32, 42))
        painter.fillRect(0, 0, w, h, gradient)

    def _draw_grid(self, painter: QPainter, w: int, h: int) -> None:
        """Grid lines."""
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
        """Draw differential clock waveform for one channel (+/-).

        Args:
            painter: QPainter object
            width: widget width
            y_center: channel Y center
            max_height: max height allocated to channel
            channel_idx: channel index (0=Q0, 1=Q1)
            is_active: OE enable state
        """
        ch_name, color_pos, color_neg = CHANNELS[channel_idx]

        # Channel name label
        font = QFont("Consolas", 12, QFont.Weight.Bold)
        painter.setFont(font)
        label_color = color_pos if is_active else COLOR_DISABLED
        painter.setPen(QPen(label_color, 1))
        painter.drawText(8, int(y_center - max_height / 2 + 16), ch_name)

        if not is_active:
            # Inactive: dashed center line + DISABLED
            pen = QPen(COLOR_DISABLED, 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(50, int(y_center), width - 15, int(y_center))

            painter.setPen(QPen(QColor(90, 95, 105), 1))
            small_font = QFont("Consolas", 10)
            painter.setFont(small_font)
            painter.drawText(width // 2 - 30, int(y_center - 3), "DISABLED")
            return

        # -- Active differential waveform rendering --
        amp_scale = self._amplitude_v / 0.9
        wave_height = max_height * amp_scale * 0.22  # half-height per signal

        # Edge width based on per-channel slew (Q0/Q1)
        # 0=Slow, 1=Fast (datasheet Byte2[1]/[2])
        q_slew = self._q_slew_bits[channel_idx] if channel_idx < len(self._q_slew_bits) else 1
        edge_width = 16.0 if q_slew == 0 else 4.0

        # Q+ waveform (upper region)
        path_pos = self._build_clock_path(
            x_start=50, x_end=width - 15,
            y_center=y_center - wave_height * 0.8,
            wave_height=wave_height,
            edge_width=edge_width,
            inverted=False,
        )

        # Q- waveform (lower region, inverted)
        path_neg = self._build_clock_path(
            x_start=50, x_end=width - 15,
            y_center=y_center + wave_height * 0.8,
            wave_height=wave_height,
            edge_width=edge_width,
            inverted=True,
        )

        # Render - Q+ (glow + main)
        self._render_path(painter, path_pos, color_pos)

        # Render - Q- (glow + main, slightly transparent)
        self._render_path(painter, path_neg, color_neg)

        # Label (+/-)
        label_font = QFont("Consolas", 10, QFont.Weight.Medium)
        painter.setFont(label_font)
        painter.setPen(QPen(color_pos, 1))
        painter.drawText(30, int(y_center - wave_height * 0.8 - wave_height + 4), f"{ch_name}+")
        painter.setPen(QPen(color_neg, 1))
        painter.drawText(30, int(y_center + wave_height * 0.8 + wave_height + 6), f"{ch_name}-")

        # Voltage annotation
        value_font = QFont("Consolas", 10, QFont.Weight.Medium)
        painter.setFont(value_font)
        painter.setPen(QPen(COLOR_LABEL, 1))
        painter.drawText(
            width - 50, int(y_center - max_height / 2 + 14),
            f"+/-{self._amplitude_v:.1f}V"
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
        """Generate clock square-wave path.

        Args:
            x_start: start X
            x_end: end X
            y_center: center Y
            wave_height: waveform half height
            edge_width: edge transition width (Slew Rate)
            inverted: inverted (for Q-)
        """
        path = QPainterPath()
        period = 80
        flat_len = max((period / 2.0) - edge_width, 5)

        x = float(x_start)
        state_high = not inverted  # Q+ starts High, Q- starts Low
        first = True

        while x < x_end:
            y_high = y_center - wave_height
            y_low = y_center + wave_height

            if first:
                path.moveTo(x, y_high if state_high else y_low)
                first = False

            # Horizontal segment
            y_current = y_high if state_high else y_low
            x_next = min(x + flat_len, x_end)
            path.lineTo(x_next, y_current)
            x = x_next

            if x >= x_end:
                break

            # Edge transition
            y_target = y_low if state_high else y_high
            x_edge_end = min(x + edge_width, x_end)
            path.lineTo(x_edge_end, y_target)
            x = x_edge_end
            state_high = not state_high

        return path

    def _render_path(self, painter: QPainter, path: QPainterPath, color: QColor) -> None:
        """Render waveform path (glow + main line)."""
        # Glow
        glow_color = QColor(color)
        glow_color.setAlpha(35)
        glow_pen = QPen(glow_color, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(glow_pen)
        painter.drawPath(path)

        # Main line
        main_pen = QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(main_pen)
        painter.drawPath(path)

    def _draw_legend(self, painter: QPainter, w: int, h: int) -> None:
        """Legend (top)."""
        font = QFont("Segoe UI", 8)
        painter.setFont(font)

        x_pos = 10
        y_pos = 14

        for idx, (ch_name, color_pos, color_neg) in enumerate(CHANNELS):
            is_on = self._oe_states[idx] if idx < len(self._oe_states) else False
            status = "ON" if is_on else "OFF"
            color = color_pos if is_on else COLOR_DISABLED

            # Color sample
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(x_pos, y_pos - 8, 10, 10, 2, 2)

            # Text
            painter.setPen(QPen(color, 1))
            painter.drawText(x_pos + 14, y_pos, f"{ch_name}(+/-):{status}")
            x_pos += 110

    def _draw_info(self, painter: QPainter, w: int, h: int) -> None:
        """Parameter info (bottom)."""
        font = QFont("Consolas", 8)
        painter.setFont(font)
        painter.setPen(QPen(COLOR_LABEL, 1))

        active = sum(1 for s in self._oe_states if s)
        info_text = (
            f"Amplitude: {self._amplitude_v:.1f}V  |  "
            f"Slew Rate: Lv.{self._slew_rate_level}/15  |  "
            f"Q0:{'Fast' if self._q_slew_bits[0] else 'Slow'} / "
            f"Q1:{'Fast' if self._q_slew_bits[1] else 'Slow'}  |  "
            f"100 MHz HCSL  |  "
            f"Active: {active}/2"
        )
        painter.drawText(10, h - 8, info_text)
