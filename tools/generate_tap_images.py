"""
Generate 4K JTAG TAP state machine diagram images.
Horizontal layout: TLR/RTI top-center, DR chain left, IR chain right.
Rendered at 2x scale for HiDPI clarity.

Run once:  python tools/generate_tap_images.py
Outputs:   assets/jtag_tap_dark.png, assets/jtag_tap_light.png
"""
from __future__ import annotations

import math
import os
import sys

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QImage, QPainter, QPen, QColor, QFont, QPolygonF, QPainterPath,
)
from PySide6.QtWidgets import QApplication

# ── 2x HiDPI scale factor ──
S = 2

# ── Logical dimensions (actual pixels = S * these) ──
# Wide balanced view — 1.5x height for spacious vertical fill
W, H = 1060, 440

# ── Box size ──
BOX_W, BOX_H = 140, 28

# ── Layout — balanced spacing for 560px height ──
ROW_GAP = 48
COL_LEFT = 215
COL_RIGHT = W - 215

# Center (slight left shift for asymmetric balance)
TOP_CX = W / 2 - 15
TOP_Y = 22   # minimal top margin — no route goes above TLR now
RTI_Y = TOP_Y + ROW_GAP

CHAIN_TOP = RTI_Y + ROW_GAP + 12

STATES = [
    ("Test-Logic-Reset", TOP_CX, TOP_Y),
    ("Run-Test/Idle",    TOP_CX, RTI_Y),
    ("Select-DR-Scan",   COL_LEFT, CHAIN_TOP),
    ("Capture-DR",       COL_LEFT, CHAIN_TOP + ROW_GAP),
    ("Shift-DR",         COL_LEFT, CHAIN_TOP + ROW_GAP * 2),
    ("Exit1-DR",         COL_LEFT, CHAIN_TOP + ROW_GAP * 3),
    ("Pause-DR",         COL_LEFT, CHAIN_TOP + ROW_GAP * 4),
    ("Exit2-DR",         COL_LEFT, CHAIN_TOP + ROW_GAP * 5),
    ("Update-DR",        COL_LEFT, CHAIN_TOP + ROW_GAP * 6),
    ("Select-IR-Scan",   COL_RIGHT, CHAIN_TOP),
    ("Capture-IR",       COL_RIGHT, CHAIN_TOP + ROW_GAP),
    ("Shift-IR",         COL_RIGHT, CHAIN_TOP + ROW_GAP * 2),
    ("Exit1-IR",         COL_RIGHT, CHAIN_TOP + ROW_GAP * 3),
    ("Pause-IR",         COL_RIGHT, CHAIN_TOP + ROW_GAP * 4),
    ("Exit2-IR",         COL_RIGHT, CHAIN_TOP + ROW_GAP * 5),
    ("Update-IR",        COL_RIGHT, CHAIN_TOP + ROW_GAP * 6),
]

POS = {n: (cx, cy) for n, cx, cy in STATES}

SHORT = {
    "Test-Logic-Reset": "Test-Logic-Reset",
    "Run-Test/Idle":    "Run-Test/Idle",
    "Select-DR-Scan":   "Select-DR-Scan",
    "Capture-DR":       "Capture-DR",
    "Shift-DR":         "Shift-DR",
    "Exit1-DR":         "Exit1-DR",
    "Pause-DR":         "Pause-DR",
    "Exit2-DR":         "Exit2-DR",
    "Update-DR":        "Update-DR",
    "Select-IR-Scan":   "Select-IR-Scan",
    "Capture-IR":       "Capture-IR",
    "Shift-IR":         "Shift-IR",
    "Exit1-IR":         "Exit1-IR",
    "Pause-IR":         "Pause-IR",
    "Exit2-IR":         "Exit2-IR",
    "Update-IR":        "Update-IR",
}

SELF_LOOPS = {
    "Test-Logic-Reset": 1, "Run-Test/Idle": 0,
    "Shift-DR": 0, "Shift-IR": 0,
    "Pause-DR": 0, "Pause-IR": 0,
}

