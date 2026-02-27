"""
INA228 QThread Worker - async I2C polling

DIAG_ALRT.CNVRF bit polling -> VSHUNT/VBUS read -> convert -> emit signal
Ref: INA228Controller.run() logic port
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
    """Single INA228 measurement result."""
    timestamp: float       # time.time()
    vshunt_mv: float       # Shunt voltage (mV)
    vbus_v: float          # Bus voltage (V)
    current_ma: float      # Current (mA)
    power_mw: float        # Power (mW)
    die_temp_c: float = 0.0  # Die temperature (C)


class INA228Worker(QObject):
    """INA228 async measurement worker.

    Runs in QThread and polls VSHUNT/VBUS periodically.
    Uses FtdiManager.i2c_read/write for thread-safe I2C access.

    Usage:
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
        """Set worker parameters (before run()).

        Args:
            slave_addr: 7-bit I2C slave address
            adc_range: 0=+/-163.84mV, 1=+/-40.96mV
            shunt_resistor: Shunt resistance (Ohm)
            poll_interval_ms: Polling interval (ms)
            avg_index: AVG bits (0~7)
            vbusct_index: VBUSCT bits (0~7)
            vshct_index: VSHCT bits (0~7)
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
        """Main polling loop (runs in QThread)."""
        self._running = True
        self.log_message.emit(f"[INA228] Worker start - addr: 0x{self._slave_addr:02X}")

        # Device configuration
        if not self._configure_device():
            self.error_occurred.emit("INA228 configuration failed")
            self._running = False
            return

        # First dummy read (settle delay)
        time.sleep(0.1)
        self._read_vshunt()

        while self._running:
            try:
                now = time.time()
                if now < self._backoff_until:
                    time.sleep(0.01)
                    continue
                # Wait for CNVRF bit
                if self._wait_conversion_ready(timeout_s=1.0):
                    vshunt_raw = self._read_vshunt()
                    vbus_raw = self._read_vbus()
                    temp_raw = self._read_dietemp()

                    # Skip on read failure (NACK, etc)
                    if vshunt_raw is None or vbus_raw is None:
                        self._record_failure("read_none")
                        continue

                    vshunt_mv = INA228Conversion.raw_to_shunt_voltage_mv(vshunt_raw, self._adc_range)
                    vbus_v = INA228Conversion.raw_to_bus_voltage_v(vbus_raw)

                    # Zero spike filter: ignore sudden drop to 0 vs last valid
                    if vbus_raw == 0 and self._last_vbus_v > 0.01:
                        self._record_failure("vbus_zero_spike")
                        continue
                    if vshunt_raw == 0 and abs(self._last_vshunt_mv) > 0.0001:
                        self._record_failure("vshunt_zero_spike")
                        continue
                    # Negative spike filter: ignore sudden cross from valid to extreme negative
                    if vshunt_mv < -1000.0 and abs(self._last_vshunt_mv) < 100.0:
                        self._record_failure("vshunt_neg_spike")
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
                    self.error_occurred.emit(f"Worker error: {e}")
                    time.sleep(0.5)

        self.log_message.emit("[INA228] Worker stop")

    def stop(self) -> None:
        """Stop polling loop signal."""
        self._running = False

    # -- I2C register access --

    def _write_register_16(self, reg: INA228Reg, value: int) -> bool:
        """Write 16-bit register.

        Args:
            reg: register address
            value: 16-bit value

        Returns:
            True if write succeeds.
        """
        data = bytes([reg.value, (value >> 8) & 0xFF, value & 0xFF])
        return self._ftdi.i2c_write(self._slave_addr, data)

    def _read_register_raw(self, reg: INA228Reg) -> Optional[int]:
        """Read raw register value.

        Read 2-3 bytes based on REGISTER_SIZE.
        3-byte registers return top 20 bits (>>4).

        Args:
            reg: register address

        Returns:
            Raw int value or None on error.
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
        """Read 2-byte (16-bit) register raw value."""
        raw = self._ftdi.i2c_read(self._slave_addr, bytes([reg.value]), 2)
        if raw is None or len(raw) < 2:
            return None
        return (raw[0] << 8) | raw[1]

    def _wait_conversion_ready(self, timeout_s: float = 1.0) -> bool:
        """Poll CNVRF bit in DIAG_ALRT register.

        Ref: ina228.py main() conversion-complete wait logic

        Args:
            timeout_s: timeout (s)

        Returns:
            True if conversion ready.
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
        """Configure INA228 CONFIG and ADC_CONFIG registers.

        Ref: INA228Controller.configure_ina228()

        Returns:
            True if configuration succeeds.
        """
        # CONFIG: ADCRANGE setting
        config_val = 0x0010 if self._adc_range == 1 else 0x0000
        if not self._write_register_16(INA228Reg.CONFIG, config_val):
            return False

        # ADC_CONFIG: continuous mode (0xF), conversion time, averaging
        # MODE = 0xF (continuous voltage + temperature)
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
            f"[INA228] Config done - ADC_RANGE={self._adc_range}, "
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
        """External register map refresh helper (safe to call from UI thread).

        Args:
            reg: register address

        Returns:
            16-bit raw value or None
        """
        return self._read_register_16_raw(reg)

    def write_register_for_map(self, reg: INA228Reg, value: int) -> bool:
        """Called when editing from register map table.

        Args:
            reg: register address
            value: 16-bit value

        Returns:
            success flag
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
