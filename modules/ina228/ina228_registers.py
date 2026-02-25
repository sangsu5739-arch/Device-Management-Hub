"""
INA228 레지스터 정의 및 변환 유틸리티

참조: D:/rfsw_repo/trunk/N_COMMON/2026/ADC/INA228/ina228.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional


class INA228Reg(IntEnum):
    """INA228 레지스터 주소"""
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


# 레지스터별 바이트 크기 (읽기 크기)
REGISTER_SIZE: Dict[INA228Reg, int] = {
    INA228Reg.CONFIG:          2,
    INA228Reg.ADC_CONFIG:      2,
    INA228Reg.SHUNT_CAL:       2,
    INA228Reg.SHUNT_TEMPCO:    2,
    INA228Reg.VSHUNT:          3,  # 24-bit, 상위 20비트 사용
    INA228Reg.VBUS:            3,  # 24-bit, 상위 20비트 사용
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

# 레지스터 이름 매핑 (UI 표시용)
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
    INA228Reg.CONFIG:          "설정 레지스터 (RESET, RSTACC, AVG, ADCRANGE)",
    INA228Reg.ADC_CONFIG:      "ADC 구성 (MODE, VBUSCT, VSHCT, VTCT, AVG)",
    INA228Reg.SHUNT_CAL:       "Shunt 보정 값",
    INA228Reg.SHUNT_TEMPCO:    "Shunt 온도 계수",
    INA228Reg.VSHUNT:          "Shunt 전압 측정값 (20비트 부호있는)",
    INA228Reg.VBUS:            "버스 전압 측정값 (20비트 부호없는)",
    INA228Reg.DIETEMP:         "다이 온도 측정값",
    INA228Reg.CURRENT:         "계산된 전류값",
    INA228Reg.POWER:           "계산된 전력값",
    INA228Reg.ENERGY:          "에너지 누적값 (40비트)",
    INA228Reg.CHARGE:          "충전량 누적값 (40비트)",
    INA228Reg.DIAG_ALRT:       "진단 / 알림 (CNVRF 등)",
    INA228Reg.SOVL:            "Shunt 과전압 한계",
    INA228Reg.SUVL:            "Shunt 저전압 한계",
    INA228Reg.BOVL:            "버스 과전압 한계",
    INA228Reg.BUVL:            "버스 저전압 한계",
    INA228Reg.TEMP_LIMIT:      "온도 한계",
    INA228Reg.PWR_LIMIT:       "전력 한계",
    INA228Reg.MANUFACTURER_ID: "제조사 ID (TI: 0x5449)",
    INA228Reg.DEVICE_ID:       "디바이스 ID (INA228: 0x228x)",
}

# UI 드롭다운 옵션
ADC_RANGE_OPTIONS: Dict[int, str] = {
    0: "+/-163.84 mV (LSB=312.5 nV)",
    1: "+/-40.96 mV (LSB=78.125 nV)",
}

AVG_COUNT_OPTIONS: Dict[int, str] = {
    0: "1 (없음)",
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

# ADC_CONFIG 기본값: 연속 모드, 540us 변환, AVG=16
ADC_CONFIG_DEFAULT = 0xFB68


@dataclass(frozen=True)
class INA228BitField:
    """INA228 레지스터의 단일 비트 필드 정의"""
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


# 주요 비트 필드 정의 (레지스터 맵 테이블 표시용)
INA228_REGISTER_FIELDS: List[INA228BitField] = [
    # CONFIG (0x00)
    INA228BitField("RST",     "소프트웨어 리셋",        INA228Reg.CONFIG,     15, 15, {0: "Normal", 1: "Reset"}),
    INA228BitField("RSTACC",  "누적 레지스터 리셋",     INA228Reg.CONFIG,     14, 14, {0: "Normal", 1: "Reset Acc"}),
    INA228BitField("CONVDLY", "변환 지연 (2ms 단위)",   INA228Reg.CONFIG,     13,  6),
    INA228BitField("TEMPCOMP","온도 보상",               INA228Reg.CONFIG,      5,  5, {0: "Disable", 1: "Enable"}),
    INA228BitField("ADCRANGE","ADC 범위 선택",           INA228Reg.CONFIG,      4,  4, ADC_RANGE_OPTIONS),
    # ADC_CONFIG (0x01)
    INA228BitField("MODE",    "동작 모드",               INA228Reg.ADC_CONFIG, 15, 12),
    INA228BitField("VBUSCT",  "버스 전압 변환 시간",    INA228Reg.ADC_CONFIG, 11,  9, CONV_TIME_OPTIONS),
    INA228BitField("VSHCT",   "Shunt 전압 변환 시간",   INA228Reg.ADC_CONFIG,  8,  6, CONV_TIME_OPTIONS),
    INA228BitField("VTCT",    "온도 변환 시간",          INA228Reg.ADC_CONFIG,  5,  3, CONV_TIME_OPTIONS),
    INA228BitField("AVG",     "평균화 샘플 수",          INA228Reg.ADC_CONFIG,  2,  0, AVG_COUNT_OPTIONS),
    # DIAG_ALRT (0x0B)
    INA228BitField("ALATCH",  "알림 래치",               INA228Reg.DIAG_ALRT,  15, 15),
    INA228BitField("CNVR",    "변환 완료 알림 활성화",  INA228Reg.DIAG_ALRT,  14, 14),
    INA228BitField("POL",     "알림 폴라리티",           INA228Reg.DIAG_ALRT,  13, 13),
    INA228BitField("SLOWALERT","SLOWALERT 핀 선택",      INA228Reg.DIAG_ALRT,  12, 12),
    INA228BitField("APOL",    "알림 핀 폴라리티",        INA228Reg.DIAG_ALRT,  11, 11),
    INA228BitField("ENERGYOF","에너지 누적 오버플로",   INA228Reg.DIAG_ALRT,   9,  9, read_only=True),
    INA228BitField("CHARGEOF","충전 누적 오버플로",      INA228Reg.DIAG_ALRT,   8,  8, read_only=True),
    INA228BitField("MATHOF",  "수학 오버플로",           INA228Reg.DIAG_ALRT,   7,  7, read_only=True),
    INA228BitField("TMPOL",   "온도 한계 초과",          INA228Reg.DIAG_ALRT,   6,  6, read_only=True),
    INA228BitField("SHNTOL",  "Shunt 전압 한계 초과",   INA228Reg.DIAG_ALRT,   5,  5, read_only=True),
    INA228BitField("SHNTUL",  "Shunt 전압 한계 미만",   INA228Reg.DIAG_ALRT,   4,  4, read_only=True),
    INA228BitField("BUSOL",   "버스 전압 한계 초과",    INA228Reg.DIAG_ALRT,   3,  3, read_only=True),
    INA228BitField("BUSUL",   "버스 전압 한계 미만",    INA228Reg.DIAG_ALRT,   2,  2, read_only=True),
    INA228BitField("POL_FLAG","전력 한계 초과",          INA228Reg.DIAG_ALRT,   1,  1, read_only=True),
    INA228BitField("CNVRF",   "변환 완료 플래그",       INA228Reg.DIAG_ALRT,   0,  0,
                   {0: "미완료", 1: "완료"}, read_only=True),
]

INA228_FIELD_BY_NAME: Dict[str, INA228BitField] = {f.name: f for f in INA228_REGISTER_FIELDS}

# 레지스터 맵 테이블에 표시할 레지스터 목록 (읽기/쓰기 가능한 것 위주)
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
    """INA228 원시값 → 물리량 변환 유틸리티 (정적 메서드)

    참조: INA228Controller.get_shunt_voltage(), get_bus_voltage()
    """

    @staticmethod
    def raw20_to_signed(raw: int) -> int:
        """20비트 원시값을 부호있는 정수로 변환 (2의 보수)

        Args:
            raw: 20비트 원시값 (0x00000 ~ 0xFFFFF)

        Returns:
            부호있는 정수 값
        """
        if raw & 0x80000:
            return -((~raw & 0xFFFFF) + 1)
        return raw

    @staticmethod
    def raw_to_shunt_voltage_mv(raw_20bit: int, adc_range: int) -> float:
        """20비트 원시 Shunt 전압값을 mV로 변환

        Args:
            raw_20bit: 20비트 부호있는 값 (VSHUNT 레지스터 >> 4)
            adc_range: 0 = +/-163.84mV (LSB=312.5nV), 1 = +/-40.96mV (LSB=78.125nV)

        Returns:
            Shunt 전압 (mV)
        """
        signed = INA228Conversion.raw20_to_signed(raw_20bit)
        if adc_range == 1:
            voltage_v = signed * 78.125e-9
        else:
            voltage_v = signed * 312.5e-9
        return round(voltage_v * 1000.0, 6)

    @staticmethod
    def raw_to_bus_voltage_v(raw_20bit: int) -> float:
        """20비트 원시 Bus 전압값을 Volt로 변환

        LSB = 195.3125 uV

        Args:
            raw_20bit: 20비트 값 (VBUS 레지스터 >> 4)

        Returns:
            버스 전압 (V)
        """
        signed = INA228Conversion.raw20_to_signed(raw_20bit)
        return round(signed * 195.3125e-6, 6)

    @staticmethod
    def raw_to_temperature_c(raw_16bit: int) -> float:
        """16비트 원시 온도값을 섭씨로 변환

        LSB = 7.8125 m°C

        Args:
            raw_16bit: 16비트 값 (DIETEMP 레지스터)

        Returns:
            온도 (°C)
        """
        # 부호있는 16비트 → 섭씨
        if raw_16bit & 0x8000:
            raw_16bit = -((~raw_16bit & 0xFFFF) + 1)
        return round(raw_16bit * 7.8125e-3, 3)

    @staticmethod
    def calculate_current_ma(vshunt_mv: float, shunt_resistor_ohm: float) -> float:
        """I = V/R 로 전류 계산

        Args:
            vshunt_mv: Shunt 전압 (mV)
            shunt_resistor_ohm: Shunt 저항값 (Ω)

        Returns:
            전류 (mA)
        """
        if shunt_resistor_ohm <= 0:
            return 0.0
        return round(vshunt_mv / shunt_resistor_ohm, 6)

    @staticmethod
    def calculate_power_mw(vbus_v: float, current_ma: float) -> float:
        """P = V × I 로 전력 계산

        Args:
            vbus_v: 버스 전압 (V)
            current_ma: 전류 (mA)

        Returns:
            전력 (mW)
        """
        return round(vbus_v * current_ma, 6)
