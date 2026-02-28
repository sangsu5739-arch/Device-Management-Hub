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

from functools import partial

from PySide6.QtCore import Qt, Slot, QSettings, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QTabWidget, QMessageBox,
    QFrame, QButtonGroup,
)

from core.ftdi_manager import FtdiManager
from modules.base_module import BaseModule

logger = logging.getLogger(__name__)


def discover_module_classes() -> List[Type[BaseModule]]:
    """Discover BaseModule subclasses dynamically under /modules."""
    modules_dir = Path(__file__).parent / "modules"
    classes: List[Type[BaseModule]] = []

    for _, name, ispkg in pkgutil.iter_modules([str(modules_dir)]):
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
        self._active_channel_ui: str = "A"
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

        self._set_status("Ready - scanning FTDI devices...")

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

    def _make_separator(self) -> QFrame:
        """Vertical toolbar separator."""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        sep.setStyleSheet("background: #3a3f50; border: none;")
        return sep

    def _create_connection_panel(self) -> QWidget:
        """Toolbar-style FTDI connection panel (single row, grouped by separators)."""
        bar = QWidget()
        bar.setFixedHeight(46)
        bar.setStyleSheet(
            "QWidget { background: #1e2130; border-bottom: 1px solid #2e3348; }"
            "QLabel { background: transparent; border: none; }"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(0)

        # ── Group 1: Device ──────────────────────────────────────
        scan_btn = QPushButton("\u27f3")   # ⟳
        scan_btn.setToolTip("Scan FTDI devices")
        scan_btn.setFixedSize(28, 28)
        scan_btn.setStyleSheet(
            "QPushButton { background: #252838; color: #8898b8; border: 1px solid #3a3f50;"
            " border-radius: 5px; font-size: 14px; }"
            "QPushButton:hover { background: #2e334a; color: #b0c0e0; }"
            "QPushButton:pressed { background: #1e2130; }"
            "QPushButton:disabled { color: #404560; border-color: #2a2e3a; }"
        )
        scan_btn.clicked.connect(self._on_scan_devices)
        self._scan_btn = scan_btn
        layout.addWidget(scan_btn)
        layout.addSpacing(6)

        self._device_combo = QComboBox()
        self._device_combo.setFixedHeight(28)
        self._device_combo.setMinimumWidth(230)
        self._device_combo.setPlaceholderText("No devices — press \u27f3 to scan")
        self._device_combo.setStyleSheet(
            "QComboBox { background: #252838; color: #c0cce0; border: 1px solid #3a3f50;"
            " border-radius: 5px; padding: 0 8px; }"
            "QComboBox:hover { border-color: #5a6080; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
            "QComboBox QAbstractItemView { background: #1e2130; color: #c0cce0;"
            " selection-background-color: #2e3a54; border: 1px solid #3a3f50; }"
        )
        self._device_combo.currentIndexChanged.connect(self._on_device_selected)
        layout.addWidget(self._device_combo)

        layout.addSpacing(10)
        layout.addWidget(self._make_separator())
        layout.addSpacing(10)

        # ── Group 2: Channel buttons ──────────────────────────────
        ch_lbl = QLabel("CH")
        ch_lbl.setStyleSheet("color: #606880; font-size: 10px; font-weight: 600;"
                             " letter-spacing: 1px;")
        layout.addWidget(ch_lbl)
        layout.addSpacing(6)

        self._channel_btn_group = QButtonGroup(self)
        self._channel_btn_group.setExclusive(True)
        self._channel_buttons: dict[str, QPushButton] = {}
        self._channel_btn_container = QWidget()
        self._channel_btn_container.setStyleSheet("background: transparent; border: none;")
        ch_row = QHBoxLayout(self._channel_btn_container)
        ch_row.setContentsMargins(0, 0, 0, 0)
        ch_row.setSpacing(3)

        for ch in ["A", "B", "C", "D"]:
            btn = QPushButton(ch)
            btn.setCheckable(True)
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(self._ch_btn_style(active=False))
            btn.clicked.connect(partial(self._on_channel_btn_clicked, ch))
            self._channel_buttons[ch] = btn
            self._channel_btn_group.addButton(btn)
            ch_row.addWidget(btn)

        layout.addWidget(self._channel_btn_container)

        layout.addSpacing(10)
        layout.addWidget(self._make_separator())
        layout.addSpacing(10)

        # ── Group 3: Status + info ────────────────────────────────
        self._status_led = QLabel("●")
        self._status_led.setStyleSheet("color: #cc3333; font-size: 13px; background: transparent;")
        self._status_led.setFixedWidth(18)
        layout.addWidget(self._status_led)
        layout.addSpacing(4)

        self._status_text = QLabel("Disconnected")
        self._status_text.setStyleSheet("color: #cc3333; font-weight: 700; font-size: 11px;"
                                        " background: transparent;")
        layout.addWidget(self._status_text)

        layout.addSpacing(8)

        self._conn_info_label = QLabel("")
        self._conn_info_label.setStyleSheet("color: #505870; font-size: 10px;"
                                            " background: transparent;")
        layout.addWidget(self._conn_info_label)

        layout.addStretch()

        # ── Group 4: Connect button ───────────────────────────────
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setObjectName("connectToggleBtn")
        self._connect_btn.setCheckable(True)
        self._connect_btn.setFixedSize(110, 32)
        self._connect_btn.toggled.connect(self._on_connect_toggle)
        self._apply_connect_btn_style(connected=False)
        layout.addWidget(self._connect_btn)

        # Compatibility: keep _active_channel_badge as hidden attribute
        self._active_channel_badge = QLabel()
        self._active_channel_badge.hide()

        # Populate channel buttons for initial state (show only A visible)
        self._sync_channel_buttons(["A"], "A")

        return bar

    @staticmethod
    def _ch_btn_style(active: bool, enabled: bool = True) -> str:
        if not enabled:
            return (
                "QPushButton { background: #1e2130; color: #383d50; border: 1px solid #2a2e3a;"
                " border-radius: 5px; font-size: 11px; font-weight: 600; }"
            )
        if active:
            return (
                "QPushButton { background: #1d3a4a; color: #70c8e8; border: 1px solid #2a6880;"
                " border-radius: 5px; font-size: 11px; font-weight: 700; }"
                "QPushButton:hover { background: #1e4055; }"
            )
        return (
            "QPushButton { background: #252838; color: #7888a8; border: 1px solid #3a3f50;"
            " border-radius: 5px; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background: #2e334a; color: #a0b0c8; border-color: #505870; }"
            "QPushButton:checked { background: #1d3a4a; color: #70c8e8; border-color: #2a6880; }"
        )

    def _sync_channel_buttons(self, channels: list[str], selected: str) -> None:
        """Show/enable only relevant channel buttons; highlight selected."""
        all_ch = ["A", "B", "C", "D"]
        for ch in all_ch:
            btn = self._channel_buttons[ch]
            if ch in channels:
                btn.show()
                btn.setEnabled(True)
                is_active = (ch == selected)
                btn.setChecked(is_active)
                btn.setStyleSheet(self._ch_btn_style(active=is_active))
            else:
                btn.hide()
                btn.setChecked(False)


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
        devices = FtdiManager.scan_devices_with_channels()

        # Block signals while rebuilding combo to avoid spurious _on_device_selected calls
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        if not devices:
            self._device_combo.setPlaceholderText("No devices found")
            self._device_combo.blockSignals(False)
            self._set_status("No FTDI devices found", "warn")
            self._show_scan_dialog(0)
            return

        for serial, desc, channels, device_type in devices:
            self._device_combo.addItem(
                f"{serial}  ({desc})", {"serial": serial, "channels": channels, "device_type": device_type}
            )
        self._device_combo.setCurrentIndex(0)
        self._device_combo.blockSignals(False)

        # Manually trigger device selection after combo is fully populated
        self._on_device_selected(0)
        self._set_status(f"Found {len(devices)} FTDI device(s)", "ok")
        self._show_scan_dialog(len(devices))

    def _selected_channel(self) -> str:
        """Return the currently selected channel from the button group."""
        for ch, btn in self._channel_buttons.items():
            if btn.isChecked() and btn.isVisible():
                return ch
        return "A"

    @Slot()
    def _on_connect(self) -> None:
        """Connect FTDI device."""
        if self._device_combo.currentIndex() < 0:
            self._show_warning_dialog("Device not selected", "Scan and select an FTDI device first.")
            self._set_status("Select a device to connect", "warn")
            return

        data = self._device_combo.currentData()
        serial = data["serial"] if isinstance(data, dict) else data
        channels = list((data.get("channels") or []) if isinstance(data, dict) else [])
        channel = self._selected_channel() if channels else "A"

        if channels and channel not in channels:
            self._show_warning_dialog(
                "Channel not selected",
                "This device supports multiple channels.\n"
                f"Select a channel to use. (Available: {', '.join(channels)})",
            )
            self._set_status("Select a channel", "warn")
            return

        success = self._ftdi.open_device(serial, channel)
        if success:
            self._set_status(f"Connected: {serial}  CH-{channel}", "ok")
            self._active_channel_ui = channel
        else:
            self._connect_btn.blockSignals(True)
            self._connect_btn.setChecked(False)
            self._connect_btn.setText("Connect")
            self._connect_btn.blockSignals(False)
            self._apply_connect_btn_style(connected=False)

    @Slot(int)
    def _on_device_selected(self, index: int) -> None:
        data = self._device_combo.itemData(index)
        channels: List[str] = list(data.get("channels") or []) if isinstance(data, dict) else []
        if not channels:
            channels = ["A"]

        # Keep previously selected channel if still available
        current = self._active_channel_ui or "A"
        selected = current if current in channels else channels[0]
        self._active_channel_ui = selected
        self._sync_channel_buttons(channels, selected)

        # Notify modules (not yet connected, just UI preview)
        for module in self._modules:
            try:
                module.on_channel_changed(selected)
            except Exception as e:
                logger.error(f"Module on_channel_changed error: {e}")

    def _on_channel_btn_clicked(self, new_channel: str) -> None:
        """Handle channel button press (pre-connect selection or live switch)."""
        if not self._ftdi.is_connected:
            self._active_channel_ui = new_channel
            self._sync_channel_buttons(
                [ch for ch, btn in self._channel_buttons.items() if btn.isVisible()],
                new_channel,
            )
            for module in self._modules:
                try:
                    module.on_channel_changed(new_channel)
                except Exception as e:
                    logger.error(f"Module on_channel_changed error: {e}")
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

        msg = QMessageBox(self)
        msg.setWindowTitle("Confirm Channel Switch")
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText(f"Switch control channel to {new_channel}?")
        msg.setInformativeText("The active channel will be changed.")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        msg.setStyleSheet(self._MSGBOX_STYLESHEET)
        if msg.exec() != QMessageBox.StandardButton.Yes:
            # Restore button highlight to current channel
            visible = [ch for ch, btn in self._channel_buttons.items() if btn.isVisible()]
            self._sync_channel_buttons(visible, current)
            return

        if self._ftdi.set_active_channel(new_channel):
            self._active_channel_ui = new_channel
            visible = [ch for ch, btn in self._channel_buttons.items() if btn.isVisible()]
            self._sync_channel_buttons(visible, new_channel)
            for module in self._modules:
                try:
                    module.on_channel_changed(new_channel)
                except Exception:
                    pass
            self._log_channel_switch(current, new_channel)
        else:
            self._set_status("Failed to change channel", "error")

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

    def _on_connect_toggle(self, checked: bool) -> None:
        if checked:
            self._on_connect()
        else:
            self._on_disconnect()

    @Slot(str)
    def _on_hw_connected(self, info: str) -> None:
        """Update UI on successful connection."""
        self._status_led.setStyleSheet("color: #33cc33; font-size: 13px; background: transparent;")
        self._status_text.setText("Connected")
        self._status_text.setStyleSheet("color: #33cc33; font-weight: 700; font-size: 11px;"
                                        " background: transparent;")
        self._conn_info_label.setText(info)
        self._conn_info_label.setStyleSheet("color: #607870; font-size: 10px; background: transparent;")
        self._connect_btn.blockSignals(True)
        self._connect_btn.setChecked(True)
        self._connect_btn.setText("Disconnect")
        self._connect_btn.blockSignals(False)
        self._apply_connect_btn_style(connected=True)
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
        self._status_led.setStyleSheet("color: #cc3333; font-size: 13px; background: transparent;")
        self._status_text.setText("Disconnected")
        self._status_text.setStyleSheet("color: #cc3333; font-weight: 700; font-size: 11px;"
                                        " background: transparent;")
        self._conn_info_label.setText("")
        self._conn_info_label.setStyleSheet("color: #505870; font-size: 10px; background: transparent;")
        self._connect_btn.blockSignals(True)
        self._connect_btn.setChecked(False)
        self._connect_btn.setText("Connect")
        self._connect_btn.blockSignals(False)
        self._apply_connect_btn_style(connected=False)
        self._device_combo.setEnabled(True)
        self._scan_btn.setEnabled(True)
        if self._device_combo.count() > 0:
            self._device_combo.setCurrentIndex(0)

        self._set_status("Disconnected", "warn")
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
        self._status_led.setStyleSheet("color: #cccc33; font-size: 13px; background: transparent;")
        self._status_text.setText("Error")
        self._status_text.setStyleSheet("color: #cccc33; font-weight: 700; font-size: 11px;"
                                        " background: transparent;")
        if self._device_combo.count() > 0:
            self._device_combo.setCurrentIndex(0)

        self._set_status(f"Error: {error_msg}", "error")

    def _apply_connect_btn_style(self, connected: bool) -> None:
        if not hasattr(self, "_connect_btn"):
            return
        if connected:
            self._connect_btn.setStyleSheet(
                "QPushButton { background: #5a1e20; color: #f0a0a0; font-weight: 700; "
                "font-size: 12px; border: 1px solid #a03030; border-radius: 6px; padding: 4px 10px; }"
                "QPushButton:hover { background: #6e2225; color: #ffc0c0; border-color: #c04040; }"
                "QPushButton:pressed { background: #3a1215; }"
            )
        else:
            self._connect_btn.setStyleSheet(
                "QPushButton { background: #0e4a5a; color: #a0e8f8; font-weight: 700; "
                "font-size: 12px; border: 1px solid #1a7090; border-radius: 6px; padding: 4px 10px; }"
                "QPushButton:hover { background: #145870; color: #c0f0ff; border-color: #2090b0; }"
                "QPushButton:pressed { background: #0a3040; }"
                "QPushButton:disabled { background: #1a2030; color: #404860; border: 1px solid #252a38; }"
            )

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
        self._active_channel_ui = channel
        visible = [ch for ch, btn in self._channel_buttons.items() if btn.isVisible()]
        self._sync_channel_buttons(visible, channel)

    def _set_status(self, message: str, level: str = "info") -> None:
        """Show a color-coded status bar message.

        level: "info" | "ok" | "warn" | "error"
        """
        colors = {
            "info":  "#7888a0",   # default gray
            "ok":    "#80c890",   # muted green
            "warn":  "#d4a84b",   # amber
            "error": "#e07070",   # muted red
        }
        color = colors.get(level, colors["info"])
        self.statusBar().setStyleSheet(
            f"QStatusBar {{ color: {color}; background-color: #1a1c24; "
            f"border-top: 1px solid #3a3f50; font-size: 11px; font-weight: {'700' if level in ('warn', 'error') else '400'}; }}"
        )
        self.statusBar().showMessage(message)

    def _log_channel_switch(self, prev: str, new: str) -> None:
        logger.info(f"Active channel switch: {prev} -> {new}")
        if self._device_combo.count() > 0:
            self._device_combo.setCurrentIndex(0)

        self._set_status(f"Channel switched: {prev} -> {new}", "ok")

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
