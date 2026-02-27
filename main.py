"""
Universal Device Studio (UDS)

FT232H (MPSSE mode) plugin-based device control platform.
Loads devices dynamically from /modules and manages them as QTabWidget tabs.
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import signal
import sys
import traceback
from pathlib import Path
from typing import List, Optional, Type

from PySide6.QtCore import Qt, Slot, QSettings, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QPushButton, QComboBox, QTabWidget, QMessageBox,
    QSplitter, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGraphicsOpacityEffect,
)

from core.ftdi_manager import FtdiManager
from modules.base_module import BaseModule

logger = logging.getLogger(__name__)


def discover_module_classes() -> List[Type[BaseModule]]:
    """Discover BaseModule subclasses dynamically under /modules."""
    modules_dir = Path(__file__).parent / "modules"
    classes: List[Type[BaseModule]] = []

    for finder, name, ispkg in pkgutil.iter_modules([str(modules_dir)]):
        if not ispkg:
            continue
        try:
            mod = importlib.import_module(f"modules.{name}")
            if hasattr(mod, "MODULE_CLASS"):
                cls = mod.MODULE_CLASS
                if isinstance(cls, type) and issubclass(cls, BaseModule) and cls is not BaseModule:
                    classes.append(cls)
                    logger.info(f"Module found: {cls.MODULE_NAME} (modules.{name})")
        except Exception as e:
            logger.warning(f"Failed to load module '{name}': {e}")
            traceback.print_exc()

    return classes


class MainWindow(QMainWindow):
    """UDS main window.

    Top: FTDI connection panel
    Center: Device module tabs (QTabWidget)
    """

    _MSGBOX_STYLESHEET = """
        QMessageBox {
            background-color: #f7f9fc;
        }
        QMessageBox QLabel {
            color: #111111;
            font-size: 13px;
        }
        QMessageBox QPushButton {
            min-width: 92px;
            min-height: 30px;
            border-radius: 6px;
            border: 1px solid #6a8cc7;
            background-color: #e9f1ff;
            color: #111111;
            font-weight: 600;
            padding: 4px 10px;
        }
        QMessageBox QPushButton:hover {
            background-color: #dbe9ff;
        }
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Universal Device Studio")
        self.setMinimumSize(1360, 900)
        self.resize(1520, 1080)

        self._ftdi = FtdiManager.instance()
        self._modules: List[BaseModule] = []
        self._active_tab_index: int = -1
        self._settings = QSettings("UniversalDeviceStudio", "MainWindow")
        self._last_connected_serial: str = ""  # Disconnection message box

        self._init_ui()
        self._connect_signals()
        self._load_modules()

        # Debug: print loaded tab info
        tab_count = self._tab_widget.count()
        tab_names = [self._tab_widget.tabText(i) for i in range(tab_count)]
        print(f"[UDS] Loaded tabs: {tab_count} - {tab_names}")

        if self._device_combo.count() > 0:
            self._device_combo.setCurrentIndex(0)

        self.statusBar().showMessage("Ready - scanning FTDI devices...")

        # Auto-scan on startup
        QTimer.singleShot(300, self._on_scan_devices)

    def _init_ui(self) -> None:
        """Build the main UI layout."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # Top: FTDI connection panel
        top_panel = self._create_connection_panel()
        main_layout.addWidget(top_panel)

        # Module tabs
        self._tab_widget = QTabWidget()
        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self._tab_widget, 1)

    def _create_connection_panel(self) -> QGroupBox:
        """Top FTDI connection panel."""
        group = QGroupBox("FTDI Connection")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(12, 8, 12, 8)

        # Status LED
        self._status_led = QLabel("●")
        self._status_led.setObjectName("statusLed")
        self._status_led.setStyleSheet("color: #cc3333; font-size: 16px;")
        self._status_led.setFixedWidth(24)
        layout.addWidget(self._status_led)

        # Device selection
        layout.addWidget(QLabel("Device:"))
        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(260)
        self._device_combo.setPlaceholderText("Scan FTDI devices")
        self._device_combo.currentIndexChanged.connect(self._on_device_selected)
        layout.addWidget(self._device_combo)

        # Scan button
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.setFixedWidth(80)
        self._scan_btn.clicked.connect(self._on_scan_devices)
        layout.addWidget(self._scan_btn)

        # Channel selection
        layout.addWidget(QLabel("Channel:"))
        self._channel_combo = QComboBox()
        self._channel_combo.setMinimumWidth(80)
        self._channel_combo.setPlaceholderText("-")
        self._channel_combo.currentIndexChanged.connect(self._on_channel_combo_changed)
        layout.addWidget(self._channel_combo)

        self._active_channel_badge = QLabel("ACTIVE: -")
        self._active_channel_badge.setStyleSheet(
            "color: #e8f0ff; background: #2a3142; border-radius: 6px; padding: 4px 8px;"
            "font-weight: 700;"
        )
        layout.addWidget(self._active_channel_badge)

        layout.addSpacing(10)

        # Connect/disconnect buttons
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setObjectName("connectBtn")
        self._connect_btn.setFixedWidth(110)
        self._connect_btn.clicked.connect(self._on_connect)
        layout.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setObjectName("disconnectBtn")
        self._disconnect_btn.setFixedWidth(110)
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        layout.addWidget(self._disconnect_btn)

        layout.addStretch()

        # Connection info
        self._conn_info_label = QLabel("Not connected")
        self._conn_info_label.setStyleSheet("color: #6a7088; font-style: italic;")
        layout.addWidget(self._conn_info_label)

        return group


    def _connect_signals(self) -> None:
        self._ftdi.device_connected.connect(self._on_hw_connected)
        self._ftdi.device_disconnected.connect(self._on_hw_disconnected)
        self._ftdi.comm_error.connect(self._on_hw_error)
        self._ftdi.device_info_changed.connect(self._on_device_info_changed)

    def _load_modules(self) -> None:
        """Discover device modules and add as tabs."""
        module_classes = discover_module_classes()

        if not module_classes:
            placeholder = QLabel("No modules loaded.\nAdd device modules under /modules.")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #6a7088; font-size: 14px;")
            self._tab_widget.addTab(placeholder, "No Modules")
            return

        for cls in module_classes:
            try:
                module_instance = cls(self._ftdi)
                self._modules.append(module_instance)

                tab_label = f"{cls.MODULE_ICON} {cls.MODULE_NAME}" if cls.MODULE_ICON else cls.MODULE_NAME
                self._tab_widget.addTab(module_instance, tab_label)
                logger.info(f"Module tab added: {cls.MODULE_NAME}")
            except Exception as e:
                logger.error(f"Failed to create module instance '{cls.MODULE_NAME}': {e}")
                traceback.print_exc()

    # -- Message boxes --

    def _show_scan_dialog(self, device_count: int) -> None:
        """FTDI scan result message box."""
        box = QMessageBox(self)
        box.setWindowTitle("FTDI Scan")
        if device_count > 0:
            box.setIcon(QMessageBox.Icon.Information)
            box.setText("Scan complete")
            box.setInformativeText(f"Found {device_count} FTDI device(s).")
        else:
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText("No devices")
            box.setInformativeText("No connectable devices found.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setStyleSheet(self._MSGBOX_STYLESHEET)
        box.exec()

    def _show_connection_dialog(self, info: str) -> None:
        """FTDI connection success message box."""
        box = QMessageBox(self)
        box.setWindowTitle("FTDI Connected")
        box.setIcon(QMessageBox.Icon.Information)
        serial = self._ftdi.serial_number or "-"
        box.setText(f"SN {serial} connected")
        box.setInformativeText("FTDI device connection is complete.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setStyleSheet(self._MSGBOX_STYLESHEET)
        box.exec()

    def _show_warning_dialog(self, title: str, message: str) -> None:
        """Warning message box."""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(title)
        box.setInformativeText(message)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setStyleSheet(self._MSGBOX_STYLESHEET)
        box.exec()

    def _show_disconnection_dialog(self, serial: str) -> None:
        """FTDI disconnection message box."""
        box = QMessageBox(self)
        box.setWindowTitle("FTDI Disconnected")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"SN {serial} disconnected")
        box.setInformativeText("FTDI device has been disconnected.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setStyleSheet(self._MSGBOX_STYLESHEET)
        box.exec()

    # -- FTDI connection handlers --

    @Slot()
    def _on_scan_devices(self) -> None:
        """Scan FTDI devices."""
        self._device_combo.clear()

        devices = FtdiManager.scan_devices_with_channels()
        if not devices:
            self._device_combo.setPlaceholderText("No devices found")
            self.statusBar().showMessage("No FTDI devices found")
            self._show_scan_dialog(0)
            return

        for serial, desc, channels, device_type in devices:
            self._device_combo.addItem(
                f"{serial}  ({desc})", {"serial": serial, "channels": channels, "device_type": device_type}
            )

        if self._device_combo.count() > 0:
            self._device_combo.setCurrentIndex(0)

        self.statusBar().showMessage(f"Found {len(devices)} FTDI device(s)")
        self._show_scan_dialog(len(devices))
        self._on_device_selected(self._device_combo.currentIndex())

    @Slot()
    def _on_connect(self) -> None:
        """Connect FTDI device."""
        if self._device_combo.currentIndex() < 0:
            self._show_warning_dialog("Device not selected", "Scan and select an FTDI device first.")
            self.statusBar().showMessage("Select a device to connect")
            return

        data = self._device_combo.currentData()
        serial = data["serial"] if isinstance(data, dict) else data

        # Single-channel devices pass an empty channel parameter
        channels = list((data.get("channels") or []) if isinstance(data, dict) else [])

        if channels:
            channel = self._channel_combo.currentText() or "A"
            if self._channel_combo.currentIndex() < 0 or channel not in channels:
                self._show_warning_dialog(
                    "Channel not selected",
                    "This device supports multiple channels.\n"
                    f"Select a channel to use. (Available: {', '.join(channels)})",
                )
                self.statusBar().showMessage("Select a channel")
                return
        else:
            channel = "A"

        success = self._ftdi.open_device(serial, channel)
        if success:
            ch_label = f" CH-{channel}" if channel else ""
            self.statusBar().showMessage(f"Connected: {serial}{ch_label}")
            self._active_channel_ui = channel
            if hasattr(self, "_active_channel_badge"):
                self._active_channel_badge.setText(f"ACTIVE: {channel}")

    @Slot(int)
    def _on_device_selected(self, index: int) -> None:
        data = self._device_combo.itemData(index)
        channels: List[str] = []
        if isinstance(data, dict):
            channels = list(data.get("channels") or [])
        current = self._channel_combo.currentText()
        self._channel_combo.blockSignals(True)
        self._channel_combo.clear()

        if not channels:
            channels = ["A"]
        # Multi-channel devices (FT2232, FT4232) or single-channel
        self._channel_combo.addItems(channels)
        if current in channels:
            self._channel_combo.setCurrentText(current)
        self._channel_combo.setEnabled(True)
        self._channel_combo.setToolTip("Select an FTDI channel to use")

        self._channel_combo.blockSignals(False)

    @Slot(int)
    def _on_channel_combo_changed(self, index: int) -> None:
        new_channel = self._channel_combo.currentText()
        if not new_channel:
            return
        if not self._ftdi.is_connected:
            self._active_channel_ui = new_channel
            for module in self._modules:
                try:
                    module.on_channel_changed(new_channel)
                except Exception as e:
                    logger.error(f"Module on_channel_changed error: {e}")
            if hasattr(self, "_active_channel_badge"):
                self._active_channel_badge.setText(f"ACTIVE: {new_channel}")
            return

        current = getattr(self, "_active_channel_ui", self._ftdi.channel)
        if new_channel == current:
            return

        # Stop running communications before switching
        for module in self._modules:
            try:
                module.stop_communication()
            except Exception:
                pass

        # Confirmation dialog
        msg = QMessageBox(self)
        msg.setWindowTitle("Confirm Channel Switch")
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText(f"Switch control channel to {new_channel}?")
        msg.setInformativeText("The active channel will be changed.")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        msg.setStyleSheet(self._MSGBOX_STYLESHEET)
        if msg.exec() != QMessageBox.StandardButton.Yes:
            # restore previous selection
            self._channel_combo.blockSignals(True)
            self._channel_combo.setCurrentText(current)
            self._channel_combo.blockSignals(False)
            # Optionally resume communication
            for module in self._modules:
                try:
                    module.start_communication()
                except Exception:
                    pass
            return

        if self._ftdi.set_active_channel(new_channel):
            self._active_channel_ui = new_channel
            self.statusBar().showMessage(f"Active channel changed: {new_channel}")
            # Restart communications after switching
            for module in self._modules:
                try:
                    module.start_communication()
                except Exception:
                    pass
            self._log_channel_switch(current, new_channel)
        else:
            self.statusBar().showMessage("Failed to change channel")

    @Slot()
    def _on_disconnect(self) -> None:
        """Disconnect FTDI device."""
        self._last_connected_serial = self._ftdi.serial_number or "-"
        for module in self._modules:
            try:
                module.stop_communication()
            except Exception:
                pass
        self._ftdi.close_device()

    @Slot(str)
    def _on_hw_connected(self, info: str) -> None:
        """Update UI on successful connection."""
        self._status_led.setStyleSheet("color: #33cc33; font-size: 16px;")
        self._conn_info_label.setText(info)
        self._conn_info_label.setStyleSheet("color: #88cc88; font-style: normal;")
        self._connect_btn.setEnabled(False)
        self._disconnect_btn.setEnabled(True)
        self._device_combo.setEnabled(False)
        self._scan_btn.setEnabled(False)

        # Notify all modules
        for module in self._modules:
            try:
                module.on_device_connected()
            except Exception as e:
                logger.error(f"Module on_device_connected error: {e}")

        # Sync active channel
        try:
            active_ch = self._ftdi.channel
            for module in self._modules:
                try:
                    module.on_channel_changed(active_ch)
                except Exception as e:
                    logger.error(f"Module on_channel_changed error: {e}")
        except Exception:
            pass

        QApplication.beep()
        self._show_connection_dialog(info)

    @Slot()
    def _on_hw_disconnected(self) -> None:
        """Update UI on disconnection."""
        self._status_led.setStyleSheet("color: #cc3333; font-size: 16px;")
        self._conn_info_label.setText("Not connected")
        self._conn_info_label.setStyleSheet("color: #6a7088; font-style: italic;")
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._device_combo.setEnabled(True)
        self._scan_btn.setEnabled(True)
        self._channel_combo.setEnabled(True)
        if self._device_combo.count() > 0:
            self._device_combo.setCurrentIndex(0)

        self.statusBar().showMessage("Disconnected")
        self._show_disconnection_dialog(self._last_connected_serial)

        # Notify all modules
        for module in self._modules:
            try:
                module.on_device_disconnected()
            except Exception as e:
                logger.error(f"Module on_device_disconnected error: {e}")

    @Slot(str)
    def _on_hw_error(self, error_msg: str) -> None:
        """Show hardware error."""
        self._status_led.setStyleSheet("color: #cccc33; font-size: 16px;")
        if self._device_combo.count() > 0:
            self._device_combo.setCurrentIndex(0)

        self.statusBar().showMessage(f"Error: {error_msg}")

    @Slot(object)
    def _on_device_info_changed(self, info: dict) -> None:
        channel = info.get("channel", "")
        if not channel:
            return
        for module in self._modules:
            try:
                module.on_channel_changed(channel)
            except Exception as e:
                logger.error(f"Module on_channel_changed error: {e}")
        if hasattr(self, "_active_channel_badge"):
            self._active_channel_badge.setText(f"ACTIVE: {channel}")

    def _log_channel_switch(self, prev: str, new: str) -> None:
        logger.info(f"Active channel switch: {prev} -> {new}")
        if self._device_combo.count() > 0:
            self._device_combo.setCurrentIndex(0)

        self.statusBar().showMessage(f"Channel switched: {prev} -> {new}")

    # -- Tab change handler --

    @Slot(int)
    def _on_tab_changed(self, index: int) -> None:
        """Activate/deactivate modules on tab change."""
        if 0 <= self._active_tab_index < len(self._modules):
            self._modules[self._active_tab_index].on_tab_deactivated()

        self._active_tab_index = index
        if 0 <= index < len(self._modules):
            self._modules[index].on_tab_activated()

    def closeEvent(self, event) -> None:
        """Confirm exit, stop all communications, and disconnect FTDI."""
        box = QMessageBox(self)
        box.setWindowTitle("Confirm Exit")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("Exit Universal Device Studio?")
        if self._ftdi.is_connected:
            box.setInformativeText(
                f"FTDI device (SN: {self._ftdi.serial_number}) is connected.\n"
                "Disconnect will happen automatically on exit."
            )
        else:
            box.setInformativeText("If you have in-progress work, save before exiting.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("Exit")
        box.button(QMessageBox.StandardButton.No).setText("Cancel")
        box.setStyleSheet(self._MSGBOX_STYLESHEET)

        if box.exec() != QMessageBox.StandardButton.Yes:
            event.ignore()
            return

        for module in self._modules:
            try:
                module.stop_communication()
            except Exception:
                pass
        if self._ftdi.is_connected:
            self._ftdi.close_device()
        super().closeEvent(event)


def main() -> None:
    """Application entry point."""
    # Add project root to sys.path (ensure imports work from any cwd)
    project_root = str(Path(__file__).parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = QApplication(sys.argv)

    # Clean exit on Ctrl+C
    signal.signal(signal.SIGINT, lambda *_: app.quit())

    # Load dark theme
    qss_path = Path(__file__).parent / "assets" / "dark_theme.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    # Default font
    default_font = QFont("Segoe UI", 10)
    default_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(default_font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
