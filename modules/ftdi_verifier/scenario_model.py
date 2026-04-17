"""
Scenario data model for ATP pattern execution workflows.

Pure Python dataclasses — no Qt dependencies.
Supports JSON serialization for scenario save/load.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ── Enums ──────────────────────────────────────────────────────────

class StepType(Enum):
    """Scenario step types. Extensible — add new types here."""
    ATP_PATTERN = "atp_pattern"
    GPIO_SEQUENCE = "gpio_sequence"
    DELAY = "delay"


class StepStatus(Enum):
    """Runtime status of a scenario step."""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class DynamicMode(Enum):
    """Source mode for resolving dynamic TDI values."""
    NONE = "none"
    AUTO = "auto"
    CSV = "csv"
    MANUAL = "manual"
    SCRIPT = "script"


# ── Dynamic Field (extracted from ATP file) ────────────────────────

@dataclass
class DynamicFieldInfo:
    """One REGWRITE_MASK_DYN entry parsed from an ATP file."""
    reg_name: str
    bit_width: int
    address: int = 0
    mask: int = 0
    description: str = ""
    resolved_value: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "reg_name": self.reg_name,
            "bit_width": self.bit_width,
            "address": self.address,
            "mask": self.mask,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DynamicFieldInfo:
        return cls(
            reg_name=d.get("reg_name", ""),
            bit_width=d.get("bit_width", 0),
            address=d.get("address", 0),
            mask=d.get("mask", 0),
            description=d.get("description", ""),
        )


# ── ATP Pattern Summary ───────────────────────────────────────────

@dataclass
class AtpPatternSummary:
    """Cached quick-scan summary of an ATP file."""
    total_vectors: int = 0
    repeat_expanded_count: int = 0
    dynamic_field_count: int = 0
    total_dynamic_bits: int = 0
    has_readback: bool = False
    expected_compare_count: int = 0
    signal_names: List[str] = field(default_factory=list)
    dynamic_fields: List[DynamicFieldInfo] = field(default_factory=list)


# ── Dynamic Source Config ──────────────────────────────────────────

@dataclass
class DynamicSourceConfig:
    """Configuration for how dynamic values are resolved for a step."""
    mode: DynamicMode = DynamicMode.NONE
    csv_path: str = ""
    manual_values: Dict[str, int] = field(default_factory=dict)
    script_expr: str = ""
    capture_step_id: str = ""
    register_map_path: str = ""

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "csv_path": self.csv_path,
            "manual_values": dict(self.manual_values),
            "script_expr": self.script_expr,
            "capture_step_id": self.capture_step_id,
            "register_map_path": self.register_map_path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DynamicSourceConfig:
        mode_str = d.get("mode", "none")
        try:
            mode = DynamicMode(mode_str)
        except ValueError:
            mode = DynamicMode.NONE
        return cls(
            mode=mode,
            csv_path=d.get("csv_path", ""),
            manual_values=d.get("manual_values", {}),
            script_expr=d.get("script_expr", ""),
            capture_step_id=d.get("capture_step_id", ""),
            register_map_path=d.get("register_map_path", ""),
        )


# ── Scenario Step ──────────────────────────────────────────────────

@dataclass
class ScenarioStep:
    """One step in the execution scenario."""
    step_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    enabled: bool = True
    step_type: StepType = StepType.ATP_PATTERN
    name: str = ""
    filename: str = ""
    dynamic_source: DynamicSourceConfig = field(default_factory=DynamicSourceConfig)
    status: StepStatus = StepStatus.PENDING
    result_message: str = ""
    elapsed_ms: float = 0.0
    # DELAY type
    delay_ms: int = 0
    # GPIO_SEQUENCE type
    gpio_config: Dict[str, Any] = field(default_factory=dict)

    def reset_status(self) -> None:
        """Reset runtime state before a new execution."""
        self.status = StepStatus.PENDING
        self.result_message = ""
        self.elapsed_ms = 0.0

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "enabled": self.enabled,
            "step_type": self.step_type.value,
            "name": self.name,
            "filename": self.filename,
            "dynamic_source": self.dynamic_source.to_dict(),
            "delay_ms": self.delay_ms,
            "gpio_config": dict(self.gpio_config),
        }

    @classmethod
    def from_dict(cls, d: dict) -> ScenarioStep:
        type_str = d.get("step_type", "atp_pattern")
        try:
            step_type = StepType(type_str)
        except ValueError:
            step_type = StepType.ATP_PATTERN
        ds = d.get("dynamic_source", {})
        return cls(
            step_id=d.get("step_id", str(uuid.uuid4())),
            enabled=d.get("enabled", True),
            step_type=step_type,
            name=d.get("name", ""),
            filename=d.get("filename", ""),
            dynamic_source=DynamicSourceConfig.from_dict(ds) if isinstance(ds, dict) else DynamicSourceConfig(),
            delay_ms=d.get("delay_ms", 0),
            gpio_config=d.get("gpio_config", {}),
        )


# ── Scenario (top-level, JSON serializable) ────────────────────────

@dataclass
class Scenario:
    """Complete scenario definition."""
    name: str = "Untitled Scenario"
    description: str = ""
    pattern_folder: str = ""
    register_map_path: str = ""
    tck_frequency_hz: int = 1_000_000
    steps: List[ScenarioStep] = field(default_factory=list)
    created_at: str = ""
    modified_at: str = ""

    def reset_all_status(self) -> None:
        """Reset all step statuses before a new execution run."""
        for step in self.steps:
            step.reset_status()

    def get_step_by_id(self, step_id: str) -> Optional[ScenarioStep]:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def enabled_steps(self) -> List[ScenarioStep]:
        return [s for s in self.steps if s.enabled]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "pattern_folder": self.pattern_folder,
            "register_map_path": self.register_map_path,
            "tck_frequency_hz": self.tck_frequency_hz,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Scenario:
        steps_raw = d.get("steps", [])
        steps = [ScenarioStep.from_dict(s) for s in steps_raw if isinstance(s, dict)]
        return cls(
            name=d.get("name", "Untitled Scenario"),
            description=d.get("description", ""),
            pattern_folder=d.get("pattern_folder", ""),
            register_map_path=d.get("register_map_path", ""),
            tck_frequency_hz=d.get("tck_frequency_hz", 1_000_000),
            steps=steps,
            created_at=d.get("created_at", ""),
            modified_at=d.get("modified_at", ""),
        )

    def to_json(self, path: str) -> None:
        """Save scenario to JSON file."""
        self.modified_at = datetime.now().isoformat(timespec="seconds")
        if not self.created_at:
            self.created_at = self.modified_at
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, path: str) -> Scenario:
        """Load scenario from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
