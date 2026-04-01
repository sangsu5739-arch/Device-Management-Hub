"""
Universal Device Studio - FTDI MPSSE manager (Singleton)

FT4232H via ftd2xx + MPSSE for I2C / SPI access.
Thread-safe FTDI access.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal, QMutex, QMutexLocker, QThread, Slot

from core.i2c_controller import I2cController
from core.spi_controller import SpiController
from core.ftdi_bitbang import BitbangController

logger = logging.getLogger(__name__)


@dataclass
class FtdiTaskResult:
    """Result payload for async FTDI protocol tasks."""

    success: bool
    payload: Any = None
    error: str = ""
    stage: str = ""


class _FtdiProtocolTaskWorker(QObject):
    """Run a short FTDI restore + task sequence off the UI thread."""

    finished = Signal(object)

    def __init__(
        self,
        ftdi: "FtdiManager",
        protocol: Optional[str],
        force: bool,
        settle_ms: int,
        prepare: Optional[Callable[[], Any]],
        task: Optional[Callable[[], Any]],
    ) -> None:
        super().__init__()
        self._ftdi = ftdi
        self._protocol = protocol.upper() if protocol else None
        self._force = force
        self._settle_ms = max(0, int(settle_ms))
        self._prepare = prepare
        self._task = task

    @Slot()
    def run(self) -> None:
        self.finished.emit(self._execute())

    def _normalize_result(self, value: Any, stage: str) -> FtdiTaskResult:
        if isinstance(value, FtdiTaskResult):
            if not value.stage:
                value.stage = stage
            return value
        if value is False:
            return FtdiTaskResult(False, error=f"{stage} failed.", stage=stage)
        return FtdiTaskResult(True, payload=value, stage=stage)

    def _execute(self) -> FtdiTaskResult:
        if not self._ftdi.is_connected:
            return FtdiTaskResult(False, error="FTDI device is not connected.", stage="connect")

        try:
            if self._protocol:
                if not self._ftdi.set_protocol_mode(self._protocol, force=self._force):
                    return FtdiTaskResult(
                        False,
                        error=f"{self._protocol} restore failed.",
                        stage="restore",
                    )
                self._ftdi.purge_pending_io()

            if self._prepare is not None:
                prepare_result = self._normalize_result(self._prepare(), "prepare")
                if not prepare_result.success:
                    return prepare_result

            if self._settle_ms > 0:
                time.sleep(self._settle_ms / 1000.0)

            self._ftdi.purge_pending_io()

            if self._task is None:
                return FtdiTaskResult(True, stage="restore")

            return self._normalize_result(self._task(), "task")
        except Exception as exc:
            return FtdiTaskResult(False, error=str(exc), stage="task")


class _FtdiTaskCallbackRelay(QObject):
    """Deliver async task completion back on the manager's thread."""

    def __init__(self, callback: Callable[[FtdiTaskResult], None], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._callback = callback

    @Slot(object)
    def deliver(self, result: object) -> None:
        try:
            if isinstance(result, FtdiTaskResult):
                self._callback(result)
        except Exception:
            logger.exception("Async FTDI callback failed.")
        finally:
            self.deleteLater()


class FtdiManager(QObject):
    """Singleton FTDI MPSSE I2C/SPI manager.

       FTDI access is protected by a mutex.
    QMutex ensures thread-safe I2C/SPI access.

    Attributes:
        device_connected: emitted on connect
        device_disconnected: emitted on disconnect
        comm_error: communication error
        data_sent: TX log
        data_received: RX log
        log_message: log output
    """

    device_connected = Signal(str)
    device_disconnected = Signal()
    comm_error = Signal(str)
    data_sent = Signal(str)
    data_received = Signal(str)
    log_message = Signal(str)
    device_info_changed = Signal(object)
    active_channel_changed = Signal(str)

    # Singleton
    _instance: Optional[FtdiManager] = None
    _initialized: bool = False
    _device_cache: dict = {}

    def __new__(cls, parent: Optional[QObject] = None) -> FtdiManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, parent: Optional[QObject] = None) -> None:
        if self._initialized:
            return
        super().__init__(parent)
        self._ft = None
        self._ft_handles: dict[str, object] = {}
        self._channel_index_map: dict[str, int] = {}
        self._available_channels: list[str] = []
        self._active_channel: str = "A"
        self._is_connected: bool = False
        self._serial_number: str = ""
        self._channel: str = "A"
        self._channel_modes: dict[str, str] = {}
        self._bitbang_mask: int = 0xFF
        self._bitbang_i2c_warned: bool = False
        self._mode_switch_guard_warned: bool = False
        self._mode_switch_ts: float = 0.0
        self._mode_switch_guard_ms: int = 300
        self._mutex = QMutex()
        self._i2c_retry_count: int = 2
        self._i2c_retry_delay_s: float = 0.01
        self._i2c_clock_khz: int = 100
        self._i2c_hold_mask: int = 0x00
        self._i2c_hold_value: int = 0x00
        self._gpio_out_value: int = 0x00
        self._gpio_high_out_value: int = 0x00
        self._gpio_low_direction: int = 0x00
        self._gpio_high_direction: int = 0x00
        self._async_tasks: dict[int, tuple[QThread, _FtdiProtocolTaskWorker, Optional[_FtdiTaskCallbackRelay]]] = {}
        self._async_task_counter: int = 0
        self._i2c = I2cController(self)
        self._spi = SpiController(self)
        self._bitbang = BitbangController(self)
        self._active_protocol: str = "I2C"  # current protocol mode
        FtdiManager._initialized = True

    @classmethod
    def instance(cls) -> FtdiManager:
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # Properties

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def serial_number(self) -> str:
        return self._serial_number

    @property
    def channel(self) -> str:
        return self._active_channel

    @property
    def available_channels(self) -> list[str]:
        return list(self._available_channels)

    def set_i2c_retry(self, retries: int, delay_ms: int) -> None:
        self._i2c_retry_count = max(0, int(retries))
        self._i2c_retry_delay_s = max(0.0, float(delay_ms) / 1000.0)

    def set_i2c_clock_khz(self, khz: int) -> None:
        self._i2c_clock_khz = max(10, int(khz))
        if self._ft is None or not self.supports_mpsse(self._active_channel):
            return
        try:
            self._i2c.set_i2c_clock(self._i2c_clock_khz)
        except Exception as e:
            self._log(f"[WARN] Failed to set I2C clock: {e}")

    def supports_mpsse(self, channel: Optional[str] = None) -> bool:
        ch = (channel or self._active_channel or "A").upper()
        info = self.get_device_info(self._serial_number)
        dtype = (info.get("device_type") or "").upper()
        if "4232" in dtype:
            return ch in ("A", "B")
        if "2232" in dtype:
            return ch in ("A", "B")
        return True

    def set_active_channel(self, channel: str) -> bool:
        ch = channel.upper()
        locker = QMutexLocker(self._mutex)
        if ch not in self._ft_handles:
            if not self._is_connected:
                return False
            if ch not in self._available_channels:
                return False
            if self._is_ft2232_device():
                err = f"Pre-opened handle missing: CH={ch}"
                self._log(f"[ERROR] {err}")
                self.comm_error.emit(err)
                return False
            if not self._open_channel_handle(ch):
                err = f"Channel open failed: CH={ch}"
                self._log(f"[ERROR] {err}")
                self.comm_error.emit(err)
                return False
        self._active_channel = ch
        self._channel = ch
        self._ft = self._ft_handles.get(ch)
        del locker
        self.active_channel_changed.emit(ch)
        self._emit_current_device_info()
        return True

    def run_async_protocol_task(
        self,
        protocol: Optional[str],
        *,
        force: bool = False,
        settle_ms: int = 0,
        prepare: Optional[Callable[[], Any]] = None,
        task: Optional[Callable[[], Any]] = None,
        on_done: Optional[Callable[[FtdiTaskResult], None]] = None,
    ) -> int:
        """Run a short protocol restore + FTDI task sequence on a worker thread."""
        worker = _FtdiProtocolTaskWorker(
            self,
            protocol=protocol,
            force=force,
            settle_ms=settle_ms,
            prepare=prepare,
            task=task,
        )
        thread = QThread(self)
        relay = _FtdiTaskCallbackRelay(on_done, self) if on_done is not None else None
        self._async_task_counter += 1
        task_id = self._async_task_counter
        self._async_tasks[task_id] = (thread, worker, relay)

        worker.moveToThread(thread)
        if relay is not None:
            worker.finished.connect(relay.deliver)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda task_key=task_id: self._async_tasks.pop(task_key, None))
        thread.started.connect(worker.run)
        thread.start()
        return task_id

    def set_protocol_mode(self, mode: str, force: bool = False) -> bool:
        """Switch FTDI mode based on protocol selection.

        Args:
            mode: target protocol ("I2C", "SPI", "GPIO", "JTAG", "UART", "PROG")
            force: if True, skip idempotency check and do a full re-init.
                   Use this when returning from GPIO operations to ensure
                   the MPSSE engine and USB buffers are in a clean state.
        """
        if not self._is_connected or self._ft is None:
            return False

        ch = self._active_channel
        mode = mode.upper()
        locker = QMutexLocker(self._mutex)

        # Skip redundant re-init when already in the correct mode.
        # This prevents double ft.resetDevice() calls during tab switches
        # (e.g. _force_stop_gpio_polling → I2C, then on_tab_activated → I2C).
        if not force:
            cur_mode = self._channel_modes.get(ch, "")
            if mode in ("I2C", "JTAG") and cur_mode == "mpsse":
                return True
            if mode == "SPI" and cur_mode == "spi":
                return True
            if mode == "GPIO" and cur_mode == "bitbang":
                return True
            if mode == "PROG" and cur_mode == "prog":
                return True

        self._mode_switch_ts = time.time()
        self._mode_switch_guard_warned = False
        try:
            self._purge_pending_io()
            if mode == "GPIO":
                self._bitbang.enable(self._bitbang_mask)
                self._channel_modes[ch] = "bitbang"
                self._active_protocol = "GPIO"
                self._bitbang_i2c_warned = False
                self._mode_switch_ts = 0
                try:
                    self._ft.write(bytes([self._gpio_out_value & 0xFF]))
                except Exception:
                    pass
                return True

            # Leave bitbang when switching away from GPIO
            if self._channel_modes.get(ch) == "bitbang":
                try:
                    self._bitbang.disable()
                except Exception as e:
                    self._log(f"[WARN] bitbang.disable() failed: {e}")
                # Always clear bitbang state so I2C is not permanently blocked
                self._channel_modes[ch] = ""

            if mode == "SPI":
                if self.supports_mpsse(ch):
                    self._spi.configure(clock_hz=1_000_000)
                    self._channel_modes[ch] = "spi"
                    self._active_protocol = "SPI"
                    self._bitbang_i2c_warned = False
                    self._mode_switch_ts = 0
                    self._log(f"[INFO] Protocol mode: SPI (CH={ch})")
                else:
                    self._channel_modes[ch] = "uart"
                    self._active_protocol = "UART"
                    self._bitbang_i2c_warned = False
                    self._mode_switch_ts = 0
                return True

            if mode in ("I2C", "JTAG"):
                if self.supports_mpsse(ch):
                    self._i2c.configure()
                    self._channel_modes[ch] = "mpsse"
                    self._active_protocol = mode
                    self._bitbang_i2c_warned = False
                    self._mode_switch_ts = 0
                    self._gpio_low_direction = 0x00
                    # Ensure SCL/SDA bits are high so apply_gpio_out
                    # doesn't pull the I2C bus low after GPIO/bitbang mode.
                    self._gpio_out_value |= 0x03
                    try:
                        self._i2c.apply_gpio_out(self._gpio_out_value)
                    except Exception:
                        pass
                    # Restore high byte GPIO state after MPSSE re-init
                    if self._gpio_high_direction:
                        try:
                            self._i2c.set_bits_high(
                                self._gpio_high_out_value & 0xFF,
                                self._gpio_high_direction & 0xFF,
                            )
                        except Exception:
                            pass
                    # I2C bus recovery: if a previous GPIO/bitbang operation
                    # left a slave holding SDA low, clock SCL 9 times to
                    # free it.  Only on force re-init to avoid overhead on
                    # normal idempotent mode switches.
                    if force:
                        try:
                            self._i2c.recover_bus()
                        except Exception:
                            pass
                    self._log(f"[INFO] Protocol mode: {mode} (CH={ch})")
                else:
                    self._channel_modes[ch] = "uart"
                    self._active_protocol = "UART"
                    self._bitbang_i2c_warned = False
                    self._mode_switch_ts = 0
                return True

            if mode == "UART":
                try:
                    self._bitbang.disable()
                except Exception:
                    pass
                self._channel_modes[ch] = "uart"
                self._active_protocol = "UART"
                self._bitbang_i2c_warned = False
                self._mode_switch_ts = 0
                return True

            if mode == "PROG":
                # EEPROM operations use D2XX API directly;
                # reset bit mode to ensure clean D2XX state for eeRead/eeProgram.
                try:
                    self._ft.setBitMode(0x00, 0x00)
                except Exception:
                    pass
                self._channel_modes[ch] = "prog"
                self._active_protocol = "PROG"
                self._bitbang_i2c_warned = False
                self._mode_switch_ts = 0
                self._log(f"[INFO] Protocol mode: PROG (CH={ch})")
                return True
            self._log(f"[WARN] Unsupported protocol mode requested: {mode}")
        except Exception as e:
            self._channel_modes[ch] = ""
            self._mode_switch_ts = 0
            self._log(f"[ERROR] Operation failed: {e}")
            return False
        return False

    def set_gpio_backend(self, backend: str, force: bool = False) -> bool:
        """Force GPIO backend without changing protocol selection (GPIO tab use)."""
        if not self._is_connected or self._ft is None:
            return False
        ch = self._active_channel
        backend = backend.lower()
        locker = QMutexLocker(self._mutex)
        try:
            if backend == "bitbang":
                self._purge_pending_io()
                self._bitbang.enable(self._bitbang_mask)
                self._channel_modes[ch] = "bitbang"
                self._active_protocol = "GPIO"
                self._bitbang_i2c_warned = False
                self._mode_switch_ts = 0
                try:
                    self._ft.write(bytes([self._gpio_out_value & 0xFF]))
                except Exception:
                    pass
                return True
            if backend == "mpsse":
                if not self.supports_mpsse(ch):
                    return False
                cur_mode = self._channel_modes.get(ch, "?")
                if cur_mode == "mpsse" and force:
                    cur_mode = ""
                if cur_mode == "mpsse":
                    return True  # Already in MPSSE — skip re-init and purge
                self._purge_pending_io()
                if self._channel_modes.get(ch) == "bitbang":
                    self._bitbang.disable()
                self._gpio_low_direction = 0x00
                self._i2c.configure()
                self._channel_modes[ch] = "mpsse"
                self._active_protocol = "I2C"
                self._bitbang_i2c_warned = False
                self._mode_switch_ts = 0
                self._gpio_out_value |= 0x03
                try:
                    self._i2c.apply_gpio_out(self._gpio_out_value)
                except Exception:
                    pass
                # Restore high byte GPIO state after MPSSE re-init
                if self._gpio_high_direction:
                    try:
                        self._i2c.set_bits_high(
                            self._gpio_high_out_value & 0xFF,
                            self._gpio_high_direction & 0xFF,
                        )
                    except Exception:
                        pass
                return True
        except Exception as e:
            self._channel_modes[ch] = ""
            self._mode_switch_ts = 0
            self._log(f"[ERROR] GPIO backend switch failed: {e}")
        return False

    def _i2c_guard_active(self) -> bool:
        if self._mode_switch_ts <= 0:
            return False
        elapsed_ms = (time.time() - self._mode_switch_ts) * 1000.0
        return elapsed_ms < float(self._mode_switch_guard_ms)

    def _is_bitbang_active(self) -> bool:
        return self._channel_modes.get(self._active_channel) == "bitbang"

    def set_bitbang_mask(self, mask: int) -> None:
        """Update bitbang direction mask (1=output, 0=input)."""
        self._bitbang_mask = mask & 0xFF
        if not self._is_connected or self._ft is None:
            return
        if self._channel_modes.get(self._active_channel) == "bitbang":
            locker = QMutexLocker(self._mutex)
            try:
                self._bitbang.enable(self._bitbang_mask)
            except Exception as e:
                self._log(f"[ERROR] Bitbang mask set failed: {e}")

    def mpsse_set_gpio_low(self, mask: int, value: int) -> None:
        """Set low-byte GPIO pins directly via MPSSE set_bits_low.

        Unlike set_gpio_masked (which routes through apply_gpio_out with I2C
        direction 0x03), this method accumulates direction bits across calls
        (like set_gpio_high_masked does for the high byte) so that previously
        configured GPIO output pins are not reverted to input.

        Writes the MPSSE command directly to the FTDI handle to avoid any
        intermediate buffering or routing through the I2C controller.
        """
        locker = QMutexLocker(self._mutex)
        self._gpio_out_value = (self._gpio_out_value & ~mask) | (value & mask)
        self._gpio_low_direction |= (mask & 0xFF)
        if not self._is_connected or self._ft is None:
            return
        if self._channel_modes.get(self._active_channel, "") != "mpsse":
            return
        # Force SCL(D0)/SDA(D1) high for I2C bus safety
        out = self._gpio_out_value | 0x03
        # Direction: I2C pins + accumulated GPIO directions
        # NOTE: I2C hold is NOT applied here — this function is for explicit
        # GPIO control (FTDI Verifier GPIO tab).  The hold mask is designed
        # for I2C operations and must not override user GPIO output values.
        direction = 0x03 | self._gpio_low_direction
        out_byte = out & 0xFF
        dir_byte = direction & 0xFF
        try:
            # Write MPSSE set_bits_low + send_immediate directly to FTDI handle
            self._ft.write(bytes([0x80, out_byte, dir_byte, 0x87]))
            self._log(
                f"[GPIO-LOW] mask=0x{mask:02X} val=0x{value:02X} "
                f"out=0x{out_byte:02X} dir=0x{dir_byte:02X}"
            )
        except Exception as e:
            self._log(f"[ERROR] MPSSE GPIO write failed: {e}")

    def set_gpio_low(self, bit: int, high: bool) -> None:
        """Set a single ADBUS GPIO bit high/low in current mode."""
        if bit < 0 or bit > 7:
            return
        locker = QMutexLocker(self._mutex)
        if high:
            self._gpio_out_value |= (1 << bit)
        else:
            self._gpio_out_value &= ~(1 << bit)
        self._apply_gpio_out_locked()

    def set_gpio_masked(self, mask: int, value: int) -> None:
        """Set GPIO outputs with mask."""
        locker = QMutexLocker(self._mutex)
        self._gpio_out_value = (self._gpio_out_value & ~mask) | (value & mask)
        self._apply_gpio_out_locked()

    def _apply_gpio_out(self) -> None:
        if not self._is_connected or self._ft is None:
            return
        locker = QMutexLocker(self._mutex)
        self._apply_gpio_out_locked()

    def _apply_gpio_out_locked(self) -> None:
        if not self._is_connected or self._ft is None:
            return
        mode = self._channel_modes.get(self._active_channel, "")
        try:
            if mode == "bitbang":
                self._ft.write(bytes([self._gpio_out_value & 0xFF]))
                return
            if mode == "spi":
                # D4-D7은 SPI 핀이 아니므로 set_gpio로 제어
                self._spi.set_gpio(0xF0, self._gpio_out_value & 0xF0, 0xF0)
                return
            if mode == "mpsse":
                self._i2c.apply_gpio_out(self._gpio_out_value)
        except Exception as e:
            self._log(f"[ERROR] GPIO write failed: {e}")

    def set_gpio_high_masked(self, mask: int, value: int) -> None:
        """Set GPIO outputs on the high byte (ACBUS/BCBUS) in MPSSE.

        Accumulates direction and value state across calls so that
        previously configured pins are not reset to input.
        """
        if not self._is_connected or self._ft is None:
            return
        locker = QMutexLocker(self._mutex)
        mode = self._channel_modes.get(self._active_channel, "")
        if mode != "mpsse":
            return
        try:
            self._gpio_high_direction |= (mask & 0xFF)
            self._gpio_high_out_value = (self._gpio_high_out_value & ~mask) | (value & mask)
            val_byte = self._gpio_high_out_value & 0xFF
            dir_byte = self._gpio_high_direction & 0xFF
            self._log(
                f"[GPIO-HIGH] mode={mode}, mask=0x{mask:02X}, "
                f"val=0x{val_byte:02X}, dir=0x{dir_byte:02X}"
            )
            self._i2c.set_bits_high(val_byte, dir_byte)
        except Exception as e:
            self._log(f"[ERROR] GPIO high write failed: {e}")

    def get_device_info(self, serial: Optional[str] = None) -> dict:
        key = serial or self._serial_number
        info = FtdiManager._device_cache.get(key, {}).copy()
        if info:
            info["channel"] = self._channel
            info["connected"] = self._is_connected
        return info

    def _device_type_upper(self) -> str:
        return str(self.get_device_info(self._serial_number).get("device_type") or "").upper()

    def _is_ft2232_device(self) -> bool:
        return "2232" in self._device_type_upper()

    def _emit_current_device_info(self) -> None:
        if not self._serial_number:
            return
        cached = FtdiManager._device_cache.get(self._serial_number, {})
        self.device_info_changed.emit(
            {
                "serial": self._serial_number,
                "channel": self._active_channel,
                "desc": cached.get("desc", ""),
                "channels": list(self._available_channels),
                "device_type": cached.get("device_type", ""),
                "connected": self._is_connected,
            }
        )

    # Device enumeration

    @staticmethod
    def _clean_ftdi_text(value: object) -> str:
        if isinstance(value, (bytes, bytearray)):
            text = value.decode(errors="ignore")
        else:
            text = str(value or "")
        return text.replace("\x00", "").strip()

    @staticmethod
    def _detect_channel(serial: str, desc: str) -> Optional[str]:
        """Detect channel letter only when both serial and description agree.

        Multi-channel FTDI devices (FT2232H, FT4232H) have the channel
        letter appended to BOTH serial and description by the D2XX driver.
        Single-channel devices (FT232H) do not.  Requiring agreement
        prevents false detection when a single-channel device's serial or
        description happens to end with A-D (e.g. serial 'FTBEQDX' with
        desc ending in ' D').
        """
        serial_ch = None
        if serial and serial[-1].upper() in ("A", "B", "C", "D"):
            serial_ch = serial[-1].upper()

        desc_ch = None
        desc_upper = desc.upper()
        for ch in ("A", "B", "C", "D"):
            if desc_upper.endswith(f" {ch}"):
                desc_ch = ch
                break

        if serial_ch and desc_ch and serial_ch == desc_ch:
            return serial_ch
        return None

    @staticmethod
    def _normalize_serial(serial_raw: str, detected_channel: Optional[str] = None) -> str:
        """Strip trailing channel letter to get base serial.

        Only strips the last character when *detected_channel* confirms it
        is actually a channel suffix.  This prevents mis-stripping from
        single-channel devices whose serial happens to end with A-D.
        """
        serial = serial_raw.strip()
        if detected_channel:
            if len(serial) >= 2 and serial[-1].upper() == detected_channel:
                return serial[:-1]
            if len(serial) == 1 and serial.upper() == detected_channel:
                return ""
        return serial

    @staticmethod
    def _infer_device_type(desc: str, channels: List[str]) -> str:
        desc_upper = (desc or "").upper()
        if "4232" in desc_upper or len(channels) >= 4:
            return "FT4232H"
        if "2232" in desc_upper or "DUAL" in desc_upper or len(channels) >= 2:
            return "FT2232H"
        if "232" in desc_upper or len(channels) <= 1:
            return "FT232H"
        return "FTDI"

    @staticmethod
    def scan_devices() -> List[Tuple[str, str]]:
        """FTDI device scan helper.

        Returns:
            Returns [(base_serial, description), ...]
        """
        devices: List[Tuple[str, str]] = []
        for serial, desc, _channels, _dtype in FtdiManager.scan_devices_with_channels():
            devices.append((serial, desc))
        return devices

    @staticmethod
    def scan_devices_with_channels() -> List[Tuple[str, str, List[str], str]]:
        # Scan FTDI devices with channel list
        devices_map: dict[str, dict[str, object]] = {}
        try:
            import ftd2xx

            count = ftd2xx.createDeviceInfoList()
            for i in range(count):
                info = ftd2xx.getDeviceInfoDetail(i)
                serial = FtdiManager._clean_ftdi_text(info.get("serial", b""))
                desc = FtdiManager._clean_ftdi_text(info.get("description", b""))

                # Detect channel first so _normalize_serial knows whether
                # the trailing letter is actually a channel suffix.
                channel = FtdiManager._detect_channel(serial, desc)
                base_serial = FtdiManager._normalize_serial(serial, channel)

                # When serial is just a channel letter (e.g. "A", "B"),
                # derive group key from description base
                if not base_serial:
                    desc_base = desc.strip()
                    if channel and desc_base.upper().endswith(f" {channel}"):
                        desc_base = desc_base[:-2].strip()
                    base_serial = desc_base if desc_base else serial
                    if not base_serial:
                        continue

                entry = devices_map.setdefault(
                    base_serial, {"desc": desc, "channels": set(), "index_map": {}, "entries": []}
                )
                if desc and not entry["desc"]:
                    entry["desc"] = desc
                entry["entries"].append({"index": i, "channel": channel})
                if channel:
                    entry["channels"].add(channel)
                    entry["index_map"][channel] = i

        except ImportError:
            logger.warning("ftd2xx library is not installed.")
        except Exception as e:
            logger.error(f"device scan error: {e}")

        devices: List[Tuple[str, str, List[str], str]] = []
        FtdiManager._device_cache = {}
        for serial, meta in devices_map.items():
            desc = str(meta.get("desc") or "")
            index_map = dict(meta.get("index_map") or {})
            entries = sorted(meta.get("entries") or [], key=lambda item: int(item["index"]))
            entry_count = len(entries)
            expected_count = 1
            desc_upper = desc.upper()
            if "4232" in desc_upper or entry_count >= 4:
                expected_count = 4
            elif "2232" in desc_upper or "DUAL" in desc_upper or entry_count >= 2:
                expected_count = 2
            remaining_channels = [
                ch for ch in ("A", "B", "C", "D")[:expected_count] if ch not in index_map
            ]
            for entry_info in entries:
                if entry_info.get("channel") or not remaining_channels:
                    continue
                index_map.setdefault(remaining_channels.pop(0), int(entry_info["index"]))
            channels = sorted(index_map.keys() or ["A"])
            device_type = FtdiManager._infer_device_type(desc, channels)
            devices.append((serial, desc, channels, device_type))
            FtdiManager._device_cache[serial] = {
                "serial": serial,
                "desc": desc,
                "channels": channels,
                "device_type": device_type,
                "index_map": index_map,
            }
        return devices

    def _find_device_index(self, serial_number: str, channel: str) -> Optional[int]:
        """Find the device index for a serial/channel pair."""
        import ftd2xx

        # Normalize target serial using target channel context
        target_ch = channel.upper()
        target_base = self._normalize_serial(serial_number, target_ch)
        count = ftd2xx.createDeviceInfoList()
        fallback_index: Optional[int] = None

        for i in range(count):
            info = ftd2xx.getDeviceInfoDetail(i)
            serial = self._clean_ftdi_text(info.get("serial", b""))
            desc = self._clean_ftdi_text(info.get("description", b""))

            detected_ch = self._detect_channel(serial, desc)
            base_serial = self._normalize_serial(serial, detected_ch)

            # Match by normalized serial or by description base
            match = False
            if target_base and base_serial == target_base:
                match = True
            elif not base_serial:
                # Serial is a single channel letter — match via description
                desc_base = desc.strip()
                if detected_ch and desc_base.upper().endswith(f" {detected_ch}"):
                    desc_base = desc_base[:-2].strip()
                if desc_base == target_base:
                    match = True

            if not match:
                continue

            if detected_ch == target_ch:
                return i

            if fallback_index is None:
                fallback_index = i
        return fallback_index

    def _build_device_index_map(
        self, serial_number: str, channels: List[str]
    ) -> dict:
        """Enumerate devices once and return {channel: index} map."""
        import ftd2xx

        # Use first channel letter as context for normalizing target serial
        ch_ctx = channels[0] if channels else None
        target_base = self._normalize_serial(serial_number, ch_ctx)
        result: dict = {}
        unknown_indexes: list[int] = []
        count = ftd2xx.createDeviceInfoList()

        for i in range(count):
            info = ftd2xx.getDeviceInfoDetail(i)
            serial = self._clean_ftdi_text(info.get("serial", b""))
            desc = self._clean_ftdi_text(info.get("description", b""))

            channel = self._detect_channel(serial, desc)
            base_serial = self._normalize_serial(serial, channel)
            match = False
            if target_base and base_serial == target_base:
                match = True
            elif not base_serial:
                desc_base = desc.strip()
                if channel and desc_base.upper().endswith(f" {channel}"):
                    desc_base = desc_base[:-2].strip()
                if desc_base == target_base:
                    match = True
            if not match:
                continue
            if channel and channel in channels and channel not in result:
                result[channel] = i
                continue
            unknown_indexes.append(i)

        remaining_channels = [ch for ch in channels if ch not in result]
        for idx, ch in zip(unknown_indexes, remaining_channels):
            result[ch] = idx

        return result

    def _resolve_device_index_map(
        self, serial_number: str, channels: List[str]
    ) -> dict[str, int]:
        cached = FtdiManager._device_cache.get(serial_number, {})
        cached_map = dict(cached.get("index_map") or {})
        if all(ch in cached_map for ch in channels):
            return {ch: int(cached_map[ch]) for ch in channels}

        rebuilt = self._build_device_index_map(serial_number, channels)
        merged = {**cached_map, **rebuilt}
        if serial_number in FtdiManager._device_cache:
            FtdiManager._device_cache[serial_number]["index_map"] = dict(merged)
        return {ch: int(merged[ch]) for ch in channels if ch in merged}

    def _open_channel_handle(self, channel: str) -> bool:
        ch = channel.upper()
        if ch in self._ft_handles:
            return True

        index = self._channel_index_map.get(ch)
        if index is None:
            self._log(f"[ERROR] No cached device index for CH={ch}")
            return False

        try:
            import ftd2xx

            prev_ft = self._ft
            prev_channel = self._channel
            ft = ftd2xx.open(index)
            self._ft_handles[ch] = ft
            self._ft = ft
            self._channel = ch
            self._log(
                f"[INFO] Opened requested channel: SN={self._serial_number}, CH={ch}, IDX={index}"
            )
            if self.supports_mpsse(ch):
                self._configure_mpsse()
                self._channel_modes[ch] = "mpsse"
                self._active_protocol = "I2C"
                self._bitbang_i2c_warned = False
                self._mode_switch_ts = 0
                self._gpio_out_value = 0x03  # Clean start: only SCL/SDA high
                try:
                    self._i2c.apply_gpio_out(self._gpio_out_value)
                except Exception:
                    pass
                if self._gpio_high_direction:
                    try:
                        self._i2c.set_bits_high(
                            self._gpio_high_out_value & 0xFF,
                            self._gpio_high_direction & 0xFF,
                        )
                    except Exception:
                        pass
                # I2C bus recovery on connect: free any slave stuck from
                # a previous session's interrupted transaction.
                try:
                    self._i2c.recover_bus()
                except Exception:
                    pass
            else:
                self._channel_modes[ch] = "uart"
                self._active_protocol = "UART"

            if self._active_channel != ch and prev_ft is not None:
                self._ft = prev_ft
                self._channel = prev_channel
            return True
        except Exception as e:
            self._log(
                f"[ERROR] Channel open/init failed: SN={self._serial_number}, CH={ch}, IDX={index}, ERR={e}"
            )
            ft = self._ft_handles.pop(ch, None)
            if ft is not None:
                try:
                    ft.close()
                except Exception:
                    pass
            if self._active_channel in self._ft_handles:
                self._ft = self._ft_handles[self._active_channel]
                self._channel = self._active_channel
            else:
                self._ft = None
            return False

    def _set_lines(self, scl_high: bool, sda_high: bool) -> None:
        """Configure SCL/SDA GPIO lines."""
        self._i2c.set_lines(scl_high=scl_high, sda_high=sda_high)

    def _purge_pending_io(self) -> None:
        """Best-effort purge of stale USB/MPSSE data on the active handle."""
        if self._ft is None:
            return
        try:
            self._ft.purge(3)
        except Exception:
            pass
        try:
            queued = self._ft.getQueueStatus()
        except Exception:
            queued = 0
        if queued > 0:
            try:
                self._ft.read(queued)
            except Exception:
                pass

    def purge_pending_io(self) -> None:
        """Thread-safe public wrapper for best-effort USB/MPSSE purge."""
        if not self._is_connected or self._ft is None:
            return
        locker = QMutexLocker(self._mutex)
        self._purge_pending_io()

    def set_i2c_hold(self, mask: int, value: int) -> None:
        """Hold GPIO states on ADBUS while in MPSSE I2C (bits 4-7 recommended)."""
        self._i2c_hold_mask = mask & 0xFF
        self._i2c_hold_value = value & self._i2c_hold_mask
        if not self._is_connected or self._ft is None:
            return
        if not self.supports_mpsse(self._active_channel):
            return
        locker = QMutexLocker(self._mutex)
        try:
            self._i2c.apply_i2c_hold()
        except Exception as e:
            self._log(f"[WARN] I2C hold apply failed: {e}")

    def clear_i2c_hold(self) -> None:
        self.set_i2c_hold(0x00, 0x00)

    def get_i2c_hold(self) -> tuple[int, int]:
        return self._i2c_hold_mask, self._i2c_hold_value

    def _configure_mpsse(self) -> None:
        """Initialize MPSSE for I2C."""
        self._i2c.configure()

    def open_device(self, serial_number: str, channel: str = "A") -> bool:
        """Open FTDI device.

        Args:
            serial_number: FTDI serial number
            channel: channel (A/B/C/D)

        Returns:
              
        """
        if self._is_connected:
            self.close_device()

        self._serial_number = serial_number
        self._active_channel = channel.upper()
        self._channel = self._active_channel
        self._ft_handles = {}
        self._channel_index_map = {}
        self._available_channels = []
        self._channel_modes = {}

        try:
            cached = FtdiManager._device_cache.get(self._serial_number, {})
            channels = cached.get("channels") or [self._active_channel]
            device_type = str(cached.get("device_type") or "").upper()
            self._available_channels = list(channels)
            if self._active_channel not in channels:
                raise RuntimeError(
                    f"Requested channel is not available. SN={serial_number}, CH={self._active_channel}"
                )

            self._channel_index_map = self._resolve_device_index_map(serial_number, channels)
            self._log(
                f"[INFO] Connect request: SN={serial_number}, requested={self._active_channel}, "
                f"available={','.join(channels)}, cached={sorted(self._channel_index_map.keys())}"
            )
            if self._active_channel not in self._channel_index_map:
                raise RuntimeError(
                    f"Requested channel index was not resolved. SN={serial_number}, CH={self._active_channel}"
                )
            preopen_channels = list(channels) if "2232" in device_type else [self._active_channel]
            if len(preopen_channels) > 1:
                self._log(
                    f"[INFO] Pre-opening channels: SN={serial_number}, CH={','.join(preopen_channels)}"
                )
            for ch in preopen_channels:
                if not self._open_channel_handle(ch):
                    raise RuntimeError(
                        f"Channel open failed during connect. SN={serial_number}, CH={ch}"
                    )

            self._ft = self._ft_handles[self._active_channel]
            self._channel = self._active_channel

            self._is_connected = True
            info = f"Connected: SN={serial_number}, CH={self._active_channel}"
            self._log(info)
            self.device_connected.emit(info)
            self._emit_current_device_info()
            return True
        except ImportError:
            err = "ftd2xx library is not installed."
            self._log(f"[ERROR] {err}")
            self.comm_error.emit(err)
            return False
        except Exception as e:
            err = f"Open error: {e}"
            self._log(f"[ERROR] {err}")
            self.comm_error.emit(err)
            try:
                for ft in self._ft_handles.values():
                    try:
                        ft.close()
                    except Exception:
                        pass
            except Exception:
                pass
            self._ft_handles = {}
            self._ft = None
            self._is_connected = False
            self._available_channels = []
            self._channel_modes = {}
            self._channel_index_map = {}
            self._serial_number = ""
            self._active_channel = "A"
            self._channel = "A"
            return False

    def close_device(self) -> None:
        """Close FTDI device and release all handles."""
        # Close handles under mutex to prevent worker race conditions
        with QMutexLocker(self._mutex):
            try:
                for ch, ft in list(self._ft_handles.items()):
                    if ft is None:
                        continue
                    try:
                        self._ft = ft
                        if self.supports_mpsse(ch):
                            try:
                                self._set_lines(scl_high=True, sda_high=True)
                            except Exception:
                                pass
                            ft.setBitMode(0x00, 0x00)
                        else:
                            try:
                                ft.setBitMode(0x00, 0x00)
                            except Exception:
                                pass
                        ft.close()
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Device close warning: {e}")
            finally:
                self._ft = None
                self._ft_handles = {}
                self._channel_index_map = {}
                self._available_channels = []
                self._is_connected = False
                self._serial_number = ""
                self._active_channel = "A"
                self._channel = "A"
                self._channel_modes = {}
                self._gpio_out_value = 0x00
                self._gpio_low_direction = 0x00
                self._gpio_high_out_value = 0x00
                self._gpio_high_direction = 0x00
                self._i2c_hold_mask = 0x00
                self._i2c_hold_value = 0x00
                self._mode_switch_ts = 0.0
                self._mode_switch_guard_warned = False
                self._bitbang_i2c_warned = False
        # Emit signals AFTER releasing mutex to avoid deadlock
        self._log("Disconnected.")
        self.device_disconnected.emit()
        self.device_info_changed.emit(
                {
                    "serial": "",
                    "channel": "",
                    "desc": "",
                    "channels": [],
                    "device_type": "",
                    "connected": False,
                }
            )

    # I2C access (with mutex)

    # I2C (delegated to MPSSE controller)

    def i2c_write(self, slave_addr: int, data: bytes) -> bool:
        # I2C write transaction (thread-safe)
        if not self._is_connected or self._ft is None:
            self.comm_error.emit("Device not connected.")
            return False
        if self._i2c_guard_active():
            if not self._mode_switch_guard_warned:
                self._log("[WARN] I2C blocked during mode switch.")
                self._mode_switch_guard_warned = True
            return False
        if self._is_bitbang_active():
            if not self._bitbang_i2c_warned:
                self._log("[WARN] Bitbang mode blocks I2C.")
                self._bitbang_i2c_warned = True
            return False
        if not self.supports_mpsse(self._active_channel):
            self.comm_error.emit("MPSSE is required for I2C.")
            return False
        if self._channel_modes.get(self._active_channel, "") != "mpsse":
            if not self.set_protocol_mode("I2C"):
                self.comm_error.emit("I2C backend is not ready.")
                return False

        locker = QMutexLocker(self._mutex)
        return self._i2c.i2c_write(slave_addr, data)

    def i2c_read(self, slave_addr: int, write_prefix: bytes, read_len: int) -> Optional[bytes]:
        # I2C read transaction (thread-safe)
        if not self._is_connected or self._ft is None:
            self.comm_error.emit("Device not connected.")
            return None
        if self._i2c_guard_active():
            if not self._mode_switch_guard_warned:
                self._log("[WARN] I2C blocked during mode switch.")
                self._mode_switch_guard_warned = True
            return None
        if self._is_bitbang_active():
            if not self._bitbang_i2c_warned:
                self._log("[WARN] Bitbang mode blocks I2C.")
                self._bitbang_i2c_warned = True
            return None
        if not self.supports_mpsse(self._active_channel):
            self.comm_error.emit("MPSSE is required for I2C.")
            return None
        if self._channel_modes.get(self._active_channel, "") != "mpsse":
            if not self.set_protocol_mode("I2C"):
                self.comm_error.emit("I2C backend is not ready.")
                return None
        locker = QMutexLocker(self._mutex)
        return self._i2c.i2c_read(slave_addr, write_prefix, read_len)

    def i2c_scan(self, addr_start: int = 0x08, addr_end: int = 0x77) -> List[int]:
        """Scan I2C addresses.

        Args:
            addr_start: start 7-bit address
            addr_end: end 7-bit address (inclusive)

        Returns:
            ACKed 7-bit addresses
        """
        if not self._is_connected or self._ft is None:
            return []
        if self._i2c_guard_active():
            if not self._mode_switch_guard_warned:
                self._log("[WARN] I2C blocked during mode switch.")
                self._mode_switch_guard_warned = True
            return []
        if self._is_bitbang_active():
            if not self._bitbang_i2c_warned:
                self._log("[WARN] Bitbang mode blocks I2C.")
                self._bitbang_i2c_warned = True
            return []
        if not self.supports_mpsse(self._active_channel):
            self.comm_error.emit("MPSSE is required for I2C.")
            return []
        if self._channel_modes.get(self._active_channel, "") != "mpsse":
            if not self.set_protocol_mode("I2C"):
                self.comm_error.emit("I2C backend is not ready.")
                return []

        locker = QMutexLocker(self._mutex)
        return self._i2c.i2c_scan(addr_start, addr_end)

    # SPI access (with mutex)

    def spi_configure(self, clock_hz: int = 1_000_000,
                      cpol: int = 0, cpha: int = 0) -> None:
        """Configure SPI clock and mode."""
        if not self._is_connected or self._ft is None:
            return
        if self._channel_modes.get(self._active_channel) != "spi":
            if not self.set_protocol_mode("SPI"):
                return
        locker = QMutexLocker(self._mutex)
        self._spi.reconfigure(clock_hz=clock_hz, cpol=cpol, cpha=cpha)

    def spi_transfer(self, tx_data: bytes,
                     cs_pin: int = SpiController.PIN_CS0) -> Optional[bytes]:
        """Full-duplex SPI transfer (thread-safe).

        Args:
            tx_data: bytes to transmit
            cs_pin: chip-select pin mask (default ADBUS3=0x08)

        Returns:
            Received bytes, or None on error.
        """
        if not self._is_connected or self._ft is None:
            self.comm_error.emit("Device not connected.")
            return None
        if not self.supports_mpsse(self._active_channel):
            self.comm_error.emit("MPSSE is required for SPI.")
            return None
        if self._channel_modes.get(self._active_channel) != "spi":
            if not self.set_protocol_mode("SPI"):
                return None
        locker = QMutexLocker(self._mutex)
        try:
            return self._spi.transfer(tx_data, cs_pin)
        except Exception as e:
            err = f"SPI transfer error: {e}"
            self._log(f"[Error] {err}")
            self.comm_error.emit(err)
            return None

    def spi_write(self, tx_data: bytes,
                  cs_pin: int = SpiController.PIN_CS0) -> bool:
        """Write-only SPI transfer (thread-safe)."""
        if not self._is_connected or self._ft is None:
            self.comm_error.emit("Device not connected.")
            return False
        if not self.supports_mpsse(self._active_channel):
            self.comm_error.emit("MPSSE is required for SPI.")
            return False
        if self._channel_modes.get(self._active_channel) != "spi":
            if not self.set_protocol_mode("SPI"):
                return False
        locker = QMutexLocker(self._mutex)
        try:
            self._spi.write_only(tx_data, cs_pin)
            return True
        except Exception as e:
            err = f"SPI write error: {e}"
            self._log(f"[Error] {err}")
            self.comm_error.emit(err)
            return False

    def spi_write_then_read(self, write_data: bytes, read_len: int,
                            cs_pin: int = SpiController.PIN_CS0) -> Optional[bytes]:
        """Half-duplex SPI transaction under one CS: write then read."""
        if not self._is_connected or self._ft is None:
            self.comm_error.emit("Device not connected.")
            return None
        if not self.supports_mpsse(self._active_channel):
            self.comm_error.emit("MPSSE is required for SPI.")
            return None
        if self._channel_modes.get(self._active_channel) != "spi":
            if not self.set_protocol_mode("SPI"):
                return None
        locker = QMutexLocker(self._mutex)
        try:
            return self._spi.write_then_read(write_data, read_len, cs_pin)
        except Exception as e:
            err = f"SPI write/read error: {e}"
            self._log(f"[Error] {err}")
            self.comm_error.emit(err)
            return None

    def spi_set_gpio(self, mask: int, value: int,
                     direction: int = 0xFF) -> None:
        """Set GPIO on SPI low-byte non-SPI pins."""
        if not self._is_connected or self._ft is None:
            return
        locker = QMutexLocker(self._mutex)
        try:
            self._spi.set_gpio(mask, value, direction)
        except Exception as e:
            self._log(f"[Error] SPI GPIO error: {e}")

    def read_gpio_low(self) -> Optional[int]:
        """Read low GPIO bits (ADBUS) in current mode."""
        if not self._is_connected or self._ft is None:
            return None
        locker = QMutexLocker(self._mutex)
        try:
            mode = self._channel_modes.get(self._active_channel, "")
            if mode == "bitbang":
                value = self._bitbang.read_pins()
                return value if value is not None else None
            if mode == "spi":
                return self._spi.read_gpio_low()
            if mode == "mpsse":
                return self._i2c.read_gpio_low()
            return None
        except Exception as e:
            self._log(f"[ERROR] GPIO read failed: {e}")
            return None

    def read_gpio_high(self) -> Optional[int]:
        """Read high GPIO bits (ACBUS/BCBUS) in MPSSE mode."""
        if not self._is_connected or self._ft is None:
            return None
        locker = QMutexLocker(self._mutex)
        try:
            mode = self._channel_modes.get(self._active_channel, "")
            if mode != "mpsse":
                return None
            return self._i2c.read_gpio_high()
        except Exception as e:
            self._log(f"[ERROR] GPIO high read failed: {e}")
            return None

    # SMBus helpers (PI6CG18201)

    def smbus_block_write(self, slave_addr: int, command: int, data: bytes) -> bool:
        """SMBus Block Write.

        Format: [slave_addr_w, command, byte_count, data...]

        Args:
            slave_addr: 7-bit slave address
            command: 8-bit command
            data: payload bytes

        Returns:
            True if write succeeds.
        """
        if len(data) == 0 or len(data) > 0x20:
            raise ValueError(f"SMBus block write length mismatch: {len(data)}")
        payload = bytes([command, len(data)]) + bytes(data)
        return self.i2c_write(slave_addr, payload)

    def smbus_block_read(self, slave_addr: int, command: int, length: int) -> Optional[bytes]:
        """SMBus Block Read.

        Args:
            slave_addr: 7-bit slave address
            command: 8-bit command
            length: expected byte count

        Returns:
            Returns None on byte_count mismatch.
        """
        if not (1 <= length <= 0x20):
            raise ValueError(f"SMBus block read length mismatch: {length}")

        raw = self.i2c_read(slave_addr, bytes([command]), length + 1)
        if raw is None or len(raw) < 1:
            return None

        count = raw[0]
        data = raw[1:]
        if count != len(data):
            self._log(
                f"[WARN] SMBus count({count}) != expected({len(data)})"
            )
        return data[:length]

    # Misc

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        full_msg = f"[{timestamp}] {message}"
        logger.info(full_msg)
        self.log_message.emit(full_msg)
