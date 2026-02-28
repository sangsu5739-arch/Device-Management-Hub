"""
Bitbang controller for FTDI devices.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.ftdi_manager import FtdiManager


class BitbangController:
    """Bit-bang GPIO control for FTDI devices."""

    def __init__(self, owner: "FtdiManager") -> None:
        self._o = owner

    def enable(self, direction_mask: int = 0xFF) -> None:
        if self._o._ft is None:
            raise RuntimeError("FTDI handle is not open.")
        self._o._ft.setBitMode(direction_mask & 0xFF, 0x01)  # BITBANG
        time.sleep(0.01)

    def disable(self) -> None:
        if self._o._ft is None:
            return
        self._o._ft.setBitMode(0x00, 0x00)
        time.sleep(0.01)

    def read_pins(self) -> Optional[int]:
        if self._o._ft is None:
            return None
        return self._o._ft.getBitMode()
