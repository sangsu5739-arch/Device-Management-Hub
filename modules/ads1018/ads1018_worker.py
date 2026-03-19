"""
ADS1018 Worker — threading.Thread-based SPI ADC polling

Uses threading.Thread instead of QThread so that VSCode debugpy
can attach breakpoints inside the worker loop.
Qt Signals are still used for cross-thread UI updates (PySide6
supports emit from non-Qt threads via queued connections).
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from typing import Optional, List

from PySide6.QtCore import QObject, Signal

from core.ftdi_manager import FtdiManager
from modules.ads1018.ads1018_driver import (
    ADS1018Driver, ADS1018Config, ChannelConfig, ChannelMode,
    PGA, DataRate,
)


@dataclass
class ADS1018Measurement:
    """Single 4-channel measurement snapshot."""
    timestamp: float = 0.0
    values: list = None      # [ch0, ch1, ch2, ch3] float values
    modes: list = None       # [mode0, mode1, ...] ChannelMode
    units: list = None       # ["V", "mA", ...]
    raw_values: list = None  # [raw0, raw1, raw2, raw3]

    def __post_init__(self):
        if self.values is None:
            self.values = [0.0, 0.0, 0.0, 0.0]
        if self.modes is None:
            self.modes = [0, 0, 0, 0]
        if self.units is None:
            self.units = ["V", "V", "V", "V"]
        if self.raw_values is None:
            self.raw_values = [0, 0, 0, 0]


class ADS1018Worker(QObject):
    """ADS1018 measurement worker using threading.Thread.

    Runs in a standard Python thread (debugpy-compatible) and
    emits Qt Signals for UI updates via queued connections.
    """

    measurement = Signal(object)   # ADS1018Measurement
    error_occurred = Signal(str)
    log_message = Signal(str)

    def __init__(self, ftdi: FtdiManager) -> None:
        super().__init__()
        self._ftdi = ftdi
        self._running = False
        self._poll_interval_ms: int = 100
        self._driver: Optional[ADS1018Driver] = None
        self._config = ADS1018Config()
        self._thread: Optional[threading.Thread] = None

    def configure(
        self,
        pga: int = PGA.FS4096,
        data_rate: int = DataRate.SPS3300,
        pullup: bool = True,
        continuous: bool = False,
        ts_mode: bool = False,
        cs_pin: int = 0x08,
        poll_interval_ms: int = 100,
        channel_configs: Optional[List[ChannelConfig]] = None,
    ) -> None:
        """Set worker parameters before start()."""
        self._config.pga = pga
        self._config.data_rate = data_rate
        self._config.pullup_enable = pullup
        self._config.continuous = continuous
        self._config.ts_mode = ts_mode
        self._config.cs_pin = cs_pin
        self._poll_interval_ms = max(20, poll_interval_ms)

        if channel_configs is not None:
            self._config.channels = channel_configs

    def start(self) -> None:
        """Start the worker thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="ADS1018Worker")
        self._thread.start()

    def _run(self) -> None:
        """Main polling loop (runs in threading.Thread — debugpy breakpoints work here)."""
        self._log("ADS1018 Worker started")

        try:
            self._driver = ADS1018Driver(self._ftdi, self._config)
            self._log(
                f"ADS1018 configured: PGA={PGA.LABELS.get(self._config.pga, '?')}, "
                f"Rate={DataRate.LABELS.get(self._config.data_rate, '?')}, "
                f"CS=0x{self._config.cs_pin:02X}"
            )
            if self._config.continuous:
                self._log("ADS1018 timing: CPOL=1 CPHA=0, continuous conversion")
            else:
                self._log(
                    f"ADS1018 timing: CPOL=1 CPHA=0, single-shot wait={self._driver.conversion_delay_ms:.2f} ms"
                )
        except Exception as e:
            self.error_occurred.emit(f"ADS1018 init error: {e}")
            self._running = False
            return

        consecutive_errors = 0
        max_errors = 10

        while self._running:
            try:
                m = ADS1018Measurement(timestamp=time.time())

                for ch in range(4):
                    ch_cfg = self._config.channels[ch]
                    if not ch_cfg.enabled:
                        continue

                    raw = self._driver.read_channel(ch)
                    if raw is None:
                        consecutive_errors += 1
                        if consecutive_errors >= max_errors:
                            self.error_occurred.emit(
                                f"ADS1018: {max_errors} consecutive read errors"
                            )
                            self._running = False
                            break
                        continue

                    consecutive_errors = 0
                    m.raw_values[ch] = raw
                    m.modes[ch] = ch_cfg.mode

                    if self._config.ts_mode:
                        m.values[ch] = self._driver.calculate_temperature(raw)
                        m.units[ch] = "\u00b0C"
                    elif ch_cfg.mode == ChannelMode.CURRENT:
                        m.values[ch] = self._driver.calculate_current(
                            raw, ch_cfg.gain, ch_cfg.shunt_resistor
                        )
                        m.units[ch] = "mA"
                    else:
                        m.values[ch] = self._driver.calculate_voltage(raw, ch_cfg.voltage_divider)
                        m.units[ch] = "V"

                if self._running:
                    self.measurement.emit(m)

            except Exception as e:
                self.error_occurred.emit(f"ADS1018 poll error: {e}")
                consecutive_errors += 1
                if consecutive_errors >= max_errors:
                    self._running = False
                    break

            time.sleep(self._poll_interval_ms / 1000.0)

        self._log("ADS1018 Worker stopped")

    def stop(self) -> None:
        """Signal worker to stop."""
        self._running = False

    def wait(self, timeout_ms: int = 2000) -> None:
        """Wait for the thread to finish."""
        if self._thread is not None:
            self._thread.join(timeout=timeout_ms / 1000.0)
            self._thread = None

    def _log(self, msg: str) -> None:
        self.log_message.emit(msg)
