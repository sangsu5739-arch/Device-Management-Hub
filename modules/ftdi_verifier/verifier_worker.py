"""
FTDI Verifier Worker — GPIO 폴링, I2C/SPI 프로토콜 테스트 비동기 처리

UI 스레드를 블로킹하지 않도록 QThread에서 실행됩니다.
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
    """GPIO 핀 상태 스냅샷"""
    timestamp: float = 0.0
    pin_states: Dict[int, bool] = field(default_factory=dict)  # pin_number → high/low


@dataclass
class I2CScanResult:
    """I2C 스캔 결과"""
    timestamp: float = 0.0
    found_addresses: List[int] = field(default_factory=list)
    total_scanned: int = 0


@dataclass
class ProtocolTestResult:
    """프로토콜 테스트 결과"""
    timestamp: float = 0.0
    protocol: str = ""
    success: bool = False
    message: str = ""
    raw_data: bytes = b""


class VerifierWorker(QObject):
    """비동기 하드웨어 검증 워커

    Signals:
        gpio_updated(object): GpioState 수신
        i2c_scan_done(object): I2CScanResult 수신
        protocol_test_done(object): ProtocolTestResult 수신
        log_message(str): 로그 메시지
        error_occurred(str): 에러 메시지
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

    # ── GPIO 폴링 ──

    def start_gpio_polling(self, interval_ms: int = 200) -> None:
        """GPIO 폴링 시작 (run() 루프 내에서 호출됨)"""
        self._poll_interval_ms = interval_ms
        self._gpio_polling = True

    def stop_gpio_polling(self) -> None:
        self._gpio_polling = False

    def run(self) -> None:
        """메인 루프 — QThread에서 실행"""
        self._running = True
        self._log("Verifier Worker 시작됨")

        while self._running:
            if self._gpio_polling and self._ftdi.is_connected:
                try:
                    self._poll_gpio()
                except Exception as e:
                    self.error_occurred.emit(f"GPIO 폴링 오류: {e}")

            time.sleep(self._poll_interval_ms / 1000.0)

        self._log("Verifier Worker 종료됨")

    def stop(self) -> None:
        self._running = False
        self._gpio_polling = False

    def _poll_gpio(self) -> None:
        """MPSSE 로우바이트 읽기로 GPIO 상태 확인"""
        raw = self._ftdi.read_gpio_low()
        state = GpioState(timestamp=time.time())
        if raw is not None:
            for bit in range(8):
                state.pin_states[bit] = bool(raw & (1 << bit))
        self.gpio_updated.emit(state)

    # ── I2C 스캔 ──

    def run_i2c_scan(self, addr_start: int = 0x08, addr_end: int = 0x77) -> None:
        """I2C 버스 스캔 (동기 호출 — Worker 스레드에서)"""
        if not self._ftdi.is_connected:
            self.error_occurred.emit("FTDI 미연결 — I2C 스캔 불가")
            return

        self._log(f"I2C 스캔 시작: 0x{addr_start:02X} ~ 0x{addr_end:02X}")
        start = time.time()

        found = self._ftdi.i2c_scan(addr_start, addr_end)

        result = I2CScanResult(
            timestamp=time.time(),
            found_addresses=found,
            total_scanned=addr_end - addr_start + 1,
        )
        elapsed = time.time() - start
        self._log(
            f"I2C 스캔 완료: {len(found)}개 발견 "
            f"({result.total_scanned}개 주소, {elapsed:.2f}s)"
        )
        for addr in found:
            self._log(f"  ACK: 0x{addr:02X}")

        self.i2c_scan_done.emit(result)

    # ── I2C 단일 주소 테스트 ──

    def test_i2c_address(self, addr: int) -> None:
        """특정 I2C 주소에 대한 ACK 테스트"""
        if not self._ftdi.is_connected:
            self.error_occurred.emit("FTDI 미연결")
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
        self._log(f"I2C Test 0x{addr:02X} → {color}")
        self.protocol_test_done.emit(result)

    # ── I2C 레지스터 읽기 테스트 ──

    def test_i2c_read(self, addr: int, reg: int, length: int = 1) -> None:
        """I2C 레지스터 읽기"""
        if not self._ftdi.is_connected:
            self.error_occurred.emit("FTDI 미연결")
            return

        data = self._ftdi.i2c_read(addr, bytes([reg]), length)
        if data is not None:
            hex_str = " ".join(f"{b:02X}" for b in data)
            result = ProtocolTestResult(
                timestamp=time.time(), protocol="I2C", success=True,
                message=f"Read 0x{addr:02X} reg=0x{reg:02X}: [{hex_str}]",
                raw_data=data,
            )
            self._log(f"I2C Read 0x{addr:02X}[0x{reg:02X}] → {hex_str}")
        else:
            result = ProtocolTestResult(
                timestamp=time.time(), protocol="I2C", success=False,
                message=f"Read 0x{addr:02X} reg=0x{reg:02X}: FAILED",
            )
            self._log(f"I2C Read 0x{addr:02X}[0x{reg:02X}] → FAILED")

        self.protocol_test_done.emit(result)

    # ── SPI 루프백 테스트 ──

    def test_spi_loopback(self) -> None:
        """SPI 루프백 테스트 (MOSI → MISO 핀 연결 필요)

        현재 FtdiManager에 SPI API가 없으므로 placeholder.
        """
        result = ProtocolTestResult(
            timestamp=time.time(), protocol="SPI", success=False,
            message="SPI 루프백 테스트 — FtdiManager SPI API 미구현",
        )
        self._log("SPI 루프백 테스트: 미구현 (FtdiManager SPI API 필요)")
        self.protocol_test_done.emit(result)

    # ── 유틸 ──

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_message.emit(f"[{ts}] {msg}")
