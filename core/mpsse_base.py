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
    _MAX_INIT_RETRIES = 3

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
        queued = 0
        for _ in range(retries):
            try:
                queued = self._o._ft.getQueueStatus() if self._o._ft is not None else 0
            except Exception:
                queued = 0
            if queued >= length:
                break
            time.sleep(0.005)
        if queued < length:
            return b""
        try:
            return self.read(length)
        except Exception:
            return b""

    def init_mpsse(self) -> None:
        """Initialize the FTDI chip into MPSSE mode and synchronize it.

        Retries the full sequence (purge + reset + MPSSE enable + sync)
        up to ``_MAX_INIT_RETRIES`` times with progressive delays so that
        the USB subsystem has enough time to recover — especially on
        FT2232H with multiple open channel handles.

        Raises ``RuntimeError`` if synchronization fails on all attempts.
        """
        if self._o._ft is None:
            raise RuntimeError("FTDI handle is not open.")

        ft = self._o._ft

        for attempt in range(self._MAX_INIT_RETRIES):
            # Progressive delay: 50ms, 100ms, 150ms
            settle_ms = 0.05 * (attempt + 1)

            # 1. Purge stale data from previous mode
            try:
                ft.purge(self._PURGE_RXTX)
            except Exception:
                pass

            # 2. USB-level reset
            ft.resetDevice()
            time.sleep(settle_ms)

            # 3. Flush any residual RX data
            try:
                stale = ft.getQueueStatus()
                if stale > 0:
                    ft.read(stale)
            except Exception:
                pass

            # 4. Configure USB parameters
            ft.setUSBParameters(65536, 65535)
            ft.setChars(0, 0, 0, 0)  # disable event/error characters
            ft.setTimeouts(0, 5000)  # read=immediate return, write=5s
            ft.setLatencyTimer(1)  # 1ms latency

            # 5. Enable MPSSE mode
            ft.setBitMode(0x00, 0x02)  # 0x02 = MPSSE
            time.sleep(settle_ms)

            # 6. Verify MPSSE engine is responsive
            if self.sync_mpsse():
                # Final flush: ensure RX queue is clean before first transfer
                try:
                    leftover = ft.getQueueStatus()
                    if leftover > 0:
                        ft.read(leftover)
                except Exception:
                    pass
                if attempt > 0:
                    self._o._log(
                        f"[INFO] MPSSE init succeeded on attempt {attempt + 1}"
                    )
                return  # Success

            if attempt < self._MAX_INIT_RETRIES - 1:
                self._o._log(
                    f"[WARN] MPSSE init attempt {attempt + 1}/{self._MAX_INIT_RETRIES} "
                    f"failed, retrying (next settle={int(settle_ms * 2000)}ms)..."
                )

        raise RuntimeError(
            f"MPSSE sync failed after {self._MAX_INIT_RETRIES} full "
            f"init attempts — device may need power cycle"
        )

    def sync_mpsse(self) -> bool:
        """Send a bad opcode to verify the MPSSE engine is responsive.

        Returns ``True`` if the expected error response (``0xFA 0xAA``)
        was received, ``False`` otherwise.
        """
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
                    self._o._log("[INFO] MPSSE sync OK")
                    return True

                # Trim log to avoid huge spam
                hex_str = resp.hex(" ")
                if len(hex_str) > 200:
                    hex_str = hex_str[:200] + " ..."
                self._o._log(f"[WARN] MPSSE sync mismatch: {hex_str}")
            else:
                self._o._log("[WARN] MPSSE sync timeout (no response)")

        self._o._log("[ERROR] MPSSE sync FAILED after 3 attempts")
        # Extra purge to avoid stale data in further ops
        try:
            self._o._ft.purge(self._PURGE_RXTX)
        except Exception:
            pass
        return False
