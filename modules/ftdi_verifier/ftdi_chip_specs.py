"""
FTDI chipset spec definitions - FT232H / FT2232H / FT4232H

Pin map, channel capability, and protocol support are stored as class constants.
Avoids hardcoding and keeps extensibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple


# -- Pin function enum --

class PinFunction(Enum):
    """Pin supported function types."""
    GPIO_OUT = auto()
    GPIO_IN = auto()
    I2C_SCL = auto()
    I2C_SDA_OUT = auto()
    I2C_SDA_IN = auto()
    SPI_SCK = auto()
    SPI_MOSI = auto()
    SPI_MISO = auto()
    SPI_CS = auto()
    JTAG_TCK = auto()
    JTAG_TDI = auto()
    JTAG_TDO = auto()
    JTAG_TMS = auto()
    UART_TX = auto()
    UART_RX = auto()
    UART_RTS = auto()
    UART_CTS = auto()
    UART_DTR = auto()
    UART_DSR = auto()
    UART_DCD = auto()
    UART_RI = auto()
    POWER = auto()
    GROUND = auto()
    NC = auto()       # No Connect
    SPECIAL = auto()  # Other (RESET, EECS, etc)


class PinDirection(Enum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


class ProtocolMode(Enum):
    I2C = "I2C"
    SPI = "SPI"
    JTAG = "JTAG"
    UART = "UART"
    GPIO = "GPIO"
    BITBANG = "Bit-Bang"


# -- Pin data structure --

@dataclass
class PinSpec:
    """Pin spec."""
    number: int                     # Physical pin number
    name: str                       # Pin name (AD0, BD7, etc)
    direction: PinDirection         # Package side orientation
    functions: List[PinFunction]    # Supported functions
    default_function: PinFunction   # Default function
    channel: str = ""               # Channel (A/B/C/D or "")
    mpsse_bit: int = -1             # MPSSE bit index (0-7, for GPIO)
    description: str = ""           # Function description


@dataclass
class ChannelSpec:
    """Spec for a chip channel."""
    name: str                                 # "A", "B", "C", "D"
    supports_mpsse: bool = True               # MPSSE support
    supported_protocols: List[ProtocolMode] = field(default_factory=list)
    data_pins: List[str] = field(default_factory=list)   # xDBUS0-7
    ctrl_pins: List[str] = field(default_factory=list)   # xCBUS0-9


@dataclass
class ChipSpec:
    """Full FTDI chip spec."""
    name: str                       # "FT232H", "FT2232H", "FT4232H"
    package: str                    # "LQFP48", "LQFP64"
    pin_count: int                  # 48, 64
    vid: int = 0x0403               # Vendor ID
    pid: int = 0x6014               # Product ID
    channels: Dict[str, ChannelSpec] = field(default_factory=dict)
    pins: Dict[int, PinSpec] = field(default_factory=dict)  # pin_number -> PinSpec
    description: str = ""


# -- Pin color mapping --

PIN_COLORS: Dict[PinFunction, str] = {
    PinFunction.I2C_SCL:     "#00d2ff",  # cyan
    PinFunction.I2C_SDA_OUT: "#00d2ff",
    PinFunction.I2C_SDA_IN:  "#00d2ff",
    PinFunction.SPI_SCK:     "#ff9933",  # orange
    PinFunction.SPI_MOSI:    "#ff9933",
    PinFunction.SPI_MISO:    "#ff9933",
    PinFunction.SPI_CS:      "#ff9933",
    PinFunction.JTAG_TCK:    "#cc66ff",  # purple
    PinFunction.JTAG_TDI:    "#cc66ff",
    PinFunction.JTAG_TDO:    "#cc66ff",
    PinFunction.JTAG_TMS:    "#cc66ff",
    PinFunction.UART_TX:     "#66ff66",  # green
    PinFunction.UART_RX:     "#66ff66",
    PinFunction.UART_RTS:    "#44cc44",
    PinFunction.UART_CTS:    "#44cc44",
    PinFunction.UART_DTR:    "#44cc44",
    PinFunction.UART_DSR:    "#44cc44",
    PinFunction.UART_DCD:    "#44cc44",
    PinFunction.UART_RI:     "#44cc44",
    PinFunction.GPIO_OUT:    "#ffcc44",  # yellow
    PinFunction.GPIO_IN:     "#ffcc44",
    PinFunction.POWER:       "#ff4444",  # red
    PinFunction.GROUND:      "#666666",  # gray
    PinFunction.NC:          "#444444",  # dark gray
    PinFunction.SPECIAL:     "#cc8844",  # brown
}

# Protocol -> color
PROTOCOL_COLORS: Dict[ProtocolMode, str] = {
    ProtocolMode.I2C:     "#00d2ff",
    ProtocolMode.SPI:     "#ff9933",
    ProtocolMode.JTAG:    "#cc66ff",
    ProtocolMode.UART:    "#66ff66",
    ProtocolMode.GPIO:    "#ffcc44",
    ProtocolMode.BITBANG: "#ff64b4",
}


# -----------------------------------------------
# FT232H - single channel, LQFP48
# ------------------------------------------------

def _build_ft232h() -> ChipSpec:
    channels = {
        "A": ChannelSpec(
            name="A",
            supports_mpsse=True,
            supported_protocols=[
                ProtocolMode.I2C, ProtocolMode.SPI,
                ProtocolMode.JTAG, ProtocolMode.UART,
                ProtocolMode.GPIO,
            ],
            data_pins=[f"AD{i}" for i in range(8)],
            ctrl_pins=[f"AC{i}" for i in range(10)],
        ),
    }

    pins: Dict[int, PinSpec] = {}

    # -- ADBUS 0-7 (left side) --
    ad_mpsse_funcs = [
        ("AD0", [PinFunction.I2C_SCL, PinFunction.SPI_SCK, PinFunction.JTAG_TCK, PinFunction.UART_TX, PinFunction.GPIO_OUT],
         PinFunction.GPIO_OUT, "SCK / SCL / TCK / TX"),
        ("AD1", [PinFunction.I2C_SDA_OUT, PinFunction.SPI_MOSI, PinFunction.JTAG_TDI, PinFunction.UART_RX, PinFunction.GPIO_OUT],
         PinFunction.GPIO_OUT, "MOSI / SDA(out) / TDI / RX"),
        ("AD2", [PinFunction.I2C_SDA_IN, PinFunction.SPI_MISO, PinFunction.JTAG_TDO, PinFunction.GPIO_IN],
         PinFunction.GPIO_IN, "MISO / SDA(in) / TDO"),
        ("AD3", [PinFunction.SPI_CS, PinFunction.JTAG_TMS, PinFunction.GPIO_OUT],
         PinFunction.GPIO_OUT, "CS / TMS"),
        ("AD4", [PinFunction.GPIO_OUT, PinFunction.GPIO_IN], PinFunction.GPIO_OUT, "GPIOL0"),
        ("AD5", [PinFunction.GPIO_OUT, PinFunction.GPIO_IN], PinFunction.GPIO_OUT, "GPIOL1"),
        ("AD6", [PinFunction.GPIO_OUT, PinFunction.GPIO_IN], PinFunction.GPIO_OUT, "GPIOL2"),
        ("AD7", [PinFunction.GPIO_OUT, PinFunction.GPIO_IN], PinFunction.GPIO_OUT, "GPIOL3"),
    ]
    for i, (name, funcs, default, desc) in enumerate(ad_mpsse_funcs):
        pins[13 + i] = PinSpec(
            number=13 + i, name=name, direction=PinDirection.LEFT,
            functions=funcs, default_function=default,
            channel="A", mpsse_bit=i, description=desc,
        )

    # -- ACBUS 0-7 (right side) --
    ac_names = [
        ("AC0", "GPIOH0"), ("AC1", "GPIOH1"), ("AC2", "GPIOH2"),
        ("AC3", "GPIOH3"), ("AC4", "GPIOH4"), ("AC5", "GPIOH5"),
        ("AC6", "GPIOH6"), ("AC7", "GPIOH7"),
    ]
    for i, (name, desc) in enumerate(ac_names):
        pins[21 + i] = PinSpec(
            number=21 + i, name=name, direction=PinDirection.RIGHT,
            functions=[PinFunction.GPIO_OUT, PinFunction.GPIO_IN],
            default_function=PinFunction.GPIO_OUT,
            channel="A", mpsse_bit=i, description=desc,
        )

    # -- Power/GND pins (top/bottom) --
    power_pins = [
        (1,  "GND",     PinDirection.TOP),
        (3,  "VCC",     PinDirection.TOP),
        (5,  "VCCIO",   PinDirection.TOP),
        (7,  "GND",     PinDirection.TOP),
        (10, "VPHY",    PinDirection.TOP),
        (11, "GND",     PinDirection.TOP),
        (42, "GND",     PinDirection.BOTTOM),
        (46, "VCCIO",   PinDirection.BOTTOM),
    ]
    for num, name, direction in power_pins:
        func = PinFunction.GROUND if "GND" in name else PinFunction.POWER
        pins[num] = PinSpec(
            number=num, name=name, direction=direction,
            functions=[func], default_function=func,
            description="Power" if func == PinFunction.POWER else "Ground",
        )

    # -- Special pins --
    special_pins = [
        (34, "RESET#", PinDirection.BOTTOM, "Active-low reset"),
        (36, "EECS",   PinDirection.BOTTOM, "EEPROM chip select"),
        (37, "EECLK",  PinDirection.BOTTOM, "EEPROM clock"),
        (38, "EEDATA", PinDirection.BOTTOM, "EEPROM data"),
    ]
    for num, name, direction, desc in special_pins:
        pins[num] = PinSpec(
            number=num, name=name, direction=direction,
            functions=[PinFunction.SPECIAL], default_function=PinFunction.SPECIAL,
            description=desc,
        )

    return ChipSpec(
        name="FT232H", package="LQFP48", pin_count=48,
        pid=0x6014, channels=channels, pins=pins,
        description="Single-channel USB Hi-Speed to MPSSE / UART / FIFO",
    )


# ------------------------------------------------
# FT2232H - dual channel, LQFP64
# ------------------------------------------------

def _build_ft2232h() -> ChipSpec:
    channels = {
        "A": ChannelSpec(
            name="A", supports_mpsse=True,
            supported_protocols=[
                ProtocolMode.I2C, ProtocolMode.SPI,
                ProtocolMode.JTAG, ProtocolMode.UART, ProtocolMode.GPIO,
            ],
            data_pins=[f"AD{i}" for i in range(8)],
            ctrl_pins=[f"AC{i}" for i in range(8)],
        ),
        "B": ChannelSpec(
            name="B", supports_mpsse=True,
            supported_protocols=[
                ProtocolMode.I2C, ProtocolMode.SPI,
                ProtocolMode.JTAG, ProtocolMode.UART, ProtocolMode.GPIO,
            ],
            data_pins=[f"BD{i}" for i in range(8)],
            ctrl_pins=[f"BC{i}" for i in range(8)],
        ),
    }

    pins: Dict[int, PinSpec] = {}

    # Channel A ADBUS
    ad_funcs = [
        ("AD0", PinFunction.GPIO_OUT, "SCK / SCL / TCK / TX"),
        ("AD1", PinFunction.GPIO_OUT, "MOSI / SDA(out) / TDI / RX"),
        ("AD2", PinFunction.GPIO_IN,  "MISO / SDA(in) / TDO"),
        ("AD3", PinFunction.GPIO_OUT, "CS / TMS"),
        ("AD4", PinFunction.GPIO_OUT, "GPIOL0"),
        ("AD5", PinFunction.GPIO_OUT, "GPIOL1"),
        ("AD6", PinFunction.GPIO_OUT, "GPIOL2"),
        ("AD7", PinFunction.GPIO_OUT, "GPIOL3"),
    ]
    base_funcs_mpsse = [
        [PinFunction.I2C_SCL, PinFunction.SPI_SCK, PinFunction.JTAG_TCK, PinFunction.UART_TX, PinFunction.GPIO_OUT],
        [PinFunction.I2C_SDA_OUT, PinFunction.SPI_MOSI, PinFunction.JTAG_TDI, PinFunction.UART_RX, PinFunction.GPIO_OUT],
        [PinFunction.I2C_SDA_IN, PinFunction.SPI_MISO, PinFunction.JTAG_TDO, PinFunction.GPIO_IN],
        [PinFunction.SPI_CS, PinFunction.JTAG_TMS, PinFunction.GPIO_OUT],
        [PinFunction.GPIO_OUT, PinFunction.GPIO_IN],
        [PinFunction.GPIO_OUT, PinFunction.GPIO_IN],
        [PinFunction.GPIO_OUT, PinFunction.GPIO_IN],
        [PinFunction.GPIO_OUT, PinFunction.GPIO_IN],
    ]

    for i, (name, default, desc) in enumerate(ad_funcs):
        pins[16 + i] = PinSpec(
            number=16 + i, name=name, direction=PinDirection.LEFT,
            functions=base_funcs_mpsse[i], default_function=default,
            channel="A", mpsse_bit=i, description=desc,
        )

    # Channel A ACBUS
    for i in range(8):
        pins[26 + i] = PinSpec(
            number=26 + i, name=f"AC{i}", direction=PinDirection.LEFT,
            functions=[PinFunction.GPIO_OUT, PinFunction.GPIO_IN],
            default_function=PinFunction.GPIO_OUT,
            channel="A", mpsse_bit=i, description=f"GPIOH{i}",
        )

    # Channel B BDBUS
    bd_funcs = [
        ("BD0", PinFunction.GPIO_OUT, "SCK / SCL / TCK / TX"),
        ("BD1", PinFunction.GPIO_OUT, "MOSI / SDA(out) / TDI / RX"),
        ("BD2", PinFunction.GPIO_IN,  "MISO / SDA(in) / TDO"),
        ("BD3", PinFunction.GPIO_OUT, "CS / TMS"),
        ("BD4", PinFunction.GPIO_OUT, "GPIOL0"),
        ("BD5", PinFunction.GPIO_OUT, "GPIOL1"),
        ("BD6", PinFunction.GPIO_OUT, "GPIOL2"),
        ("BD7", PinFunction.GPIO_OUT, "GPIOL3"),
    ]
    for i, (name, default, desc) in enumerate(bd_funcs):
        pins[38 + i] = PinSpec(
            number=38 + i, name=name, direction=PinDirection.RIGHT,
            functions=base_funcs_mpsse[i], default_function=default,
            channel="B", mpsse_bit=i, description=desc,
        )

    # Channel B BCBUS
    for i in range(8):
        pins[48 + i] = PinSpec(
            number=48 + i, name=f"BC{i}", direction=PinDirection.RIGHT,
            functions=[PinFunction.GPIO_OUT, PinFunction.GPIO_IN],
            default_function=PinFunction.GPIO_OUT,
            channel="B", mpsse_bit=i, description=f"GPIOH{i}",
        )

    # Power/GND
    for num, name, d in [
        (1, "GND", PinDirection.TOP), (4, "VCC", PinDirection.TOP),
        (8, "VCCIO", PinDirection.TOP), (12, "GND", PinDirection.TOP),
        (60, "GND", PinDirection.BOTTOM), (64, "VCCIO", PinDirection.BOTTOM),
    ]:
        func = PinFunction.GROUND if "GND" in name else PinFunction.POWER
        pins[num] = PinSpec(
            number=num, name=name, direction=d,
            functions=[func], default_function=func,
            description="Power" if func == PinFunction.POWER else "Ground",
        )

    return ChipSpec(
        name="FT2232H", package="LQFP64", pin_count=64,
        pid=0x6010, channels=channels, pins=pins,
        description="Dual-channel USB Hi-Speed to MPSSE / UART / FIFO",
    )


# ------------------------------------------------
# FT4232H - quad channel, LQFP64
# ------------------------------------------------

def _build_ft4232h() -> ChipSpec:
    channels = {
        "A": ChannelSpec(
            name="A", supports_mpsse=True,
            supported_protocols=[
                ProtocolMode.I2C, ProtocolMode.SPI,
                ProtocolMode.JTAG, ProtocolMode.UART, ProtocolMode.GPIO,
            ],
            data_pins=[f"AD{i}" for i in range(8)],
            ctrl_pins=[],
        ),
        "B": ChannelSpec(
            name="B", supports_mpsse=True,
            supported_protocols=[
                ProtocolMode.I2C, ProtocolMode.SPI,
                ProtocolMode.JTAG, ProtocolMode.UART, ProtocolMode.GPIO,
            ],
            data_pins=[f"BD{i}" for i in range(8)],
            ctrl_pins=[],
        ),
        "C": ChannelSpec(
            name="C", supports_mpsse=False,
            supported_protocols=[
                ProtocolMode.UART, ProtocolMode.BITBANG, ProtocolMode.GPIO,
            ],
            data_pins=[f"CD{i}" for i in range(8)],
            ctrl_pins=[],
        ),
        "D": ChannelSpec(
            name="D", supports_mpsse=False,
            supported_protocols=[
                ProtocolMode.UART, ProtocolMode.BITBANG, ProtocolMode.GPIO,
            ],
            data_pins=[f"DD{i}" for i in range(8)],
            ctrl_pins=[],
        ),
    }

    pins: Dict[int, PinSpec] = {}

    mpsse_funcs = [
        [PinFunction.I2C_SCL, PinFunction.SPI_SCK, PinFunction.JTAG_TCK, PinFunction.UART_TX, PinFunction.GPIO_OUT],
        [PinFunction.I2C_SDA_OUT, PinFunction.SPI_MOSI, PinFunction.JTAG_TDI, PinFunction.UART_RX, PinFunction.GPIO_OUT],
        [PinFunction.I2C_SDA_IN, PinFunction.SPI_MISO, PinFunction.JTAG_TDO, PinFunction.GPIO_IN],
        [PinFunction.SPI_CS, PinFunction.JTAG_TMS, PinFunction.GPIO_OUT],
        [PinFunction.GPIO_OUT, PinFunction.GPIO_IN],
        [PinFunction.GPIO_OUT, PinFunction.GPIO_IN],
        [PinFunction.GPIO_OUT, PinFunction.GPIO_IN],
        [PinFunction.GPIO_OUT, PinFunction.GPIO_IN],
    ]
    uart_only_funcs = [
        [PinFunction.UART_TX, PinFunction.GPIO_OUT],
        [PinFunction.UART_RX, PinFunction.GPIO_IN],
        [PinFunction.UART_RTS, PinFunction.GPIO_OUT],
        [PinFunction.UART_CTS, PinFunction.GPIO_IN],
        [PinFunction.UART_DTR, PinFunction.GPIO_OUT],
        [PinFunction.UART_DSR, PinFunction.GPIO_IN],
        [PinFunction.UART_DCD, PinFunction.GPIO_IN],
        [PinFunction.UART_RI, PinFunction.GPIO_IN],
    ]
    mpsse_desc = [
        "SCK / SCL / TCK / TX", "MOSI / SDA(out) / TDI / RX",
        "MISO / SDA(in) / TDO", "CS / TMS",
        "GPIOL0", "GPIOL1", "GPIOL2", "GPIOL3",
    ]
    uart_desc = [
        "TX", "RX", "RTS#", "CTS#", "DTR#", "DSR#", "DCD#", "RI#",
    ]

    # Channel A (top-left)
    for i in range(8):
        pins[16 + i] = PinSpec(
            number=16 + i, name=f"AD{i}", direction=PinDirection.LEFT,
            functions=mpsse_funcs[i], default_function=mpsse_funcs[i][-1],
            channel="A", mpsse_bit=i, description=mpsse_desc[i],
        )

    # Channel B (bottom-left)
    for i in range(8):
        pins[24 + i] = PinSpec(
            number=24 + i, name=f"BD{i}", direction=PinDirection.LEFT,
            functions=mpsse_funcs[i], default_function=mpsse_funcs[i][-1],
            channel="B", mpsse_bit=i, description=mpsse_desc[i],
        )

    # Channel C (top-right) - UART/Bit-bang only
    for i in range(8):
        pins[38 + i] = PinSpec(
            number=38 + i, name=f"CD{i}", direction=PinDirection.RIGHT,
            functions=uart_only_funcs[i],
            default_function=uart_only_funcs[i][-1],
            channel="C", mpsse_bit=i, description=uart_desc[i],
        )

    # Channel D (bottom-right) - UART/Bit-bang only
    for i in range(8):
        pins[46 + i] = PinSpec(
            number=46 + i, name=f"DD{i}", direction=PinDirection.RIGHT,
            functions=uart_only_funcs[i],
            default_function=uart_only_funcs[i][-1],
            channel="D", mpsse_bit=i, description=uart_desc[i],
        )

    # Power/GND
    for num, name, d in [
        (1, "GND", PinDirection.TOP), (4, "VCC", PinDirection.TOP),
        (8, "VCCIO", PinDirection.TOP), (12, "GND", PinDirection.TOP),
        (60, "GND", PinDirection.BOTTOM), (64, "VCCIO", PinDirection.BOTTOM),
    ]:
        func = PinFunction.GROUND if "GND" in name else PinFunction.POWER
        pins[num] = PinSpec(
            number=num, name=name, direction=d,
            functions=[func], default_function=func,
            description="Power" if func == PinFunction.POWER else "Ground",
        )

    return ChipSpec(
        name="FT4232H", package="LQFP64", pin_count=64,
        pid=0x6011, channels=channels, pins=pins,
        description="Quad-channel USB Hi-Speed - A/B: MPSSE, C/D: UART only",
    )


# -- Chipset registry --

CHIP_SPECS: Dict[str, ChipSpec] = {
    "FT232H":  _build_ft232h(),
    "FT2232H": _build_ft2232h(),
    "FT4232H": _build_ft4232h(),
}

# PID -> chip name mapping
PID_TO_CHIP: Dict[int, str] = {
    0x6014: "FT232H",
    0x6010: "FT2232H",
    0x6011: "FT4232H",
}


def get_chip_spec(chip_name: str) -> Optional[ChipSpec]:
    """Get spec by chip name."""
    return CHIP_SPECS.get(chip_name)


def get_chip_by_pid(pid: int) -> Optional[ChipSpec]:
    """Get spec by PID."""
    name = PID_TO_CHIP.get(pid)
    return CHIP_SPECS.get(name) if name else None


def get_channel_protocols(chip_name: str, channel: str) -> List[ProtocolMode]:
    """Supported protocol list for a chip/channel."""
    spec = CHIP_SPECS.get(chip_name)
    if spec is None:
        return []
    ch = spec.channels.get(channel)
    return list(ch.supported_protocols) if ch else []
