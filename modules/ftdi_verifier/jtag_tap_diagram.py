"""
JTAG TAP State Diagram -- QGraphicsView scene-based widget.

IEEE 1149.1 TAP controller 16-state FSM.
Two-column layout (DR left, IR right) with shared TLR/RTI at top.
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QFont, QPolygonF, QPainterPath, QBrush,
)
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QGraphicsSimpleTextItem, QGraphicsPathItem, QGraphicsPolygonItem,
    QWidget,
)

from core.theme_manager import ThemeManager

# ── Layout constants (scene coordinates) ──

_BOX_W = 128
_BOX_H = 26
_ROW_GAP = 40
_COL_GAP = 170

# State positions: (col_x_center, row_index)
# col 0 = center (for TLR, RTI)
# col -1 = DR column, col +1 = IR column
_DR_COL_X = -_COL_GAP / 2
_IR_COL_X = _COL_GAP / 2

_STATE_LAYOUT: dict[str, tuple[float, float]] = {
    "Test-Logic-Reset":  (0, 0),
    "Run-Test/Idle":     (0, 1),
    "Select-DR-Scan":    (_DR_COL_X, 2),
    "Capture-DR":        (_DR_COL_X, 3),
    "Shift-DR":          (_DR_COL_X, 4),
    "Exit1-DR":          (_DR_COL_X, 5),
    "Pause-DR":          (_DR_COL_X, 6),
    "Exit2-DR":          (_DR_COL_X, 7),
    "Update-DR":         (_DR_COL_X, 8),
    "Select-IR-Scan":    (_IR_COL_X, 2),
    "Capture-IR":        (_IR_COL_X, 3),
    "Shift-IR":          (_IR_COL_X, 4),
    "Exit1-IR":          (_IR_COL_X, 5),
    "Pause-IR":          (_IR_COL_X, 6),
    "Exit2-IR":          (_IR_COL_X, 7),
    "Update-IR":         (_IR_COL_X, 8),
}

_DR_STATES = {"Select-DR-Scan", "Capture-DR", "Shift-DR", "Exit1-DR",
              "Pause-DR", "Exit2-DR", "Update-DR"}
_IR_STATES = {"Select-IR-Scan", "Capture-IR", "Shift-IR", "Exit1-IR",
              "Pause-IR", "Exit2-IR", "Update-IR"}

# Transitions: (from, to, tms_value)
_TRANSITIONS: list[tuple[str, str, int]] = [
    ("Test-Logic-Reset", "Test-Logic-Reset", 1),
    ("Test-Logic-Reset", "Run-Test/Idle", 0),
    ("Run-Test/Idle", "Run-Test/Idle", 0),
    ("Run-Test/Idle", "Select-DR-Scan", 1),
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


def _box_rect(name: str) -> QRectF:
    """Return the scene-space bounding rect for a state box."""
    cx, row = _STATE_LAYOUT[name]
    y = row * _ROW_GAP
    return QRectF(cx - _BOX_W / 2, y - _BOX_H / 2, _BOX_W, _BOX_H)


def _box_center(name: str) -> QPointF:
    cx, row = _STATE_LAYOUT[name]
    return QPointF(cx, row * _ROW_GAP)


class TapStateDiagram(QGraphicsView):
    """JTAG TAP 16-state FSM diagram using QGraphicsScene."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        self._scene = QGraphicsScene()
        super().__init__(self._scene, parent)
        self._current_state: str = "Test-Logic-Reset"
        self._box_items: dict[str, QGraphicsRectItem] = {}
        self._text_items: dict[str, QGraphicsSimpleTextItem] = {}
        self._arrow_items: list = []  # all transition graphics
        self._label_items: list = []  # TMS label items
        self._region_items: list[QGraphicsRectItem] = []

        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(self.Shape.NoFrame)
        self.setMinimumSize(300, 250)

        self._build_scene()
        self._apply_theme()

        tm = ThemeManager.instance()
        tm.theme_changed.connect(self._apply_theme)

    @property
    def current_state(self) -> str:
        return self._current_state

    def set_current_state(self, name: str) -> None:
        if name in _STATE_LAYOUT and name != self._current_state:
            self._current_state = name
            self._apply_theme()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        sr = self._scene.sceneRect()
        if not sr.isEmpty():
            self.fitInView(sr, Qt.AspectRatioMode.KeepAspectRatio)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        sr = self._scene.sceneRect()
        if not sr.isEmpty():
            self.fitInView(sr, Qt.AspectRatioMode.KeepAspectRatio)

    # ── Scene construction (called once) ──

    def _build_scene(self) -> None:
        s = self._scene

        # Region backgrounds
        self._build_regions(s)

        # Transitions (drawn first, behind boxes)
        for src, dst, tms in _TRANSITIONS:
            if src == dst:
                self._add_self_loop(s, src, tms)
            else:
                self._add_transition(s, src, dst, tms)

        # State boxes (drawn last = on top)
        box_font = QFont("Segoe UI", 8)
        for name in _STATE_LAYOUT:
            rect = _box_rect(name)
            box = QGraphicsRectItem(rect)
            box.setZValue(10)
            s.addItem(box)
            self._box_items[name] = box

            # Short display label
            label = name.replace("Test-Logic-Reset", "TLR") \
                        .replace("Run-Test/Idle", "RTI") \
                        .replace("Select-DR-Scan", "Sel-DR") \
                        .replace("Select-IR-Scan", "Sel-IR") \
                        .replace("Capture-", "Cap-") \
                        .replace("Shift-", "Shft-") \
                        .replace("Exit1-", "Ex1-") \
                        .replace("Exit2-", "Ex2-") \
                        .replace("Pause-", "Pau-") \
                        .replace("Update-", "Upd-")
            txt = QGraphicsSimpleTextItem(label)
            txt.setFont(box_font)
            txt.setZValue(11)
            # Center text in box
            tb = txt.boundingRect()
            txt.setPos(rect.center().x() - tb.width() / 2,
                       rect.center().y() - tb.height() / 2)
            s.addItem(txt)
            self._text_items[name] = txt

        # Set scene rect with margin
        sr = s.itemsBoundingRect().adjusted(-20, -20, 20, 20)
        s.setSceneRect(sr)

    def _build_regions(self, s: QGraphicsScene) -> None:
        """Add subtle background rectangles for DR and IR regions."""
        for states in (_DR_STATES, _IR_STATES):
            x0 = y0 = 1e9
            x1 = y1 = -1e9
            for name in states:
                r = _box_rect(name)
                x0 = min(x0, r.left())
                y0 = min(y0, r.top())
                x1 = max(x1, r.right())
                y1 = max(y1, r.bottom())
            pad = 14
            region = QGraphicsRectItem(
                QRectF(x0 - pad, y0 - pad, x1 - x0 + pad * 2, y1 - y0 + pad * 2)
            )
            region.setZValue(-1)
            s.addItem(region)
            self._region_items.append(region)

    # ── Transitions ──

    def _add_transition(self, s: QGraphicsScene, src: str, dst: str,
                        tms: int) -> None:
        rs = _box_rect(src)
        rd = _box_rect(dst)
        cs = _box_center(src)
        cd = _box_center(dst)
        pair = (src, dst)

        # Determine routing
        points = self._route_points(pair, rs, rd, cs, cd)
        if points is None:
            # Fallback: straight line
            points = [QPointF(cs.x(), rs.bottom()),
                      QPointF(cd.x(), rd.top())]

        # Draw polyline path
        if len(points) >= 2:
            path = QPainterPath(points[0])
            for pt in points[1:]:
                path.lineTo(pt)
            item = QGraphicsPathItem(path)
            item.setZValue(1)
            s.addItem(item)
            self._arrow_items.append(item)

            # Arrowhead at last segment
            self._add_arrowhead(s, points[-2], points[-1])

            # TMS label at midpoint of path
            mid_idx = len(points) // 2
            if len(points) > 2:
                lx = (points[mid_idx - 1].x() + points[mid_idx].x()) / 2
                ly = (points[mid_idx - 1].y() + points[mid_idx].y()) / 2
            else:
                lx = (points[0].x() + points[1].x()) / 2
                ly = (points[0].y() + points[1].y()) / 2
            self._add_tms_label(s, lx, ly, tms)

    def _route_points(self, pair: tuple, rs: QRectF, rd: QRectF,
                      cs: QPointF, cd: QPointF) -> Optional[list[QPointF]]:
        src, dst = pair

        # Adjacent vertical (same column, consecutive rows)
        _ADJ = {
            ("Test-Logic-Reset", "Run-Test/Idle"),
            ("Select-DR-Scan", "Capture-DR"), ("Capture-DR", "Shift-DR"),
            ("Shift-DR", "Exit1-DR"), ("Exit1-DR", "Pause-DR"),
            ("Pause-DR", "Exit2-DR"),
            ("Select-IR-Scan", "Capture-IR"), ("Capture-IR", "Shift-IR"),
            ("Shift-IR", "Exit1-IR"), ("Exit1-IR", "Pause-IR"),
            ("Pause-IR", "Exit2-IR"),
        }
        if pair in _ADJ:
            ox = -8
            return [QPointF(cs.x() + ox, rs.bottom()),
                    QPointF(cd.x() + ox, rd.top())]

        # Adjacent vertical TMS=1 (right side offset for Exit2→Update)
        _ADJ1 = {
            ("Exit2-DR", "Update-DR"), ("Exit2-IR", "Update-IR"),
        }
        if pair in _ADJ1:
            ox = 8
            return [QPointF(cs.x() + ox, rs.bottom()),
                    QPointF(cd.x() + ox, rd.top())]

        # Skip vertical: Capture→Exit1 (TMS=1, bypass Shift via right)
        if pair in {("Capture-DR", "Exit1-DR"), ("Capture-IR", "Exit1-IR")}:
            off = _BOX_W / 2 + 20
            sp = QPointF(rs.right(), cs.y())
            mx = rs.right() + off
            ep = QPointF(rd.right(), cd.y())
            return [sp, QPointF(mx, sp.y()), QPointF(mx, ep.y()), ep]

        # Skip: Exit1→Update (TMS=1, bypass Pause+Exit2 via right)
        if pair in {("Exit1-DR", "Update-DR"), ("Exit1-IR", "Update-IR")}:
            off = _BOX_W / 2 + 35
            sp = QPointF(rs.right(), cs.y())
            mx = rs.right() + off
            ep = QPointF(rd.right(), cd.y())
            return [sp, QPointF(mx, sp.y()), QPointF(mx, ep.y()), ep]

        # Loop-back up: Exit2→Shift (TMS=0, left side)
        if pair in {("Exit2-DR", "Shift-DR"), ("Exit2-IR", "Shift-IR")}:
            off = _BOX_W / 2 + 20
            sp = QPointF(rs.left(), cs.y())
            mx = rs.left() - off
            ep = QPointF(rd.left(), cd.y())
            return [sp, QPointF(mx, sp.y()), QPointF(mx, ep.y()), ep]

        # Loop-back up: Update→Select (TMS=1, right side)
        if pair in {("Update-DR", "Select-DR-Scan"),
                    ("Update-IR", "Select-DR-Scan")}:
            off = _BOX_W / 2 + 50
            sp = QPointF(rs.right(), cs.y())
            sel_rect = _box_rect("Select-DR-Scan")
            ep = QPointF(sel_rect.right(), _box_center("Select-DR-Scan").y())
            mx = max(sp.x(), ep.x()) + off
            return [sp, QPointF(mx, sp.y()), QPointF(mx, ep.y()), ep]

        # Horizontal: RTI→Select-DR
        if pair == ("Run-Test/Idle", "Select-DR-Scan"):
            sp = QPointF(cs.x() - 10, rs.bottom())
            ep = QPointF(cd.x(), rd.top())
            return [sp, ep]

        # Horizontal: Select-DR→Select-IR
        if pair == ("Select-DR-Scan", "Select-IR-Scan"):
            sp = QPointF(rs.right(), cs.y())
            ep = QPointF(rd.left(), cd.y())
            return [sp, ep]

        # Update-DR→RTI (TMS=0, route down-left-up)
        if pair == ("Update-DR", "Run-Test/Idle"):
            rti_rect = _box_rect("Run-Test/Idle")
            sp = QPointF(rs.left(), cs.y())
            bot_y = cs.y() + 18
            lx = rti_rect.left() - 40
            ep = QPointF(rti_rect.left(), _box_center("Run-Test/Idle").y())
            return [sp, QPointF(lx, sp.y()), QPointF(lx, ep.y()), ep]

        # Update-IR→RTI (TMS=0, route down-left-up, wider)
        if pair == ("Update-IR", "Run-Test/Idle"):
            rti_rect = _box_rect("Run-Test/Idle")
            sp = QPointF(rs.left(), cs.y())
            lx = rti_rect.left() - 55
            ep = QPointF(rti_rect.left(), _box_center("Run-Test/Idle").y())
            return [sp, QPointF(lx, sp.y()), QPointF(lx, ep.y()), ep]

        # Select-IR→TLR (TMS=1, route up and across top)
        if pair == ("Select-IR-Scan", "Test-Logic-Reset"):
            tlr_rect = _box_rect("Test-Logic-Reset")
            sp = QPointF(cs.x(), rs.top())
            top_y = tlr_rect.top() - 18
            rx = tlr_rect.right() + 15
            ep = QPointF(tlr_rect.right(), _box_center("Test-Logic-Reset").y())
            return [sp, QPointF(sp.x(), top_y),
                    QPointF(rx, top_y), QPointF(rx, ep.y()), ep]

        return None

    def _add_arrowhead(self, s: QGraphicsScene,
                       src: QPointF, tip: QPointF, sz: float = 5) -> None:
        dx = tip.x() - src.x()
        dy = tip.y() - src.y()
        length = math.hypot(dx, dy)
        if length < 1:
            return
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        base = QPointF(tip.x() - ux * sz, tip.y() - uy * sz)
        left = QPointF(base.x() + px * sz * 0.5, base.y() + py * sz * 0.5)
        right = QPointF(base.x() - px * sz * 0.5, base.y() - py * sz * 0.5)
        arrow = QGraphicsPolygonItem(QPolygonF([tip, left, right]))
        arrow.setZValue(2)
        s.addItem(arrow)
        self._arrow_items.append(arrow)

    def _add_tms_label(self, s: QGraphicsScene,
                       x: float, y: float, tms: int) -> None:
        font = QFont("Consolas", 7, QFont.Weight.Bold)
        txt = QGraphicsSimpleTextItem(str(tms))
        txt.setFont(font)
        txt.setZValue(5)
        tb = txt.boundingRect()
        txt.setPos(x - tb.width() / 2 + 6, y - tb.height() / 2)
        s.addItem(txt)
        self._label_items.append(txt)

    def _add_self_loop(self, s: QGraphicsScene, state: str,
                       tms: int) -> None:
        rect = _box_rect(state)
        r = 10

        if state == "Run-Test/Idle":
            # Loop on left side
            cx = rect.left() - r
            cy = rect.center().y()
            path = QPainterPath()
            arc_rect = QRectF(cx - r, cy - r, r * 2, r * 2)
            path.arcMoveTo(arc_rect, 60)
            path.arcTo(arc_rect, 60, 240)
            item = QGraphicsPathItem(path)
            item.setZValue(1)
            s.addItem(item)
            self._arrow_items.append(item)
            self._add_tms_label(s, cx - r - 4, cy, tms)
        else:
            # Loop on top
            cx = rect.center().x()
            cy = rect.top() - r
            path = QPainterPath()
            arc_rect = QRectF(cx - r, cy - r, r * 2, r * 2)
            path.arcMoveTo(arc_rect, -30)
            path.arcTo(arc_rect, -30, 240)
            item = QGraphicsPathItem(path)
            item.setZValue(1)
            s.addItem(item)
            self._arrow_items.append(item)
            self._add_tms_label(s, cx, cy - r - 2, tms)

    # ── Theme application ──

    def _apply_theme(self) -> None:
        tm = ThemeManager.instance()
        dark = tm.is_dark

        # Background
        bg = QColor(tm.color("jtag_tap_bg"))
        self.setStyleSheet(f"background: {bg.name()};")
        self._scene.setBackgroundBrush(QBrush(bg))

        # State box colors
        inactive_fill = QColor(tm.color("jtag_tap_state"))
        active_fill = QColor(tm.color("jtag_tap_state_active"))
        text_color = QColor(tm.color("jtag_tap_text"))
        border_color = QColor(tm.color("jtag_tap_arrow"))
        arrow_color = QColor(tm.color("jtag_tap_arrow"))

        for name, box in self._box_items.items():
            is_active = name == self._current_state
            fill = active_fill if is_active else inactive_fill
            bdr = QColor("#ffffff") if is_active else border_color
            box.setBrush(QBrush(fill))
            box.setPen(QPen(bdr, 2.0 if is_active else 1.0))

        for name, txt in self._text_items.items():
            is_active = name == self._current_state
            tc = QColor("#ffffff") if is_active else text_color
            txt.setBrush(QBrush(tc))

        # Arrow/line colors
        arrow_pen = QPen(arrow_color, 1.2)
        for item in self._arrow_items:
            if isinstance(item, QGraphicsPolygonItem):
                item.setBrush(QBrush(arrow_color))
                item.setPen(QPen(arrow_color, 0.5))
            else:
                item.setPen(arrow_pen)

        # TMS label colors
        for item in self._label_items:
            item.setBrush(QBrush(text_color))

        # Region backgrounds
        if len(self._region_items) >= 2:
            dr_fill = QColor(30, 80, 30, 40) if dark else QColor(180, 220, 180, 50)
            dr_border = QColor(60, 100, 60, 60) if dark else QColor(120, 170, 120, 80)
            ir_fill = QColor(30, 30, 80, 40) if dark else QColor(180, 190, 230, 50)
            ir_border = QColor(60, 60, 120, 60) if dark else QColor(110, 120, 180, 80)
            self._region_items[0].setBrush(QBrush(dr_fill))
            self._region_items[0].setPen(QPen(dr_border, 1.0))
            self._region_items[1].setBrush(QBrush(ir_fill))
            self._region_items[1].setPen(QPen(ir_border, 1.0))

        self.viewport().update()
