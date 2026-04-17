"""
MPSSE JTAG controller for FTDI devices.

IEEE 1149.1 compliant TAP state machine + low-level JTAG operations
via FTDI MPSSE engine.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from core.mpsse_base import MpsseBaseController

if TYPE_CHECKING:
    from core.ftdi_manager import FtdiManager

logger = logging.getLogger(__name__)


# ── IEEE 1149.1 TAP State Machine ──────────────────────────────────

# TAP_TRANSITIONS[state] = (next_if_TMS0, next_if_TMS1)
TAP_TRANSITIONS: Dict[str, Tuple[str, str]] = {
    "TLR":        ("RTI",       "TLR"),
    "RTI":        ("RTI",       "Sel-DR"),
    "Sel-DR":     ("Cap-DR",    "Sel-IR"),
    "Cap-DR":     ("Shift-DR",  "Exit1-DR"),
    "Shift-DR":   ("Shift-DR",  "Exit1-DR"),
    "Exit1-DR":   ("Pause-DR",  "Update-DR"),
    "Pause-DR":   ("Pause-DR",  "Exit2-DR"),
    "Exit2-DR":   ("Shift-DR",  "Update-DR"),
    "Update-DR":  ("RTI",       "Sel-DR"),
    "Sel-IR":     ("Cap-IR",    "TLR"),
    "Cap-IR":     ("Shift-IR",  "Exit1-IR"),
    "Shift-IR":   ("Shift-IR",  "Exit1-IR"),
    "Exit1-IR":   ("Pause-IR",  "Update-IR"),
    "Pause-IR":   ("Pause-IR",  "Exit2-IR"),
    "Exit2-IR":   ("Shift-IR",  "Update-IR"),
    "Update-IR":  ("RTI",       "Sel-DR"),
}

# Full names for UI display
TAP_DISPLAY_NAMES: Dict[str, str] = {
    "TLR":        "Test-Logic-Reset",
    "RTI":        "Run-Test/Idle",
    "Sel-DR":     "Select-DR-Scan",
    "Cap-DR":     "Capture-DR",
    "Shift-DR":   "Shift-DR",
    "Exit1-DR":   "Exit1-DR",
    "Pause-DR":   "Pause-DR",
    "Exit2-DR":   "Exit2-DR",
    "Update-DR":  "Update-DR",
    "Sel-IR":     "Select-IR-Scan",
    "Cap-IR":     "Capture-IR",
    "Shift-IR":   "Shift-IR",
    "Exit1-IR":   "Exit1-IR",
    "Pause-IR":   "Pause-IR",
    "Exit2-IR":   "Exit2-IR",
    "Update-IR":  "Update-IR",
}


def navigate_tap(current: str, target: str) -> List[int]:
    """BFS shortest path from *current* to *target* TAP state.

    Returns a list of TMS bit values (0 or 1) to clock.
    """
    if current == target:
        return []
    queue: deque[Tuple[str, List[int]]] = deque([(current, [])])
    visited = {current}
    while queue:
        state, path = queue.popleft()
        for tms_val in (0, 1):
            nxt = TAP_TRANSITIONS[state][tms_val]
            new_path = path + [tms_val]
            if nxt == target:
                return new_path
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, new_path))
    raise ValueError(f"No TAP path from {current} to {target}")


# ── IDCODE parsing ─────────────────────────────────────────────────

@dataclass
class JtagIdcode:
    """Parsed JTAG IDCODE fields."""
    raw: int = 0
    version: int = 0       # bits [31:28]
    part_number: int = 0   # bits [27:12]
    manufacturer: int = 0  # bits [11:1]
    lsb: int = 0           # bit [0] — must be 1 for valid IDCODE

    @property
    def is_valid(self) -> bool:
        return self.lsb == 1 and self.raw != 0 and self.raw != 0xFFFFFFFF

    @classmethod
    def from_raw(cls, raw: int) -> "JtagIdcode":
        return cls(
            raw=raw,
            version=(raw >> 28) & 0xF,
            part_number=(raw >> 12) & 0xFFFF,
            manufacturer=(raw >> 1) & 0x7FF,
            lsb=raw & 1,
        )

    def hex_str(self) -> str:
        return f"0x{self.raw:08X}"

    def mfr_hex(self) -> str:
        return f"0x{self.manufacturer:03X}"

    def part_hex(self) -> str:
        return f"0x{self.part_number:04X}"


@dataclass
class JtagChainInfo:
    """JTAG device chain scan result."""
    devices: List[JtagIdcode] = field(default_factory=list)
    ir_lengths: List[int] = field(default_factory=list)

    @property
    def device_count(self) -> int:
        return len(self.devices)


# ── JTAG Controller ────────────────────────────────────────────────

class JtagController(MpsseBaseController):
    """MPSSE JTAG controller for FTDI devices.

    Pin mapping (ADBUS):
        ADBUS0 = TCK  (output)
        ADBUS1 = TDI  (output)
        ADBUS2 = TDO  (input)
        ADBUS3 = TMS  (output)
    """

    # JTAG pin definitions
    _PIN_TCK = MpsseBaseController.PIN_ADBUS0   # bit 0
    _PIN_TDI = MpsseBaseController.PIN_ADBUS1   # bit 1
    _PIN_TDO = MpsseBaseController.PIN_ADBUS2   # bit 2 (input)
    _PIN_TMS = MpsseBaseController.PIN_ADBUS3   # bit 3

    # Direction mask: TCK + TDI + TMS = output, TDO = input
    _JTAG_DIR = _PIN_TCK | _PIN_TDI | _PIN_TMS  # 0x0B

    # MPSSE JTAG opcodes
    # TMS clocking: clock data to TMS pin (no read)
    _MPSSE_TMS_OUT = 0x4B           # TMS out, -ve edge, no read
    _MPSSE_TMS_OUT_IN = 0x6B        # TMS out, -ve edge, read TDO

    # Data shift: TDI out / TDO in (TMS=0)
    _MPSSE_DATA_OUT_BYTES_NEG = 0x19   # bytes out on -ve, MSB first
    _MPSSE_DATA_IN_BYTES_POS = 0x28    # bytes in on +ve, MSB first
    _MPSSE_DATA_INOUT_BYTES = 0x39     # bytes out -ve, in +ve, MSB first

    _MPSSE_DATA_OUT_BITS_NEG = 0x1B    # bits out on -ve, MSB first
    _MPSSE_DATA_IN_BITS_POS = 0x2A     # bits in on +ve, MSB first
    _MPSSE_DATA_INOUT_BITS = 0x3B      # bits out -ve, in +ve, MSB first

    # Maximum devices in a chain we'll attempt to detect
    _MAX_CHAIN_DEVICES = 32

    def __init__(self, owner: "FtdiManager") -> None:
        super().__init__(owner)
        self._tap_state: str = "TLR"
        self._clock_hz: int = 1_000_000

    # ── Configuration ──────────────────────────────────────────────

    def configure(self, clock_hz: int = 1_000_000) -> None:
        """Initialize MPSSE for JTAG mode."""
        self.init_mpsse()

        # 60 MHz master clock, no divide-by-5, adaptive off, 3-phase off
        self.write(bytes([
            self._MPSSE_DIV_BY_5_DISABLE,
            self._MPSSE_DISABLE_ADAPTIVE,
            self._MPSSE_DISABLE_3_PHASE,
            self._MPSSE_DISABLE_LOOPBACK,
        ]))

        self.set_clock(clock_hz)

        # Set initial pin state: TMS=1, TDI=0, TCK=0
        initial_value = self._PIN_TMS
        self.write(bytes([
            self._MPSSE_SET_BITS_LOW,
            initial_value & 0xFF,
            self._JTAG_DIR & 0xFF,
        ]))

        self._tap_state = "TLR"
        self._o._log(f"[INFO] JTAG configured: TCK={clock_hz}Hz")

    def set_clock(self, clock_hz: int) -> None:
        """Set TCK frequency.

        FTDI MPSSE clock formula (60 MHz mode):
            TCK = 60 MHz / ((1 + divisor) * 2)
        """
        clock_hz = max(100, min(clock_hz, 30_000_000))
        divisor = max(0, (30_000_000 // clock_hz) - 1)
        self._clock_hz = 30_000_000 // (divisor + 1)
        self.write(bytes([
            self._MPSSE_CLOCK_DIVISOR,
            divisor & 0xFF,
            (divisor >> 8) & 0xFF,
        ]))

    @property
    def tap_state(self) -> str:
        return self._tap_state

    @property
    def tap_state_display(self) -> str:
        return TAP_DISPLAY_NAMES.get(self._tap_state, self._tap_state)

    # ── Low-level MPSSE JTAG primitives ────────────────────────────

    def clock_tms(self, tms_bits: int, bit_count: int,
                  tdi_level: bool = True, read_tdo: bool = False) -> Optional[int]:
        """Clock TMS sequence.

        Args:
            tms_bits: TMS bit pattern (LSB first, up to 7 bits).
            bit_count: Number of TMS bits to clock (1..7).
            tdi_level: TDI pin level during TMS clocking.
            read_tdo: If True, read TDO on the last bit.

        Returns:
            TDO value if read_tdo, else None.
        """
        if bit_count < 1 or bit_count > 7:
            raise ValueError(f"TMS bit_count must be 1..7, got {bit_count}")

        tdi_bit = 0x80 if tdi_level else 0x00
        data_byte = (tms_bits & 0x7F) | tdi_bit

        opcode = self._MPSSE_TMS_OUT_IN if read_tdo else self._MPSSE_TMS_OUT
        cmd = bytes([opcode, bit_count - 1, data_byte])

        if read_tdo:
            cmd += bytes([self._MPSSE_SEND_IMMEDIATE])

        self.write(cmd)

        # Update TAP state tracker
        for i in range(bit_count):
            tms = (tms_bits >> i) & 1
            self._tap_state = TAP_TRANSITIONS[self._tap_state][tms]

        if read_tdo:
            resp = self.read_with_wait(1, retries=10)
            if resp:
                return resp[0] & 1
            return None
        return None

    def shift_data(self, tdi_data: bytes, bit_count: int,
                   read_tdo: bool = True, exit_shift: bool = True) -> Optional[bytes]:
        """Shift data through TDI/TDO in Shift-DR or Shift-IR state.

        TMS is held LOW during the shift (staying in Shift-xR).
        If exit_shift is True, the last bit is sent with TMS=1 to
        transition to Exit1-xR.

        Args:
            tdi_data: Data bytes to shift out on TDI (MSB first).
            bit_count: Total number of bits to shift.
            read_tdo: If True, capture TDO data.
            exit_shift: If True, exit Shift state on last bit.

        Returns:
            TDO bytes if read_tdo, else None.
        """
        if bit_count == 0:
            return b"" if read_tdo else None

        # Split into full bytes + remaining bits
        # If exit_shift, reserve the last bit for TMS=1 transition
        if exit_shift:
            main_bits = bit_count - 1
        else:
            main_bits = bit_count

        full_bytes = main_bits // 8
        remaining_bits = main_bits % 8
        expected_read = 0
        cmd = bytearray()

        # Shift full bytes (TMS=0)
        if full_bytes > 0:
            length = full_bytes - 1  # MPSSE uses length-1
            if read_tdo:
                cmd.append(self._MPSSE_DATA_INOUT_BYTES)
            else:
                cmd.append(self._MPSSE_DATA_OUT_BYTES_NEG)
            cmd.append(length & 0xFF)
            cmd.append((length >> 8) & 0xFF)
            cmd.extend(tdi_data[:full_bytes])
            if read_tdo:
                expected_read += full_bytes

        # Shift remaining bits (TMS=0)
        if remaining_bits > 0:
            byte_idx = full_bytes
            data_byte = tdi_data[byte_idx] if byte_idx < len(tdi_data) else 0x00
            if read_tdo:
                cmd.append(self._MPSSE_DATA_INOUT_BITS)
            else:
                cmd.append(self._MPSSE_DATA_OUT_BITS_NEG)
            cmd.append(remaining_bits - 1)  # MPSSE uses bit_count - 1
            cmd.append(data_byte)
            if read_tdo:
                expected_read += 1

        # Last bit with TMS=1 to exit Shift state
        if exit_shift:
            last_byte_idx = (bit_count - 1) // 8
            last_bit_pos = (bit_count - 1) % 8
            last_bit = 0
            if last_byte_idx < len(tdi_data):
                last_bit = (tdi_data[last_byte_idx] >> (7 - last_bit_pos)) & 1

            tdi_level = bool(last_bit)
            tdo_bit = self.clock_tms(
                tms_bits=0x01,  # TMS=1 → Exit1
                bit_count=1,
                tdi_level=tdi_level,
                read_tdo=read_tdo,
            )
            # TAP state already updated by clock_tms

        if not read_tdo:
            if cmd:
                self.write(bytes(cmd))
            return None

        if cmd:
            cmd.append(self._MPSSE_SEND_IMMEDIATE)
            self.write(bytes(cmd))

        # Read TDO data
        if expected_read > 0:
            tdo_raw = self.read_with_wait(expected_read, retries=15)
        else:
            tdo_raw = b""

        # Assemble TDO result
        result = bytearray(tdo_raw) if tdo_raw else bytearray(expected_read)

        # Append last TDO bit if exit_shift
        if exit_shift and tdo_bit is not None:
            # Pack the last bit into the result
            if remaining_bits > 0 and len(result) > 0:
                # Last byte from bits read needs the exit bit appended
                result[-1] = (result[-1] & 0xFE) | (tdo_bit & 1)
            else:
                result.append(tdo_bit & 1)

        return bytes(result)

    # ── TAP navigation ─────────────────────────────────────────────

    def reset_tap(self) -> None:
        """Force TAP to Test-Logic-Reset by clocking 5x TMS=1."""
        self.clock_tms(tms_bits=0x1F, bit_count=5, tdi_level=True)
        self._tap_state = "TLR"

    def goto_state(self, target: str) -> None:
        """Navigate TAP to the target state via shortest path."""
        if self._tap_state == target:
            return

        path = navigate_tap(self._tap_state, target)
        if not path:
            return

        # Clock TMS in chunks of 7 bits max
        while path:
            chunk = path[:7]
            path = path[7:]
            tms_val = 0
            for i, bit in enumerate(chunk):
                tms_val |= (bit << i)
            self.clock_tms(tms_bits=tms_val, bit_count=len(chunk))

    def goto_shift_dr(self) -> None:
        """Navigate to Shift-DR state."""
        self.goto_state("Shift-DR")

    def goto_shift_ir(self) -> None:
        """Navigate to Shift-IR state."""
        self.goto_state("Shift-IR")

    def goto_rti(self) -> None:
        """Navigate to Run-Test/Idle."""
        self.goto_state("RTI")

    # ── High-level JTAG operations ─────────────────────────────────

    def read_idcode(self) -> JtagChainInfo:
        """Read IDCODE(s) from the JTAG chain.

        Sequence: Reset → Shift-DR → read 32-bit IDCODEs until
        we get all-ones (bypass) or all-zeros (end of chain).
        """
        chain = JtagChainInfo()

        # Reset puts IDCODE into DR
        self.reset_tap()
        self.goto_state("Shift-DR")

        # Read up to MAX_CHAIN_DEVICES IDCODEs
        for _ in range(self._MAX_CHAIN_DEVICES):
            # Read 32 bits without exiting Shift-DR
            tdi_zeros = bytes(4)
            tdo = self.shift_data(
                tdi_data=tdi_zeros,
                bit_count=32,
                read_tdo=True,
                exit_shift=False,
            )
            if tdo is None or len(tdo) < 4:
                break

            # Convert bytes to 32-bit value (LSB first from JTAG)
            raw = 0
            for i, b in enumerate(tdo[:4]):
                raw |= (b << (i * 8))

            if raw == 0x00000000 or raw == 0xFFFFFFFF:
                break

            idcode = JtagIdcode.from_raw(raw)
            if not idcode.is_valid:
                break

            chain.devices.append(idcode)

        # Return to RTI
        self.goto_state("RTI")

        self._o._log(
            f"[INFO] JTAG chain: {chain.device_count} device(s) found"
        )
        for i, dev in enumerate(chain.devices):
            self._o._log(
                f"  [{i}] IDCODE={dev.hex_str()} "
                f"Mfr={dev.mfr_hex()} Part={dev.part_hex()} "
                f"Ver={dev.version}"
            )

        return chain

    def bypass_test(self, device_count: int = 1) -> bool:
        """Test JTAG chain using BYPASS instruction.

        Loads BYPASS (all 1s) into all device IR registers,
        then shifts a known pattern through DR and checks the
        output offset by device_count bits (each BYPASS register
        is 1 bit).

        Returns True if the test passes.
        """
        if device_count < 1:
            return False

        # Load BYPASS into all IRs (all 1s)
        self.reset_tap()
        self.goto_state("Shift-IR")

        # Send enough 1-bits for all devices' IR
        # Conservative: 256 bits of all 1s to fill all IR registers
        ir_bytes = bytes([0xFF] * 32)
        self.shift_data(
            tdi_data=ir_bytes,
            bit_count=256,
            read_tdo=False,
            exit_shift=True,
        )

        # Now shift a test pattern through DR
        self.goto_state("Shift-DR")

        # Total bits = test_bits + device_count (bypass delay)
        test_bits = 8
        total_bits = test_bits + device_count
        total_bytes = (total_bits + 7) // 8

        # Test pattern: 0xA5 followed by zeros
        tdi = bytearray(total_bytes)
        tdi[0] = 0xA5

        tdo = self.shift_data(
            tdi_data=bytes(tdi),
            bit_count=total_bits,
            read_tdo=True,
            exit_shift=True,
        )

        if tdo is None:
            self._o._log("[WARN] JTAG bypass test: no TDO data")
            return False

        # The test pattern should appear shifted by device_count bits
        # Extract the shifted result
        tdo_bits = 0
        for i, b in enumerate(tdo):
            tdo_bits |= (b << (i * 8))

        # Expected: test pattern shifted right by device_count
        expected = 0xA5 << device_count
        mask = 0xFF << device_count
        actual = tdo_bits & mask

        passed = actual == expected
        self._o._log(
            f"[INFO] JTAG bypass test: "
            f"{'PASS' if passed else 'FAIL'} "
            f"(expected=0x{expected:04X}, got=0x{actual:04X}, "
            f"devices={device_count})"
        )

        self.goto_state("RTI")
        return passed

    def scan_ir_length(self, max_bits: int = 256) -> List[int]:
        """Detect IR lengths for each device in the chain.

        Fills IR with all 1s (BYPASS), then shifts 0s and counts
        how many clocks before the 0 appears for each device.

        Returns a list of IR lengths per device.
        """
        self.reset_tap()
        self.goto_state("Shift-IR")

        # Fill with 1s
        ones = bytes([0xFF] * (max_bits // 8))
        self.shift_data(ones, max_bits, read_tdo=False, exit_shift=False)

        # Now shift 0s and read
        zeros = bytes(max_bits // 8)
        tdo = self.shift_data(zeros, max_bits, read_tdo=True, exit_shift=True)

        if tdo is None:
            return []

        # Convert to bit array
        tdo_bits = []
        for b in tdo:
            for bit_pos in range(8):
                tdo_bits.append((b >> bit_pos) & 1)
                if len(tdo_bits) >= max_bits:
                    break

        # Parse IR lengths: count consecutive 1s between 0-transitions
        lengths = []
        count = 0
        for bit in tdo_bits:
            if bit == 1:
                count += 1
            else:
                if count > 0:
                    lengths.append(count)
                count = 0
        if count > 0:
            lengths.append(count)

        self.goto_state("RTI")

        self._o._log(f"[INFO] IR lengths detected: {lengths}")
        return lengths

    def read_gpio_state(self) -> Optional[int]:
        """Read JTAG pin states (low byte)."""
        self.write(bytes([
            self._MPSSE_READ_BITS_LOW,
            self._MPSSE_SEND_IMMEDIATE,
        ]))
        resp = self.read_with_wait(1, retries=10)
        if resp:
            return resp[0]
        return None

    # ── ATP Vector batch execution ─────────────────────────────────
    #
    # Reimplements reference analyze_JTAG_atp_v09.py state_machine()
    # + send_packet() for efficient MPSSE command buffering.
    #
    # TAP state names used here match the reference JTAG_STATE dict:
    #   "Test-Logic-Reset", "Run-Test-Idle", "Shift-IR", "Shift-DR", etc.
    #
    # The reference uses two command types:
    #   0x4B — TMS clocking (max 7 bits per packet)
    #   0x3B — TDI/TDO data shift (max 8 bits per packet)
    #   0x6B — Last TDI bit + TMS exit
    #
    # TDO capture happens only when 'V' appears in the TDO column.

    # TAP state map matching the reference (full state names)
    _REF_TAP = {
        "Test-Logic-Reset": ("Run-Test-Idle",    "Test-Logic-Reset"),
        "Run-Test-Idle":    ("Run-Test-Idle",    "Select-DR-Scan"),
        "Select-DR-Scan":   ("Capture-DR",       "Select-IR-Scan"),
        "Capture-DR":       ("Shift-DR",         "Exit1-DR"),
        "Shift-DR":         ("Shift-DR",         "Exit1-DR"),
        "Exit1-DR":         ("Pause-DR",         "Update-DR"),
        "Pause-DR":         ("Pause-DR",         "Exit2-DR"),
        "Exit2-DR":         ("Shift-DR",         "Update-DR"),
        "Update-DR":        ("Run-Test-Idle",    "Select-DR-Scan"),
        "Select-IR-Scan":   ("Capture-IR",       "Test-Logic-Reset"),
        "Capture-IR":       ("Shift-IR",         "Exit1-IR"),
        "Shift-IR":         ("Shift-IR",         "Exit1-IR"),
        "Exit1-IR":         ("Pause-IR",         "Update-IR"),
        "Pause-IR":         ("Pause-IR",         "Exit2-IR"),
        "Exit2-IR":         ("Shift-IR",         "Update-IR"),
        "Update-IR":        ("Run-Test-Idle",    "Select-DR-Scan"),
    }

    _MAX_TMS_PACKET = 7
    _MAX_DATA_PACKET = 8

    def clock_vectors_batch(
        self,
        vectors: list,
        progress_callback=None,
    ) -> "VectorBatchResult":
        """Execute ATP vectors through JTAG MPSSE, matching reference state_machine().

        Args:
            vectors: List of AtpVector (tms, tdi, tdo_mode).
            progress_callback: Optional callable(current, total) for progress.

        Returns:
            VectorBatchResult with success, captured TDO data, compare results.
        """
        import time as _time

        result = VectorBatchResult()
        if not vectors:
            return result

        total = len(vectors)
        state = "Test-Logic-Reset"

        # Accumulators (matching reference variable names)
        state_tms = []
        ir_tms, ir_tdi, ir_tdo = [], [], []
        dr_tms, dr_tdi, dr_tdo = [], [], []

        cmd_buf = bytearray()
        output_data: List[str] = []

        def _flush_tms(tms_list: list) -> None:
            """Build 0x4B TMS packets and append to cmd_buf."""
            buf = list(tms_list)
            while buf:
                chunk = buf[:self._MAX_TMS_PACKET]
                buf = buf[self._MAX_TMS_PACKET:]
                # Reverse bit order (reference: reversed(data_buff))
                rev = list(reversed(chunk))
                val = 0
                for b in rev:
                    val = (val << 1) | int(b)
                cmd_buf.extend([0x4B, len(chunk) - 1, val])

        def _send_shift_data(tdi_list: list, tdo_list: list) -> None:
            """Build 0x3B data shift packets + 0x6B exit. Handle TDO capture."""
            has_capture = "V" in [t.upper() for t in tdo_list]

            if has_capture:
                # Flush pending cmd_buf first
                if cmd_buf:
                    self.write(bytes(cmd_buf))
                    cmd_buf.clear()
                # Send immediate + clear queue before capture
                self.write(bytes([self._MPSSE_SEND_IMMEDIATE]))
                _time.sleep(0.001)
                # Flush RX
                try:
                    q = self._o._ft.getQueueStatus() if self._o._ft else 0
                    if q > 0:
                        self._o._ft.read(q)
                except Exception:
                    pass

            # Main data bits (all except last)
            main_tdi = tdi_list[:-1]
            local_buf = bytearray()
            buf = list(main_tdi)
            while buf:
                chunk = buf[:self._MAX_DATA_PACKET]
                buf = buf[self._MAX_DATA_PACKET:]
                rev = list(reversed(chunk))
                val = 0
                for b in rev:
                    val = (val << 1) | int(b)
                local_buf.extend([0x3B, len(chunk) - 1, val])

            # Last bit + TMS exit (0x6B)
            last_tdi = int(tdi_list[-1]) if tdi_list else 0
            if last_tdi:
                local_buf.extend([0x6B, 0x00, 0x83])
            else:
                local_buf.extend([0x6B, 0x00, 0x03])

            self.write(bytes(local_buf))

            # Read TDO if capture needed
            if has_capture:
                self.write(bytes([self._MPSSE_SEND_IMMEDIATE]))
                _time.sleep(0.001)

                try:
                    q = self._o._ft.getQueueStatus() if self._o._ft else 0
                    if q > 0:
                        read_data = list(self._o._ft.read(q))
                    else:
                        read_data = []
                except Exception:
                    read_data = []

                if read_data:
                    extracted = self._extract_read_data(read_data, tdo_list)
                    converted = self._convert_read_data(extracted, tdo_list)
                    output_data.append(converted)

        # ── Main state machine loop ───────────────────────────────

        for cnt, vec in enumerate(vectors):
            tms_val = str(vec.tms)
            tdi_val = str(vec.tdi)
            tdo_val = vec.tdo_mode

            new_state = self._REF_TAP[state][int(tms_val)]

            if state == "Shift-IR":
                ir_tms.append(tms_val)
                ir_tdi.append(tdi_val)
                ir_tdo.append(tdo_val)

                if new_state != state:
                    _flush_tms(state_tms)
                    if cmd_buf:
                        self.write(bytes(cmd_buf))
                        cmd_buf.clear()
                    _send_shift_data(ir_tdi, ir_tdo)
                    ir_tms, ir_tdi, ir_tdo = [], [], []
                    dr_tms, dr_tdi, dr_tdo = [], [], []
                    state_tms = []

            elif state == "Shift-DR":
                dr_tms.append(tms_val)
                dr_tdi.append(tdi_val)
                dr_tdo.append(tdo_val)

                if new_state != state:
                    _flush_tms(state_tms)
                    if cmd_buf:
                        self.write(bytes(cmd_buf))
                        cmd_buf.clear()
                    _send_shift_data(dr_tdi, dr_tdo)
                    ir_tms, ir_tdi, ir_tdo = [], [], []
                    dr_tms, dr_tdi, dr_tdo = [], [], []
                    state_tms = []

            elif state == "Run-Test-Idle":
                ir_tms, ir_tdi, ir_tdo = [], [], []
                dr_tms, dr_tdi, dr_tdo = [], [], []
                state_tms.append(tms_val)

            else:
                state_tms.append(tms_val)

            state = new_state

            # Progress callback every 10000 vectors
            if progress_callback and (cnt + 1) % 10000 == 0:
                progress_callback(cnt + 1, total)

        # Flush remaining TMS
        if state_tms:
            _flush_tms(state_tms)
        if cmd_buf:
            cmd_buf.append(self._MPSSE_SEND_IMMEDIATE)
            self.write(bytes(cmd_buf))
            cmd_buf.clear()

        if progress_callback:
            progress_callback(total, total)

        result.success = True
        result.output_data = output_data
        result.total_vectors = total
        return result

    # ── TDO data extraction (matches reference) ───────────────────

    def _extract_read_data(self, read_data: list, tdo_list: list) -> str:
        """Extract TDO bits from FTDI response bytes.

        Matches reference extract_read_data() logic.
        """
        tdo_data_bits = len(tdo_list) - 1  # excluding last bit
        full_count = tdo_data_bits // self._MAX_DATA_PACKET
        remain_bits = tdo_data_bits % self._MAX_DATA_PACKET

        # Reverse bits within each byte
        reversed_bits = [format(b, '08b')[::-1] for b in read_data]

        # Full packets
        merged = ''.join(reversed_bits[:full_count])

        # Remaining bits from second-to-last byte
        if remain_bits and len(reversed_bits) >= 2:
            merged += reversed_bits[-2][-remain_bits:]

        # Last bit from final byte
        if reversed_bits:
            merged += reversed_bits[-1][-1:]

        return merged

    def _convert_read_data(self, merged: str, tdo_list: list) -> str:
        """Convert extracted bits to hex, filtering by 'V' positions.

        Matches reference convert_read_data() logic.
        """
        binary_str = merged[:len(tdo_list)]

        # Extract only bits at 'V' positions
        extracted = ''.join(
            binary_str[i] for i, c in enumerate(tdo_list)
            if c.upper() == 'V' and i < len(binary_str)
        )

        if not extracted:
            return "0x00000000"

        # Reverse and convert to hex
        reversed_bits = extracted[::-1]
        int_val = int(reversed_bits, 2)
        return f"0x{int_val:08X}"


@dataclass
class VectorBatchResult:
    """Result of clock_vectors_batch()."""
    success: bool = False
    output_data: List[str] = field(default_factory=list)
    total_vectors: int = 0
    error_message: str = ""
