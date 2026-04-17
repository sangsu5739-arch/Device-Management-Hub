"""
Generic register map manager.

Loads a CSV mapping of register/field names to bit positions
within a captured data stream. Not tied to any specific use case
(eFuse, OTP, config registers, etc.).

CSV format (minimum):
    name,group,msb,lsb

Example:
    name,group,msb,lsb
    ana_rcal_core_da,rcal,164,160
    ana_v2i_1p8_r_trim_da,rcal,174,170
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from modules.ftdi_verifier.scenario_model import DynamicFieldInfo

logger = logging.getLogger(__name__)


@dataclass
class RegisterEntry:
    """One register/field mapping entry."""
    name: str
    group: str
    msb: int
    lsb: int

    @property
    def bit_width(self) -> int:
        return self.msb - self.lsb + 1


class RegisterMap:
    """Generic register name → bit position mapping table.

    Loads from CSV and provides lookup + bit extraction from
    captured data (bytes or bit string).
    """

    def __init__(self) -> None:
        self._entries: Dict[str, RegisterEntry] = {}
        self._filepath: str = ""

    @property
    def filepath(self) -> str:
        return self._filepath

    @property
    def entries(self) -> Dict[str, RegisterEntry]:
        return dict(self._entries)

    @property
    def count(self) -> int:
        return len(self._entries)

    def load_csv(self, filepath: str) -> int:
        """Load register map from CSV file.

        Auto-detects column positions by header names.
        Required columns: name (or 'efuse name'), msb, lsb
        Optional column: group

        Returns number of entries loaded.
        """
        self._entries.clear()
        self._filepath = filepath

        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f)
                rows = list(reader)
        except OSError as e:
            logger.error(f"Cannot read register map CSV: {e}")
            return 0

        if not rows:
            return 0

        # Auto-detect column indices from header
        header = [h.strip().lower() for h in rows[0]]
        name_idx = self._find_col(header, ("name", "efuse name", "register", "field"))
        group_idx = self._find_col(header, ("group", "category", "type"))
        msb_idx = self._find_col(header, ("msb", "msb_bit", "bit_high"))
        lsb_idx = self._find_col(header, ("lsb", "lsb_bit", "bit_low"))

        if name_idx < 0 or msb_idx < 0 or lsb_idx < 0:
            logger.error(
                f"Register map CSV missing required columns. "
                f"Found: {header}. Need: name, msb, lsb"
            )
            return 0

        for row in rows[1:]:
            if len(row) <= max(name_idx, msb_idx, lsb_idx):
                continue
            name = row[name_idx].strip()
            if not name:
                continue
            try:
                msb = int(row[msb_idx].strip())
                lsb = int(row[lsb_idx].strip())
            except (ValueError, IndexError):
                continue

            group = row[group_idx].strip() if group_idx >= 0 and group_idx < len(row) else ""

            self._entries[name] = RegisterEntry(
                name=name,
                group=group,
                msb=msb,
                lsb=lsb,
            )

        logger.info(f"Register map loaded: {len(self._entries)} entries from {filepath}")
        return len(self._entries)

    def get_entry(self, name: str) -> Optional[RegisterEntry]:
        """Look up a register by name."""
        return self._entries.get(name)

    def extract_value(
        self,
        name: str,
        capture_bits: str,
    ) -> Optional[int]:
        """Extract a field's value from captured bit string.

        Args:
            name: Register/field name.
            capture_bits: Binary string of captured data
                          (index 0 = bit 0, matching reference convention).

        Returns:
            Integer value of the extracted field, or None if not found.
        """
        entry = self._entries.get(name)
        if entry is None:
            return None

        total_bits = len(capture_bits)
        if total_bits == 0:
            return None

        # Reference convention: efuse_values[-msb-1 : total-lsb]
        # This is because the bit string is stored with bit 0 at the end
        start = total_bits - entry.msb - 1
        end = total_bits - entry.lsb

        if start < 0:
            start = 0
        if end > total_bits:
            end = total_bits
        if start >= end:
            return None

        bits = capture_bits[start:end]
        if not bits:
            return None

        try:
            return int(bits, 2)
        except ValueError:
            return None

    def resolve_dynamic_fields(
        self,
        fields: List[DynamicFieldInfo],
        capture_bits: str,
    ) -> Dict[str, int]:
        """Resolve all dynamic fields from capture data.

        Args:
            fields: List of DynamicFieldInfo from ATP parser.
            capture_bits: Binary string of captured data.

        Returns:
            Dict of {reg_name: resolved_int_value}.
            Missing entries are omitted (not included with default).
        """
        result: Dict[str, int] = {}
        for f in fields:
            val = self.extract_value(f.reg_name, capture_bits)
            if val is not None:
                result[f.reg_name] = val
                f.resolved_value = val
            else:
                logger.debug(
                    f"Register '{f.reg_name}' not found in map or capture data"
                )
        return result

    @staticmethod
    def _find_col(header: List[str], candidates: tuple) -> int:
        """Find column index matching any candidate (case-insensitive)."""
        for i, h in enumerate(header):
            for c in candidates:
                if c in h:
                    return i
        return -1
