"""
JTAG TAP State Diagram -- Static image viewer.

Displays pre-rendered TAP state machine diagram image.
Switches dark/light based on theme. Fills width via setScaledContents.
"""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QWidget, QSizePolicy

from core.theme_manager import ThemeManager

_ASSETS = os.path.join(os.path.dirname(__file__), "..", "..", "assets")
_DARK = os.path.join(_ASSETS, "jtag_tap_dark.png")
_LIGHT = os.path.join(_ASSETS, "jtag_tap_light.png")


class TapStateDiagram(QLabel):
    """Static TAP state machine image viewer."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_state: str = "Test-Logic-Reset"
        self._dark_pix: Optional[QPixmap] = None
        self._light_pix: Optional[QPixmap] = None

        self.setScaledContents(True)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumHeight(300)

        self._load()
        self._apply_theme()
        ThemeManager.instance().theme_changed.connect(self._apply_theme)

    def _load(self) -> None:
        if os.path.isfile(_DARK):
            self._dark_pix = QPixmap(_DARK)
        if os.path.isfile(_LIGHT):
            self._light_pix = QPixmap(_LIGHT)

    @property
    def current_state(self) -> str:
        return self._current_state

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        # Keep around 21:9 to reduce horizontal distortion.
        return max(80, int(w * 9 / 21))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(1050, 610)

    def set_current_state(self, name: str) -> None:
        self._current_state = name

    def _apply_theme(self) -> None:
        tm = ThemeManager.instance()
        pix = self._dark_pix if tm.is_dark else self._light_pix
        if pix and not pix.isNull():
            self.setPixmap(pix)
