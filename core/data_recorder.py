"""
Common CSV data recorder for ADC modules.

Usage:
    recorder = DataRecorder()
    recorder.start("INA228", ["Vbus_V", "Current_mA", ...])
    recorder.add_row([3.301, 15.2, ...])
    recorder.stop()  # returns (filepath, sample_count)
"""

from __future__ import annotations

import csv
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple


class DataRecorder:
    """Simple CSV file recorder with automatic naming."""

    def __init__(self) -> None:
        self._file = None
        self._writer = None
        self._filepath: Optional[str] = None
        self._start_time: float = 0.0
        self._sample_count: int = 0

    @property
    def is_recording(self) -> bool:
        return self._file is not None

    @property
    def sample_count(self) -> int:
        return self._sample_count

    @property
    def elapsed_seconds(self) -> float:
        if not self.is_recording:
            return 0.0
        return time.time() - self._start_time

    @property
    def filepath(self) -> Optional[str]:
        return self._filepath

    def start(self, module_name: str, headers: List[str]) -> str:
        """Start recording. Returns the auto-generated file path."""
        if self.is_recording:
            self.stop()

        base_dir = Path.home() / "Documents" / "DeviceHub"
        base_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        filename = f"{module_name}_{now.strftime('%Y%m%d_%H%M%S')}.csv"
        self._filepath = str(base_dir / filename)

        self._file = open(self._filepath, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(["Timestamp", "Elapsed_s"] + headers)
        self._start_time = time.time()
        self._sample_count = 0

        return self._filepath

    def add_row(self, timestamp: float, values: List) -> None:
        """Append one data row."""
        if not self.is_recording:
            return
        elapsed = timestamp - self._start_time
        now_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        self._writer.writerow([now_str, f"{elapsed:.3f}"] + values)
        self._sample_count += 1

        # Flush every 100 samples
        if self._sample_count % 100 == 0:
            self._file.flush()

    def stop(self) -> Tuple[Optional[str], int]:
        """Stop recording. Returns (filepath, sample_count)."""
        if not self.is_recording:
            return None, 0

        filepath = self._filepath
        count = self._sample_count

        try:
            self._file.flush()
            self._file.close()
        except Exception:
            pass

        self._file = None
        self._writer = None
        self._filepath = None
        self._sample_count = 0

        return filepath, count
