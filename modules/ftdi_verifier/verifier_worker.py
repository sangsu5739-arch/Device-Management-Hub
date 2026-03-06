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

    # -- SPI probe test --

    def test_spi_probe(self, tx_data: bytes = b'\x00\x00\x00\x00') -> None:
        """SPI probe: send data and check for device response on MISO.

        PASS if any received byte is not 0xFF (device responded).
        FAIL if all received bytes are 0xFF (no device or MISO floating).
        """
        if not self._ftdi.is_connected:
            self.error_occurred.emit("FTDI not connected")
            return

        hex_tx = " ".join(f"{b:02X}" for b in tx_data)
        self._log(f"SPI probe TX: {hex_tx}")

        try:
            rx_data = self._ftdi.spi_transfer(tx_data)
            if rx_data is None:
                result = ProtocolTestResult(
                    timestamp=time.time(), protocol="SPI", success=False,
                    message="SPI probe: transfer returned None",
                )
                self._log("SPI probe: transfer failed (None)")
            else:
                hex_rx = " ".join(f"{b:02X}" for b in rx_data)
                has_response = any(b != 0xFF for b in rx_data)
                if has_response:
                    result = ProtocolTestResult(
                        timestamp=time.time(), protocol="SPI", success=True,
                        message=f"SPI probe OK: [{hex_rx}]",
                        raw_data=rx_data,
                    )
                    self._log(f"SPI probe PASS: RX = {hex_rx}")
                else:
                    result = ProtocolTestResult(
                        timestamp=time.time(), protocol="SPI", success=False,
                        message=f"SPI probe: no response (0xFF) [{hex_rx}]",
                        raw_data=rx_data,
                    )
                    self._log(f"SPI probe FAIL: RX = {hex_rx} (all 0xFF)")
        except Exception as e:
            result = ProtocolTestResult(
                timestamp=time.time(), protocol="SPI", success=False,
                message=f"SPI probe error: {e}",
            )
            self._log(f"SPI probe error: {e}")

        self.protocol_test_done.emit(result)

    # -- SPI device ID read --

    def test_spi_read_id(self, register: int = 0x9F, length: int = 2,
                         expected: Optional[bytes] = None) -> None:
        """Read SPI device ID via command + read-back.

        Sends the register command byte followed by `length` dummy bytes
        and reads back the response (full-duplex).

        Args:
            register: ID register command (e.g. 0x9F for JEDEC ID).
            length: number of ID bytes to read.
            expected: optional expected bytes for comparison.
        """
        if not self._ftdi.is_connected:
            self.error_occurred.emit("FTDI not connected")
            return

        self._log(f"SPI Read ID: cmd=0x{register:02X}, len={length}")

        try:
            # tc72-style sequence: command write phase, then read phase under one CS
            rx_data = self._ftdi.spi_write_then_read(bytes([register]), length)
            if rx_data is None:
                result = ProtocolTestResult(
                    timestamp=time.time(), protocol="SPI", success=False,
                    message="SPI ID read: transfer returned None",
                )
                self._log("SPI ID read: transfer failed (None)")
            else:
                id_bytes = rx_data
                hex_str = " ".join(f"0x{b:02X}" for b in id_bytes)

                if expected and id_bytes != expected:
                    exp_str = " ".join(f"0x{b:02X}" for b in expected)
                    result = ProtocolTestResult(
                        timestamp=time.time(), protocol="SPI", success=False,
                        message=f"ID Mismatch: got [{hex_str}], expected [{exp_str}]",
                        raw_data=id_bytes,
                    )
                    self._log(f"SPI ID MISMATCH: {hex_str} (expected {exp_str})")
                else:
                    result = ProtocolTestResult(
                        timestamp=time.time(), protocol="SPI", success=True,
                        message=f"ID: [{hex_str}]",
                        raw_data=id_bytes,
                    )
                    self._log(f"SPI ID read OK: {hex_str}")
        except Exception as e:
            result = ProtocolTestResult(
                timestamp=time.time(), protocol="SPI", success=False,
                message=f"SPI ID read error: {e}",
            )
            self._log(f"SPI ID read error: {e}")

        self.protocol_test_done.emit(result)

    # -- Utils --

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_message.emit(f"[{ts}] {msg}")
