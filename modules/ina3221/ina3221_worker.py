"""
INA3221 power monitor worker.
Polls register data via FtdiManager I2C interface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import QObject, Signal

from core.ftdi_manager import FtdiManager
from modules.ina3221.ina3221_registers import INA3221Reg, INA3221Conversion


@dataclass
class INA3221Measurement:
    timestamp: float
    vbus_v: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    vshunt_mv: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    current_ma: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])


class INA3221Worker(QObject):
    measurement_ready = Signal(object)
    error_occurred = Signal(str)
    log_message = Signal(str)

    def __init__(self, ftdi_manager: FtdiManager) -> None:
        super().__init__()
        self._ftdi = ftdi_manager
        self._running = False

        self._slave_addr = 0x40
        self._poll_interval_s = 0.1
        self._shunt_resistors = [0.01, 0.01, 0.01]

    def configure(self, slave_addr: int, poll_interval_ms: int, shunt_resistors: List[float]) -> None:
        self._slave_addr = slave_addr
        self._poll_interval_s = max(10, poll_interval_ms) / 1000.0
        self._shunt_resistors = shunt_resistors.copy()

    def run(self) -> None:
        self._running = True
        self.log_message.emit("INA3221: Worker started")

        while self._running:
            t0 = time.time()
            if not self._ftdi.is_connected:
                self.error_occurred.emit("FTDI disconnected")
                break

            m = self._read_measurement()
            if m is not None:
                self.measurement_ready.emit(m)

            elapsed = time.time() - t0
            sleep_time = self._poll_interval_s - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        self.log_message.emit("INA3221: Worker stopped")

    def stop(self) -> None:
        self._running = False

    def _read_measurement(self) -> Optional[INA3221Measurement]:
        m = INA3221Measurement(timestamp=time.time())

        # The registers are consecutive: VSHUNT1, VBUS1, VSHUNT2, VBUS2, VSHUNT3, VBUS3
        # Start at 0x01
        regs = [
            INA3221Reg.SHUNT_VOLTAGE_1, INA3221Reg.BUS_VOLTAGE_1,
            INA3221Reg.SHUNT_VOLTAGE_2, INA3221Reg.BUS_VOLTAGE_2,
            INA3221Reg.SHUNT_VOLTAGE_3, INA3221Reg.BUS_VOLTAGE_3
        ]

        # Read 3 channels
        for i in range(3):
            # Read Shunt
            raw_sh = self._ftdi.i2c_read(self._slave_addr, bytes([regs[i*2].value]), 2)
            if raw_sh is not None and len(raw_sh) >= 2:
                val16 = (raw_sh[0] << 8) | raw_sh[1]
                m.vshunt_mv[i] = INA3221Conversion.raw_to_shunt_voltage_mv(val16)
                m.current_ma[i] = INA3221Conversion.calculate_current_ma(m.vshunt_mv[i], self._shunt_resistors[i])
            else:
                return None  # I2C fail

            # Read Bus
            raw_bus = self._ftdi.i2c_read(self._slave_addr, bytes([regs[i*2 + 1].value]), 2)
            if raw_bus is not None and len(raw_bus) >= 2:
                val16 = (raw_bus[0] << 8) | raw_bus[1]
                m.vbus_v[i] = INA3221Conversion.raw_to_bus_voltage_v(val16)
            else:
                return None  # I2C fail

        return m
