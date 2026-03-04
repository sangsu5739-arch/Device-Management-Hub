"""
FTDI Verifier Worker - async GPIO polling, I2C/SPI protocol tests

Runs in QThread to avoid blocking the UI thread.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal, QMutex, QMutexLocker

from core.ftdi_manager import FtdiManager


@dataclass
class GpioState:
    """GPIO pin state snapshot."""
    timestamp: float = 0.0
    pin_states: Dict[int, bool] = field(default_factory=dict)


@dataclass
class I2CScanResult:
    """I2C scan result."""
    timestamp: float = 0.0
    found_addresses: List[int] = field(default_factory=list)
    total_scanned: int = 0


@dataclass
class ProtocolTestResult:
    """Protocol test result."""
    timestamp: float = 0.0
    protocol: str = ""
    success: bool = False
    message: str = ""
    raw_data: bytes = b""


class VerifierWorker(QObject):
    """Async hardware verification worker.

    Signals:
        gpio_updated(object): GpioState received
        i2c_scan_done(object): I2CScanResult received
        protocol_test_done(object): ProtocolTestResult received
        log_message(str): log message
        error_occurred(str): error message
    """

    gpio_updated = Signal(object)
    i2c_scan_done = Signal(object)
    protocol_test_done = Signal(object)
    log_message = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, ftdi: FtdiManager, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._ftdi = ftdi
        self._running = False
        self._gpio_polling = False
        self._poll_interval_ms: int = 200
        self._mutex = QMutex()

    # -- GPIO polling --

    def start_gpio_polling(self, interval_ms: int = 200) -> None:
        """Start GPIO polling (called inside run loop)."""
        self._poll_interval_ms = interval_ms
        self._gpio_polling = True

    def stop_gpio_polling(self) -> None:
        self._gpio_polling = False

    def run(self) -> None:
        """Main loop - runs in QThread."""
        self._running = True
        self._log("Verifier Worker started")

        while self._running:
            if self._gpio_polling and self._ftdi.is_connected:
                try:
                    self._poll_gpio()
                except Exception as e:
                    self.error_occurred.emit(f"GPIO polling error: {e}")

            time.sleep(self._poll_interval_ms / 1000.0)

        self._log("Verifier Worker stopped")

    def stop(self) -> None:
        self._running = False
        self._gpio_polling = False

    def _poll_gpio(self) -> None:
        """Check GPIO state via MPSSE low-byte read."""
        raw = self._ftdi.read_gpio_low()
        state = GpioState(timestamp=time.time())
        if raw is not None:
            for bit in range(8):
                state.pin_states[bit] = bool(raw & (1 << bit))
        self.gpio_updated.emit(state)

    # -- I2C scan --

    def run_i2c_scan(self, addr_start: int = 0x08, addr_end: int = 0x77) -> None:
        """I2C bus scan (sync call in worker thread)."""
        if not self._ftdi.is_connected:
            self.error_occurred.emit("FTDI not connected - I2C scan unavailable")
            return

        self._log(f"I2C scan start: 0x{addr_start:02X} ~ 0x{addr_end:02X}")
        start = time.time()

        found = self._ftdi.i2c_scan(addr_start, addr_end)

        result = I2CScanResult(
            timestamp=time.time(),
            found_addresses=found,
            total_scanned=addr_end - addr_start + 1,
        )
        elapsed = time.time() - start
        self._log(
            f"I2C scan done: {len(found)} found "
            f"({result.total_scanned} addresses, {elapsed:.2f}s)"
        )
        for addr in found:
            self._log(f"  ACK: 0x{addr:02X}")

        self.i2c_scan_done.emit(result)

    # -- I2C single address test --

    def test_i2c_address(self, addr: int) -> None:
        """ACK test for a specific I2C address."""
        if not self._ftdi.is_connected:
            self.error_occurred.emit("FTDI not connected")
            return

        found = self._ftdi.i2c_scan(addr, addr)
        ack = addr in found

        result = ProtocolTestResult(
            timestamp=time.time(),
            protocol="I2C",
            success=ack,
            message=f"0x{addr:02X} {'ACK' if ack else 'NACK'}",
        )
        color = "ACK" if ack else "NACK"
        self._log(f"I2C Test 0x{addr:02X} -> {color}")
        self.protocol_test_done.emit(result)

    # -- I2C register read test --

    def test_i2c_read(self, addr: int, reg: int, length: int = 1) -> None:
        """I2C register read."""
        if not self._ftdi.is_connected:
            self.error_occurred.emit("FTDI not connected")
            return

        data = self._ftdi.i2c_read(addr, bytes([reg]), length)
        if data is not None:
            hex_str = " ".join(f"{b:02X}" for b in data)
            result = ProtocolTestResult(
                timestamp=time.time(), protocol="I2C", success=True,
                message=f"Read 0x{addr:02X} reg=0x{reg:02X}: [{hex_str}]",
                raw_data=data,
            )
            self._log(f"I2C Read 0x{addr:02X}[0x{reg:02X}] -> {hex_str}")
        else:
            result = ProtocolTestResult(
                timestamp=time.time(), protocol="I2C", success=False,
                message=f"Read 0x{addr:02X} reg=0x{reg:02X}: FAILED",
            )
            self._log(f"I2C Read 0x{addr:02X}[0x{reg:02X}] -> FAILED")

        self.protocol_test_done.emit(result)

    # -- SPI loopback test --

    def test_spi_loopback(self) -> None:
        """SPI loopback test (requires MOSI -> MISO wiring).

        Sends a known pattern via SPI and checks if the same data
        is received back (loopback wiring required).
        """
        if not self._ftdi.is_connected:
            self.error_occurred.emit("FTDI not connected")
            return

        tx_pattern = bytes([0xA5, 0x5A, 0xDE, 0xAD])
        self._log(f"SPI loopback TX: {' '.join(f'{b:02X}' for b in tx_pattern)}")

        try:
            rx_data = self._ftdi.spi_transfer(tx_pattern)
            if rx_data is None:
                result = ProtocolTestResult(
                    timestamp=time.time(), protocol="SPI", success=False,
                    message="SPI loopback: transfer returned None",
                )
                self._log("SPI loopback: transfer failed (None)")
            elif rx_data == tx_pattern:
                hex_str = " ".join(f"{b:02X}" for b in rx_data)
                result = ProtocolTestResult(
                    timestamp=time.time(), protocol="SPI", success=True,
                    message=f"SPI loopback OK: [{hex_str}]",
                    raw_data=rx_data,
                )
                self._log(f"SPI loopback PASS: RX = {hex_str}")
            else:
                hex_rx = " ".join(f"{b:02X}" for b in rx_data)
                result = ProtocolTestResult(
                    timestamp=time.time(), protocol="SPI", success=False,
                    message=f"SPI loopback MISMATCH: RX=[{hex_rx}]",
                    raw_data=rx_data,
                )
                self._log(f"SPI loopback FAIL: RX = {hex_rx}")
        except Exception as e:
            result = ProtocolTestResult(
                timestamp=time.time(), protocol="SPI", success=False,
                message=f"SPI loopback error: {e}",
            )
            self._log(f"SPI loopback error: {e}")

        self.protocol_test_done.emit(result)

    # -- Utils --

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_message.emit(f"[{ts}] {msg}")
