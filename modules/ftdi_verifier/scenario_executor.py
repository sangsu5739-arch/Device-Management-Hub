"""
Scenario execution engine.

QObject-based worker that runs in a QThread.
Executes scenario steps sequentially with status callbacks.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal, QMutex, QMutexLocker

from core.ftdi_manager import FtdiManager
from modules.ftdi_verifier.scenario_model import (
    Scenario, ScenarioStep, StepType, StepStatus,
    DynamicMode, DynamicSourceConfig,
)
from modules.ftdi_verifier.atp_parser import AtpParser
from modules.ftdi_verifier.register_map import RegisterMap


class ExecutionMode(Enum):
    RUN_ALL = "run_all"
    RUN_SELECTED = "run_selected"
    RUN_FROM_HERE = "run_from_here"
    DRY_RUN = "dry_run"


@dataclass
class StepResult:
    """Result of a single step execution."""
    step_id: str = ""
    status: StepStatus = StepStatus.PENDING
    message: str = ""
    elapsed_ms: float = 0.0
    tdo_data: List[str] = field(default_factory=list)
    compare_pass: bool = True
    compare_details: str = ""


class ScenarioExecutor(QObject):
    """Executes scenario steps sequentially in a worker thread."""

    # Signals
    step_started = Signal(str)                    # step_id
    step_progress = Signal(str, int, int)         # step_id, current, total
    step_completed = Signal(object)               # StepResult
    execution_finished = Signal(bool, str)        # success, summary
    log_message = Signal(str)
    tdo_data_captured = Signal(str, str)           # step_id, hex_value

    def __init__(
        self,
        ftdi: FtdiManager,
        scenario: Scenario,
        register_map: Optional[RegisterMap] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._ftdi = ftdi
        self._scenario = scenario
        self._register_map = register_map
        self._stop_requested = False
        self._mutex = QMutex()

        # Capture store: step_id → list of hex strings from TDO 'V' capture
        self._capture_store: Dict[str, List[str]] = {}

        # Execution mode + target filtering
        self._mode = ExecutionMode.RUN_ALL
        self._target_step_ids: Optional[List[str]] = None
        self._start_from_id: Optional[str] = None

    def request_stop(self) -> None:
        with QMutexLocker(self._mutex):
            self._stop_requested = True

    def _is_stopped(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._stop_requested

    # ── Main entry point ───────────────────────────────────────────

    def execute(
        self,
        mode: ExecutionMode = ExecutionMode.RUN_ALL,
        step_ids: Optional[List[str]] = None,
        start_from_id: Optional[str] = None,
    ) -> None:
        """Main execution — called from QThread.started signal."""
        self._mode = mode
        self._target_step_ids = step_ids
        self._start_from_id = start_from_id
        self._stop_requested = False

        dry_run = (mode == ExecutionMode.DRY_RUN)
        steps = self._resolve_steps()
        total_steps = len(steps)
        passed_count = 0
        failed_count = 0

        self._log(
            f"Scenario execution started: {self._scenario.name} "
            f"({total_steps} steps, mode={mode.value})"
        )

        # Reset status for steps we'll run
        for step in steps:
            step.reset_status()

        for i, step in enumerate(steps):
            if self._is_stopped():
                # Mark remaining as skipped
                for remaining in steps[i:]:
                    remaining.status = StepStatus.SKIPPED
                    result = StepResult(
                        step_id=remaining.step_id,
                        status=StepStatus.SKIPPED,
                        message="Stopped by user",
                    )
                    self.step_completed.emit(result)
                break

            self.step_started.emit(step.step_id)
            t0 = time.time()

            try:
                result = self._execute_step(step, dry_run=dry_run)
            except Exception as e:
                result = StepResult(
                    step_id=step.step_id,
                    status=StepStatus.ERROR,
                    message=str(e),
                )

            result.elapsed_ms = (time.time() - t0) * 1000
            step.status = result.status
            step.result_message = result.message
            step.elapsed_ms = result.elapsed_ms

            if result.status == StepStatus.PASSED:
                passed_count += 1
            elif result.status in (StepStatus.FAILED, StepStatus.ERROR):
                failed_count += 1

            self.step_completed.emit(result)

        success = failed_count == 0 and not self._is_stopped()
        summary = (
            f"Completed: {passed_count} passed, {failed_count} failed"
            f" ({total_steps} total)"
        )
        if self._is_stopped():
            summary = f"Stopped. {summary}"
        self._log(summary)
        self.execution_finished.emit(success, summary)

    # ── Step resolution ────────────────────────────────────────────

    def _resolve_steps(self) -> List[ScenarioStep]:
        """Determine which steps to run based on mode."""
        all_enabled = self._scenario.enabled_steps()

        if self._mode == ExecutionMode.RUN_SELECTED and self._target_step_ids:
            target_set = set(self._target_step_ids)
            return [s for s in all_enabled if s.step_id in target_set]

        if self._mode == ExecutionMode.RUN_FROM_HERE and self._start_from_id:
            found = False
            result = []
            for s in all_enabled:
                if s.step_id == self._start_from_id:
                    found = True
                if found:
                    result.append(s)
            return result

        # RUN_ALL and DRY_RUN
        return all_enabled

    # ── Step dispatch ──────────────────────────────────────────────

    def _execute_step(self, step: ScenarioStep, dry_run: bool = False) -> StepResult:
        self._log(f"Step: {step.name} ({step.step_type.value})")

        if step.step_type == StepType.ATP_PATTERN:
            return self._execute_atp_pattern(step, dry_run)
        elif step.step_type == StepType.DELAY:
            return self._execute_delay(step, dry_run)
        elif step.step_type == StepType.GPIO_SEQUENCE:
            return self._execute_gpio(step, dry_run)
        else:
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.ERROR,
                message=f"Unknown step type: {step.step_type.value}",
            )

    # ── ATP pattern execution ──────────────────────────────────────

    def _execute_atp_pattern(self, step: ScenarioStep, dry_run: bool) -> StepResult:
        folder = self._scenario.pattern_folder
        filepath = os.path.join(folder, step.filename) if folder else step.filename

        if not os.path.isfile(filepath):
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.ERROR,
                message=f"File not found: {step.filename}",
            )

        # Resolve dynamic values
        dynamic_values = None
        if step.dynamic_source.mode != DynamicMode.NONE:
            dynamic_values = self._resolve_dynamic_values(step)
            if dynamic_values is None and step.dynamic_source.mode != DynamicMode.NONE:
                self._log(f"  Warning: dynamic values not resolved, using defaults")

        # Parse vectors
        self._log(f"  Parsing: {step.filename}")
        try:
            vectors = AtpParser.parse_vectors(filepath, dynamic_values)
        except Exception as e:
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.ERROR,
                message=f"Parse error: {e}",
            )

        self._log(f"  Vectors: {len(vectors)}")

        if dry_run:
            # Dry run: parse only, no hardware
            has_dyn = AtpParser.has_dynamic_fields(filepath)
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.PASSED,
                message=f"Dry run OK ({len(vectors)} vectors"
                        f"{', dynamic' if has_dyn else ''})",
            )

        # Execute via FTDI
        if not self._ftdi.is_connected:
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.ERROR,
                message="FTDI not connected",
            )

        def _progress(current, total):
            self.step_progress.emit(step.step_id, current, total)

        self._log(f"  Executing {len(vectors)} vectors...")
        batch_result = self._ftdi.jtag_execute_vectors(vectors, _progress)

        if batch_result is None:
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.ERROR,
                message="JTAG execution failed",
            )

        # Store captured TDO data for later steps
        if batch_result.output_data:
            self._capture_store[step.step_id] = batch_result.output_data
            for hex_val in batch_result.output_data:
                self.tdo_data_captured.emit(step.step_id, hex_val)
            self._log(f"  Captured {len(batch_result.output_data)} TDO value(s)")

        return StepResult(
            step_id=step.step_id,
            status=StepStatus.PASSED if batch_result.success else StepStatus.FAILED,
            message=f"{batch_result.total_vectors} vectors"
                    f" | {len(batch_result.output_data)} captures"
                    if batch_result.success else
                    batch_result.error_message or "Execution failed",
            tdo_data=batch_result.output_data,
        )

    # ── Dynamic value resolution ───────────────────────────────────

    def _resolve_dynamic_values(self, step: ScenarioStep) -> Optional[Dict[str, int]]:
        """Resolve dynamic values based on step's DynamicSourceConfig."""
        mode = step.dynamic_source.mode

        if mode == DynamicMode.AUTO:
            return self._resolve_auto(step)
        elif mode == DynamicMode.CSV:
            return self._resolve_csv(step)
        elif mode == DynamicMode.MANUAL:
            return step.dynamic_source.manual_values or None
        elif mode == DynamicMode.SCRIPT:
            self._log("  Script mode not yet implemented")
            return None
        return None

    def _resolve_auto(self, step: ScenarioStep) -> Optional[Dict[str, int]]:
        """AUTO mode: extract from capture data via register map."""
        capture_id = step.dynamic_source.capture_step_id
        if not capture_id or capture_id not in self._capture_store:
            self._log("  AUTO: no capture data available")
            return None

        reg_map = self._register_map
        if reg_map is None or reg_map.count == 0:
            self._log("  AUTO: no register map loaded")
            return None

        # Get captured hex values and convert to bit string
        capture_hex_list = self._capture_store[capture_id]
        if not capture_hex_list:
            return None

        # Concatenate all captured values into one big bit string
        all_bits = ""
        for hex_val in capture_hex_list:
            val = int(hex_val, 16)
            all_bits += format(val, '032b')

        # Reverse to match reference convention
        all_bits = all_bits[::-1]

        # Get dynamic fields from ATP file
        folder = self._scenario.pattern_folder
        filepath = os.path.join(folder, step.filename) if folder else step.filename
        fields = AtpParser.get_register_list(filepath)

        if not fields:
            return None

        # Resolve via register map
        values = reg_map.resolve_dynamic_fields(fields, all_bits)
        self._log(f"  AUTO resolved {len(values)}/{len(fields)} fields")
        return values if values else None

    def _resolve_csv(self, step: ScenarioStep) -> Optional[Dict[str, int]]:
        """CSV mode: read reg_name→value from CSV file."""
        csv_path = step.dynamic_source.csv_path
        if not csv_path or not os.path.isfile(csv_path):
            self._log(f"  CSV: file not found: {csv_path}")
            return None

        import csv
        values: Dict[str, int] = {}
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) >= 2:
                        name = row[0].strip()
                        val_str = row[1].strip()
                        if name and val_str:
                            try:
                                val = int(val_str, 16) if val_str.startswith("0x") else int(val_str)
                                values[name] = val
                            except ValueError:
                                pass
        except OSError as e:
            self._log(f"  CSV read error: {e}")
            return None

        return values if values else None

    # ── Delay step ─────────────────────────────────────────────────

    def _execute_delay(self, step: ScenarioStep, dry_run: bool) -> StepResult:
        ms = step.delay_ms
        if dry_run:
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.PASSED,
                message=f"Dry run: delay {ms}ms",
            )
        self._log(f"  Delay: {ms}ms")
        time.sleep(ms / 1000.0)
        return StepResult(
            step_id=step.step_id,
            status=StepStatus.PASSED,
            message=f"{ms}ms",
        )

    # ── GPIO step ──────────────────────────────────────────────────

    def _execute_gpio(self, step: ScenarioStep, dry_run: bool) -> StepResult:
        if dry_run:
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.PASSED,
                message=f"Dry run: GPIO config={step.gpio_config}",
            )
        # GPIO execution placeholder — Phase 5+ will implement full bitbang
        self._log(f"  GPIO: config={step.gpio_config} (stub)")
        return StepResult(
            step_id=step.step_id,
            status=StepStatus.PASSED,
            message="GPIO (stub)",
        )

    # ── Validation ─────────────────────────────────────────────────

    def validate_scenario(self) -> List[Tuple[str, str]]:
        """Preflight validation. Returns [(step_id, error)] pairs."""
        errors: List[Tuple[str, str]] = []
        folder = self._scenario.pattern_folder

        for step in self._scenario.enabled_steps():
            if step.step_type == StepType.ATP_PATTERN:
                filepath = os.path.join(folder, step.filename) if folder else step.filename
                if not os.path.isfile(filepath):
                    errors.append((step.step_id, f"File not found: {step.filename}"))
                    continue

                # Check dynamic source
                has_dyn = AtpParser.has_dynamic_fields(filepath)
                mode = step.dynamic_source.mode

                if has_dyn and mode == DynamicMode.NONE:
                    errors.append((
                        step.step_id,
                        f"Has dynamic fields but mode=None: {step.filename}"
                    ))
                if mode == DynamicMode.AUTO:
                    if not self._register_map or self._register_map.count == 0:
                        errors.append((step.step_id, "Auto mode: no register map loaded"))
                    if not step.dynamic_source.capture_step_id:
                        errors.append((step.step_id, "Auto mode: no capture step specified"))
                elif mode == DynamicMode.CSV:
                    if not step.dynamic_source.csv_path:
                        errors.append((step.step_id, "CSV mode: no CSV path specified"))
                elif mode == DynamicMode.MANUAL:
                    if not step.dynamic_source.manual_values:
                        errors.append((step.step_id, "Manual mode: no values entered"))

        return errors

    # ── Utils ──────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log_message.emit(f"[{ts}] {msg}")
