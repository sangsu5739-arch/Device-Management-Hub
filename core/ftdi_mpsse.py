"""
MPSSE controller for FTDI devices.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List

if TYPE_CHECKING:
    from core.ftdi_manager import FtdiManager


class MpsseController:
    """MPSSE control for FTDI devices."""

    _PURGE_RXTX = 3

    # FTDI ADBUS GPIO pins (MPSSE)
    _PIN_SCL = 1 << 0  # AD0
    _PIN_SDA = 1 << 1  # AD1
    _PIN_SDA_IN = 1 << 2  # AD2

    _I2C_DIR_SDA_OUT = _PIN_SCL | _PIN_SDA
    _I2C_DIR_SDA_IN = _PIN_SCL

    # MPSSE opcodes
    _MPSSE_SET_BITS_LOW = 0x80
    _MPSSE_SET_BITS_HIGH = 0x82
    _MPSSE_READ_BITS_LOW = 0x81
    _MPSSE_READ_BITS_HIGH = 0x83
    _MPSSE_SEND_IMMEDIATE = 0x87
    _MPSSE_DATA_OUT_BYTES_NEG = 0x11
    _MPSSE_DATA_OUT_BITS_POS = 0x12
    _MPSSE_DATA_IN_BYTES_POS = 0x20
    _MPSSE_DATA_IN_BITS_POS = 0x22

    def __init__(self, owner: "FtdiManager") -> None:
        self._o = owner

    def configure(self) -> None:
        if self._o._ft is None:
            raise RuntimeError("FTDI handle is not open.")

        self._o._ft.resetDevice()
        time.sleep(0.05)
        self._o._ft.purge(self._PURGE_RXTX)
        time.sleep(0.05)
        self._o._ft.purge(self._PURGE_RXTX)
        self._o._ft.setUSBParameters(65536, 65536)
        self._o._ft.setLatencyTimer(2)
        self._o._ft.setTimeouts(3000, 3000)

        self._o._ft.setBitMode(0x00, 0x00)
        time.sleep(0.08)
        self._o._ft.setBitMode(0x00, 0x02)  # MPSSE
        time.sleep(0.08)
        self._o._ft.purge(self._PURGE_RXTX)
        time.sleep(0.05)
        self._o._ft.purge(self._PURGE_RXTX)
        time.sleep(0.05)

        # MPSSE sync
        synced = False
        for _ in range(3):
            self.write(b"\xAA")
            time.sleep(0.03)
            rxn = self._o._ft.getQueueStatus()
            if rxn > 0:
                resp = self.read(rxn)
                if b"\xFA\xAA" in resp:
                    synced = True
                    break
                # Trim log to avoid huge spam
                hex_str = resp.hex(" ")
                if len(hex_str) > 200:
                    hex_str = hex_str[:200] + " ..."
                self._o._log(f"[WARN] MPSSE sync mismatch: {hex_str}")
            else:
                self._o._log("[WARN] MPSSE sync timeout (no response)")
        if not synced:
            # extra purge to avoid stale data in further ops
            try:
                self._o._ft.purge(self._PURGE_RXTX)
            except Exception:
                pass

        # 60MHz, adaptive off, 3-phase on, loopback off
        self.write(bytes([0x8A, 0x97, 0x8C, 0x85]))

        # I2C clock
        self.set_i2c_clock(self._o._i2c_clock_khz)

        self.set_lines(scl_high=True, sda_high=True)

    def write(self, data: bytes) -> None:
        if self._o._ft is None:
            raise RuntimeError("FTDI handle is not open.")
        self._o._ft.write(data)

    def read(self, length: int) -> bytes:
        if self._o._ft is None:
            raise RuntimeError("FTDI handle is not open.")
        return self._o._ft.read(length)

    def read_with_wait(self, length: int) -> bytes:
        if length <= 0:
            return b""
        for _ in range(5):
            try:
                queued = self._o._ft.getQueueStatus() if self._o._ft is not None else 0
            except Exception:
                queued = 0
            if queued >= length:
                break
            time.sleep(0.005)
        try:
            return self.read(length)
        except Exception:
            return b""

    def set_lines(self, scl_high: bool, sda_high: bool) -> None:
        value = 0x00
        if scl_high:
            value |= self._PIN_SCL
        if sda_high:
            value |= self._PIN_SDA
        cmd = bytes([self._MPSSE_SET_BITS_LOW, value & 0xFF, self._I2C_DIR_SDA_OUT])
        self.write(cmd)

    def set_bits_low(self, value: int, direction: int) -> None:
        self.write(bytes([self._MPSSE_SET_BITS_LOW, value & 0xFF, direction & 0xFF]))

    def set_bits_high(self, value: int, direction: int) -> None:
        self.write(bytes([self._MPSSE_SET_BITS_HIGH, value & 0xFF, direction & 0xFF]))

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
        val, direction = self._merge_i2c_hold(self._PIN_SCL | self._PIN_SDA, self._I2C_DIR_SDA_OUT)
        self.set_bits_low(val, direction)

    def _i2c_start(self) -> None:
        buf = bytearray()
        val, dir_mask = self._merge_i2c_hold(self._PIN_SCL | self._PIN_SDA, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        val, dir_mask = self._merge_i2c_hold(self._PIN_SCL, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        val, dir_mask = self._merge_i2c_hold(0x00, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        self.write(bytes(buf))

    def _i2c_stop(self) -> None:
        buf = bytearray()
        for _ in range(4):
            val, dir_mask = self._merge_i2c_hold(0x00, self._I2C_DIR_SDA_OUT)
            buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        for _ in range(4):
            val, dir_mask = self._merge_i2c_hold(self._PIN_SCL, self._I2C_DIR_SDA_OUT)
            buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        for _ in range(4):
            val, dir_mask = self._merge_i2c_hold(self._PIN_SCL | self._PIN_SDA, self._I2C_DIR_SDA_OUT)
            buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
        self.write(bytes(buf))

    def _i2c_write_byte(self, value: int) -> bool:
        buf = bytearray()
        val, dir_mask = self._merge_i2c_hold(0x00, self._I2C_DIR_SDA_OUT)
        buf.extend([self._MPSSE_SET_BITS_LOW, val, dir_mask])
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
