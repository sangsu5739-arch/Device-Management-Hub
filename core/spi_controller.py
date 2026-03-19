"""
SPI Controller for FTDI MPSSE devices.

Provides SPI transfer, chip-select management, and GPIO control
on non-SPI pins.  Works with FT232H / FT4232H via ftd2xx.

Standard MPSSE SPI pin mapping:
    ADBUS0 = SK   (SPI Clock)
    ADBUS1 = DO   (MOSI)
    ADBUS2 = DI   (MISO)
    ADBUS3 = CS0  (default chip select)
    ADBUS4-7      (available as GPIO or extra CS)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from core.mpsse_base import MpsseBaseController

if TYPE_CHECKING:
    from core.ftdi_manager import FtdiManager


class SpiController(MpsseBaseController):
    """MPSSE SPI controller.

    Handles SPI clock configuration, full-duplex transfer,
    and software chip-select toggling via GPIO.
    """

    # SPI data transfer opcodes (indexed by [cpol][cpha])
    # Each entry = (write-only opcode, full-duplex opcode)
    #
    # MPSSE opcode semantics:
    #   0x11/0x31: data OUT on -ve (falling), data IN on +ve (rising)
    #   0x10/0x34: data OUT on +ve (rising),  data IN on -ve (falling)
    #
    # Mode 0 (CPOL=0,CPHA=0): sample rising,  shift falling → 0x31
    # Mode 1 (CPOL=0,CPHA=1): shift rising,   sample falling → 0x34
    # Mode 2 (CPOL=1,CPHA=0): sample falling,  shift rising  → 0x34
    # Mode 3 (CPOL=1,CPHA=1): shift falling,  sample rising  → 0x31
    _XFER_OPCODES = {
        (0, 0): (0x11, 0x31),  # Mode 0: OUT -ve, IN +ve
        (0, 1): (0x10, 0x34),  # Mode 1: OUT +ve, IN -ve
        (1, 0): (0x10, 0x34),  # Mode 2: OUT +ve, IN -ve
        (1, 1): (0x11, 0x31),  # Mode 3: OUT -ve, IN +ve
    }
    _READ_OPCODES = {
        (0, 0): 0x20,  # Mode 0: IN +ve
        (0, 1): 0x24,  # Mode 1: IN -ve
        (1, 0): 0x24,  # Mode 2: IN -ve
        (1, 1): 0x20,  # Mode 3: IN +ve
    }

    # Deprecated: use MpsseBaseController.PIN_ADBUS* going forward
    PIN_CLK  = MpsseBaseController.PIN_ADBUS0   # ADBUS0
    PIN_MOSI = MpsseBaseController.PIN_ADBUS1   # ADBUS1
    PIN_MISO = MpsseBaseController.PIN_ADBUS2   # ADBUS2
    PIN_CS0  = MpsseBaseController.PIN_ADBUS3   # ADBUS3 (default CS)

    def __init__(self, owner: "FtdiManager") -> None:
        super().__init__(owner)
        self._clock_hz: int = 1_000_000
        self._cpol: int = 0
        self._cpha: int = 0

        # Low byte GPIO state
        self._gpio_direction: int = 0  # 1=output  (SPI pins + CS)
        self._gpio_value: int = 0

        # Extra GPIO pins managed alongside SPI
        self._extra_gpio_mask: int = 0
        self._extra_gpio_dir: int = 0
        self._extra_gpio_val: int = 0

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(
        self,
        clock_hz: int = 1_000_000,
        cpol: int = 0,
        cpha: int = 0,
    ) -> None:
        """Full MPSSE initialization + SPI mode setup.

        Called once when first entering SPI protocol mode.
        For subsequent mode/clock changes, use reconfigure().

        Args:
            clock_hz: SPI clock frequency (max 30 MHz with divide-by-5 off).
            cpol: Clock polarity (0 or 1).
            cpha: Clock phase (0 or 1).
        """
        self.init_mpsse()
        self._apply_spi_setup(clock_hz, cpol, cpha)

    def reconfigure(
        self,
        clock_hz: int = 1_000_000,
        cpol: int = 0,
        cpha: int = 0,
    ) -> None:
        """Change SPI parameters without full MPSSE re-initialization.

        Skips resetDevice/setBitMode/sync — only sends MPSSE commands
        to update clock divisor, pin idle state, and mode variables.
        """
        self._apply_spi_setup(clock_hz, cpol, cpha)

    def _apply_spi_setup(
        self,
        clock_hz: int,
        cpol: int,
        cpha: int,
    ) -> None:
        """Send MPSSE commands to configure SPI clock and pin states."""
        self._cpol = cpol
        self._cpha = cpha

        # 60 MHz base clock, adaptive off, loopback off.
        # 3-phase clocking is always disabled — matches C++ FTDI reference
        # (DISABLE_3_PHASE = 0x8D) and avoids timing issues with devices
        # like ADS1018 that are sensitive to the extra half-clock edge.
        self.write(bytes([
            self._MPSSE_DIV_BY_5_DISABLE,
            self._MPSSE_DISABLE_ADAPTIVE,
            self._MPSSE_DISABLE_3_PHASE,
            self._MPSSE_DISABLE_LOOPBACK,
        ]))

        # Clock divisor: freq = 60 MHz / ((1 + divisor) * 2)
        divisor = max(0, int(30_000_000 / clock_hz) - 1)
        self.write(bytes([
            self._MPSSE_CLOCK_DIVISOR,
            divisor & 0xFF,
            (divisor >> 8) & 0xFF,
        ]))

        # Pin direction: all output except MISO(bit2) and LED(bit7) — matches Ansari 0x7B
        # This ensures CS pins on ADBUS3-6 are driven HIGH (inactive) to prevent
        # floating CS lines from accidentally selecting SPI slaves.
        idle_clk = self.PIN_ADBUS0 if cpol else 0
        self._gpio_direction = 0x7B  # 0111_1011: all output except MISO, LED
        # All CS pins (ADBUS3-6) HIGH, CLK to idle state
        all_cs_high = self.PIN_ADBUS3 | self.PIN_ADBUS4 | self.PIN_ADBUS5 | self.PIN_ADBUS6
        self._gpio_value = idle_clk | all_cs_high
        self._set_low_bits(self._gpio_value, self._gpio_direction)

    # ------------------------------------------------------------------
    # Chip Select
    # ------------------------------------------------------------------

    def set_cs(self, pin_mask: int, active: bool) -> None:
        """Assert or deassert a chip-select pin (active-low).

        Args:
            pin_mask: Bit mask for the CS pin (e.g. 0x08 for ADBUS3).
            active:   True = assert (pull low), False = deassert (pull high).
        """
        if active:
            self._gpio_value &= ~pin_mask
        else:
            self._gpio_value |= pin_mask
        # Ensure CS pin is an output
        self._gpio_direction |= pin_mask
        self._set_low_bits(self._gpio_value, self._gpio_direction)

    # ------------------------------------------------------------------
    # SPI Transfer
    # ------------------------------------------------------------------

    def transfer(self, tx_data: bytes, cs_pin: int = MpsseBaseController.PIN_ADBUS3) -> bytes:
        """Full-duplex SPI transfer with automatic CS handling.

        Matches Ansari ADS1018.py tx_packet + rx_packet pattern:
        entire transaction (CS assert → CLK idle → data → cleanup → CS deassert)
        is sent as one MPSSE buffer, then SEND_IMMEDIATE separately.

        Args:
            tx_data: Bytes to transmit.
            cs_pin:  CS pin mask (default ADBUS3=0x08).

        Returns:
            Received bytes (same length as tx_data).
        """
        if not tx_data:
            return b""

        _, duplex_op = self._XFER_OPCODES[(self._cpol, self._cpha)]
        length = len(tx_data)
        len_lo = (length - 1) & 0xFF
        len_hi = ((length - 1) >> 8) & 0xFF
        direction = self._gpio_direction & 0xFF

        # Base port_value: all CS pins HIGH (Ansari pattern)
        all_cs_high = self.PIN_ADBUS3 | self.PIN_ADBUS4 | self.PIN_ADBUS5 | self.PIN_ADBUS6
        port_value = all_cs_high & ~cs_pin  # target CS LOW, others HIGH

        cmd = bytearray()

        # 1. Assert CS (5× for timing margin — Ansari pattern)
        for _ in range(5):
            cmd.extend([self._MPSSE_SET_BITS_LOW, port_value & 0xFF, direction])

        # 2. Set CLK HIGH before data (Ansari explicit step)
        port_value |= self.PIN_ADBUS0
        cmd.extend([self._MPSSE_SET_BITS_LOW, port_value & 0xFF, direction])

        # 3. Clock data in/out
        cmd.append(duplex_op)
        cmd.append(len_lo)
        cmd.append(len_hi)
        cmd.extend(tx_data)

        # 4. Post-transfer cleanup: CLK LOW, MOSI LOW, MISO LOW (5×)
        port_value &= ~self.PIN_ADBUS0  # CLK LOW
        port_value &= ~self.PIN_ADBUS1  # MOSI LOW
        port_value &= ~self.PIN_ADBUS2  # MISO LOW (input, no effect but matches Ansari)
        for _ in range(5):
            cmd.extend([self._MPSSE_SET_BITS_LOW, port_value & 0xFF, direction])

        # 5. Deassert CS (5×)
        port_value |= cs_pin
        for _ in range(5):
            cmd.extend([self._MPSSE_SET_BITS_LOW, port_value & 0xFF, direction])

        # Write entire transaction as one buffer
        self.write(bytes(cmd))

        # Send SEND_IMMEDIATE separately, then read all available (Ansari rx_packet)
        self.write(bytes([self._MPSSE_SEND_IMMEDIATE]))
        time.sleep(0.01)  # 10ms — matches Ansari

        # Read all available bytes (Ansari pattern), extract last N bytes
        try:
            queued = self._o._ft.getQueueStatus() if self._o._ft else 0
        except Exception:
            queued = 0
        if queued <= 0:
            # Retry once after short wait
            time.sleep(0.005)
            try:
                queued = self._o._ft.getQueueStatus() if self._o._ft else 0
            except Exception:
                queued = 0

        print(f"[SPI] queued={queued} expected={length}")

        if queued > 0:
            all_data = self.read(queued)
            print(f"[SPI] read({queued})={all_data.hex(' ')}")
            # Return last 'length' bytes (Ansari uses rx_buffer[rx_len-4], [rx_len-3])
            if len(all_data) >= length:
                return all_data[-length:]
            return all_data

        print("[SPI] WARNING: no data in queue, returning zeros")
        return b"\x00" * length

    def write_only(self, tx_data: bytes, cs_pin: int = MpsseBaseController.PIN_ADBUS3) -> None:
        """Write-only SPI transfer with automatic CS handling.

        Args:
            tx_data: Bytes to transmit.
            cs_pin:  CS pin mask.
        """
        if not tx_data:
            return

        write_op, _ = self._XFER_OPCODES[(self._cpol, self._cpha)]
        length = len(tx_data)
        len_lo = (length - 1) & 0xFF
        len_hi = ((length - 1) >> 8) & 0xFF

        cmd = bytearray()

        # 1. Assert CS (5×)
        cs_val = self._gpio_value & ~cs_pin
        self._gpio_direction |= cs_pin
        for _ in range(5):
            cmd.extend([self._MPSSE_SET_BITS_LOW, cs_val & 0xFF, self._gpio_direction & 0xFF])

        # 2. CLK idle state
        idle_clk = self.PIN_ADBUS0 if self._cpol else 0
        pre_data_val = (cs_val & ~self.PIN_ADBUS0) | idle_clk
        cmd.extend([self._MPSSE_SET_BITS_LOW, pre_data_val & 0xFF, self._gpio_direction & 0xFF])

        # 3. Clock data out
        cmd.append(write_op)
        cmd.append(len_lo)
        cmd.append(len_hi)
        cmd.extend(tx_data)

        # 4. Post-transfer cleanup: CLK LOW, MOSI LOW (5×)
        cleanup_val = cs_val & ~self.PIN_ADBUS0 & ~self.PIN_ADBUS1
        for _ in range(5):
            cmd.extend([self._MPSSE_SET_BITS_LOW, cleanup_val & 0xFF, self._gpio_direction & 0xFF])

        # 5. Deassert CS (5×)
        cs_high_val = cleanup_val | cs_pin
        for _ in range(5):
            cmd.extend([self._MPSSE_SET_BITS_LOW, cs_high_val & 0xFF, self._gpio_direction & 0xFF])

        self.write(bytes(cmd))

    def write_then_read(
        self,
        write_data: bytes,
        read_len: int,
        cs_pin: int = MpsseBaseController.PIN_ADBUS3,
    ) -> bytes:
        """Half-duplex SPI sequence under one CS: write phase, then read phase.

        This mirrors the tc72.py access style:
          1) CLOCK_DATA_OUT_* (register/command)
          2) CLOCK_DATA_IN_*  (response bytes)
        """
        if read_len < 0:
            raise ValueError("read_len must be >= 0")
        if not write_data and read_len == 0:
            return b""

        write_op, _ = self._XFER_OPCODES[(self._cpol, self._cpha)]
        read_op = self._READ_OPCODES[(self._cpol, self._cpha)]

        self._assert_cs(cs_pin)

        cmd = bytearray()
        if write_data:
            wlen = len(write_data) - 1
            cmd.extend([write_op, wlen & 0xFF, (wlen >> 8) & 0xFF])
            cmd.extend(write_data)

        if read_len > 0:
            rlen = read_len - 1
            cmd.extend([read_op, rlen & 0xFF, (rlen >> 8) & 0xFF])
            cmd.append(self._MPSSE_SEND_IMMEDIATE)

        self.write(bytes(cmd))

        if read_len <= 0:
            self._deassert_cs(cs_pin)
            return b""

        time.sleep(0.001)
        rx = self.read_with_wait(read_len)
        self._deassert_cs(cs_pin)
        return rx

    # ------------------------------------------------------------------
    # GPIO (non-SPI pins)
    # ------------------------------------------------------------------

    def set_gpio(self, mask: int, value: int, direction: int = 0xFF) -> None:
        """Set GPIO state on non-SPI low-byte pins (ADBUS4-7).

        Args:
            mask:      Bit mask of pins to affect.
            value:     Pin values (1=high, 0=low).
            direction: Pin directions (1=output, 0=input).
        """
        spi_dir = self.PIN_ADBUS0 | self.PIN_ADBUS1 | self.PIN_ADBUS3
        spi_val_mask = self.PIN_ADBUS0 | self.PIN_ADBUS1 | self.PIN_ADBUS2 | self.PIN_ADBUS3
        # Preserve SPI pin directions
        spi_mask = self.PIN_ADBUS0 | self.PIN_ADBUS1 | self.PIN_ADBUS2 | self.PIN_ADBUS3
        self._extra_gpio_mask = mask & ~spi_mask
        self._extra_gpio_dir = direction & self._extra_gpio_mask
        self._extra_gpio_val = value & self._extra_gpio_mask

        self._gpio_direction = (self._gpio_direction & spi_mask) | self._extra_gpio_dir
        self._gpio_value = (self._gpio_value & spi_mask) | self._extra_gpio_val
        self._set_low_bits(self._gpio_value, self._gpio_direction)

    def read_gpio_low(self) -> Optional[int]:
        """Read low-byte GPIO pins."""
        self.write(bytes([self._MPSSE_READ_BITS_LOW, self._MPSSE_SEND_IMMEDIATE]))
        time.sleep(0.005)
        try:
            rxn = self._o._ft.getQueueStatus() if self._o._ft else 0
            if rxn > 0:
                data = self.read(rxn)
                return data[-1] if data else None
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _set_low_bits(self, value: int, direction: int) -> None:
        self.write(bytes([self._MPSSE_SET_BITS_LOW, value & 0xFF, direction & 0xFF]))

    def _assert_cs(self, cs_pin: int) -> None:
        cs_val = self._gpio_value & ~cs_pin
        self._gpio_direction |= cs_pin
        cmd = bytearray()
        for _ in range(5):
            cmd.extend([self._MPSSE_SET_BITS_LOW, cs_val & 0xFF, self._gpio_direction & 0xFF])
        self.write(bytes(cmd))

    def _deassert_cs(self, cs_pin: int) -> None:
        cs_val_high = self._gpio_value | cs_pin
        cmd = bytearray()
        for _ in range(5):
            cmd.extend([self._MPSSE_SET_BITS_LOW, cs_val_high & 0xFF, self._gpio_direction & 0xFF])
        self.write(bytes(cmd))
