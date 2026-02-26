"""
FTDI Interactive Pinout Widget — STM32 CubeIDE 스타일 핀맵 시각화

QPainter로 칩 패키지를 렌더링하고, 핀 위에 마우스를 올리면
해당 핀의 속성과 현재 상태를 표시합니다.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QSize, QTimer
from PySide6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics, QPen, QBrush,
    QPainterPath, QLinearGradient, QMouseEvent, QPaintEvent,
)
from PySide6.QtWidgets import QWidget, QToolTip

from modules.ftdi_verifier.ftdi_chip_specs import (
    ChipSpec, PinSpec, PinFunction, PinDirection,
    PIN_COLORS, ProtocolMode,
)


# ── 핀 기능 → 사람이 읽을 수 있는 약어 ──
_FUNC_SHORT_LABELS: Dict[PinFunction, str] = {
    PinFunction.I2C_SCL:     "SCL",
    PinFunction.I2C_SDA_OUT: "SDA",
    PinFunction.I2C_SDA_IN:  "SDA",
    PinFunction.SPI_SCK:     "SCK",
    PinFunction.SPI_MOSI:    "MOSI",
    PinFunction.SPI_MISO:    "MISO",
    PinFunction.SPI_CS:      "CS",
    PinFunction.JTAG_TCK:    "TCK",
    PinFunction.JTAG_TDI:    "TDI",
    PinFunction.JTAG_TDO:    "TDO",
    PinFunction.JTAG_TMS:    "TMS",
    PinFunction.UART_TX:     "TX",
    PinFunction.UART_RX:     "RX",
    PinFunction.UART_RTS:    "RTS",
    PinFunction.UART_CTS:    "CTS",
    PinFunction.UART_DTR:    "DTR",
    PinFunction.UART_DSR:    "DSR",
    PinFunction.UART_DCD:    "DCD",
    PinFunction.UART_RI:     "RI",
    PinFunction.GPIO_OUT:    "GPIO",
    PinFunction.GPIO_IN:     "GPIO",
    PinFunction.POWER:       "VCC",
    PinFunction.GROUND:      "GND",
    PinFunction.NC:          "NC",
    PinFunction.SPECIAL:     "SPEC",
}


class PinoutWidget(QWidget):
    """CubeIDE 스타일 인터랙티브 FTDI 핀맵 위젯

    Signals:
        pin_clicked(int): 핀 번호 클릭 시 발행
        pin_hovered(int): 핀 위에 마우스 올릴 때 발행 (-1 = 벗어남)
    """

    pin_clicked = Signal(int)
    pin_hovered = Signal(int)

    # ── 레이아웃 상수 (크게 확대) ──
    _CHIP_BODY_RATIO = 0.38
    _PIN_WIDTH = 56          # 20 → 56 (핀 이름이 들어갈 만큼 넓게)
    _PIN_HEIGHT = 22         # 14 → 22
    _PIN_SPACING = 3
    _PIN_LABEL_MARGIN = 8
    _CORNER_RADIUS = 10

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(550, 450)

        self._chip: Optional[ChipSpec] = None
        self._pin_rects: Dict[int, QRectF] = {}
        self._pin_states: Dict[int, bool] = {}
        self._pin_active_funcs: Dict[int, PinFunction] = {}
        self._hovered_pin: int = -1
        self._selected_pin: int = -1
        self._channel_filter: str = ""
        self._blink_on: bool = True
        self._painting: bool = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(450)
        self._blink_timer.setSingleShot(False)
        self._blink_timer.timeout.connect(self._on_blink)
        self._blink_timer.start()

    # ── 공개 API ──

    def set_chip(self, chip: ChipSpec) -> None:
        self._chip = chip
        self._pin_rects.clear()
        self._pin_states = {num: False for num in chip.pins}
        self._pin_active_funcs = {num: p.default_function for num, p in chip.pins.items()}
        self._hovered_pin = -1
        self._selected_pin = -1
        self.update()

    def set_pin_state(self, pin_number: int, high: bool) -> None:
        if pin_number in self._pin_states:
            self._pin_states[pin_number] = high
            self.update()

    def set_pin_states_bulk(self, states: Dict[int, bool]) -> None:
        self._pin_states.update(states)
        self.update()

    def set_pin_function(self, pin_number: int, func: PinFunction) -> None:
        if pin_number in self._pin_active_funcs:
            self._pin_active_funcs[pin_number] = func
            self.update()

    def set_channel_filter(self, channel: str) -> None:
        self._channel_filter = channel
        self.update()

    def get_selected_pin(self) -> int:
        return self._selected_pin

    # ── 페인트 ──

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._chip is None or self._painting:
            return

        self._painting = True
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

            # 배경
            painter.fillRect(self.rect(), QColor("#2a3040"))

            w, h = self.width(), self.height()
            self._pin_rects.clear()

            # 칩 바디 영역
            body_w = w * self._CHIP_BODY_RATIO
            body_h = h * 0.60
            body_x = (w - body_w) / 2
            body_y = (h - body_h) / 2
            body_rect = QRectF(body_x, body_y, body_w, body_h)

            self._draw_chip_body(painter, body_rect)
            self._draw_pins(painter, body_rect)
            self._draw_hover_tooltip(painter)

            painter.end()
        finally:
            self._painting = False

    def _draw_chip_body(self, p: QPainter, r: QRectF) -> None:
        """칩 바디 — 진한 회색 + 금속 테두리 + 로고"""
        # 외곽 그림자
        shadow = QRectF(r.x() + 3, r.y() + 3, r.width(), r.height())
        p.setBrush(QColor(0, 0, 0, 60))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(shadow, self._CORNER_RADIUS, self._CORNER_RADIUS)

        # 그라데이션 배경 (무광 에폭시 느낌)
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0.0, QColor("#1A1A1A"))
        grad.setColorAt(0.5, QColor("#222222"))
        grad.setColorAt(1.0, QColor("#2A2A2A"))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor("#3a3a3a"), 2.5))
        p.drawRoundedRect(r, self._CORNER_RADIUS, self._CORNER_RADIUS)

        # 내부 테두리 (밝은 선)
        inner = r.adjusted(4, 4, -4, -4)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(100, 120, 160, 50), 1))
        p.drawRoundedRect(inner, self._CORNER_RADIUS - 2, self._CORNER_RADIUS - 2)

        # 1번 핀 마커
        marker_r = 7
        p.setBrush(QColor("#8899bb"))
        p.setPen(QPen(QColor("#aabbdd"), 1.5))
        p.drawEllipse(QPointF(r.left() + 18, r.top() + 18), marker_r, marker_r)

        # 칩 이름 (크고 선명하게)
        name_font = QFont("Segoe UI", 18, QFont.Weight.Bold)
        p.setFont(name_font)
        p.setPen(QColor("#D1D1D1"))
        name_rect = QRectF(r.x(), r.center().y() - 22, r.width(), 36)
        p.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, self._chip.name)

        # 패키지 + 설명
        sub_font = QFont("Segoe UI", 10)
        p.setFont(sub_font)
        p.setPen(QColor("#B8B8B8"))
        sub_rect = QRectF(r.x(), r.center().y() + 16, r.width(), 20)
        p.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, self._chip.package)

        # FTDI 로고 텍스트
        logo_font = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
        p.setFont(logo_font)
        p.setPen(QColor("#D1D1D1"))
        logo_rect = QRectF(r.x(), r.bottom() - 24, r.width(), 16)
        p.drawText(logo_rect, Qt.AlignmentFlag.AlignCenter, "FTDI")

    def _draw_pins(self, p: QPainter, body: QRectF) -> None:
        if self._chip is None:
            return

        dir_pins: Dict[PinDirection, List[PinSpec]] = {
            d: [] for d in PinDirection
        }
        for pin in self._chip.pins.values():
            dir_pins[pin.direction].append(pin)
        for pins_list in dir_pins.values():
            pins_list.sort(key=lambda pp: pp.number)

        pw, ph = self._PIN_WIDTH, self._PIN_HEIGHT
        sp = self._PIN_SPACING

        # 좌측
        left_pins = dir_pins[PinDirection.LEFT]
        if left_pins:
            total_h = len(left_pins) * (ph + sp) - sp
            start_y = body.center().y() - total_h / 2
            for i, pin in enumerate(left_pins):
                y = start_y + i * (ph + sp)
                rect = QRectF(body.left() - pw - 4, y, pw, ph)
                self._pin_rects[pin.number] = rect
                self._draw_single_pin(p, pin, rect, PinDirection.LEFT, body)

        # 우측
        right_pins = dir_pins[PinDirection.RIGHT]
        if right_pins:
            total_h = len(right_pins) * (ph + sp) - sp
            start_y = body.center().y() - total_h / 2
            for i, pin in enumerate(right_pins):
                y = start_y + i * (ph + sp)
                rect = QRectF(body.right() + 4, y, pw, ph)
                self._pin_rects[pin.number] = rect
                self._draw_single_pin(p, pin, rect, PinDirection.RIGHT, body)

        # 상단
        top_pins = dir_pins[PinDirection.TOP]
        if top_pins:
            total_w = len(top_pins) * (ph + sp) - sp  # 상/하는 핀높이 = 폭
            start_x = body.center().x() - total_w / 2
            for i, pin in enumerate(top_pins):
                x = start_x + i * (ph + sp)
                rect = QRectF(x, body.top() - pw * 0.6, ph, pw * 0.6)
                self._pin_rects[pin.number] = rect
                self._draw_single_pin(p, pin, rect, PinDirection.TOP, body)

        # 하단
        bottom_pins = dir_pins[PinDirection.BOTTOM]
        if bottom_pins:
            total_w = len(bottom_pins) * (ph + sp) - sp
            start_x = body.center().x() - total_w / 2
            for i, pin in enumerate(bottom_pins):
                x = start_x + i * (ph + sp)
                rect = QRectF(x, body.bottom() + 2, ph, pw * 0.6)
                self._pin_rects[pin.number] = rect
                self._draw_single_pin(p, pin, rect, PinDirection.BOTTOM, body)

    def _draw_single_pin(
        self, p: QPainter, pin: PinSpec, rect: QRectF,
        direction: PinDirection, body: QRectF,
    ) -> None:
        active_func = self._pin_active_funcs.get(pin.number, pin.default_function)
        color_str = PIN_COLORS.get(active_func, "#555555")
        base_color = QColor(color_str)

        dimmed = False
        if self._channel_filter and pin.channel and pin.channel != self._channel_filter:
            dimmed = True
            base_color = QColor("#292d3a")

        is_hover = (pin.number == self._hovered_pin)
        is_selected = (pin.number == self._selected_pin)

        if is_selected:
            border_color = QColor("#ffd24a")
            border_w = 3.5
        elif is_hover:
            border_color = base_color.lighter(170)
            border_w = 2.5
        else:
            border_color = base_color.darker(110)
            border_w = 1.2

        state = self._pin_states.get(pin.number, False)
        if state and not dimmed:
            fill = base_color.lighter(140)
        else:
            fill = base_color
        if is_selected and not dimmed:
            fill = fill.lighter(120)

        # Selected glow / blink
        if is_selected and not dimmed:
            glow = QColor("#ffd24a")
            glow.setAlpha(110 if self._blink_on else 40)
            p.setPen(QPen(glow, 6.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 5, 5)


        # ── 핀 사각형 (좌우 핀은 넓게, 상하 핀은 작게) ──
        p.setBrush(QBrush(fill))
        p.setPen(QPen(border_color, border_w))
        p.drawRoundedRect(rect, 4, 4)

        # ── 핀 내부에 약어 표시 (좌/우 핀) ──
        func_label = _FUNC_SHORT_LABELS.get(active_func, "")
        if direction in (PinDirection.LEFT, PinDirection.RIGHT):
            inner_font = QFont("Consolas", 8, QFont.Weight.Bold)
            p.setFont(inner_font)
            text_color = QColor("#111111") if fill.lightness() > 140 else QColor("#e8eeff")
            p.setPen(text_color)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, func_label)

        # ── 핀 이름 라벨 (외부) ──
        label_font = QFont("Consolas", 9, QFont.Weight.Bold)
        p.setFont(label_font)
        fm = QFontMetrics(label_font)
        label_color = QColor("#d0d8ee") if not dimmed else QColor("#3a3f50")
        p.setPen(label_color)

        margin = self._PIN_LABEL_MARGIN
        if direction == PinDirection.LEFT:
            lw = fm.horizontalAdvance(pin.name)
            gap = 12
            label_rect = QRectF(rect.left() - lw - gap, rect.top(), lw, rect.height())
            p.drawText(label_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, pin.name)
        elif direction == PinDirection.RIGHT:
            lw = fm.horizontalAdvance(pin.name)
            label_rect = QRectF(rect.right() + margin, rect.top(), lw + margin, rect.height())
            p.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, pin.name)
        elif direction == PinDirection.TOP:
            p.save()
            p.translate(rect.center().x(), rect.top() - margin)
            p.rotate(-90)
            p.drawText(0, -6, fm.horizontalAdvance(pin.name) + 8, 14,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, pin.name)
            p.restore()
        elif direction == PinDirection.BOTTOM:
            p.save()
            p.translate(rect.center().x(), rect.bottom() + margin)
            p.rotate(90)
            p.drawText(0, -6, fm.horizontalAdvance(pin.name) + 8, 14,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, pin.name)
            p.restore()

        # ── 연결선 (점선) ──
        line_color = fill.darker(130) if not dimmed else QColor("#222233")
        p.setPen(QPen(line_color, 1.0, Qt.PenStyle.DotLine))
        if direction == PinDirection.LEFT:
            p.drawLine(QPointF(rect.right(), rect.center().y()),
                       QPointF(body.left(), rect.center().y()))
        elif direction == PinDirection.RIGHT:
            p.drawLine(QPointF(rect.left(), rect.center().y()),
                       QPointF(body.right(), rect.center().y()))
        elif direction == PinDirection.TOP:
            p.drawLine(QPointF(rect.center().x(), rect.bottom()),
                       QPointF(rect.center().x(), body.top()))
        elif direction == PinDirection.BOTTOM:
            p.drawLine(QPointF(rect.center().x(), rect.top()),
                       QPointF(rect.center().x(), body.bottom()))

    def _draw_hover_tooltip(self, p: QPainter) -> None:
        if self._hovered_pin < 0 or self._chip is None:
            return
        pin = self._chip.pins.get(self._hovered_pin)
        if pin is None:
            return
        rect = self._pin_rects.get(self._hovered_pin)
        if rect is None:
            return

        active_func = self._pin_active_funcs.get(pin.number, pin.default_function)
        state_str = "HIGH" if self._pin_states.get(pin.number, False) else "LOW"
        funcs_str = ", ".join(_FUNC_SHORT_LABELS.get(f, f.name) for f in pin.functions[:5])

        lines = [
            f"Pin {pin.number}: {pin.name}",
            f"Channel: {pin.channel or 'N/A'}",
            f"Active: {_FUNC_SHORT_LABELS.get(active_func, active_func.name)}",
            f"State: {state_str}",
            f"Modes: {funcs_str}",
        ]
        if pin.description:
            lines.append(f"{pin.description}")

        tip_font = QFont("Segoe UI", 10)
        p.setFont(tip_font)
        fm = QFontMetrics(tip_font)
        line_h = fm.height() + 4
        max_w = max(fm.horizontalAdvance(ln) for ln in lines) + 28
        box_h = len(lines) * line_h + 16

        tip_x = rect.right() + 16
        tip_y = rect.center().y() - box_h / 2
        if tip_x + max_w > self.width():
            tip_x = rect.left() - max_w - 16
        if tip_y < 4:
            tip_y = 4
        if tip_y + box_h > self.height() - 4:
            tip_y = self.height() - box_h - 4

        tip_rect = QRectF(tip_x, tip_y, max_w, box_h)

        # 반투명 배경 + 테두리
        p.setBrush(QColor(18, 22, 34, 240))
        func_color = QColor(PIN_COLORS.get(active_func, "#5577aa"))
        p.setPen(QPen(func_color, 2.0))
        p.drawRoundedRect(tip_rect, 8, 8)

        # 헤더 (첫 줄) — 강조색
        p.setPen(func_color.lighter(130))
        header_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        p.setFont(header_font)
        y0 = tip_rect.top() + 10
        p.drawText(QPointF(tip_rect.left() + 14, y0 + fm.ascent()), lines[0])

        # 나머지 줄
        p.setFont(tip_font)
        p.setPen(QColor("#c8d4ee"))
        for i, line in enumerate(lines[1:], start=1):
            y = y0 + i * line_h
            p.drawText(QPointF(tip_rect.left() + 14, y + fm.ascent()), line)

    # ── 마우스 이벤트 ──

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        old_hover = self._hovered_pin
        self._hovered_pin = -1

        for num, rect in self._pin_rects.items():
            if rect.contains(pos):
                self._hovered_pin = num
                break

        if self._hovered_pin != old_hover:
            self.pin_hovered.emit(self._hovered_pin)
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            for num, rect in self._pin_rects.items():
                if rect.contains(pos):
                    self._selected_pin = num
                    self.pin_clicked.emit(num)
                    self.update()
                    return
            self._selected_pin = -1
            self.pin_clicked.emit(-1)
            self.update()

    def leaveEvent(self, event) -> None:
        self._hovered_pin = -1
        self.pin_hovered.emit(-1)
        self.update()

    def _on_blink(self) -> None:
        self._blink_on = not self._blink_on
        if self._selected_pin >= 0 and not self._painting:
            rect = self._pin_rects.get(self._selected_pin)
            if rect is not None:
                # 선택된 핀 영역만 다시 그리기
                self.update(rect.adjusted(-8, -8, 8, 8).toAlignedRect())
            else:
                self.update()

    def sizeHint(self) -> QSize:
        return QSize(650, 550)

    def closeEvent(self, event) -> None:
        """위젯이 닫힐 때 타이머 정리."""
        self._blink_timer.stop()
        super().closeEvent(event)
