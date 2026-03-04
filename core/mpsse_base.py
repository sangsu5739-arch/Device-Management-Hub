"""
Base MPSSE controller for FTDI devices.
Provides common initialization, synchronization, and low-level I/O operations
shared by both I2C and SPI controllers.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.ftdi_manager import FtdiManager


class MpsseBaseController:
    """Base class for MPSSE-based protocol controllers."""

    _PURGE_RXTX = 3

    # Common MPSSE opcodes shared across protocols
    _MPSSE_SET_BITS_LOW = 0x80
    _MPSSE_SET_BITS_HIGH = 0x82
    _MPSSE_READ_BITS_LOW = 0x81
    _MPSSE_READ_BITS_HIGH = 0x83
    _MPSSE_SEND_IMMEDIATE = 0x87
    _MPSSE_DIV_BY_5_DISABLE = 0x8A
    _MPSSE_DIV_BY_5_ENABLE = 0x8B
    _MPSSE_ENABLE_ADAPTIVE = 0x96
    _MPSSE_DISABLE_ADAPTIVE = 0x97
    _MPSSE_ENABLE_3_PHASE = 0x8C
    _MPSSE_DISABLE_3_PHASE = 0x8D
    _MPSSE_DISABLE_LOOPBACK = 0x85
    _MPSSE_CLOCK_DIVISOR = 0x86

    # Common ADBUS pins
    PIN_ADBUS0 = 1 << 0
    PIN_ADBUS1 = 1 << 1
    PIN_ADBUS2 = 1 << 2
    PIN_ADBUS3 = 1 << 3
    PIN_ADBUS4 = 1 << 4
    PIN_ADBUS5 = 1 << 5
    PIN_ADBUS6 = 1 << 6
    PIN_ADBUS7 = 1 << 7

    def __init__(self, owner: "FtdiManager") -> None:
        self._o = owner

    def write(self, data: bytes) -> None:
        """Write raw bytes to the FTDI device."""
        if self._o._ft is None:
            raise RuntimeError("FTDI handle is not open.")
        self._o._ft.write(data)

    def read(self, length: int) -> bytes:
        """Read exact number of bytes from the FTDI device."""
        if self._o._ft is None:
            raise RuntimeError("FTDI handle is not open.")
        data = self._o._ft.read(length)
        return bytes(data) if data else b""

    def read_with_wait(self, length: int, retries: int = 5) -> bytes:
        """Wait for expected bytes to land in the RX queue, then read."""
        if length <= 0:
            return b""
        for _ in range(retries):
            try:
                queued = self._o._ft.getQueueStatus() if self._o._ft is not None else 0
            except Exception:
                queued = 0
            if queued >= length:
                break
            time.sleep(0.005)
        try:
            return self.read(length)
        except Exception:
            return b""

    def init_mpsse(self) -> None:
        """Initialize the FTDI chip into MPSSE mode and synchronize it."""
        if self._o._ft is None:
            raise RuntimeError("FTDI handle is not open.")

        ft = self._o._ft
        ft.resetDevice()
        time.sleep(0.02)
        ft.purge(self._PURGE_RXTX)
        ft.setUSBParameters(65536, 65536)
        ft.setLatencyTimer(2)
        ft.setTimeouts(3000, 3000)

        # Reset bitmode then set to MPSSE mode
        ft.setBitMode(0x00, 0x00)
        time.sleep(0.02)
        ft.setBitMode(0x00, 0x02)  # 0x02 = MPSSE
        time.sleep(0.02)
        ft.purge(self._PURGE_RXTX)

        self.sync_mpsse()

    def sync_mpsse(self) -> None:
        """Send a bad opcode to verify the MPSSE engine is responsive."""
        synced = False
        for _ in range(3):
            self.write(b"\xAA")  # 0xAA is an invalid opcode
            time.sleep(0.02)
            try:
                rxn = self._o._ft.getQueueStatus() if self._o._ft is not None else 0
            except Exception:
                rxn = 0

            if rxn > 0:
                resp = self.read(rxn)
                if b"\xFA\xAA" in resp:  # 0xFA = Bad Command, 0xAA = the command
                    synced = True
                    break
                
                # Trim log to avoid huge spam
                hex_str = resp.hex(" ")
                if len(hex_str) > 200:
                    hex_str = hex_str[:200] + " ..."
                self._o._log(f"[WARN] MPSSE sync mismatch: {hex_str}")
            else:
                self._o._log("[WARN] MPSSE sync timeout (no response)")
        
        if not synced:
            # Extra purge to avoid stale data in further ops
            try:
                self._o._ft.purge(self._PURGE_RXTX)
            except Exception:
                pass
