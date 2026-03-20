"""
Unified Clock Generator Group Module — Universal Device Studio plugin
Uses macOS-style sidebar navigation like ADC Group.
"""
from typing import Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QStackedWidget
)

from core.ftdi_manager import FtdiManager
from core.theme_manager import ThemeManager
from modules.base_module import BaseModule

# Sub-modules
from modules.pi6cg18201.pi6cg_module import PI6CGModule

class ClockGroupModule(BaseModule):
    MODULE_NAME = "Clock Gen"
    MODULE_ICON = "🎛️"
    MODULE_VERSION = "1.0.0"
    MODULE_ORDER = 40  # Put after ADC
    REQUIRED_MODE = None

    def __init__(self, ftdi_manager: FtdiManager, parent: Optional[QWidget] = None) -> None:
        self._sub_modules = []
        super().__init__(ftdi_manager, parent)

    def init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. Sidebar (QListWidget)
        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(140)
        self._sidebar.setObjectName("clockSidebar")
        self._sidebar.currentRowChanged.connect(self._on_sidebar_row_changed)
        layout.addWidget(self._sidebar)

        # 2. Main content area (QStackedWidget)
        self._stacked = QStackedWidget()
        layout.addWidget(self._stacked, 1)

        # 3. Instantiate sub-modules
        self._m_pi6cg = PI6CGModule(self._ftdi, self)

        self._sub_modules = [
            ("PI6CG18201", self._m_pi6cg, "I2C Clock Generator")
        ]

        for i, (name, mod, desc) in enumerate(self._sub_modules):
            item = QListWidgetItem(name)
            item.setToolTip(desc)
            item.setSizeHint(QSize(140, 40))
            self._sidebar.addItem(item)
            self._stacked.addWidget(mod)
            
            # Forward signals to our own
            mod.status_message.connect(self.status_message.emit)
            mod.log_message.connect(self.log_message.emit)

        # Initial selection
        self._sidebar.setCurrentRow(0)

        # Apply theme
        self._apply_theme()
        ThemeManager.instance().theme_changed.connect(self._apply_theme)

    def _apply_theme(self) -> None:
        tm = ThemeManager.instance()
        bg = tm.color('bg_panel')
        text = tm.color('text_primary')
        hover = tm.color('bg_hover')
        selected_bg = tm.color('btn_auto_checked_bg')
        selected_text = tm.color('btn_auto_checked_text')
        border = tm.color('border_subtle')

        self._sidebar.setStyleSheet(f"""
            QListWidget#clockSidebar {{
                background-color: {bg};
                color: {text};
                border: none;
                border-right: 1px solid {border};
                outline: 0;
                padding-top: 10px;
            }}
            QListWidget#clockSidebar::item {{
                padding-left: 20px;
                border: none;
                margin: 2px 10px;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 600;
            }}
            QListWidget#clockSidebar::item:hover {{
                background-color: {hover};
            }}
            QListWidget#clockSidebar::item:selected {{
                background-color: {selected_bg};
                color: {selected_text};
            }}
        """)

    @property
    def current_module(self) -> BaseModule:
        return self._sub_modules[self._sidebar.currentRow()][1]

    def _on_sidebar_row_changed(self, row: int) -> None:
        if row < 0:
            return
        
        old_mod = self._stacked.currentWidget()
        if isinstance(old_mod, BaseModule):
            old_mod.on_tab_deactivated()
        
        self._stacked.setCurrentIndex(row)
        new_mod = self._stacked.currentWidget()
        if isinstance(new_mod, BaseModule):
            new_mod.on_tab_activated()

    # ── BaseModule Overrides ─────────────────────────────────────────

    def on_device_connected(self) -> None:
        for _, mod, _ in self._sub_modules:
            mod.on_device_connected()

    def on_device_disconnected(self) -> None:
        for _, mod, _ in self._sub_modules:
            mod.on_device_disconnected()

    def on_tab_activated(self) -> None:
        super().on_tab_activated()
        self.current_module.on_tab_activated()

    def on_tab_deactivated(self) -> None:
        super().on_tab_deactivated()
        self.current_module.on_tab_deactivated()

    def on_channel_changed(self, channel: str) -> None:
        for _, mod, _ in self._sub_modules:
            mod.on_channel_changed(channel)

    def start_communication(self) -> None:
        self.current_module.start_communication()

    def stop_communication(self) -> None:
        self.current_module.stop_communication()

    def update_data(self) -> None:
        self.current_module.update_data()