TRANSITIONS = [
    ("Test-Logic-Reset", "Run-Test/Idle", 0),
    ("Run-Test/Idle", "Select-DR-Scan", 1),
    ("Select-DR-Scan", "Capture-DR", 0),
    ("Select-DR-Scan", "Select-IR-Scan", 1),
    ("Capture-DR", "Shift-DR", 0),
    ("Capture-DR", "Exit1-DR", 1),
    ("Shift-DR", "Exit1-DR", 1),
    ("Exit1-DR", "Pause-DR", 0),
    ("Exit1-DR", "Update-DR", 1),
    ("Pause-DR", "Exit2-DR", 1),
    ("Exit2-DR", "Shift-DR", 0),
    ("Exit2-DR", "Update-DR", 1),
    ("Update-DR", "Run-Test/Idle", 0),
    ("Update-DR", "Select-DR-Scan", 1),
    ("Select-IR-Scan", "Capture-IR", 0),
    ("Select-IR-Scan", "Test-Logic-Reset", 1),
    ("Capture-IR", "Shift-IR", 0),
    ("Capture-IR", "Exit1-IR", 1),
    ("Shift-IR", "Exit1-IR", 1),
    ("Exit1-IR", "Pause-IR", 0),
    ("Exit1-IR", "Update-IR", 1),
    ("Pause-IR", "Exit2-IR", 1),
    ("Exit2-IR", "Shift-IR", 0),
    ("Exit2-IR", "Update-IR", 1),
    ("Update-IR", "Run-Test/Idle", 0),
    ("Update-IR", "Select-DR-Scan", 1),
]


def rect_of(n: str) -> QRectF:
    cx, cy = POS[n]
    return QRectF(cx - BOX_W / 2, cy - BOX_H / 2, BOX_W, BOX_H)


def ctr(n: str) -> QPointF:
    cx, cy = POS[n]
    return QPointF(cx, cy)


def draw_arrow(p, src, tip, color, sz=11):
    """Filled triangle arrowhead."""
    dx, dy = tip.x() - src.x(), tip.y() - src.y()
    ln = math.hypot(dx, dy)
    if ln < 1:
        return
    ux, uy = dx / ln, dy / ln
    px, py = -uy, ux
    base = QPointF(tip.x() - ux * sz, tip.y() - uy * sz)
    l = QPointF(base.x() + px * sz * 0.42, base.y() + py * sz * 0.42)
    r = QPointF(base.x() - px * sz * 0.42, base.y() - py * sz * 0.42)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([tip, l, r]))


# ── Routing channels to prevent overlap ──

