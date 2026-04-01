"""
INA3221 register definitions and conversion utilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List

class INA3221Reg(IntEnum):
    """INA3221 register addresses."""
    CONFIG               = 0x00
    SHUNT_VOLTAGE_1      = 0x01
    BUS_VOLTAGE_1        = 0x02
    SHUNT_VOLTAGE_2      = 0x03
    BUS_VOLTAGE_2        = 0x04
    SHUNT_VOLTAGE_3      = 0x05
    BUS_VOLTAGE_3        = 0x06
    CRITICAL_ALERT_1     = 0x07
    WARNING_ALERT_1      = 0x08
    CRITICAL_ALERT_2     = 0x09
    WARNING_ALERT_2      = 0x0A
    CRITICAL_ALERT_3     = 0x0B
    WARNING_ALERT_3      = 0x0C
    SHUNT_VOLTAGE_SUM    = 0x0D
    SHUNT_VOLTAGE_SUM_LIMIT = 0x0E
    MASK_ENABLE          = 0x0F
    PWR_VALID_UPPER      = 0x10
    PWR_VALID_LOWER      = 0x11
    MANUFACTURER_ID      = 0xFE
    DIE_ID               = 0xFF

REGISTER_NAMES: Dict[INA3221Reg, str] = {
    INA3221Reg.CONFIG:               "CONFIG",
    INA3221Reg.SHUNT_VOLTAGE_1:      "SHUNT_VOLTAGE_1",
    INA3221Reg.BUS_VOLTAGE_1:        "BUS_VOLTAGE_1",
    INA3221Reg.SHUNT_VOLTAGE_2:      "SHUNT_VOLTAGE_2",
    INA3221Reg.BUS_VOLTAGE_2:        "BUS_VOLTAGE_2",
    INA3221Reg.SHUNT_VOLTAGE_3:      "SHUNT_VOLTAGE_3",
    INA3221Reg.BUS_VOLTAGE_3:        "BUS_VOLTAGE_3",
    INA3221Reg.CRITICAL_ALERT_1:     "CRITICAL_ALERT_1",
    INA3221Reg.WARNING_ALERT_1:      "WARNING_ALERT_1",
    INA3221Reg.CRITICAL_ALERT_2:     "CRITICAL_ALERT_2",
    INA3221Reg.WARNING_ALERT_2:      "WARNING_ALERT_2",
    INA3221Reg.CRITICAL_ALERT_3:     "CRITICAL_ALERT_3",
    INA3221Reg.WARNING_ALERT_3:      "WARNING_ALERT_3",
    INA3221Reg.SHUNT_VOLTAGE_SUM:    "SHUNT_VOLTAGE_SUM",
    INA3221Reg.SHUNT_VOLTAGE_SUM_LIMIT: "SHUNT_VOLTAGE_SUM_LIMIT",
    INA3221Reg.MASK_ENABLE:          "MASK_ENABLE",
    INA3221Reg.PWR_VALID_UPPER:      "PWR_VALID_UPPER",
    INA3221Reg.PWR_VALID_LOWER:      "PWR_VALID_LOWER",
    INA3221Reg.MANUFACTURER_ID:      "MANUFACTURER_ID",
    INA3221Reg.DIE_ID:               "DIE_ID",
}

REGISTER_DESCRIPTIONS: Dict[INA3221Reg, str] = {
    INA3221Reg.CONFIG:               "Configuration Register",
    INA3221Reg.SHUNT_VOLTAGE_1:      "Channel 1 Shunt Voltage",
    INA3221Reg.BUS_VOLTAGE_1:        "Channel 1 Bus Voltage",
    INA3221Reg.SHUNT_VOLTAGE_2:      "Channel 2 Shunt Voltage",
    INA3221Reg.BUS_VOLTAGE_2:        "Channel 2 Bus Voltage",
    INA3221Reg.SHUNT_VOLTAGE_3:      "Channel 3 Shunt Voltage",
    INA3221Reg.BUS_VOLTAGE_3:        "Channel 3 Bus Voltage",
    INA3221Reg.CRITICAL_ALERT_1:     "Channel 1 Critical Alert Limit",
    INA3221Reg.WARNING_ALERT_1:      "Channel 1 Warning Alert Limit",
    INA3221Reg.CRITICAL_ALERT_2:     "Channel 2 Critical Alert Limit",
    INA3221Reg.WARNING_ALERT_2:      "Channel 2 Warning Alert Limit",
    INA3221Reg.CRITICAL_ALERT_3:     "Channel 3 Critical Alert Limit",
    INA3221Reg.WARNING_ALERT_3:      "Channel 3 Warning Alert Limit",
    INA3221Reg.SHUNT_VOLTAGE_SUM:    "Shunt Voltage Sum",
    INA3221Reg.SHUNT_VOLTAGE_SUM_LIMIT: "Shunt Voltage Sum Limit",
    INA3221Reg.MASK_ENABLE:          "Mask/Enable Register",
    INA3221Reg.PWR_VALID_UPPER:      "Power Valid Upper Limit",
    INA3221Reg.PWR_VALID_LOWER:      "Power Valid Lower Limit",
    INA3221Reg.MANUFACTURER_ID:      "Manufacturer ID (0x5449)",
    INA3221Reg.DIE_ID:               "Die ID (0x3220)",
}

DISPLAY_REGISTERS: List[INA3221Reg] = [
    INA3221Reg.CONFIG,
    INA3221Reg.SHUNT_VOLTAGE_1,
    INA3221Reg.BUS_VOLTAGE_1,
    INA3221Reg.SHUNT_VOLTAGE_2,
    INA3221Reg.BUS_VOLTAGE_2,
    INA3221Reg.SHUNT_VOLTAGE_3,
    INA3221Reg.BUS_VOLTAGE_3,
    INA3221Reg.CRITICAL_ALERT_1,
    INA3221Reg.WARNING_ALERT_1,
    INA3221Reg.CRITICAL_ALERT_2,
    INA3221Reg.WARNING_ALERT_2,
    INA3221Reg.CRITICAL_ALERT_3,
    INA3221Reg.WARNING_ALERT_3,
    INA3221Reg.SHUNT_VOLTAGE_SUM,
    INA3221Reg.SHUNT_VOLTAGE_SUM_LIMIT,
    INA3221Reg.MASK_ENABLE,
    INA3221Reg.PWR_VALID_UPPER,
    INA3221Reg.PWR_VALID_LOWER,
    INA3221Reg.MANUFACTURER_ID,
    INA3221Reg.DIE_ID,
]

AVG_OPTIONS: Dict[int, str] = {
    0: "1", 1: "4", 2: "16", 3: "64",
    4: "128", 5: "256", 6: "512", 7: "1024"
}

CT_OPTIONS: Dict[int, str] = {
    0: "140 us", 1: "204 us", 2: "332 us", 3: "588 us",
    4: "1.1 ms", 5: "2.116 ms", 6: "4.156 ms", 7: "8.244 ms"
}

OP_MODE_OPTIONS: Dict[int, str] = {
    0: "Power-Down",
    1: "Shunt Voltage, Triggered",
    2: "Bus Voltage, Triggered",
    3: "Shunt and Bus, Triggered",
    4: "Power-Down",
    5: "Shunt Voltage, Continuous",
    6: "Bus Voltage, Continuous",
    7: "Shunt and Bus, Continuous",
}

class INA3221Conversion:
    @staticmethod
    def raw16_to_signed(raw: int) -> int:
        if raw > 32767:
            return raw - 65536
        return raw

    @staticmethod
    def raw_to_bus_voltage_v(raw_16bit: int) -> float:
        # Bus voltage is unsigned (0-26V range). D15-D3 data, D2-D0 reserved.
        # LSB = 8mV; (raw >> 3) * 0.008 == raw * 0.001
        return float(raw_16bit & 0xFFFF) * 0.001

    @staticmethod
    def raw_to_shunt_voltage_mv(raw_16bit: int) -> float:
        signed = INA3221Conversion.raw16_to_signed(raw_16bit)
        return float(signed) * 0.005

    @staticmethod
    def calculate_current_ma(vshunt_mv: float, shunt_resistor_ohm: float) -> float:
        if shunt_resistor_ohm <= 0:
            return 0.0
        return round(vshunt_mv / shunt_resistor_ohm, 3)

