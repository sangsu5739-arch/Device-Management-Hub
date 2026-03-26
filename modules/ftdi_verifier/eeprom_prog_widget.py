from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Slot, Signal, QThread
from PySide6.QtGui import QRegularExpressionValidator, QFont
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QGroupBox, QLabel, QPushButton, QComboBox, QSpinBox,
    QLineEdit, QRadioButton, QCheckBox, QMessageBox,
)

from core.ftdi_manager import FtdiManager
from core.theme_manager import ThemeManager
from modules.ftdi_verifier.eeprom_worker import EepromWorker


class EepromProgPanel(QWidget):
    """EEPROM Programming Panel for FTDI verification

    Allows read/write of FTDI EEPROM parameters:
    VID, PID, Current, Manufacturer, Product desc, etc.

    Uses a persistent worker thread to avoid QThread create/destroy
    lifecycle crashes from stale queued signals.
    """

    # Internal signals to trigger worker operations (queued cross-thread)
    _trigger_read = Signal()
    _trigger_write = Signal()
    _trigger_reset = Signal()

    def __init__(self, ftdi_manager: FtdiManager, append_log_cb, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ftdi = ftdi_manager
        self._append_log = append_log_cb

        self._busy: bool = False
        self._eeprom_read_done: bool = False  # Write requires Read first

        self._init_ui()
        self._init_worker_thread()

    def _init_worker_thread(self) -> None:
        """Create a persistent worker + thread that lives for the panel lifetime."""
        self._worker = EepromWorker(self._ftdi)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        # Connect trigger signals → worker slots (queued: main → worker thread)
        self._trigger_read.connect(self._worker.read_eeprom)
        self._trigger_write.connect(self._worker.write_eeprom)
        self._trigger_reset.connect(self._worker.reset_device)

        # Connect worker result signals → UI slots (queued: worker → main thread)
        self._worker.log_message.connect(self._append_log)
        self._worker.eeprom_data_read.connect(self._on_eeprom_data_read)
        self._worker.operation_finished.connect(self._on_operation_finished)
        self._worker.request_disconnect.connect(self._on_request_disconnect)

        self._thread.start()

    def cleanup(self) -> None:
        """Stop the persistent worker thread. Call before destroying the panel."""
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
        self._worker = None

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1. Device Selection (Upper)
        dev_group = QGroupBox("Target Device")
        dev_layout = QHBoxLayout(dev_group)
        dev_layout.setContentsMargins(8, 8, 8, 8)
        dev_layout.addWidget(QLabel("Current Device:"))
        self._device_combo = QComboBox()
        self._device_combo.addItem("Auto (from FtdiManager connection)")
        self._device_combo.setEnabled(False)  # Managed centrally by MainWindow
        dev_layout.addWidget(self._device_combo, 1)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        dev_layout.addWidget(self._refresh_btn)
        layout.addWidget(dev_group)

        # 2 & 3. Current State / New Settings (Middle)
        mid_layout = QHBoxLayout()
        mid_layout.setSpacing(8)

        # 2. Current EEPROM (Read-only)
        curr_group = QGroupBox("Current Config (RO)")
        curr_layout = QFormLayout(curr_group)
        curr_layout.setContentsMargins(8, 8, 8, 8)
        curr_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._ro_serial = QLineEdit("-")
        self._ro_mfg = QLineEdit("-")
        self._ro_desc = QLineEdit("-")
        self._ro_vid = QLineEdit("-")
        self._ro_pid = QLineEdit("-")
        self._ro_max_pwr = QLineEdit("-")
        self._ro_pwr_mode = QLineEdit("-")
        self._ro_wakeup = QLineEdit("-")

        self._ro_fields = (
            self._ro_serial, self._ro_mfg, self._ro_desc,
            self._ro_vid, self._ro_pid, self._ro_max_pwr,
            self._ro_pwr_mode, self._ro_wakeup,
        )
        tm = ThemeManager.instance()
        for w in self._ro_fields:
            w.setReadOnly(True)
            w.setMinimumWidth(120)
            w.setStyleSheet(
                f"background-color: {tm.color('bg_deep')};"
                f" color: {tm.color('text_primary')};"
                f" border: 1px solid {tm.color('border_subtle')};"
                f" border-radius: 4px; padding: 4px; font-weight: 600;"
            )

        curr_layout.addRow("Serial:", self._ro_serial)
        curr_layout.addRow("Mfg:", self._ro_mfg)
        curr_layout.addRow("Product:", self._ro_desc)
        curr_layout.addRow("VID:", self._ro_vid)
        curr_layout.addRow("PID:", self._ro_pid)
        curr_layout.addRow("Power(mA):", self._ro_max_pwr)
        curr_layout.addRow("Pwr Mode:", self._ro_pwr_mode)
        curr_layout.addRow("Wakeup:", self._ro_wakeup)

        mid_layout.addWidget(curr_group, 1)

        # 3. New Settings Input
        new_group = QGroupBox("New Config")
        new_layout = QFormLayout(new_group)
        new_layout.setContentsMargins(8, 8, 8, 8)
        new_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._inp_serial = QLineEdit()
        self._inp_mfg = QLineEdit()
        self._inp_desc = QLineEdit()

        # Hex validator for VID/PID
        hex_validator = QRegularExpressionValidator(QRegularExpression(r"^[0-9A-Fa-f]{0,4}$"))
        self._inp_vid = QLineEdit()
        self._inp_vid.setValidator(hex_validator)
        self._inp_vid.setPlaceholderText("e.g. 0403")

        self._inp_pid = QLineEdit()
        self._inp_pid.setValidator(hex_validator)
        self._inp_pid.setPlaceholderText("e.g. 6014")

        self._inp_max_pwr = QSpinBox()
        self._inp_max_pwr.setRange(0, 500)
        self._inp_max_pwr.setSingleStep(2)
        self._inp_max_pwr.setSuffix(" mA")

        self._pwr_bus = QRadioButton("Bus")
        self._pwr_self = QRadioButton("Self")
        self._pwr_bus.setChecked(True)
        pwr_hb = QHBoxLayout()
        pwr_hb.addWidget(self._pwr_bus)
        pwr_hb.addWidget(self._pwr_self)

        self._inp_wakeup = QCheckBox("Remote Wakeup")

        new_layout.addRow("New Serial:", self._inp_serial)
        new_layout.addRow("New Mfg:", self._inp_mfg)
        new_layout.addRow("New Product:", self._inp_desc)
        new_layout.addRow("VID (Hex):", self._inp_vid)
        new_layout.addRow("PID (Hex):", self._inp_pid)
        new_layout.addRow("Max Power:", self._inp_max_pwr)
        new_layout.addRow("Power Mode:", pwr_hb)
        new_layout.addRow("Options:", self._inp_wakeup)

        mid_layout.addWidget(new_group, 1)
        layout.addLayout(mid_layout)

        # 4. Action Buttons (Lower Center)
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 4, 0, 4)
        btn_layout.addStretch()

        self._read_btn = QPushButton("Read EEPROM")
        self._read_btn.setMinimumSize(130, 36)
        btn_font = self._read_btn.font()
        btn_font.setWeight(QFont.Weight.Bold)
        btn_font.setPointSize(10)
        self._read_btn.setFont(btn_font)
        self._read_btn.clicked.connect(self._on_read_clicked)
        btn_layout.addWidget(self._read_btn)

        self._write_btn = QPushButton("Write EEPROM")
        self._write_btn.setMinimumSize(130, 36)
        self._write_btn.setFont(btn_font)
        self._write_btn.clicked.connect(self._on_write_clicked)
        btn_layout.addWidget(self._write_btn)

        self._reset_btn = QPushButton("Reset Device")
        self._reset_btn.setMinimumSize(130, 36)
        reset_font = self._reset_btn.font()
        reset_font.setPointSize(10) # regular weight
        self._reset_btn.setFont(reset_font)
        self._reset_btn.clicked.connect(self._on_reset_clicked)
        btn_layout.addWidget(self._reset_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        layout.addStretch()

    # -- Slots --

    @Slot()
    def _on_refresh_clicked(self) -> None:
        self._append_log("[PROG] Refresh UI status.")
        if self._ftdi.is_connected:
            self._device_combo.setItemText(0, f"Connected: {self._ftdi.channel}")
        else:
            self._device_combo.setItemText(0, "Disconnected")

    @Slot()
    def _on_read_clicked(self) -> None:
        if self._busy:
            return
        if not self._ftdi.is_connected:
            self._append_log("[PROG] Cannot read: Device not connected.")
            return

        self._append_log("[PROG] Start Read EEPROM request...")
        self._busy = True
        self._toggle_buttons(False)
        self._trigger_read.emit()

    @Slot()
    def _on_write_clicked(self) -> None:
        if self._busy:
            return
        if not self._ftdi.is_connected:
            self._append_log("[PROG] Cannot write: Device not connected.")
            return
        if not self._eeprom_read_done:
            self._append_log("[PROG] Cannot write: Read EEPROM first to load current config.")
            QMessageBox.warning(
                self, "Read Required",
                "Please read the current EEPROM config first\n"
                "before writing to prevent unintended overwrites.",
            )
            return

        # Explicit warning
        title = "EEPROM Overwrite Warning"
        msg = (
            "You are about to overwrite the EEPROM.\n"
            "Invalid VID/PID inputs may cause the device to become unrecognizable.\n\n"
            "Are you sure you want to proceed?"
        )
        reply = QMessageBox.warning(
            self, title, msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            self._append_log("[PROG] User cancelled EEPROM write.")
            return

        params = {
            "serial": self._inp_serial.text().strip(),
            "manufacturer": self._inp_mfg.text().strip(),
            "description": self._inp_desc.text().strip(),
            "vid": self._inp_vid.text().strip(),
            "pid": self._inp_pid.text().strip(),
            "max_power": self._inp_max_pwr.value(),
            "self_powered": self._pwr_self.isChecked(),
            "remote_wakeup": self._inp_wakeup.isChecked(),
        }

        # Set params before emitting signal — safe because the signal
        # is queued (cross-thread), so the worker reads params only
        # after the main thread returns to the event loop.
        self._worker._pending_params = params
        self._busy = True
        self._toggle_buttons(False)
        self._trigger_write.emit()

    @Slot()
    def _on_reset_clicked(self) -> None:
        if self._busy:
            return
        if not self._ftdi.is_connected:
            self._append_log("[PROG] Cannot reset: Device not connected.")
            return

        self._append_log("[PROG] Start Reset Device request...")
        self._busy = True
        self._toggle_buttons(False)
        self._trigger_reset.emit()

    def _set_ro_text(self, widget: QLineEdit, text: str) -> None:
        """Set text and tooltip together so truncated values are hoverable."""
        widget.setText(text)
        widget.setToolTip(text)

    @Slot(dict)
    def _on_eeprom_data_read(self, data: dict) -> None:
        self._set_ro_text(self._ro_serial, data.get("serial", "-"))
        self._set_ro_text(self._ro_mfg, data.get("manufacturer", "-"))
        self._set_ro_text(self._ro_desc, data.get("description", "-"))
        self._set_ro_text(self._ro_vid, data.get("vid", "-"))
        self._set_ro_text(self._ro_pid, data.get("pid", "-"))
        self._set_ro_text(self._ro_max_pwr, f"{data.get('max_power', 0)}")
        self._set_ro_text(self._ro_pwr_mode, "Self" if data.get("self_powered") else "Bus")
        self._set_ro_text(self._ro_wakeup, "Supported" if data.get("remote_wakeup") else "Not Supported")

        # Auto-fill new inputs
        self._inp_serial.setText(data.get("serial", ""))
        self._inp_mfg.setText(data.get("manufacturer", ""))
        self._inp_desc.setText(data.get("description", ""))
        self._inp_vid.setText(data.get("vid", ""))
        self._inp_pid.setText(data.get("pid", ""))
        self._inp_max_pwr.setValue(data.get("max_power", 0))
        if data.get("self_powered"):
            self._pwr_self.setChecked(True)
        else:
            self._pwr_bus.setChecked(True)
        self._inp_wakeup.setChecked(data.get("remote_wakeup", False))
        self._eeprom_read_done = True

    @Slot(bool, str)
    def _on_operation_finished(self, success: bool, msg: str) -> None:
        self._busy = False
        self._toggle_buttons(True)

    @Slot()
    def _on_request_disconnect(self) -> None:
        """Handle invalidated device after reset/cyclePort."""
        self._eeprom_read_done = False
        self._append_log("[PROG] Device handle invalidated after reset. Disconnecting...")
        try:
            self._ftdi.close_device()
        except Exception:
            pass

    def _toggle_buttons(self, enabled: bool) -> None:
        self._read_btn.setEnabled(enabled)
        self._write_btn.setEnabled(enabled)
        self._reset_btn.setEnabled(enabled)
