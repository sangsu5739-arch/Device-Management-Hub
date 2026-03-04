"""
Pinmap controller for FTDI Verifier.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from modules.ftdi_verifier.ftdi_chip_specs import PinFunction

if TYPE_CHECKING:
    from modules.ftdi_verifier.ftdi_verifier_module import FtdiVerifierModule


class PinmapController:
    def __init__(self, module: "FtdiVerifierModule") -> None:
        self._m = module

    def apply_mode(self, text: str) -> None:
        if self._m._current_chip is None:
            return

        mode_map = {
            "I2C": {
                0: PinFunction.I2C_SCL,
                1: PinFunction.I2C_SDA_OUT,
                2: PinFunction.I2C_SDA_IN,
            },
            "SPI": {
                0: PinFunction.SPI_SCK,
                1: PinFunction.SPI_MOSI,
                2: PinFunction.SPI_MISO,
                3: PinFunction.SPI_CS,
            },
            "JTAG": {
                0: PinFunction.JTAG_TCK,
                1: PinFunction.JTAG_TDI,
                2: PinFunction.JTAG_TDO,
                3: PinFunction.JTAG_TMS,
            },
            "UART": {
                0: PinFunction.UART_TX,
                1: PinFunction.UART_RX,
                2: PinFunction.UART_RTS,
                3: PinFunction.UART_CTS,
                4: PinFunction.UART_DTR,
                5: PinFunction.UART_DSR,
                6: PinFunction.UART_DCD,
                7: PinFunction.UART_RI,
            },
        }

        func_map = mode_map.get(text, {})
        ch_spec = self._m._current_chip.channels.get(self._m._current_channel)
        supports_mpsse = bool(ch_spec and ch_spec.supports_mpsse)
        force_gpio = (not supports_mpsse) and text in ("I2C", "SPI", "JTAG")

        for num, pin in self._m._current_chip.pins.items():
            if pin.channel != self._m._current_channel:
                continue

            if text == "GPIO" or force_gpio:
                if PinFunction.GPIO_OUT in pin.functions:
                    self._m._pinout.set_pin_function(num, PinFunction.GPIO_OUT)
                elif PinFunction.GPIO_IN in pin.functions:
                    self._m._pinout.set_pin_function(num, PinFunction.GPIO_IN)
                continue

            assigned = func_map.get(pin.mpsse_bit)
            if assigned and assigned in pin.functions:
                self._m._pinout.set_pin_function(num, assigned)
            elif PinFunction.GPIO_OUT in pin.functions:
                self._m._pinout.set_pin_function(num, PinFunction.GPIO_OUT)
            elif PinFunction.GPIO_IN in pin.functions:
                self._m._pinout.set_pin_function(num, PinFunction.GPIO_IN)
            else:
                self._m._pinout.set_pin_function(num, pin.default_function)
