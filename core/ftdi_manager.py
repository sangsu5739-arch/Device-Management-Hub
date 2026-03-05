"""
Universal Device Studio - FTDI MPSSE manager (Singleton)

FT4232H via ftd2xx + MPSSE for I2C / SPI access.
Thread-safe FTDI access.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, Signal, QMutex, QMutexLocker

from core.i2c_controller import I2cController
from core.spi_controller import SpiController
from core.ftdi_bitbang import BitbangController

logger = logging.getLogger(__name__)


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
        self._gpio_high_direction: int = 0x00
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
        if ch not in self._ft_handles:
            return False
        self._active_channel = ch
        self._channel = ch
        self._ft = self._ft_handles.get(ch)
        self.active_channel_changed.emit(ch)
        return True

    def set_protocol_mode(self, mode: str) -> None:
        """Switch FTDI mode based on protocol selection."""
        if not self._is_connected or self._ft is None:
            return

        ch = self._active_channel
        mode = mode.upper()
        self._mode_switch_ts = time.time()
        self._mode_switch_guard_warned = False
        try:
            if mode == "GPIO":
                self._bitbang.enable(self._bitbang_mask)
                self._channel_modes[ch] = "bitbang"
                self._bitbang_i2c_warned = False
                try:
                    self._ft.write(bytes([self._gpio_out_value & 0xFF]))
                except Exception:
                    pass
                return

            # Leave bitbang when switching away from GPIO
            if self._channel_modes.get(ch) == "bitbang":
                self._bitbang.disable()

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
                    self._bitbang_i2c_warned = False
                return

            if mode in ("I2C", "JTAG"):
                if self.supports_mpsse(ch):
                    self._i2c.configure()
                    self._channel_modes[ch] = "mpsse"
                    self._active_protocol = "I2C"
                    self._bitbang_i2c_warned = False
                    self._mode_switch_ts = 0
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
                    self._log(f"[INFO] Protocol mode: I2C (CH={ch})")
                else:
                    self._channel_modes[ch] = "uart"
                    self._bitbang_i2c_warned = False
                return

            if mode == "UART":
                self._bitbang.disable()
                self._channel_modes[ch] = "uart"
                self._bitbang_i2c_warned = False
        except Exception as e:
            self._log(f"[ERROR] Operation failed: {e}")

    def set_gpio_backend(self, backend: str) -> bool:
        """Force GPIO backend without changing protocol selection (GPIO tab use)."""
        if not self._is_connected or self._ft is None:
            return False
        ch = self._active_channel
        backend = backend.lower()
        try:
            if backend == "bitbang":
                self._bitbang.enable(self._bitbang_mask)
                self._channel_modes[ch] = "bitbang"
                self._bitbang_i2c_warned = False
                try:
                    self._ft.write(bytes([self._gpio_out_value & 0xFF]))
                except Exception:
                    pass
                return True
            if backend == "mpsse":
                if not self.supports_mpsse(ch):
                    return False
                cur_mode = self._channel_modes.get(ch, "?")
                self._log(f"[GPIO-BACKEND] mpsse requested, current mode={cur_mode}")
                if cur_mode == "mpsse":
                    return True  # Already in MPSSE — skip re-init
                if self._channel_modes.get(ch) == "bitbang":
                    self._bitbang.disable()
                self._i2c.configure()
                self._channel_modes[ch] = "mpsse"
                self._bitbang_i2c_warned = False
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
            try:
                self._bitbang.enable(self._bitbang_mask)
            except Exception as e:
                self._log(f"[ERROR] Bitbang mask set failed: {e}")

    def set_gpio_low(self, bit: int, high: bool) -> None:
        """Set a single ADBUS GPIO bit high/low in current mode."""
        if bit < 0 or bit > 7:
            return
        if high:
            self._gpio_out_value |= (1 << bit)
        else:
            self._gpio_out_value &= ~(1 << bit)
        self._apply_gpio_out()

    def set_gpio_masked(self, mask: int, value: int) -> None:
        """Set GPIO outputs with mask."""
        self._gpio_out_value = (self._gpio_out_value & ~mask) | (value & mask)
        self._apply_gpio_out()

    def _apply_gpio_out(self) -> None:
        if not self._is_connected or self._ft is None:
            return
        mode = self._channel_modes.get(self._active_channel, "mpsse")
        try:
            if mode == "bitbang":
                self._ft.write(bytes([self._gpio_out_value & 0xFF]))
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
        mode = self._channel_modes.get(self._active_channel, "mpsse")
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

    # Device enumeration

    @staticmethod
    def _normalize_serial(serial_raw: str) -> str:
        """List channels for the current device (A/B/C/D)."""
        serial = serial_raw.strip()
        if len(serial) >= 2 and serial[-1] in ("A", "B", "C", "D"):
            return serial[:-1]
        return serial

    @staticmethod
    def _infer_device_type(desc: str, channels: List[str]) -> str:
        desc_upper = (desc or "").upper()
        if "4232" in desc_upper or len(channels) >= 4:
            return "FT4232H"
        if "2232" in desc_upper or len(channels) >= 2:
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
                serial_raw = info.get("serial", b"")
                desc_raw = info.get("description", b"")
                serial = (
                    serial_raw.decode(errors="ignore")
                    if isinstance(serial_raw, (bytes, bytearray))
                    else str(serial_raw)
                )
                desc = (
                    desc_raw.decode(errors="ignore")
                    if isinstance(desc_raw, (bytes, bytearray))
                    else str(desc_raw)
                )
                base_serial = FtdiManager._normalize_serial(serial)
                if not base_serial:
                    continue

                channel = None
                if serial and serial[-1].upper() in ("A", "B", "C", "D"):
                    channel = serial[-1].upper()
                else:
                    desc_upper = desc.upper()
                    for ch in ("A", "B", "C", "D"):
                        if desc_upper.endswith(f" {ch}"):
                            channel = ch
                            break

                entry = devices_map.setdefault(
                    base_serial, {"desc": desc, "channels": set()}
                )
                if desc and not entry["desc"]:
                    entry["desc"] = desc
                if channel:
                    entry["channels"].add(channel)
                else:
                    entry["channels"].add("A")

        except ImportError:
            logger.warning("ftd2xx library is not installed.")
        except Exception as e:
            logger.error(f"device scan error: {e}")

        devices: List[Tuple[str, str, List[str], str]] = []
        FtdiManager._device_cache = {}
        for serial, meta in devices_map.items():
            desc = str(meta.get("desc") or "")
            channels = sorted(meta.get("channels") or ["A"])
            device_type = FtdiManager._infer_device_type(desc, channels)
            devices.append((serial, desc, channels, device_type))
            FtdiManager._device_cache[serial] = {
                "serial": serial,
                "desc": desc,
                "channels": channels,
                "device_type": device_type,
            }
        return devices

    def _find_device_index(self, serial_number: str, channel: str) -> Optional[int]:
        """Find the device index for a serial/channel pair."""
        import ftd2xx

        target_base = self._normalize_serial(serial_number)
        target_ch = channel.upper()
        count = ftd2xx.createDeviceInfoList()
        fallback_index: Optional[int] = None

        for i in range(count):
            info = ftd2xx.getDeviceInfoDetail(i)
            serial_raw = info.get("serial", b"")
            desc_raw = info.get("description", b"")
            serial = (
                serial_raw.decode(errors="ignore")
                if isinstance(serial_raw, (bytes, bytearray))
                else str(serial_raw)
            )
            desc = (
                desc_raw.decode(errors="ignore")
                if isinstance(desc_raw, (bytes, bytearray))
                else str(desc_raw)
            )
            base_serial = self._normalize_serial(serial)
            if base_serial != target_base:
                continue

            serial_ch = serial[-1].upper() if serial else ""
            desc_upper = desc.upper()
            if serial_ch == target_ch or desc_upper.endswith(f" {target_ch}"):
                return i

            if fallback_index is None:
                fallback_index = i
        return fallback_index

    def _set_lines(self, scl_high: bool, sda_high: bool) -> None:
        """Configure SCL/SDA GPIO lines."""
        self._i2c.set_lines(scl_high=scl_high, sda_high=sda_high)

    def set_i2c_hold(self, mask: int, value: int) -> None:
        """Hold GPIO states on ADBUS while in MPSSE I2C (bits 4-7 recommended)."""
        self._i2c_hold_mask = mask & 0xFF
        self._i2c_hold_value = value & self._i2c_hold_mask
        if not self._is_connected or self._ft is None:
            return
        if not self.supports_mpsse(self._active_channel):
            return
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
        self._available_channels = []

        try:
            import ftd2xx

            cached = FtdiManager._device_cache.get(self._serial_number, {})
            channels = cached.get("channels") or [self._active_channel]
            self._available_channels = list(channels)

            for ch in channels:
                index = self._find_device_index(serial_number, ch)
                if index is None:
                    continue
                self._log(f"[INFO] Opened: SN={serial_number}, CH={ch}, IDX={index}")
                ft = ftd2xx.open(index)
                self._ft_handles[ch] = ft
                # Configure MPSSE only on supported channels
                self._ft = ft
                self._channel = ch
                if self.supports_mpsse(ch):
                    self._configure_mpsse()
                    self._channel_modes[ch] = "mpsse"
                else:
                    self._channel_modes[ch] = "uart"

            if self._active_channel not in self._ft_handles and self._ft_handles:
                self._active_channel = sorted(self._ft_handles.keys())[0]
            if self._active_channel in self._ft_handles:
                self._ft = self._ft_handles[self._active_channel]
                self._channel = self._active_channel

            if not self._ft_handles:
                raise RuntimeError(
                    f"Open failed. SN={serial_number}"
                )

            self._is_connected = True
            info = f"Connected: SN={serial_number}, CH={self._active_channel}"
            self._log(info)
            self.device_connected.emit(info)
            self.device_info_changed.emit(
                {
                    "serial": self._serial_number,
                    "channel": self._active_channel,
                    "desc": cached.get("desc", ""),
                    "channels": channels,
                    "device_type": cached.get("device_type", ""),
                    "connected": True,
                }
            )
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
            return False

    def close_device(self) -> None:
        """Close FTDI device and release all handles."""
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
            self._available_channels = []
            self._is_connected = False
            self._serial_number = ""
            self._channel_modes = {}
            self._gpio_high_out_value = 0x00
            self._gpio_high_direction = 0x00
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

        locker = QMutexLocker(self._mutex)
        return self._i2c.i2c_scan(addr_start, addr_end)

    # SPI access (with mutex)

    def spi_configure(self, clock_hz: int = 1_000_000,
                      cpol: int = 0, cpha: int = 0) -> None:
        """Configure SPI clock and mode."""
        if not self._is_connected or self._ft is None:
            return
        if self._channel_modes.get(self._active_channel) != "spi":
            self.set_protocol_mode("SPI")
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
            self.set_protocol_mode("SPI")
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
            self.set_protocol_mode("SPI")
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
            self.set_protocol_mode("SPI")
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
        """Read low GPIO bits (ADBUS) in MPSSE mode."""
        if not self._is_connected or self._ft is None:
            return None
        locker = QMutexLocker(self._mutex)
        try:
            mode = self._channel_modes.get(self._active_channel, "mpsse")
            if mode == "bitbang":
                value = self._bitbang.read_pins()
                return value if value is not None else None
            return self._i2c.read_gpio_low()
        except Exception as e:
            self._log(f"[ERROR] GPIO read failed: {e}")
            return None

    def read_gpio_high(self) -> Optional[int]:
        """Read high GPIO bits (ACBUS/BCBUS) in MPSSE mode."""
        if not self._is_connected or self._ft is None:
            return None
        locker = QMutexLocker(self._mutex)
        try:
            mode = self._channel_modes.get(self._active_channel, "mpsse")
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
