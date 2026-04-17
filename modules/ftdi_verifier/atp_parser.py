"""
ATP pattern file parser.

Handles JCET-style ATP vector files with:
- vm_vector header parsing (signal name → column index)
- Vector line parsing with repeat expansion
- REGWRITE_MASK_DYN dynamic field extraction
- Dynamic value injection (TDI 'X' replacement)
- Quick-scan summary for UI (non-blocking for large files)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from modules.ftdi_verifier.scenario_model import (
    AtpPatternSummary,
    DynamicFieldInfo,
)

logger = logging.getLogger(__name__)


@dataclass
class AtpVector:
    """One parsed ATP vector (after repeat expansion)."""
    tms: int          # 0 or 1
    tdi: int          # 0 or 1
    tdo_mode: str     # 'X'=ignore, 'V'=capture, '0'/'1'=compare
    repeat: int = 1


# Pre-compiled patterns
_RE_REPEAT = re.compile(r"repeat\s+(\d+)", re.IGNORECASE)
_RE_DYN = re.compile(r"//\s*REGWRITE_MASK_DYN\s+", re.IGNORECASE)


class AtpParser:
    """Parser for JCET-style ATP vector files."""

    # ── Quick scan (UI summary, non-blocking) ──────────────────────

    @staticmethod
    def scan_summary(filepath: str, max_bytes: int = 0) -> AtpPatternSummary:
        """Quick scan: extract summary without building full vector list.

        Args:
            filepath: Path to ATP file.
            max_bytes: If > 0, read only first N bytes (for large files).

        Returns:
            AtpPatternSummary with counts and metadata.
        """
        summary = AtpPatternSummary()

        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_bytes) if max_bytes > 0 else f.read()
        except OSError as e:
            logger.warning(f"Cannot read ATP file: {e}")
            return summary

        lines = content.splitlines()
        header_parsed = False
        tdo_pos = -1

        for line in lines:
            stripped = line.strip()

            # Parse vm_vector header for signal names
            if not header_parsed and "$tset" in stripped:
                summary.signal_names = AtpParser._parse_header_signals(stripped)
                tdo_pos = AtpParser._find_column_index(
                    summary.signal_names, ("RH_JTAG_TDO", "TDO", "JTAG_TDO")
                )
                header_parsed = True
                continue

            # Count REGWRITE_MASK_DYN entries
            if _RE_DYN.search(stripped):
                field = AtpParser._parse_dyn_line(stripped)
                if field:
                    summary.dynamic_fields.append(field)
                    summary.dynamic_field_count += 1
                    summary.total_dynamic_bits += field.bit_width
                continue

            # Count vector lines
            if "> tsetJTAG" not in stripped and "> tset" not in stripped.lower():
                continue

            repeat = 1
            m = _RE_REPEAT.search(stripped)
            if m:
                repeat = int(m.group(1))

            summary.total_vectors += 1
            summary.repeat_expanded_count += repeat

            # Check TDO column for readback / compare
            if tdo_pos >= 0:
                tokens = AtpParser._extract_vector_tokens(stripped)
                if tdo_pos < len(tokens):
                    tdo_val = tokens[tdo_pos].upper()
                    if tdo_val == "V":
                        summary.has_readback = True
                    elif tdo_val in ("0", "1"):
                        summary.expected_compare_count += repeat

        return summary

    # ── Dynamic field detection ────────────────────────────────────

    @staticmethod
    def has_dynamic_fields(filepath: str) -> bool:
        """Quick check: does the file contain REGWRITE_MASK_DYN lines?"""
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "REGWRITE_MASK_DYN" in line:
                        return True
        except OSError:
            pass
        return False

    @staticmethod
    def get_register_list(filepath: str) -> List[DynamicFieldInfo]:
        """Extract all REGWRITE_MASK_DYN entries from an ATP file.

        Returns list of DynamicFieldInfo in file order.
        """
        fields: List[DynamicFieldInfo] = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "REGWRITE_MASK_DYN" not in line:
                        continue
                    field = AtpParser._parse_dyn_line(line.strip())
                    if field:
                        fields.append(field)
        except OSError as e:
            logger.warning(f"Cannot read ATP file: {e}")
        return fields

    # ── Full parse (execution) ─────────────────────────────────────

    @staticmethod
    def parse_vectors(
        filepath: str,
        dynamic_values: Optional[Dict[str, int]] = None,
    ) -> List[AtpVector]:
        """Full parse: build vector list with optional dynamic value injection.

        Args:
            filepath: Path to ATP file.
            dynamic_values: If provided, {reg_name: int_value} for X replacement.
                When None, TDI 'X' is treated as 0.

        Returns:
            List of AtpVector (repeat-expanded).
        """
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            logger.error(f"Cannot read ATP file: {e}")
            return []

        lines = content.splitlines()

        # Phase 1: parse header
        tms_pos = -1
        tdi_pos = -1
        tdo_pos = -1
        signal_names: List[str] = []

        for line in lines:
            stripped = line.strip()
            if "$tset" in stripped:
                signal_names = AtpParser._parse_header_signals(stripped)
                tms_pos = AtpParser._find_column_index(
                    signal_names, ("RH_PMU_SWDIOTMS", "TMS", "SWDIOTMS")
                )
                tdi_pos = AtpParser._find_column_index(
                    signal_names, ("RH_JTAG_TDI", "TDI", "JTAG_TDI")
                )
                tdo_pos = AtpParser._find_column_index(
                    signal_names, ("RH_JTAG_TDO", "TDO", "JTAG_TDO")
                )
                break

        if tms_pos < 0 or tdi_pos < 0 or tdo_pos < 0:
            logger.error(
                f"ATP header missing required signals "
                f"(TMS={tms_pos}, TDI={tdi_pos}, TDO={tdo_pos})"
            )
            return []

        # Phase 2: build dynamic bit queue if values provided
        dyn_bits: Optional[List[str]] = None
        if dynamic_values is not None:
            dyn_fields = AtpParser.get_register_list(filepath)
            dyn_bits = AtpParser._build_dynamic_bit_queue(dyn_fields, dynamic_values)

        # Phase 3: parse vector lines
        vectors: List[AtpVector] = []
        for line in lines:
            stripped = line.strip()
            if "> tsetJTAG" not in stripped and "> tset" not in stripped.lower():
                continue

            repeat = 1
            m = _RE_REPEAT.search(stripped)
            if m:
                repeat = int(m.group(1))

            tokens = AtpParser._extract_vector_tokens(stripped)
            if max(tms_pos, tdi_pos, tdo_pos) >= len(tokens):
                continue

            tms_val = AtpParser._parse_bit(tokens[tms_pos])
            tdo_mode = tokens[tdo_pos].upper()
            if tdo_mode not in ("X", "V", "0", "1"):
                tdo_mode = "X"

            # TDI: handle dynamic replacement
            tdi_raw = tokens[tdi_pos].upper()
            for _ in range(repeat):
                if tdi_raw in ("0", "1"):
                    tdi_val = int(tdi_raw)
                elif dyn_bits and len(dyn_bits) > 0:
                    # 'X' or other → replace with dynamic bit
                    tdi_val = int(dyn_bits.pop(0))
                else:
                    tdi_val = 0  # default for X when no dynamic values

                vectors.append(AtpVector(
                    tms=tms_val,
                    tdi=tdi_val,
                    tdo_mode=tdo_mode,
                    repeat=1,
                ))

        return vectors

    # ── Internal helpers ───────────────────────────────────────────

    @staticmethod
    def _parse_header_signals(header_line: str) -> List[str]:
        """Parse vm_vector header to extract signal names.

        Input: "vm_vector ( $tset, RH_PMU_SWCLKTCK, RH_PMU_SWDIOTMS, ...)"
        Output: ["RH_PMU_SWCLKTCK", "RH_PMU_SWDIOTMS", ...]
        (excludes $tset)
        """
        # Find content between parentheses
        paren_start = header_line.find("(")
        paren_end = header_line.find(")")
        if paren_start < 0:
            # Fallback: split on whitespace after $tset
            parts = header_line.split()
            idx = -1
            for i, p in enumerate(parts):
                if "$tset" in p:
                    idx = i
                    break
            if idx >= 0:
                signals = []
                for p in parts[idx + 1:]:
                    name = p.strip(",).;")
                    if name and not name.startswith("$"):
                        signals.append(name)
                return signals
            return []

        inner = header_line[paren_start + 1: paren_end if paren_end > 0 else None]
        parts = [p.strip().strip(",") for p in inner.split(",")]
        signals = []
        for p in parts:
            p = p.strip()
            if not p or p.startswith("$"):
                continue
            signals.append(p)
        return signals

    @staticmethod
    def _find_column_index(
        signal_names: List[str],
        candidates: Tuple[str, ...],
    ) -> int:
        """Find column index matching any candidate name (case-insensitive)."""
        for i, name in enumerate(signal_names):
            for cand in candidates:
                if cand.upper() in name.upper():
                    return i
        return -1

    @staticmethod
    def _extract_vector_tokens(line: str) -> List[str]:
        """Extract value tokens from a vector line.

        Input: "... > tsetJTAG  1  0  0  X  1  1  1  ; // comment"
        Output: ["1", "0", "0", "X", "1", "1", "1"]
        """
        # Find "> tsetJTAG" or "> tset"
        idx = line.find("> tsetJTAG")
        if idx < 0:
            idx = line.find("> tset")
        if idx < 0:
            return []

        after = line[idx:]
        # Remove comment
        semi_idx = after.find(";")
        if semi_idx >= 0:
            after = after[:semi_idx]

        parts = after.split()
        # Skip ">", "tsetJTAG" (or "tset")
        if len(parts) >= 2:
            return parts[2:]  # values start after "> tsetJTAG"
        return []

    @staticmethod
    def _parse_bit(token: str) -> int:
        """Parse a single bit token to int."""
        if token == "1":
            return 1
        return 0

    @staticmethod
    def _parse_dyn_line(line: str) -> Optional[DynamicFieldInfo]:
        """Parse a REGWRITE_MASK_DYN comment line.

        Format: // REGWRITE_MASK_DYN JTAG addr mask reg_name default null description
        Example: // REGWRITE_MASK_DYN JTAG 0x810404 0x3e0 BIAS_RCAL 0xfc1f4200 null rdb_type2...
        """
        # Remove leading // and whitespace
        text = line.lstrip("/").strip()
        if not text.startswith("REGWRITE_MASK_DYN"):
            return None

        parts = text.split()
        # parts[0] = REGWRITE_MASK_DYN
        # parts[1] = JTAG (or other protocol)
        # parts[2] = address (hex)
        # parts[3] = mask (hex)
        # parts[4] = reg_name
        # parts[5] = default value (hex)
        # parts[6] = null
        # parts[7+] = description
        if len(parts) < 5:
            return None

        try:
            address = int(parts[2], 16) if parts[2].startswith("0x") else int(parts[2])
        except (ValueError, IndexError):
            address = 0

        try:
            mask = int(parts[3], 16) if parts[3].startswith("0x") else int(parts[3])
        except (ValueError, IndexError):
            mask = 0

        reg_name = parts[4]
        bit_width = bin(mask).count("1")
        description = " ".join(parts[7:]) if len(parts) > 7 else ""

        return DynamicFieldInfo(
            reg_name=reg_name,
            bit_width=bit_width,
            address=address,
            mask=mask,
            description=description,
        )

    @staticmethod
    def _build_dynamic_bit_queue(
        fields: List[DynamicFieldInfo],
        values: Dict[str, int],
    ) -> List[str]:
        """Build a flat list of bit characters for dynamic injection.

        Matches the reference implementation's ordering:
        fields are processed in file order, values prepended (reversed),
        then consumed FIFO by parse_vectors.
        """
        # Reference: atp_file_control.py builds dynamic_values by prepending
        # each register's bits, then the parser pops from front.
        all_bits = ""
        for f in fields:
            val = values.get(f.reg_name)
            if val is None:
                # Use zeros if value not resolved
                bits = "0" * f.bit_width
            else:
                bits = format(val, f"0{f.bit_width}b")
                # Ensure correct width (trim or pad)
                if len(bits) > f.bit_width:
                    bits = bits[-f.bit_width:]
                elif len(bits) < f.bit_width:
                    bits = bits.zfill(f.bit_width)
            # Prepend (matching reference: dynamic_values = value + dynamic_values)
            all_bits = bits + all_bits

        # Reverse to create FIFO queue (reference: list(dynamic_values)[::-1] then pop(0))
        return list(all_bits[::-1])