def route(src, dst):
    rs, rd = rect_of(src), rect_of(dst)
    cs, cd = ctr(src), ctr(dst)
    pair = (src, dst)

    # ── TLR → RTI (offset left to clear self-loop icons) ──
    if pair == ("Test-Logic-Reset", "Run-Test/Idle"):
        return [QPointF(cs.x() - 16, rs.bottom()),
                QPointF(cd.x() - 16, rd.top())]

    # ── RTI → Select-DR (L-route down-left, mid_y at 65% to clear RTI box) ──
    if pair == ("Run-Test/Idle", "Select-DR-Scan"):
        mid_y = rs.bottom() + (rd.top() - rs.bottom()) * 0.65
        return [QPointF(cs.x() - 14, rs.bottom()),
                QPointF(cs.x() - 14, mid_y),
                QPointF(cd.x(), mid_y),
                QPointF(cd.x(), rd.top())]

    # ── Select-DR → Select-IR (horizontal) ──
    if pair == ("Select-DR-Scan", "Select-IR-Scan"):
        return [QPointF(rs.right(), cs.y()), QPointF(rd.left(), cd.y())]

    # ── Adjacent down TMS=0 ──
    ADJ0 = {
        ("Select-DR-Scan", "Capture-DR"), ("Capture-DR", "Shift-DR"),
        ("Exit1-DR", "Pause-DR"),
        ("Select-IR-Scan", "Capture-IR"), ("Capture-IR", "Shift-IR"),
        ("Exit1-IR", "Pause-IR"),
    }
    if pair in ADJ0:
        return [QPointF(cs.x() - 10, rs.bottom()),
                QPointF(cd.x() - 10, rd.top())]

    # ── Adjacent down TMS=1 ──
    ADJ1 = {
        ("Shift-DR", "Exit1-DR"), ("Exit2-DR", "Update-DR"),
        ("Pause-DR", "Exit2-DR"),
        ("Shift-IR", "Exit1-IR"), ("Exit2-IR", "Update-IR"),
        ("Pause-IR", "Exit2-IR"),
    }
    if pair in ADJ1:
        return [QPointF(cs.x() + 10, rs.bottom()),
                QPointF(cd.x() + 10, rd.top())]

    # ── Capture → Exit1 (skip Shift, channel 1 right) ──
    if pair in {("Capture-DR", "Exit1-DR"), ("Capture-IR", "Exit1-IR")}:
        mx = rs.right() + 26
        return [QPointF(rs.right(), cs.y()),
                QPointF(mx, cs.y()), QPointF(mx, cd.y()),
                QPointF(rd.right(), cd.y())]

    # ── Exit1 → Update (skip Pause+Exit2, channel 2 right) ──
    if pair in {("Exit1-DR", "Update-DR"), ("Exit1-IR", "Update-IR")}:
        mx = rs.right() + 48
        return [QPointF(rs.right(), cs.y()),
                QPointF(mx, cs.y()), QPointF(mx, cd.y()),
                QPointF(rd.right(), cd.y())]

    # ── Exit2 → Shift (back up, channel 1 left) ──
    if pair in {("Exit2-DR", "Shift-DR"), ("Exit2-IR", "Shift-IR")}:
        mx = rs.left() - 30
        return [QPointF(rs.left(), cs.y()),
                QPointF(mx, cs.y()), QPointF(mx, cd.y()),
                QPointF(rd.left(), cd.y())]

    # ── Update-DR → Select-DR (loop up, channel 3 right) ──
    if pair == ("Update-DR", "Select-DR-Scan"):
        sel = rect_of("Select-DR-Scan")
        mx = max(rs.right(), sel.right()) + 66
        return [QPointF(rs.right(), cs.y()),
                QPointF(mx, cs.y()),
                QPointF(mx, ctr("Select-DR-Scan").y()),
                QPointF(sel.right(), ctr("Select-DR-Scan").y())]

    # ── Update-DR → RTI (channel 2 left) ──
    if pair == ("Update-DR", "Run-Test/Idle"):
        rti = rect_of("Run-Test/Idle")
        lx = rs.left() - 48
        return [QPointF(rs.left(), cs.y()),
                QPointF(lx, cs.y()),
                QPointF(lx, ctr("Run-Test/Idle").y()),
                QPointF(rti.left(), ctr("Run-Test/Idle").y())]

    # ── Update-IR → RTI (channel 2 right of IR, wide offset) ──
    if pair == ("Update-IR", "Run-Test/Idle"):
        rti = rect_of("Run-Test/Idle")
        rx = rs.right() + 66
        return [QPointF(rs.right(), cs.y()),
                QPointF(rx, cs.y()),
                QPointF(rx, ctr("Run-Test/Idle").y()),
                QPointF(rti.right(), ctr("Run-Test/Idle").y())]

    # ── Update-IR → Select-DR (cross-chain, top route above regions) ──
    if pair == ("Update-IR", "Select-DR-Scan"):
        sel_dr = rect_of("Select-DR-Scan")
        route_y = CHAIN_TOP - 34
        lx = rs.left() - 30
        return [QPointF(rs.left(), cs.y()),
                QPointF(lx, cs.y()),
                QPointF(lx, route_y),
                QPointF(sel_dr.right() + 66, route_y),
                QPointF(sel_dr.right() + 66, ctr("Select-DR-Scan").y()),
                QPointF(sel_dr.right(), ctr("Select-DR-Scan").y())]

    # ── Select-IR → TLR (right bypass → vertical up to TLR.y → horizontal to TLR) ──
    if pair == ("Select-IR-Scan", "Test-Logic-Reset"):
        tlr = rect_of("Test-Logic-Reset")
        rx = rs.right() + 24  # vertical channel right of IR column
        return [QPointF(rs.right(), cs.y()),
                QPointF(rx, cs.y()),
                QPointF(rx, ctr("Test-Logic-Reset").y()),
                QPointF(tlr.right(), ctr("Test-Logic-Reset").y())]

    return [QPointF(cs.x(), rs.bottom()), QPointF(cd.x(), rd.top())]


