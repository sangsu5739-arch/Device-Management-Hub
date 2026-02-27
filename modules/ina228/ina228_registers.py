"""
INA228 register definitions and conversion utilities

Ref: D:/rfsw_repo/trunk/N_COMMON/2026/ADC/INA228/ina228.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional


class INA228Reg(IntEnum):
    """INA228 register addresses."""
    CONFIG          = 0x00
    ADC_CONFIG      = 0x01
    SHUNT_CAL       = 0x02
    SHUNT_TEMPCO    = 0x03
    VSHUNT          = 0x04
    VBUS            = 0x05
    DIETEMP         = 0x06
    CURRENT         = 0x07
    POWER           = 0x08
    ENERGY          = 0x09
    CHARGE          = 0x0A
    DIAG_ALRT       = 0x0B
    SOVL            = 0x0C
    SUVL            = 0x0D
    BOVL            = 0x0E
    BUVL            = 0x0F
    TEMP_LIMIT      = 0x10
    PWR_LIMIT       = 0x11
    MANUFACTURER_ID = 0x3E
    DEVICE_ID       = 0x3F


# Register byte sizes (read length)
REGISTER_SIZE: Dict[INA228Reg, int] = {
    INA228Reg.CONFIG:          2,
    INA228Reg.ADC_CONFIG:      2,
    INA228Reg.SHUNT_CAL:       2,
    INA228Reg.SHUNT_TEMPCO:    2,
    INA228Reg.VSHUNT:          3,  # 24-bit, use upper 20 bits
    INA228Reg.VBUS:            3,  # 24-bit, use upper 20 bits
    INA228Reg.DIETEMP:         2,
    INA228Reg.CURRENT:         3,
    INA228Reg.POWER:           3,
    INA228Reg.ENERGY:          5,
    INA228Reg.CHARGE:          5,
    INA228Reg.DIAG_ALRT:       2,
    INA228Reg.SOVL:            2,
    INA228Reg.SUVL:            2,
    INA228Reg.BOVL:            2,
    INA228Reg.BUVL:            2,
    INA228Reg.TEMP_LIMIT:      2,
    INA228Reg.PWR_LIMIT:       2,
    INA228Reg.MANUFACTURER_ID: 2,
    INA228Reg.DEVICE_ID:       2,
}

# Register name mapping (UI)
REGISTER_NAMES: Dict[INA228Reg, str] = {
    INA228Reg.CONFIG:          "CONFIG",
    INA228Reg.ADC_CONFIG:      "ADC_CONFIG",
    INA228Reg.SHUNT_CAL:       "SHUNT_CAL",
    INA228Reg.SHUNT_TEMPCO:    "SHUNT_TEMPCO",
    INA228Reg.VSHUNT:          "VSHUNT",
    INA228Reg.VBUS:            "VBUS",
    INA228Reg.DIETEMP:         "DIETEMP",
    INA228Reg.CURRENT:         "CURRENT",
    INA228Reg.POWER:           "POWER",
    INA228Reg.ENERGY:          "ENERGY",
    INA228Reg.CHARGE:          "CHARGE",
    INA228Reg.DIAG_ALRT:       "DIAG_ALRT",
    INA228Reg.SOVL:            "SOVL",
    INA228Reg.SUVL:            "SUVL",
    INA228Reg.BOVL:            "BOVL",
    INA228Reg.BUVL:            "BUVL",
    INA228Reg.TEMP_LIMIT:      "TEMP_LIMIT",
    INA228Reg.PWR_LIMIT:       "PWR_LIMIT",
    INA228Reg.MANUFACTURER_ID: "MANUFACTURER_ID",
    INA228Reg.DEVICE_ID:       "DEVICE_ID",
}

REGISTER_DESCRIPTIONS: Dict[INA228Reg, str] = {
    INA228Reg.CONFIG:          "Config register (RESET, RSTACC, AVG, ADCRANGE)",
    INA228Reg.ADC_CONFIG:      "ADC config (MODE, VBUSCT, VSHCT, VTCT, AVG)",
    INA228Reg.SHUNT_CAL:       "Shunt calibration value",
    INA228Reg.SHUNT_TEMPCO:    "Shunt temperature coefficient",
    INA228Reg.VSHUNT:          "Shunt voltage measurement (20-bit signed)",
    INA228Reg.VBUS:            "Bus voltage measurement (20-bit unsigned)",
    INA228Reg.DIETEMP:         "Die temperature measurement",
    INA228Reg.CURRENT:         "Calculated current",
    INA228Reg.POWER:           "Calculated power",
    INA228Reg.ENERGY:          "Energy accumulator (40-bit)",
    INA228Reg.CHARGE:          "Charge accumulator (40-bit)",
    INA228Reg.DIAG_ALRT:       "Diagnostics/alert (CNVRF, etc)",
    INA228Reg.SOVL:            "Shunt overvoltage limit",
    INA228Reg.SUVL:            "Shunt undervoltage limit",
    INA228Reg.BOVL:            "Bus overvoltage limit",
    INA228Reg.BUVL:            "Bus undervoltage limit",
    INA228Reg.TEMP_LIMIT:      "Temperature limit",
    INA228Reg.PWR_LIMIT:       "Power limit",
    INA228Reg.MANUFACTURER_ID: "Manufacturer ID (TI: 0x5449)",
    INA228Reg.DEVICE_ID:       "Device ID (INA228: 0x228x)",
}

# UI dropdown options
ADC_RANGE_OPTIONS: Dict[int, str] = {
    0: "+/-163.84 mV (LSB=312.5 nV)",
    1: "+/-40.96 mV (LSB=78.125 nV)",
}

AVG_COUNT_OPTIONS: Dict[int, str] = {
    0: "1 (None)",
    1: "4",
    2: "16",
    3: "64",
    4: "128",
    5: "256",
    6: "512",
    7: "1024",
}

CONV_TIME_OPTIONS: Dict[int, str] = {
    0: "50 us",
    1: "84 us",
    2: "150 us",
    3: "280 us",
    4: "540 us",
    5: "1052 us",
    6: "2074 us",
    7: "4120 us",
}

# ADC_CONFIG default: continuous mode, 540us conversion, AVG=16
ADC_CONFIG_DEFAULT = 0xFB68


@dataclass(frozen=True)
class INA228BitField:
    """Single bit-field definition for INA228 registers."""
    name: str
    description: str
    register: INA228Reg
    bit_high: int
    bit_low: int
    options: Dict[int, str] = field(default_factory=dict)
    read_only: bool = False

    @property
    def width(self) -> int:
        return self.bit_high - self.bit_low + 1

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.bit_low

    @property
    def bit_range_str(self) -> str:
        if self.bit_high == self.bit_low:
            return f"[{self.bit_high}]"
        return f"[{self.bit_high}:{self.bit_low}]"


# Key bit-field definitions (for register map table)
INA228_REGISTER_FIELDS: List[INA228BitField] = [
    # CONFIG (0x00)
    INA228BitField("RST",     "Software reset",        INA228Reg.CONFIG,     15, 15, {0: "Normal", 1: "Reset"}),
    INA228BitField("RSTACC",  "Accumulator reset",     INA228Reg.CONFIG,     14, 14, {0: "Normal", 1: "Reset Acc"}),
    INA228BitField("CONVDLY", "Conversion delay (2ms steps)",   INA228Reg.CONFIG,     13,  6),
    INA228BitField("TEMPCOMP","Temperature compensation",               INA228Reg.CONFIG,      5,  5, {0: "Disable", 1: "Enable"}),
    INA228BitField("ADCRANGE","ADC range select",           INA228Reg.CONFIG,      4,  4, ADC_RANGE_OPTIONS),
    # ADC_CONFIG (0x01)
    INA228BitField("MODE",    "Operating mode",               INA228Reg.ADC_CONFIG, 15, 12),
    INA228BitField("VBUSCT",  "Bus voltage conversion time",    INA228Reg.ADC_CONFIG, 11,  9, CONV_TIME_OPTIONS),
    INA228BitField("VSHCT",   "Shunt voltage conversion time",   INA228Reg.ADC_CONFIG,  8,  6, CONV_TIME_OPTIONS),
    INA228BitField("VTCT",    "Temperature conversion time",          INA228Reg.ADC_CONFIG,  5,  3, CONV_TIME_OPTIONS),
    INA228BitField("AVG",     "Averaging sample count",          INA228Reg.ADC_CONFIG,  2,  0, AVG_COUNT_OPTIONS),
    # DIAG_ALRT (0x0B)
    INA228BitField("ALATCH",  "Alert latch",               INA228Reg.DIAG_ALRT,  15, 15),
    INA228BitField("CNVR",    "Conversion ready alert enable",  INA228Reg.DIAG_ALRT,  14, 14),
    INA228BitField("POL",     "Alert polarity",           INA228Reg.DIAG_ALRT,  13, 13),
    INA228BitField("SLOWALERT","SLOWALERT pin select",      INA228Reg.DIAG_ALRT,  12, 12),
    INA228BitField("APOL",    "Alert pin polarity",        INA228Reg.DIAG_ALRT,  11, 11),
    INA228BitField("ENERGYOF","Energy accumulation overflow",   INA228Reg.DIAG_ALRT,   9,  9, read_only=True),
    INA228BitField("CHARGEOF","Charge accumulation overflow",      INA228Reg.DIAG_ALRT,   8,  8, read_only=True),
    INA228BitField("MATHOF",  "Math overflow",           INA228Reg.DIAG_ALRT,   7,  7, read_only=True),
    INA228BitField("TMPOL",   "Temperature limit exceeded",          INA228Reg.DIAG_ALRT,   6,  6, read_only=True),
    INA228BitField("SHNTOL",  "Shunt voltage limit exceeded",   INA228Reg.DIAG_ALRT,   5,  5, read_only=True),
    INA228BitField("SHNTUL",  "Shunt voltage below limit",   INA228Reg.DIAG_ALRT,   4,  4, read_only=True),
    INA228BitField("BUSOL",   "Bus voltage limit exceeded",    INA228Reg.DIAG_ALRT,   3,  3, read_only=True),
    INA228BitField("BUSUL",   "Bus voltage below limit",    INA228Reg.DIAG_ALRT,   2,  2, read_only=True),
    INA228BitField("POL_FLAG","Power limit exceeded",          INA228Reg.DIAG_ALRT,   1,  1, read_only=True),
    INA228BitField("CNVRF",   "Conversion ready flag",       INA228Reg.DIAG_ALRT,   0,  0,
                   {0: "Not ready", 1: "Ready"}, read_only=True),
]

INA228_FIELD_BY_NAME: Dict[str, INA228BitField] = {f.name: f for f in INA228_REGISTER_FIELDS}

# Register list for register map table (mostly R/W)
DISPLAY_REGISTERS: List[INA228Reg] = [
    INA228Reg.CONFIG,
    INA228Reg.ADC_CONFIG,
    INA228Reg.SHUNT_CAL,
    INA228Reg.VSHUNT,
    INA228Reg.VBUS,
    INA228Reg.DIETEMP,
    INA228Reg.CURRENT,
    INA228Reg.POWER,
    INA228Reg.DIAG_ALRT,
    INA228Reg.MANUFACTURER_ID,
    INA228Reg.DEVICE_ID,
]


class INA228Conversion:
    """INA228 raw-to-physical conversion utilities (static methods).

    Ref: INA228Controller.get_shunt_voltage(), get_bus_voltage()
    """

    @staticmethod
    def raw20_to_signed(raw: int) -> int:
        """Convert 20-bit raw to signed int (two's complement).

        Args:
            raw: 20-bit raw (0x00000 ~ 0xFFFFF)

        Returns:
            Signed integer value
        """
        if raw & 0x80000:
            return -((~raw & 0xFFFFF) + 1)
        return raw

    @staticmethod
    def raw_to_shunt_voltage_mv(raw_20bit: int, adc_range: int) -> float:
        """Convert 20-bit raw shunt voltage to mV.

        Args:
            raw_20bit: 20-bit signed value (VSHUNT register >> 4)
            adc_range: 0 = +/-163.84mV (LSB=312.5nV), 1 = +/-40.96mV (LSB=78.125nV)

        Returns:
            Shunt voltage (mV)
        """
        signed = INA228Conversion.raw20_to_signed(raw_20bit)
        if adc_range == 1:
            voltage_v = signed * 78.125e-9
        else:
            voltage_v = signed * 312.5e-9
        return round(voltage_v * 1000.0, 6)

    @staticmethod
    def raw_to_bus_voltage_v(raw_20bit: int) -> float:
        """Convert 20-bit raw bus voltage to volts.

        LSB = 7.8125 mC

        Args:
            raw_20bit: 20-bit value (VBUS register >> 4)

        Returns:
            Bus voltage (V)
        """
        signed = INA228Conversion.raw20_to_signed(raw_20bit)
        return round(signed * 195.3125e-6, 6)

    @staticmethod
    def raw_to_temperature_c(raw_16bit: int) -> float:
        """Convert 16-bit raw temperature to Celsius.

        LSB = 7.8125 mC

        Args:
            raw_16bit: 16-bit value (DIETEMP register)

        Returns:
            Temperature (C)
        """
        # Signed 16-bit -> Celsius
        if raw_16bit & 0x8000:
            raw_16bit = -((~raw_16bit & 0xFFFF) + 1)
        return round(raw_16bit * 7.8125e-3, 3)

    @staticmethod
    def calculate_current_ma(vshunt_mv: float, shunt_resistor_ohm: float) -> float:
        """Compute current: I = V/R.

        Args:
            vshunt_mv: Shunt voltage (mV)
            shunt_resistor_ohm: Shunt resistance (Ohm)

        Returns:
            Current (mA)
        """
        if shunt_resistor_ohm <= 0:
            return 0.0
        return round(vshunt_mv / shunt_resistor_ohm, 6)

    @staticmethod
    def calculate_power_mw(vbus_v: float, current_ma: float) -> float:
        """Compute power: P = V x I.

        Args:
            vbus_v: Bus voltage (V)
            current_ma: Current (mA)

        Returns:
            Power (mW)
        """
        return round(vbus_v * current_ma, 6)
