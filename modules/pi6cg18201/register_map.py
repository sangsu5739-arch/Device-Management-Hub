"""
PI6CG18201 클럭 제너레이터 - 레지스터 맵 정의 모듈

데이터시트(DS39996 Rev 6-2) 기준 8바이트 레지스터 구조를 정의합니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal


# SMBus 7-bit 주소는 SADR 핀 상태에 따라 0x68 또는 0x6A
SLAVE_ADDRESS_7BIT_SADR_LOW: int = 0x68
SLAVE_ADDRESS_7BIT_SADR_HIGH: int = 0x6A

TOTAL_BYTES: int = 8

# 데이터시트 power-up 기본값(참고용)
DEFAULT_REGISTER_BYTES: bytes = bytes([0xFF, 0x16, 0xFF, 0x5F, 0xFF, 0x03, 0x02, 0x08])


@dataclass(frozen=True)
class BitField:
    name: str
    description: str
    byte_index: int
    bit_high: int
    bit_low: int
    options: Dict[int, str] = field(default_factory=dict)
    read_only: bool = False

    @property
    def mask(self) -> int:
        width = self.bit_high - self.bit_low + 1
        return ((1 << width) - 1) << self.bit_low

    @property
    def width(self) -> int:
        return self.bit_high - self.bit_low + 1

    @property
    def bit_range_str(self) -> str:
        if self.bit_high == self.bit_low:
            return f"[{self.bit_high}]"
        return f"[{self.bit_high}:{self.bit_low}]"


REGISTER_FIELDS: List[BitField] = [
    # Byte 0
    BitField("BYTE0_RSVD_7_3", "Byte0 예약 비트 [7:3] (변경 금지)", 0, 7, 3, read_only=True),
    BitField("OE_Q1", "Q1 출력 활성화 (Q1+/Q1-)", 0, 2, 2, {0: "Disabled", 1: "Enabled"}),
    BitField("OE_Q0", "Q0 출력 활성화 (Q0+/Q0-)", 0, 1, 1, {0: "Disabled", 1: "Enabled"}),
    BitField("BYTE0_RSVD_0", "Byte0 예약 비트 [0] (변경 금지)", 0, 0, 0, read_only=True),
    # Byte 1
    BitField(
        "SS_READBACK",
        "Spread Spectrum 핀 상태 Readback [7:6]",
        1,
        7,
        6,
        {0: "SS Off", 1: "-0.25%", 3: "-0.5%"},
        read_only=True,
    ),
    BitField(
        "SS_SW_CTRL",
        "Spread Spectrum SW 제어 선택 [5] (0=Pin, 1=SW)",
        1,
        5,
        5,
        {0: "Pin/Strap", 1: "Software"},
    ),
    BitField(
        "SS_MODE",
        "Spread Spectrum SW 모드 [4:3]",
        1,
        4,
        3,
        {0: "SS Off", 1: "-0.25%", 2: "Reserved", 3: "-0.5%"},
    ),
    BitField("BYTE1_RSVD_2", "Byte1 예약 비트 [2] (변경 금지)", 1, 2, 2, read_only=True),
    BitField(
        "AMPLITUDE",
        "출력 진폭 선택 [1:0]",
        1,
        1,
        0,
        {0: "0.6 V", 1: "0.7 V", 2: "0.8 V", 3: "0.9 V"},
    ),
    # Byte 2
    BitField("BYTE2_RSVD_7_3", "Byte2 예약 비트 [7:3] (변경 금지)", 2, 7, 3, read_only=True),
    BitField("SLEW_Q1", "Q1 Slew Rate [2] (0=Slow, 1=Fast)", 2, 2, 2, {0: "Slow", 1: "Fast"}),
    BitField("SLEW_Q0", "Q0 Slew Rate [1] (0=Slow, 1=Fast)", 2, 1, 1, {0: "Slow", 1: "Fast"}),
    BitField("BYTE2_RSVD_0", "Byte2 예약 비트 [0] (변경 금지)", 2, 0, 0, read_only=True),
    # Byte 3
    BitField(
        "REF_SLEW",
        "REF slew rate [7:6]",
        3,
        7,
        6,
        {0: "0.6 V/ns", 1: "1.3 V/ns", 2: "2.0 V/ns", 3: "3.0 V/ns"},
    ),
    BitField(
        "REF_PDSTATE",
        "REF power-down state [5] (0=Low, 1=Running)",
        3,
        5,
        5,
        {0: "Low", 1: "Running"},
    ),
    BitField("REF_OE", "REF 출력 활성화 [4]", 3, 4, 4, {0: "Disabled", 1: "Enabled"}),
    BitField("BYTE3_RSVD_3_0", "Byte3 예약 비트 [3:0] (변경 금지)", 3, 3, 0, read_only=True),
    # Byte 4~6
    BitField("BYTE4_RSVD", "Byte4 예약 비트 [7:0] (변경 금지)", 4, 7, 0, read_only=True),
    BitField("DEV_ID_HIGH", "Device ID 상위 바이트", 5, 7, 0, read_only=True),
    BitField("DEV_ID_LOW", "Device ID 하위 바이트", 6, 7, 0, read_only=True),
    # Byte 7
    BitField("BYTE7_RSVD_7_5", "Byte7 예약 비트 [7:5] (변경 금지)", 7, 7, 5, read_only=True),
    BitField("BYTE_COUNT", "Read-back 바이트 카운트 [4:0]", 7, 4, 0),
]

FIELD_BY_NAME: Dict[str, BitField] = {f.name: f for f in REGISTER_FIELDS}
EDITABLE_FIELDS: List[BitField] = [f for f in REGISTER_FIELDS if not f.read_only]


class RegisterMap(QObject):
    register_changed = Signal(int, int)  # byte_index, new_value
    full_map_changed = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._data: bytearray = bytearray(DEFAULT_REGISTER_BYTES)

    def get_byte(self, index: int) -> int:
        if not 0 <= index < TOTAL_BYTES:
            raise IndexError(f"바이트 인덱스 범위 초과: {index}")
        return self._data[index]

    def set_byte(self, index: int, value: int, emit: bool = True) -> None:
        if not 0 <= index < TOTAL_BYTES:
            raise IndexError(f"바이트 인덱스 범위 초과: {index}")
        value &= 0xFF
        if self._data[index] != value:
            self._data[index] = value
            if emit:
                self.register_changed.emit(index, value)

    def get_all_bytes(self) -> bytes:
        return bytes(self._data)

    def set_all_bytes(self, data: bytes, emit: bool = True) -> None:
        if len(data) != TOTAL_BYTES:
            raise ValueError(f"데이터 길이 불일치: {len(data)} (expected {TOTAL_BYTES})")
        self._data = bytearray(data)
        if emit:
            self.full_map_changed.emit()

    def get_field(self, field_name: str) -> int:
        bf = FIELD_BY_NAME[field_name]
        raw = self._data[bf.byte_index]
        return (raw & bf.mask) >> bf.bit_low

    def set_field(self, field_name: str, value: int, emit: bool = True) -> None:
        bf = FIELD_BY_NAME[field_name]
        if bf.read_only:
            raise PermissionError(f"읽기 전용 필드: {field_name}")
        max_val = (1 << bf.width) - 1
        if not 0 <= value <= max_val:
            raise ValueError(f"값 범위 초과: {value} (max {max_val})")
        raw = self._data[bf.byte_index]
        raw = (raw & ~bf.mask) | ((value << bf.bit_low) & bf.mask)
        self.set_byte(bf.byte_index, raw, emit=emit)

    @property
    def oe_q0(self) -> bool:
        return bool(self.get_field("OE_Q0"))

    @oe_q0.setter
    def oe_q0(self, enabled: bool) -> None:
        self.set_field("OE_Q0", int(enabled))

    @property
    def oe_q1(self) -> bool:
        return bool(self.get_field("OE_Q1"))

    @oe_q1.setter
    def oe_q1(self, enabled: bool) -> None:
        self.set_field("OE_Q1", int(enabled))

    @property
    def amplitude(self) -> int:
        return self.get_field("AMPLITUDE")

    @amplitude.setter
    def amplitude(self, value: int) -> None:
        self.set_field("AMPLITUDE", value)

    @property
    def amplitude_voltage(self) -> float:
        return [0.6, 0.7, 0.8, 0.9][self.amplitude]

    @property
    def spread_spectrum(self) -> int:
        # 호환성: 0=Pin/Strap, 1=-0.25%(SW), 2=Reserved(SW), 3=-0.5%(SW)
        if self.get_field("SS_SW_CTRL") == 0:
            return 0
        return self.get_field("SS_MODE")

    @spread_spectrum.setter
    def spread_spectrum(self, value: int) -> None:
        if value == 0:
            self.set_field("SS_SW_CTRL", 0, emit=False)
        else:
            self.set_field("SS_SW_CTRL", 1, emit=False)
            self.set_field("SS_MODE", value, emit=False)

    @property
    def slew_rate_coarse(self) -> int:
        # 호환성: [Q1:Q0] 2-bit 합성값
        q0 = self.get_field("SLEW_Q0")
        q1 = self.get_field("SLEW_Q1")
        return (q1 << 1) | q0

    @slew_rate_coarse.setter
    def slew_rate_coarse(self, value: int) -> None:
        if not 0 <= value <= 3:
            raise ValueError(f"값 범위 초과: {value} (max 3)")
        self.set_field("SLEW_Q0", value & 0x1, emit=False)
        self.set_field("SLEW_Q1", (value >> 1) & 0x1, emit=False)

    @property
    def slew_rate_fine(self) -> int:
        return self.get_field("REF_SLEW")

    @slew_rate_fine.setter
    def slew_rate_fine(self, value: int) -> None:
        self.set_field("REF_SLEW", value, emit=False)

    @property
    def slew_rate_combined(self) -> int:
        return (self.slew_rate_coarse << 2) | self.slew_rate_fine

    @property
    def device_id(self) -> int:
        return (self._data[5] << 8) | self._data[6]

    def get_hex_string(self, byte_index: int) -> str:
        return f"0x{self._data[byte_index]:02X}"

    def get_bin_string(self, byte_index: int) -> str:
        return f"{self._data[byte_index]:08b}"

    def __repr__(self) -> str:
        hex_str = " ".join(f"{b:02X}" for b in self._data)
        return f"RegisterMap([{hex_str}])"
