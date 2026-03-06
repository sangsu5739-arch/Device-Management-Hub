"""
JTAG TAP State Diagram \u2014 QPainter custom widget.

IEEE 1149.1 TAP controller 16-state FSM \uc744
\uc815\uc801 \ub2e4\uc774\uc5b4\uadf8\ub7a8\uc73c\ub85c \uc2dc\uac01\ud654\ud55c\ub2e4.
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath, QPolygonF
from PySide6.QtWidgets import QWidget

from core.theme_manager import ThemeManager

# ── TAP \uc0c1\ud0dc \uc815\uc758 ────────────────────────────────────────────

_TAP_STATES: list[str] = [
    "Test-Logic-Reset",
    "Run-Test/Idle",
    "Select-DR-Scan",
    "Capture-DR",
    "Shift-DR",
    "Exit1-DR",
    "Pause-DR",
    "Exit2-DR",
    "Update-DR",
    "Select-IR-Scan",
    "Capture-IR",
    "Shift-IR",
    "Exit1-IR",
    "Pause-IR",
    "Exit2-IR",
    "Update-IR",
]

# \uc815\uaddc\ud654 \uc88c\ud45c (0\u20131 \ubc94\uc704).  paintEvent \uc5d0\uc11c \uc2e4\uc81c \ud53d\uc140\ub85c \uc2a4\ucf00\uc77c\ub9c1.
# \ub808\uc774\uc544\uc6c3: \uc88c\uce21 DR \uacbd\ub85c / \uc6b0\uce21 IR \uacbd\ub85c / \uc0c1\ub2e8 \uacf5\uc6a9
_STATE_POS: dict[str, tuple[float, float]] = {
    "Test-Logic-Reset":  (0.50, 0.04),
    "Run-Test/Idle":     (0.50, 0.16),
    # DR path (\uc88c\uce21)
    "Select-DR-Scan":    (0.25, 0.16),
    "Capture-DR":        (0.25, 0.28),
    "Shift-DR":          (0.12, 0.40),
    "Exit1-DR":          (0.25, 0.40),
    "Pause-DR":          (0.12, 0.52),
    "Exit2-DR":          (0.25, 0.52),
    "Update-DR":         (0.25, 0.64),
    # IR path (\uc6b0\uce21)
    "Select-IR-Scan":    (0.75, 0.16),
    "Capture-IR":        (0.75, 0.28),
    "Shift-IR":          (0.88, 0.40),
    "Exit1-IR":          (0.75, 0.40),
    "Pause-IR":          (0.88, 0.52),
    "Exit2-IR":          (0.75, 0.52),
    "Update-IR":         (0.75, 0.64),
}

# \uc804\uc774 \uc815\uc758: (from, to, tms_value)
_TRANSITIONS: list[tuple[str, str, int]] = [
    # TLR
    ("Test-Logic-Reset", "Test-Logic-Reset", 1),
    ("Test-Logic-Reset", "Run-Test/Idle", 0),
    # RTI
    ("Run-Test/Idle", "Run-Test/Idle", 0),
    ("Run-Test/Idle", "Select-DR-Scan", 1),
    # DR path
    ("Select-DR-Scan", "Capture-DR", 0),
    ("Select-DR-Scan", "Select-IR-Scan", 1),
    ("Capture-DR", "Shift-DR", 0),
    ("Capture-DR", "Exit1-DR", 1),
    ("Shift-DR", "Shift-DR", 0),
    ("Shift-DR", "Exit1-DR", 1),
    ("Exit1-DR", "Pause-DR", 0),
    ("Exit1-DR", "Update-DR", 1),
    ("Pause-DR", "Pause-DR", 0),
    ("Pause-DR", "Exit2-DR", 1),
    ("Exit2-DR", "Shift-DR", 0),
    ("Exit2-DR", "Update-DR", 1),
    ("Update-DR", "Run-Test/Idle", 0),
    ("Update-DR", "Select-DR-Scan", 1),
    # IR path
    ("Select-IR-Scan", "Capture-IR", 0),
    ("Select-IR-Scan", "Test-Logic-Reset", 1),
    ("Capture-IR", "Shift-IR", 0),
    ("Capture-IR", "Exit1-IR", 1),
    ("Shift-IR", "Shift-IR", 0),
    ("Shift-IR", "Exit1-IR", 1),
    ("Exit1-IR", "Pause-IR", 0),
    ("Exit1-IR", "Update-IR", 1),
    ("Pause-IR", "Pause-IR", 0),
    ("Pause-IR", "Exit2-IR", 1),
    ("Exit2-IR", "Shift-IR", 0),
    ("Exit2-IR", "Update-IR", 1),
    ("Update-IR", "Run-Test/Idle", 0),
    ("Update-IR", "Select-DR-Scan", 1),
]


class TapStateDiagram(QWidget):
    """JTAG TAP 16-state FSM \uc815\uc801 \ub2e4\uc774\uc5b4\uadf8\ub7a8."""

    _BOX_W = 0.12   # \uc815\uaddc\ud654 \ub108\ube44
    _BOX_H = 0.08   # \uc815\uaddc\ud654 \ub192\uc774

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_state: str = "Test-Logic-Reset"
        self.setMinimumSize(400, 300)

        tm = ThemeManager.instance()
        tm.theme_changed.connect(self.update)

    # ── public API ──

    @property
    def current_state(self) -> str:
        return self._current_state

    def set_current_state(self, name: str) -> None:
        if name in _STATE_POS:
            self._current_state = name
            self.update()

    # ── painting ──

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        tm = ThemeManager.instance()
        w, h = self.width(), self.height()

        # \ubc30\uacbd
        bg = QColor(tm.color("jtag_tap_bg"))
        p.fillRect(self.rect(), bg)

        # \ub9c8\uc9c4 \uc801\uc6a9 (box \ud06c\uae30 \uacc4\uc0b0\uc5d0 \uc0ac\uc6a9)
        margin_x, margin_y = 10, 10
        draw_w = w - margin_x * 2
        draw_h = h - margin_y * 2

        box_w = draw_w * self._BOX_W
        box_h = draw_h * self._BOX_H

        def state_rect(name: str) -> QRectF:
            nx, ny = _STATE_POS[name]
            cx = margin_x + nx * draw_w
            cy = margin_y + ny * draw_h
            return QRectF(cx - box_w / 2, cy - box_h / 2, box_w, box_h)

        def state_center(name: str) -> QPointF:
            r = state_rect(name)
            return r.center()

        # ── \ud654\uc0b4\ud45c \uadf8\ub9ac\uae30 ──
        arrow_color = QColor(tm.color("jtag_tap_arrow"))
        arrow_pen = QPen(arrow_color, 1.2)
        label_font = QFont("Consolas", 7)
        label_color = QColor(tm.color("jtag_tap_arrow"))

        for src, dst, tms in _TRANSITIONS:
            if src == dst:
                # self-loop: \uc791\uc740 \uc6d0\ud615 \ud654\uc0b4\ud45c
                self._draw_self_loop(p, state_rect(src), tms, arrow_pen,
                                     label_font, label_color)
                continue

            c_src = state_center(src)
            c_dst = state_center(dst)
            r_dst = state_rect(dst)

            # \ub3c4\ucc29\uc810\uc744 \uc0c1\uc790 \uac00\uc7a5\uc790\ub9ac\ub85c \ud074\ub9ac\ud551
            end_pt = self._clip_to_rect(c_src, c_dst, r_dst)

            p.setPen(arrow_pen)
            p.drawLine(c_src, end_pt)
            self._draw_arrowhead(p, c_src, end_pt, arrow_pen, size=6)

            # TMS \ub77c\ubca8
            mid = QPointF((c_src.x() + end_pt.x()) / 2,
                          (c_src.y() + end_pt.y()) / 2)
            p.setFont(label_font)
            p.setPen(label_color)
            p.drawText(QRectF(mid.x() - 8, mid.y() - 6, 16, 12),
                       Qt.AlignmentFlag.AlignCenter, str(tms))

        # ── \uc0c1\ud0dc \ubc15\uc2a4 \uadf8\ub9ac\uae30 ──
        state_fill = QColor(tm.color("jtag_tap_state"))
        active_fill = QColor(tm.color("jtag_tap_state_active"))
        text_color = QColor(tm.color("jtag_tap_text"))
        border_color = QColor(tm.color("jtag_tap_arrow"))

        box_font = QFont("Segoe UI", 7, QFont.Weight.Bold)
        p.setFont(box_font)

        for name in _TAP_STATES:
            rect = state_rect(name)
            is_active = (name == self._current_state)

            fill = active_fill if is_active else state_fill
            pen = QPen(active_fill if is_active else border_color, 1.5 if is_active else 1.0)

            p.setPen(pen)
            p.setBrush(fill)
            p.drawRoundedRect(rect, 4, 4)

            # \ud14d\uc2a4\ud2b8 (\uc904\ubc14\uafc8 \ucc98\ub9ac)
            tc = QColor("#ffffff") if is_active else text_color
            p.setPen(tc)
            # \uae34 \uc774\ub984 \uc904\ubc14\uafc8
            label = name.replace("-", "\u2011")  # non-breaking hyphen
            if "/" in label:
                label = label.replace("/", "\n")
            p.drawText(rect.adjusted(2, 1, -2, -1),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       label)

        p.end()

    # ── \ubcf4\uc870 \ud568\uc218 ──

    @staticmethod
    def _clip_to_rect(src: QPointF, dst: QPointF, rect: QRectF) -> QPointF:
        """dst \uc911\uc2ec\uc774 rect \uc548\uc5d0 \uc788\uc744 \ub54c, src\u2192dst \uc120\ubd84\uc774 rect \uac00\uc7a5\uc790\ub9ac\uc640 \ub9cc\ub098\ub294 \uc810."""
        cx, cy = rect.center().x(), rect.center().y()
        dx = src.x() - cx
        dy = src.y() - cy
        if abs(dx) < 0.001 and abs(dy) < 0.001:
            return QPointF(cx, cy)
        hw, hh = rect.width() / 2, rect.height() / 2
        # x-edge
        if abs(dx) > 0.001:
            tx = hw / abs(dx)
        else:
            tx = 1e9
        if abs(dy) > 0.001:
            ty = hh / abs(dy)
        else:
            ty = 1e9
        t = min(tx, ty)
        return QPointF(cx + dx * t, cy + dy * t)

    @staticmethod
    def _draw_arrowhead(p: QPainter, src: QPointF, tip: QPointF,
                        pen: QPen, size: float = 8) -> None:
        dx = tip.x() - src.x()
        dy = tip.y() - src.y()
        length = math.hypot(dx, dy)
        if length < 1:
            return
        ux, uy = dx / length, dy / length
        # \uc218\uc9c1 \ubca1\ud130
        px, py = -uy, ux
        base = QPointF(tip.x() - ux * size, tip.y() - uy * size)
        left = QPointF(base.x() + px * size * 0.4, base.y() + py * size * 0.4)
        right = QPointF(base.x() - px * size * 0.4, base.y() - py * size * 0.4)
        tri = QPolygonF([tip, left, right])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(pen.color())
        p.drawPolygon(tri)

    @staticmethod
    def _draw_self_loop(p: QPainter, rect: QRectF, tms: int,
                        pen: QPen, font: QFont, label_color: QColor) -> None:
        """self-loop \ud654\uc0b4\ud45c \u2014 \uc0c1\uc790 \uc704\ucabd\uc5d0 \uc791\uc740 \uc6d0\ud615 arc."""
        cx = rect.center().x()
        top = rect.top()
        loop_r = min(rect.width(), rect.height()) * 0.28
        arc_rect = QRectF(cx - loop_r, top - loop_r * 1.8, loop_r * 2, loop_r * 2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(arc_rect, 30 * 16, 120 * 16)
        # TMS \ub77c\ubca8
        p.setFont(font)
        p.setPen(label_color)
        p.drawText(QRectF(cx - 8, top - loop_r * 2.0, 16, 12),
                   Qt.AlignmentFlag.AlignCenter, str(tms))