def _tms_label_pos(pts, src, dst):
    """Compute TMS label position with per-route offset tuning."""
    mi = len(pts) // 2
    if len(pts) > 2:
        ax, ay = pts[mi - 1].x(), pts[mi - 1].y()
        bx, by = pts[mi].x(), pts[mi].y()
    else:
        ax, ay = pts[0].x(), pts[0].y()
        bx, by = pts[1].x(), pts[1].y()
    mx, my = (ax + bx) / 2, (ay + by) / 2
    dx, dy = bx - ax, by - ay

    # Per-route adjustments to ensure no label is hidden behind a box
    pair = (src, dst)
    if pair == ("Test-Logic-Reset", "Run-Test/Idle"):
        return mx + 16, my  # push right of the vertical line
    if pair == ("Run-Test/Idle", "Select-DR-Scan"):
        # Place label on horizontal segment between corner and Select-DR
        return (pts[1].x() + pts[2].x()) / 2, pts[1].y() - 12
    if pair == ("Select-IR-Scan", "Test-Logic-Reset"):
        # Label on the vertical segment going up, halfway between IR and TLR
        rx = rect_of(src).right() + 24
        mid_y = (POS[src][1] + POS[dst][1]) / 2
        return rx + 12, mid_y
    if pair == ("Update-DR", "Select-DR-Scan"):
        # Push right to separate from Update-IR→Select-DR label
        return mx + 14, my
    if pair == ("Update-IR", "Select-DR-Scan"):
        # Place on the vertical segment left of IR column, well below RTI
        return pts[1].x() - 14, (pts[1].y() + pts[2].y()) / 2

    if math.hypot(dx, dy) < 1:
        return mx, my
    if abs(dx) > abs(dy):
        return mx, my - 11
    else:
        return mx - 11, my


def _draw_flat_box(p, rect, bdr_color, fill_color, text_c, text, font):
    """Draw clean flat pill box (no glow)."""
    p.setPen(QPen(bdr_color, 1.5))
    p.setBrush(fill_color)
    p.drawRoundedRect(rect, BOX_H / 2, BOX_H / 2)
    p.setPen(text_c)
    p.setFont(font)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


