"""
INA228 QThread Worker - 비동기 I2C 폴링

DIAG_ALRT.CNVRF 비트 폴링 → VSHUNT/VBUS 읽기 → 변환 → Signal 발행
참조: INA228Controller.run() 로직 포팅
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional
import math

from PySide6.QtCore import QObject, Signal, Slot

from core.ftdi_manager import FtdiManager
from modules.ina228.ina228_registers import (
    INA228Reg, REGISTER_SIZE, INA228Conversion, ADC_CONFIG_DEFAULT,
)


@dataclass
class INA228Measurement:
    """INA228 단일 측정 결과"""
    timestamp: float       # time.time()
    vshunt_mv: float       # Shunt 전압 (mV)
    vbus_v: float          # 버스 전압 (V)
    current_ma: float      # 전류 (mA)
    power_mw: float        # 전력 (mW)
    die_temp_c: float = 0.0  # 다이 온도 (°C)


class INA228Worker(QObject):
    """INA228 비동기 측정 Worker

    QThread에서 실행되며 VSHUNT, VBUS를 주기적으로 폴링합니다.
    FtdiManager.i2c_read/write를 통해 스레드 안전하게 I2C 접근합니다.

    사용 방법:
        worker = INA228Worker(ftdi_manager)
        worker.configure(slave_addr=0x40, adc_range=1, shunt_resistor=0.01)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.measurement_ready.connect(handler)
        thread.start()
    """

    measurement_ready = Signal(object)   # INA228Measurement
    error_occurred = Signal(str)
    log_message = Signal(str)

    def __init__(self, ftdi_manager: FtdiManager) -> None:
        super().__init__()
        self._ftdi = ftdi_manager
        self._running: bool = False
        self._slave_addr: int = 0x40
        self._adc_range: int = 1
        self._shunt_resistor: float = 0.01
        self._poll_interval_ms: int = 100
        self._avg_index: int = 2        # AVG=16
        self._vbusct_index: int = 4     # 540us
        self._vshct_index: int = 4      # 540us
        self._last_vbus_v: float = 0.0
        self._last_vshunt_mv: float = 0.0
        self._consecutive_failures: int = 0
        self._valid_streak: int = 0
        self._last_error_ts: float = 0.0
        self._backoff_until: float = 0.0
        self._max_consecutive_failures: int = 5
        self._error_emit_interval_s: float = 1.0
        self._backoff_step_s: float = 0.05
        self._backoff_max_s: float = 0.5

    def configure(
        self,
        slave_addr: int,
        adc_range: int,
        shunt_resistor: float,
        poll_interval_ms: int = 100,
        avg_index: int = 2,
        vbusct_index: int = 4,
        vshct_index: int = 4,
    ) -> None:
        """Worker 파라미터 설정 (run() 호출 전에 설정)

        Args:
            slave_addr: 7비트 I2C 슬레이브 주소
            adc_range: 0=+/-163.84mV, 1=+/-40.96mV
            shunt_resistor: Shunt 저항값 (Ω)
            poll_interval_ms: 폴링 간격 (ms)
            avg_index: AVG 비트값 (0~7)
            vbusct_index: VBUSCT 비트값 (0~7)
            vshct_index: VSHCT 비트값 (0~7)
        """
        self._slave_addr = slave_addr
        self._adc_range = adc_range
        self._shunt_resistor = shunt_resistor
        self._poll_interval_ms = poll_interval_ms
        self._avg_index = avg_index
        self._vbusct_index = vbusct_index
        self._vshct_index = vshct_index

    @Slot()
    def run(self) -> None:
        """메인 폴링 루프 (QThread에서 실행)"""
        self._running = True
        self.log_message.emit(f"[INA228] Worker 시작 - 주소: 0x{self._slave_addr:02X}")

        # 디바이스 설정
        if not self._configure_device():
            self.error_occurred.emit("INA228 설정 실패")
            self._running = False
            return

        # 첫 더미 읽기 (정착 대기)
        time.sleep(0.1)
        self._read_vshunt()

        while self._running:
            try:
                now = time.time()
                if now < self._backoff_until:
                    time.sleep(0.01)
                    continue
                # CNVRF 비트 대기
                if self._wait_conversion_ready(timeout_s=1.0):
                    vshunt_raw = self._read_vshunt()
                    vbus_raw = self._read_vbus()
                    temp_raw = self._read_dietemp()

                    # 읽기 실패 시 건너뛰기 (NACK 등)
                    if vshunt_raw is None or vbus_raw is None:
                        self._record_failure("read_none")
                        continue

                    vshunt_mv = INA228Conversion.raw_to_shunt_voltage_mv(vshunt_raw, self._adc_range)
                    vbus_v = INA228Conversion.raw_to_bus_voltage_v(vbus_raw)

                    # 0값 스파이크 필터: 이전 유효값 대비 급격한 0 드롭은 무시
                    if vbus_raw == 0 and self._last_vbus_v > 0.01:
                        self._record_failure("vbus_zero_spike")
                        continue
                    if vshunt_raw == 0 and abs(self._last_vshunt_mv) > 0.0001:
                        self._record_failure("vshunt_zero_spike")
                        continue

                    current_ma = INA228Conversion.calculate_current_ma(vshunt_mv, self._shunt_resistor)
                    power_mw = INA228Conversion.calculate_power_mw(vbus_v, current_ma)
                    die_temp_c = 0.0
                    if temp_raw is not None:
                        die_temp_c = INA228Conversion.raw_to_temperature_c(temp_raw)

                    if not self._is_finite_measurement(
                        vshunt_mv, vbus_v, current_ma, power_mw, die_temp_c
                    ):
                        self._record_failure("non_finite")
                        continue

                    self._last_vbus_v = vbus_v
                    self._last_vshunt_mv = vshunt_mv
                    self._consecutive_failures = 0
                    self._valid_streak += 1
                    if self._valid_streak < 2:
                        continue

                    m = INA228Measurement(
                        timestamp=time.time(),
                        vshunt_mv=vshunt_mv,
                        vbus_v=vbus_v,
                        current_ma=current_ma,
                        power_mw=power_mw,
                        die_temp_c=die_temp_c,
                    )
                    self.measurement_ready.emit(m)

                time.sleep(self._poll_interval_ms / 1000.0)

            except Exception as e:
                if self._running:
                    self.error_occurred.emit(f"Worker 오류: {e}")
                    time.sleep(0.5)

        self.log_message.emit("[INA228] Worker 종료")

    def stop(self) -> None:
        """폴링 루프 중지 신호"""
        self._running = False

    # ── I2C 레지스터 접근 ──

    def _write_register_16(self, reg: INA228Reg, value: int) -> bool:
        """16비트 레지스터 쓰기

        Args:
            reg: 레지스터 주소
            value: 16비트 값

        Returns:
            성공 여부
        """
        data = bytes([reg.value, (value >> 8) & 0xFF, value & 0xFF])
        return self._ftdi.i2c_write(self._slave_addr, data)

    def _read_register_raw(self, reg: INA228Reg) -> Optional[int]:
        """레지스터 원시값 읽기

        REGISTER_SIZE에 따라 2~3바이트를 읽고,
        3바이트 레지스터는 상위 20비트만 반환 (>>4).

        Args:
            reg: 레지스터 주소

        Returns:
            정수 원시값 또는 None (오류 시)
        """
        size = REGISTER_SIZE.get(reg, 2)
        raw = self._ftdi.i2c_read(self._slave_addr, bytes([reg.value]), size)
        if raw is None or len(raw) < size:
            return None
        if size >= 3:
            return ((raw[0] << 16) | (raw[1] << 8) | raw[2]) >> 4
        else:
            return (raw[0] << 8) | raw[1]

    def _read_register_16_raw(self, reg: INA228Reg) -> Optional[int]:
        """2바이트(16비트) 레지스터 원시값 읽기"""
        raw = self._ftdi.i2c_read(self._slave_addr, bytes([reg.value]), 2)
        if raw is None or len(raw) < 2:
            return None
        return (raw[0] << 8) | raw[1]

    def _wait_conversion_ready(self, timeout_s: float = 1.0) -> bool:
        """DIAG_ALRT 레지스터의 CNVRF 비트 폴링

        참조: ina228.py main() 루프의 변환 완료 대기 로직

        Args:
            timeout_s: 타임아웃 (초)

        Returns:
            변환 완료 여부
        """
        start = time.time()
        while (time.time() - start) < timeout_s:
            if not self._running:
                return False
            val = self._read_register_16_raw(INA228Reg.DIAG_ALRT)
            if val is not None and (val & (1 << 0)):  # CNVRF = bit0
                return True
            time.sleep(0.005)
        return False

    def _configure_device(self) -> bool:
        """INA228 CONFIG 및 ADC_CONFIG 레지스터 설정

        참조: INA228Controller.configure_ina228()

        Returns:
            성공 여부
        """
        # CONFIG: ADCRANGE 설정
        config_val = 0x0010 if self._adc_range == 1 else 0x0000
        if not self._write_register_16(INA228Reg.CONFIG, config_val):
            return False

        # ADC_CONFIG: 연속 모드(0xF), 변환시간, 평균화 설정
        # MODE = 0xF (연속 전압+온도)
        # VBUSCT = _vbusct_index << 9
        # VSHCT  = _vshct_index << 6
        # VTCT   = 0x4 (540us) << 3
        # AVG    = _avg_index
        adc_config = (
            (0xF << 12)
            | (self._vbusct_index << 9)
            | (self._vshct_index << 6)
            | (0x4 << 3)
            | self._avg_index
        )
        if not self._write_register_16(INA228Reg.ADC_CONFIG, adc_config):
            return False

        self.log_message.emit(
            f"[INA228] 설정 완료 - ADC_RANGE={self._adc_range}, "
            f"AVG={self._avg_index}, VBUSCT={self._vbusct_index}"
        )
        return True

    def _read_vshunt(self) -> Optional[int]:
        return self._read_register_raw(INA228Reg.VSHUNT)

    def _read_vbus(self) -> Optional[int]:
        return self._read_register_raw(INA228Reg.VBUS)

    def _read_dietemp(self) -> Optional[int]:
        return self._read_register_16_raw(INA228Reg.DIETEMP)

    def read_register_for_map(self, reg: INA228Reg) -> Optional[int]:
        """외부에서 레지스터 맵 갱신용으로 호출 (UI 스레드에서 직접 호출 가능)

        Args:
            reg: 레지스터 주소

        Returns:
            16비트 원시값 또는 None
        """
        return self._read_register_16_raw(reg)

    def write_register_for_map(self, reg: INA228Reg, value: int) -> bool:
        """레지스터 맵 테이블에서 직접 수정 시 호출

        Args:
            reg: 레지스터 주소
            value: 16비트 값

        Returns:
            성공 여부
        """
        return self._write_register_16(reg, value)

    def _record_failure(self, reason: str) -> None:
        # Record read/convert failures and apply backoff
        self._valid_streak = 0
        self._consecutive_failures += 1
        delay = min(self._backoff_max_s, self._backoff_step_s * self._consecutive_failures)
        self._backoff_until = max(self._backoff_until, time.time() + delay)

        if self._consecutive_failures >= self._max_consecutive_failures:
            now = time.time()
            if (now - self._last_error_ts) >= self._error_emit_interval_s:
                self.error_occurred.emit(
                    f"INA228 read failures ({self._consecutive_failures}) - {reason}"
                )
                self._last_error_ts = now

    @staticmethod
    def _is_finite_measurement(
        vshunt_mv: float,
        vbus_v: float,
        current_ma: float,
        power_mw: float,
        die_temp_c: float,
    ) -> bool:
        return all(
            math.isfinite(x)
            for x in (vshunt_mv, vbus_v, current_ma, power_mw, die_temp_c)
        )
