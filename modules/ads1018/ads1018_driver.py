"""
ADS1018 SPI ADC Driver

4-channel 12-bit ADC with integrated temperature sensor.
Config register bit-field management and voltage/current conversion.

Reference: C:/log/sample/ADS1018.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.ftdi_manager import FtdiManager


# ── ADS1018 Config Register Bit Definitions ──────────────────────────────

class DataRate:
    """Samples per second selection."""
    SPS128  = 0
    SPS250  = 1
    SPS490  = 2
    SPS920  = 3
    SPS1600 = 4  # default
    SPS2400 = 5
    SPS3300 = 6

    LABELS = {
        0: "128 SPS", 1: "250 SPS", 2: "490 SPS", 3: "920 SPS",
        4: "1600 SPS", 5: "2400 SPS", 6: "3300 SPS",
    }


class PGA:
    """Programmable Gain Amplifier — full-scale range."""
    FS6144 = 0  # ±6.144 V
    FS4096 = 1  # ±4.096 V
    FS2048 = 2  # ±2.048 V (default)
    FS1024 = 3  # ±1.024 V
    FS0512 = 4  # ±0.512 V
    FS0256 = 5  # ±0.256 V

    LABELS = {
        0: "±6.144 V", 1: "±4.096 V", 2: "±2.048 V",
        3: "±1.024 V", 4: "±0.512 V", 5: "±0.256 V",
    }

    # Full-scale range (V) and LSB (mV) per setting
    FS_RANGE = {0: 6.144, 1: 4.096, 2: 2.048, 3: 1.024, 4: 0.512, 5: 0.256}
    LSB_MV   = {0: 3.0,   1: 2.0,   2: 1.0,   3: 0.5,   4: 0.25,  5: 0.125}


class MuxConfig:
    """Input multiplexer configuration."""
    DIFF_01 = 0   # AIN0-AIN1 (differential)
    DIFF_03 = 1   # AIN0-AIN3
    DIFF_13 = 2   # AIN1-AIN3
    DIFF_23 = 3   # AIN2-AIN3
    SINGLE_0 = 4  # AIN0 vs GND
    SINGLE_1 = 5  # AIN1 vs GND
    SINGLE_2 = 6  # AIN2 vs GND
    SINGLE_3 = 7  # AIN3 vs GND


class ChannelMode:
    VOLTAGE = 0
    CURRENT = 1


@dataclass
class ChannelConfig:
    """Per-channel configuration."""
    mode: int = ChannelMode.VOLTAGE
    shunt_resistor: float = 0.02   # Ohm
    gain: float = 100.0            # Op-amp gain
    enabled: bool = True


@dataclass
class ADS1018Config:
    """ADS1018 device-level settings."""
    pga: int = PGA.FS4096
    data_rate: int = DataRate.SPS3300
    pullup_enable: bool = True
    continuous: bool = False   # True=continuous, False=single-shot
    ts_mode: bool = False      # True=Temperature, False=ADC
    cs_pin: int = 0x08         # ADBUS3 default

    channels: list = field(default_factory=lambda: [
        ChannelConfig(),
        ChannelConfig(),
        ChannelConfig(),
        ChannelConfig(),
    ])


class ADS1018Driver:
    """ADS1018 SPI ADC register-level driver.

    Manages the 16-bit config register and performs ADC reads
    via FtdiManager SPI API.
    """

    ADC_FULL_SCALE = 0x7FF0

    def __init__(self, ftdi: FtdiManager, config: Optional[ADS1018Config] = None) -> None:
        self._ftdi = ftdi
        self.config = config or ADS1018Config()
        self._config_reg: int = 0x058B  # default register value

        # Apply initial settings
        self._apply_pga(self.config.pga)
        self._apply_data_rate(self.config.data_rate)
        self._apply_pullup(self.config.pullup_enable)
        self._apply_operating_mode(self.config.continuous)
        self._apply_ts_mode(self.config.ts_mode)

    # ── Config Register Manipulation ──────────────────────────────────

    def _apply_pga(self, pga: int) -> None:
        mask = 0x7 << 9
        self._config_reg = (self._config_reg & ~mask) | (pga << 9)
        self.config.pga = pga

    def _apply_data_rate(self, rate: int) -> None:
        mask = 0x7 << 5
        self._config_reg = (self._config_reg & ~mask) | (rate << 5)
        self.config.data_rate = rate

    def _apply_pullup(self, enable: bool) -> None:
        mask = 1 << 3
        val = 1 if enable else 0
        self._config_reg = (self._config_reg & ~mask) | (val << 3)
        self.config.pullup_enable = enable

    def _apply_operating_mode(self, continuous: bool) -> None:
        mask = 1 << 8
        val = 0 if continuous else 1
        self._config_reg = (self._config_reg & ~mask) | (val << 8)
        self.config.continuous = continuous

    def _apply_ts_mode(self, is_temp: bool) -> None:
        mask = 1 << 4
        val = 1 if is_temp else 0
        self._config_reg = (self._config_reg & ~mask) | (val << 4)
        self.config.ts_mode = is_temp

    def _set_channel(self, mux: int) -> None:
        mask = 0x7 << 12
        self._config_reg = (self._config_reg & ~mask) | (mux << 12)

    def _start_conversion(self) -> None:
        self._config_reg |= (1 << 15)

    def update_settings(self, pga: int, data_rate: int, pullup: bool, continuous: bool = False, ts_mode: bool = False) -> None:
        """Update device settings (call before read_channel)."""
        self._apply_pga(pga)
        self._apply_data_rate(data_rate)
        self._apply_pullup(pullup)
        self._apply_operating_mode(continuous)
        self._apply_ts_mode(ts_mode)

    # ── ADC Read ──────────────────────────────────────────────────────

    def read_channel(self, channel: int) -> Optional[int]:
        """Read raw ADC value from a channel (0-3, single-ended).

        Performs two SPI transfers:
        1. Write config to select channel and start conversion
        2. Read back the conversion result

        Returns:
            12-bit raw ADC value (right-shifted), or None on error.
        """
        mux = MuxConfig.SINGLE_0 + channel
        self._set_channel(mux)
        self._start_conversion()

        # Build 4-byte SPI frame (config written twice per ADS1018 protocol)
        cfg_hi = (self._config_reg >> 8) & 0xFF
        cfg_lo = self._config_reg & 0xFF
        tx_data = bytes([cfg_hi, cfg_lo, cfg_hi, cfg_lo])

        cs = self.config.cs_pin

        # First transfer: write config (result is stale)
        self._ftdi.spi_transfer(tx_data, cs)

        # Second transfer: read back converted result
        rx = self._ftdi.spi_transfer(tx_data, cs)
        if rx is None or len(rx) < 4:
            return None

        # ADC value is in the first two bytes
        raw = (rx[0] << 8) | rx[1]
        if self.config.ts_mode:
            raw = raw >> 2  # 14-bit result for temperature
            if raw & 0x2000:
                raw -= 0x4000
        else:
            raw = raw >> 4  # 12-bit result for voltage
            if raw & 0x0800:
                raw -= 0x1000
        return raw

    # ── Value Conversion ──────────────────────────────────────────────

    def calculate_voltage(self, raw: int) -> float:
        """Convert raw ADC value to voltage (V)."""
        lsb_mv = PGA.LSB_MV.get(self.config.pga, 1.0)
        voltage_mv = raw * lsb_mv * 2  # ×2 for voltage divider
        return round(voltage_mv / 1000.0, 4)

    def calculate_current(self, raw: int, gain: float, shunt: float) -> float:
        """Convert raw ADC value to current (mA).

        Args:
            raw:   12-bit ADC reading
            gain:  Op-amp gain
            shunt: Sensing resistor (Ohm)
        """
        lsb_mv = PGA.LSB_MV.get(self.config.pga, 1.0)
        result_mv = raw * lsb_mv
        if gain == 0 or shunt == 0:
            return 0.0
        current_ma = result_mv / gain / shunt
        return round(current_ma, 3)

    def calculate_temperature(self, raw: int) -> float:
        """Convert raw 14-bit ADC value to temperature (°C)."""
        return round(raw * 0.03125, 3)

    def read_and_convert(self, channel: int) -> Optional[float]:
        """Read channel and convert to voltage or current based on config."""
        ch_cfg = self.config.channels[channel]
        raw = self.read_channel(channel)
        if raw is None:
            return None

        if ch_cfg.mode == ChannelMode.CURRENT:
            return self.calculate_current(raw, ch_cfg.gain, ch_cfg.shunt_resistor)
        else:
            return self.calculate_voltage(raw)