def generate(dark, out):
    if dark:
        # Industrial Engineering Dark palette — Keysight/Tek inspired
        bg       = QColor("#1E1E2E")
        box_fill = QColor("#2B2B3B")
        box_bdr  = QColor("#A9B7C6")
        text_c   = QColor("#E0E0E0")
        arrow_c  = QColor("#A9B7C6")     # Steel Silver lines
        tms_c    = QColor("#E0E0E0")     # Off-White TMS labels (calm)
        loop_c   = QColor("#E0E0E0")     # Off-White self-loop labels
        rgn_fill = QColor(74, 144, 226, 10)
        rgn_bdr  = QColor(74, 144, 226, 50)
        title_c  = QColor("#6A7080")
        rgn_title = QColor("#FFFFFF")
        label_bg = QColor(43, 43, 59, 200)
        tms_chip_bg  = QColor(30, 30, 46, 210)
        tms_chip_bdr = QColor("#4A90E2")  # Muted blue border (professional)
    else:
        bg       = QColor("#F4F5F7")
        box_fill = QColor("#E6E8EC")
        box_bdr  = QColor("#5A6270")
        text_c   = QColor("#1a1a2a")
        arrow_c  = QColor("#5A6270")
        tms_c    = QColor("#2A4060")
        loop_c   = QColor("#2A4060")
        rgn_fill = QColor(74, 144, 226, 8)
        rgn_bdr  = QColor(74, 144, 226, 30)
        title_c  = QColor("#8890A0")
        rgn_title = QColor("#2A3040")
        label_bg = QColor(230, 232, 236, 200)
        tms_chip_bg  = QColor(240, 242, 245, 210)
        tms_chip_bdr = QColor("#4A78A0")

    img = QImage(W * S, H * S, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(bg)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(S, S)  # All coordinates in logical units, rendered at 2x

    # ── Region boxes ──
    DR = {"Select-DR-Scan", "Capture-DR", "Shift-DR", "Exit1-DR",
          "Pause-DR", "Exit2-DR", "Update-DR"}
    IR = {"Select-IR-Scan", "Capture-IR", "Shift-IR", "Exit1-IR",
          "Pause-IR", "Exit2-IR", "Update-IR"}
    for states, label in [(DR, "DR Path"), (IR, "IR Path")]:
        x0 = y0 = 1e9
        x1 = y1 = -1e9
        for n in states:
            r = rect_of(n)
            x0, y0 = min(x0, r.left()), min(y0, r.top())
            x1, y1 = max(x1, r.right()), max(y1, r.bottom())
        pad = 18
        rr = QRectF(x0 - pad, y0 - pad - 18, x1 - x0 + pad * 2, y1 - y0 + pad * 2 + 18)
        p.setPen(QPen(rgn_bdr, 1.0))
        p.setBrush(rgn_fill)
        p.drawRoundedRect(rr, 4, 4)
        # Semi-transparent header badge for path label
        badge = QRectF(rr.left() + 8, rr.top() + 5, 150, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(label_bg)
        p.drawRoundedRect(badge, 5, 5)
        p.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        p.setPen(rgn_title)
        p.drawText(badge,
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "  " + label)

    # ── Transitions with TMS labels ──
    line_pen = QPen(arrow_c, 1.5)  # 1.5px Steel Silver — clean & visible
    tms_font = QFont("Segoe UI", 11, QFont.Weight.Bold)
    for src, dst, tms in TRANSITIONS:
        pts = route(src, dst)
        if len(pts) < 2:
            continue
        p.setPen(line_pen)
        for i in range(len(pts) - 1):
            p.drawLine(pts[i], pts[i + 1])
        draw_arrow(p, pts[-2], pts[-1], arrow_c)
        # TMS chip badge — muted blue border, calm Off-White text
        lx, ly = _tms_label_pos(pts, src, dst)
        chip = QRectF(lx - 9, ly - 9, 18, 18)
        p.setPen(QPen(tms_chip_bdr, 0.8))
        p.setBrush(tms_chip_bg)
        p.drawRoundedRect(chip, 3, 3)
        p.setFont(tms_font)
        p.setPen(tms_c)
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, str(tms))

    # ── State boxes (flat pill) — drawn AFTER lines so boxes are on top ──
    box_font = QFont("Segoe UI", 12, QFont.Weight.Bold)
    for name, cx, cy in STATES:
        rect = QRectF(cx - BOX_W / 2, cy - BOX_H / 2, BOX_W, BOX_H)
        _draw_flat_box(p, rect, box_bdr, box_fill, text_c, SHORT[name], box_font)

    # ── Self-loop badges (Off-White, right side of box) ──
    loop_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
    for name, tms in SELF_LOOPS.items():
        rect = rect_of(name)
        # Place self-loop chip to the right of the box, vertically centered
        lx = rect.right() + 6
        ly = rect.center().y()
        chip = QRectF(lx, ly - 8, 28, 16)
        p.setPen(QPen(tms_chip_bdr, 0.6))
        p.setBrush(tms_chip_bg)
        p.drawRoundedRect(chip, 3, 3)
        p.setFont(loop_font)
        p.setPen(loop_c)
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, f"\u21bb{tms}")

    # ── Footer ──
    p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
    p.setPen(title_c)
    p.drawText(QRectF(0, H - 18, W, 16),
               Qt.AlignmentFlag.AlignCenter,
               "IEEE 1149.1 TAP Controller State Machine")

    p.end()
    img.save(out)
    print(f"  {out}  ({img.width()}x{img.height()})")


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    base = os.path.join(os.path.dirname(__file__), "..", "assets")
    os.makedirs(base, exist_ok=True)
    print("Generating 4K TAP diagrams...")
    generate(True, os.path.join(base, "jtag_tap_dark.png"))
    generate(False, os.path.join(base, "jtag_tap_light.png"))
    print("Done.")


if __name__ == "__main__":
    main()
