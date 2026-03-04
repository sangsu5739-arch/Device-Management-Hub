"""
MPSSE I2C controller for FTDI devices.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List

from core.mpsse_base import MpsseBaseController

if TYPE_CHECKING:
    from core.ftdi_manager import FtdiManager


class I2cController(MpsseBaseController):
    """MPSSE I2C controller for FTDI devices."""

    _I2C_DIR_SDA_OUT = MpsseBaseController.PIN_ADBUS0 | MpsseBaseController.PIN_ADBUS1
    _I2C_DIR_SDA_IN = MpsseBaseController.PIN_ADBUS0

    # I2C specific MPSSE opcodes
    _MPSSE_DATA_OUT_BYTES_NEG = 0x11
    _MPSSE_DATA_OUT_BITS_POS = 0x12
    _MPSSE_DATA_IN_BYTES_POS = 0x20
    _MPSSE_DATA_IN_BITS_POS = 0x22

    def __init__(self, owner: "FtdiManager") -> None:
        super().__init__(owner)

    def configure(self) -> None:
        self.init_mpsse()

        # 60MHz, adaptive off, 3-phase on, loopback off
        self.write(bytes([
            self._MPSSE_DIV_BY_5_DISABLE,
            self._MPSSE_DISABLE_ADAPTIVE,
            self._MPSSE_ENABLE_3_PHASE,
            self._MPSSE_DISABLE_LOOPBACK
        ]))

        # I2C clock
        self.set_i2c_clock(self._o._i2c_clock_khz)

        self.set_lines(scl_high=True, sda_high=True)

    def set_lines(self, scl_high: bool, sda_high: bool) -> None:
        value = 0x00
        if scl_high:
            value |= self.PIN_ADBUS0
        if sda_high:
            value |= self.PIN_ADBUS1
        cmd = bytes([self._MPSSE_SET_BITS_LOW, value & 0xFF, self._I2C_DIR_SDA_OUT])
        self.write(cmd)

    def set_bits_low(self, value: int, direction: int) -> None:
        self.write(bytes([self._MPSSE_SET_BITS_LOW, value & 0xFF, direction & 0xFF]))

    def set_bits_high(self, value: int, direction: int) -> None:
        self.write(bytes([
            self._MPSSE_SET_BITS_HIGH, value & 0xFF, direction & 0xFF,
            self._MPSSE_SEND_IMMEDIATE,
        ]))

    def read_gpio_low(self) -> Optional[int]:
        if self._o._ft is None:
            return None
        self.write(bytes([self._MPSSE_READ_BITS_LOW, self._MPSSE_SEND_IMMEDIATE]))
        resp = self.read_with_wait(1)
        return resp[0] if resp else None

    def read_gpio_high(self) -> Optional[int]:
        if self._o._ft is None:
            return None
        self.write(bytes([self._MPSSE_READ_BITS_HIGH, self._MPSSE_SEND_IMMEDIATE]))
        resp = self.read_with_wait(1)
        return resp[0] if resp else None

    # -- I2C helpers --

    def set_i2c_clock(self, khz: int) -> None:
        if self._o._ft is None:
            return
        freq_hz = max(10, int(khz)) * 1000
        div = int((60_000_000 / (2 * freq_hz)) - 1)
        div = max(0, min(0xFFFF, div))
        self.write(bytes([0x86, div & 0xFF, (div >> 8) & 0xFF]))

    def _merge_i2c_hold(self, value: int, direction: int) -> tuple[int, int]:
        hold_mask, hold_value = self._o.get_i2c_hold()
        if hold_mask:
            value = (value & ~hold_mask) | (hold_value & hold_mask)
            direction |= hold_mask
        return value & 0xFF, direction & 0xFF

    def apply_gpio_out(self, value: int) -> None:
        val, direction = self._merge_i2c_hold(value, self._I2C_DIR_SDA_OUT)
        self.set_bits_low(val, direction)

    def apply_i2c_hold(self) -> None:
        val, direction = self._merge_i2c_hold(self.PIN_ADBUS0 | self.PIN_ADBUS1, self._I2C_DIR_SDA_OUT)
        self.set_bits_low(val, direction)

    def _i2c_start(self) -> None:
        buf = bytearray()
        # SDA high, SCL high
        val, dir_mask = self._merge_i2c_hold(self.PIN_ADBUS0 | self.PIN_ADBUS1, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        # SDA low, SCL high
        val, dir_mask = self._merge_i2c_hold(self.PIN_ADBUS0, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        # SDA low, SCL low
        val, dir_mask = self._merge_i2c_hold(0x00, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        self.write(bytes(buf))

    def _i2c_stop(self) -> None:
        buf = bytearray()
        # SCL low, SDA low
        val, dir_mask = self._merge_i2c_hold(0x00, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        # SCL high, SDA low
        val, dir_mask = self._merge_i2c_hold(self.PIN_ADBUS0, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        # SCL high, SDA high
        val, dir_mask = self._merge_i2c_hold(self.PIN_ADBUS0 | self.PIN_ADBUS1, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        self.write(bytes(buf))

    def _i2c_write_byte(self, value: int) -> bool:
        buf = bytearray()
        # 1. Set SDA low, SCL low (prepare for data)
        val, dir_mask = self._merge_i2c_hold(0x00, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        # 2. Write 8 bits, negative edge (SCL high, then low)
        buf.extend([self._MPSSE_DATA_OUT_BYTES_NEG, 0x00, 0x00, value & 0xFF])
        val, dir_mask = self._merge_i2c_hold(0x00, self._I2C_DIR_SDA_IN)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        buf.extend([self._MPSSE_DATA_IN_BITS_POS, 0x00])
        buf.append(self._MPSSE_SEND_IMMEDIATE)
        self.write(bytes(buf))
        resp = self.read_with_wait(1)
        if not resp:
            raise RuntimeError("MPSSE read timeout (ACK)")
        ack_bit = resp[0] & 0x01
        return ack_bit == 0

    def _i2c_read_byte(self, ack: bool) -> int:
        buf = bytearray()
        val, dir_mask = self._merge_i2c_hold(0x00, self._I2C_DIR_SDA_IN)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        buf.extend([self._MPSSE_DATA_IN_BYTES_POS, 0x00, 0x00])
        ack_byte = 0x00 if ack else 0xFF
        val, dir_mask = self._merge_i2c_hold(self._PIN_SDA if not ack else 0x00, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        buf.extend([self._MPSSE_DATA_OUT_BITS_POS, 0x00, ack_byte])
        buf.append(self._MPSSE_SEND_IMMEDIATE)
        self.write(bytes(buf))
        resp = self.read_with_wait(1)
        if not resp:
            raise RuntimeError("MPSSE read timeout (DATA)")
        return resp[0]

    def i2c_write(self, slave_addr: int, data: bytes) -> bool:
        attempts = self._o._i2c_retry_count + 1
        for attempt in range(attempts):
            try:
                addr_w = (slave_addr << 1) | 0
                self._i2c_start()
                if not self._i2c_write_byte(addr_w):
                    self._i2c_stop()
                    raise RuntimeError(f"Address NACK: 0x{slave_addr:02X}")
                for b in data:
                    if not self._i2c_write_byte(b):
                        self._i2c_stop()
                        raise RuntimeError(f"Data NACK: 0x{b:02X}")
                self._i2c_stop()

                hex_str = " ".join(f"{b:02X}" for b in data)
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                self._o.data_sent.emit(f"[{timestamp}] TX -> [0x{slave_addr:02X}] {hex_str}")
                return True
            except Exception as e:
                if attempt < attempts - 1:
                    time.sleep(self._o._i2c_retry_delay_s)
                    continue
                err = f"I2C write error: {e}"
                self._o._log(f"[Error] {err}")
                self._o.comm_error.emit(err)
                return False

    def i2c_read(self, slave_addr: int, write_prefix: bytes, read_len: int) -> Optional[bytes]:
        if read_len <= 0:
            return b""
        attempts = self._o._i2c_retry_count + 1
        for attempt in range(attempts):
            try:
                addr_w = (slave_addr << 1) | 0
                addr_r = (slave_addr << 1) | 1
                self._i2c_start()
                if not self._i2c_write_byte(addr_w):
                    self._i2c_stop()
                    raise RuntimeError(f"Address NACK(Write): 0x{slave_addr:02X}")
                for b in write_prefix:
                    if not self._i2c_write_byte(b):
                        self._i2c_stop()
                        raise RuntimeError(f"Prefix NACK: 0x{b:02X}")

                self._i2c_start()
                if not self._i2c_write_byte(addr_r):
                    self._i2c_stop()
                    raise RuntimeError(f"Address NACK(Read): 0x{slave_addr:02X}")

                out = bytearray()
                for i in range(read_len):
                    out.append(self._i2c_read_byte(ack=(i < read_len - 1)))
                self._i2c_stop()

                hex_str = " ".join(f"{b:02X}" for b in out)
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                self._o.data_received.emit(f"[{timestamp}] RX <- [0x{slave_addr:02X}] {hex_str}")
                return bytes(out)
            except Exception as e:
                if attempt < attempts - 1:
                    time.sleep(self._o._i2c_retry_delay_s)
                    continue
                err = f"I2C read error: {e}"
                self._o._log(f"[Error] {err}")
                self._o.comm_error.emit(err)
                return None

    def i2c_scan(self, addr_start: int = 0x08, addr_end: int = 0x77) -> List[int]:
        found: List[int] = []
        for addr in range(addr_start, addr_end + 1):
            try:
                addr_w = (addr << 1) | 0
                self._i2c_start()
                ack = self._i2c_write_byte(addr_w)
                self._i2c_stop()
                if ack:
                    found.append(addr)
            except Exception:
                try:
                    self._i2c_stop()
                except Exception:
                    pass
        return found


# Backwards compatibility alias
MpsseController = I2cController
