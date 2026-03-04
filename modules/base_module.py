"""
Universal Device Studio - Device module base class.

All device modules inherit from this class and are added as QTabWidget tabs.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Optional

from PySide6.QtWidgets import QWidget, QMessageBox
from PySide6.QtCore import Signal

from core.ftdi_manager import FtdiManager
from core.theme_manager import ThemeManager


class BaseModule(QWidget):
    """Device module base class.

    Each module implements this class and is loaded as a QTabWidget tab.

    Class Attributes:
        MODULE_NAME: Tab display name
        MODULE_ICON: Icon (emoji or path)
        MODULE_VERSION: Module version
    """

    MODULE_NAME: str = "Unknown Module"
    MODULE_ICON: str = ""
    MODULE_VERSION: str = "1.0.0"
    MODULE_ORDER: int = 100  # Tab ordering (lower = earlier)
    REQUIRED_MODE: Optional[str] = None
    REQUIRE_MPSSE: bool = False

    @classmethod
    def _msgbox_stylesheet(cls) -> str:
        tm = ThemeManager.instance()
        return (
            f"QMessageBox {{ background-color: {tm.color('msgbox_bg')}; }}"
            f"QMessageBox QLabel {{ color: {tm.color('msgbox_text')}; font-size: 13px; }}"
            f"QMessageBox QPushButton {{ min-width: 92px; min-height: 30px;"
            f" border-radius: 6px; border: 1px solid {tm.color('msgbox_btn_border')};"
            f" background-color: {tm.color('msgbox_btn_bg')};"
            f" color: {tm.color('msgbox_btn_text')}; font-weight: 600; padding: 4px 10px; }}"
            f"QMessageBox QPushButton:hover {{ background-color: {tm.color('msgbox_btn_hover')}; }}"
        )

    # Signals
    status_message = Signal(str)
    log_message = Signal(str)

    # True while a module is switching FTDI between D2XX and VCP mode.
    # MainWindow checks this to skip signal propagation to other modules.
    is_uart_switching: bool = False

    def __init__(self, ftdi_manager: FtdiManager, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ftdi = ftdi_manager
        self._is_active: bool = False
        self.init_ui()

    @abstractmethod
    def init_ui(self) -> None:
        """Initialize module UI. Called once in __init__."""
        ...

    @abstractmethod
    def on_device_connected(self) -> None:
        """Called when FTDI connects. Update UI state."""
        ...

    @abstractmethod
    def on_device_disconnected(self) -> None:
        """Called when FTDI disconnects. Disable controls."""
        ...

    @abstractmethod
    def start_communication(self) -> None:
        """Start periodic/continuous I2C communication (worker thread)."""
        ...

    @abstractmethod
    def stop_communication(self) -> None:
        """Stop I2C communication."""
        ...

    @abstractmethod
    def update_data(self) -> None:
        """Update data once from hardware."""
        ...

    def on_channel_changed(self, channel: str) -> None:
        """Called when active FTDI channel changes."""
        return

    def on_tab_activated(self) -> None:
        """Called when this module tab becomes active."""
        self._is_active = True
        if not self._ftdi.is_connected:
            return

        required = (self.REQUIRED_MODE or "").upper().strip()
        if not required:
            return

        active_ch = self._ftdi.channel
        if self.REQUIRE_MPSSE and not self._ftdi.supports_mpsse(active_ch):
            self.status_message.emit(
                f"{self.MODULE_NAME}: channel {active_ch} does not support MPSSE."
            )
            self.on_channel_changed(active_ch)
            self._show_mpsse_warning(active_ch)  # subclasses may override to suppress
            return

        self._ftdi.set_protocol_mode(required)
        self.on_channel_changed(active_ch)

    def on_tab_deactivated(self) -> None:
        """Called when this module tab becomes inactive."""
        self._is_active = False

    def _show_mpsse_warning(self, channel: str) -> None:
        """Show MPSSE-not-supported warning dialog. Override to suppress (e.g., FTDI Verifier)."""
        box = QMessageBox(self)
        box.setWindowTitle("MPSSE Not Supported")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Channel {channel} does not support MPSSE.")
        box.setInformativeText(
            f"{self.MODULE_NAME} requires I2C/MPSSE communication.\n"
            "For FT4232H, only channels A and B support MPSSE.\n\n"
            "Switch to an MPSSE-capable channel (A or B) and try again."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setStyleSheet(self._msgbox_stylesheet())
        box.exec()
