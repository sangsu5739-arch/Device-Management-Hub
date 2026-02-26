"""
Universal Device Studio (UDS)

FT232H(MPSSE 모드) 기반 플러그인 방식 디바이스 통합 제어 플랫폼.
/modules/ 폴더 내의 디바이스를 동적으로 로드하여 QTabWidget 탭으로 관리합니다.
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

from PySide6.QtCore import Qt, Slot, QSettings
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
    """modules/ 디렉토리에서 BaseModule 서브클래스를 동적 탐색"""
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
                    logger.info(f"모듈 발견: {cls.MODULE_NAME} (modules.{name})")
        except Exception as e:
            logger.warning(f"모듈 '{name}' 로드 실패: {e}")
            traceback.print_exc()

    return classes


class MainWindow(QMainWindow):
    """UDS 메인 윈도우

    상단: FTDI 연결 패널
    중앙: 디바이스 모듈 탭 (QTabWidget)
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
        self._last_connected_serial: str = ""  # 해제 메시지박스용

        self._init_ui()
        self._connect_signals()
        self._load_modules()

        # 디버그: 로드된 탭 정보 출력
        tab_count = self._tab_widget.count()
        tab_names = [self._tab_widget.tabText(i) for i in range(tab_count)]
        print(f"[UDS] 로드된 탭 {tab_count}개: {tab_names}")

        self.statusBar().showMessage("준비됨 — FTDI 장치를 연결하세요")

    def _init_ui(self) -> None:
        """전체 UI 레이아웃 구성"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # 상단: FTDI 연결 패널
        top_panel = self._create_connection_panel()
        main_layout.addWidget(top_panel)

        # 모듈 탭
        self._tab_widget = QTabWidget()
        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self._tab_widget, 1)

    def _create_connection_panel(self) -> QGroupBox:
        """상단 FTDI 연결 패널"""
        group = QGroupBox("FTDI 연결")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(12, 8, 12, 8)

        # 상태 LED
        self._status_led = QLabel("●")
        self._status_led.setObjectName("statusLed")
        self._status_led.setStyleSheet("color: #cc3333; font-size: 16px;")
        self._status_led.setFixedWidth(24)
        layout.addWidget(self._status_led)

        # 디바이스 선택
        layout.addWidget(QLabel("장치:"))
        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(200)
        self._device_combo.setPlaceholderText("FTDI 장치를 스캔하세요")
        self._device_combo.currentIndexChanged.connect(self._on_device_selected)
        layout.addWidget(self._device_combo)

        # 스캔 버튼
        self._scan_btn = QPushButton("스캔")
        self._scan_btn.setFixedWidth(80)
        self._scan_btn.clicked.connect(self._on_scan_devices)
        layout.addWidget(self._scan_btn)

        # 채널 선택
        layout.addWidget(QLabel("채널:"))
        self._channel_combo = QComboBox()
        self._channel_combo.setMinimumWidth(80)
        self._channel_combo.setPlaceholderText("—")
        self._channel_combo.currentIndexChanged.connect(self._on_channel_combo_changed)
        layout.addWidget(self._channel_combo)

        self._active_channel_badge = QLabel("ACTIVE: -")
        self._active_channel_badge.setStyleSheet(
            "color: #e8f0ff; background: #2a3142; border-radius: 6px; padding: 4px 8px;"
            "font-weight: 700;"
        )
        layout.addWidget(self._active_channel_badge)

        layout.addSpacing(10)

        # 연결/해제 버튼
        self._connect_btn = QPushButton("연결")
        self._connect_btn.setObjectName("connectBtn")
        self._connect_btn.setFixedWidth(90)
        self._connect_btn.clicked.connect(self._on_connect)
        layout.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("해제")
        self._disconnect_btn.setObjectName("disconnectBtn")
        self._disconnect_btn.setFixedWidth(90)
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        layout.addWidget(self._disconnect_btn)

        layout.addStretch()

        # 연결 정보
        self._conn_info_label = QLabel("연결 안됨")
        self._conn_info_label.setStyleSheet("color: #6a7088; font-style: italic;")
        layout.addWidget(self._conn_info_label)

        return group


    def _connect_signals(self) -> None:
        self._ftdi.device_connected.connect(self._on_hw_connected)
        self._ftdi.device_disconnected.connect(self._on_hw_disconnected)
        self._ftdi.comm_error.connect(self._on_hw_error)
        self._ftdi.active_channel_changed.connect(self._on_active_channel_changed)

    def _load_modules(self) -> None:
        """디바이스 모듈 탐색 및 탭 추가"""
        module_classes = discover_module_classes()

        if not module_classes:
            placeholder = QLabel("로드된 모듈이 없습니다.\nmodules/ 폴더에 디바이스 모듈을 추가하세요.")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #6a7088; font-size: 14px;")
            self._tab_widget.addTab(placeholder, "모듈 없음")
            return

        for cls in module_classes:
            try:
                module_instance = cls(self._ftdi)
                self._modules.append(module_instance)

                tab_label = f"{cls.MODULE_ICON} {cls.MODULE_NAME}" if cls.MODULE_ICON else cls.MODULE_NAME
                self._tab_widget.addTab(module_instance, tab_label)
                logger.info(f"모듈 탭 추가: {cls.MODULE_NAME}")
            except Exception as e:
                logger.error(f"모듈 '{cls.MODULE_NAME}' 인스턴스 생성 실패: {e}")
                traceback.print_exc()

    # ── 메시지박스 ──

    def _show_scan_dialog(self, device_count: int) -> None:
        """FTDI 스캔 결과 메시지박스"""
        box = QMessageBox(self)
        box.setWindowTitle("FTDI 스캔")
        if device_count > 0:
            box.setIcon(QMessageBox.Icon.Information)
            box.setText("스캔 완료")
            box.setInformativeText(f"{device_count}개 FTDI 장치를 발견했습니다.")
        else:
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText("디바이스 없음")
            box.setInformativeText("스캔 결과 연결 가능한 장치를 찾지 못했습니다.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setStyleSheet(self._MSGBOX_STYLESHEET)
        box.exec()

    def _show_connection_dialog(self, info: str) -> None:
        """FTDI 연결 성공 메시지박스"""
        box = QMessageBox(self)
        box.setWindowTitle("FTDI 연결")
        box.setIcon(QMessageBox.Icon.Information)
        serial = self._ftdi.serial_number or "-"
        box.setText(f"SN {serial} 연결됨")
        box.setInformativeText("FTDI 장치 연결이 완료되었습니다.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setStyleSheet(self._MSGBOX_STYLESHEET)
        box.exec()

    def _show_warning_dialog(self, title: str, message: str) -> None:
        """경고 메시지박스"""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(title)
        box.setInformativeText(message)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setStyleSheet(self._MSGBOX_STYLESHEET)
        box.exec()

    def _show_disconnection_dialog(self, serial: str) -> None:
        """FTDI 연결 해제 메시지박스"""
        box = QMessageBox(self)
        box.setWindowTitle("FTDI 해제")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"SN {serial} 연결 해제됨")
        box.setInformativeText("FTDI 장치와의 연결이 해제되었습니다.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setStyleSheet(self._MSGBOX_STYLESHEET)
        box.exec()

    # ── FTDI 연결 핸들러 ──

    @Slot()
    def _on_scan_devices(self) -> None:
        """FTDI 장치 스캔"""
        self._device_combo.clear()

        devices = FtdiManager.scan_devices_with_channels()
        if not devices:
            self._device_combo.setPlaceholderText("장치를 찾을 수 없음")
            self.statusBar().showMessage("FTDI 장치를 찾을 수 없습니다")
            self._show_scan_dialog(0)
            return

        for serial, desc, channels, device_type in devices:
            self._device_combo.addItem(
                f"{serial}  ({desc})", {"serial": serial, "channels": channels, "device_type": device_type}
            )

        self.statusBar().showMessage(f"{len(devices)}개 FTDI 장치 발견")
        self._show_scan_dialog(len(devices))
        self._on_device_selected(self._device_combo.currentIndex())

    @Slot()
    def _on_connect(self) -> None:
        """FTDI 장치 연결"""
        if self._device_combo.currentIndex() < 0:
            self._show_warning_dialog("장치 미선택", "연결할 FTDI 장치를 먼저 스캔하고 선택하세요.")
            self.statusBar().showMessage("연결할 장치를 선택하세요")
            return

        data = self._device_combo.currentData()
        serial = data["serial"] if isinstance(data, dict) else data

        # 단일 채널 장치는 채널 인자를 빈 문자열로 전달
        channels = list((data.get("channels") or []) if isinstance(data, dict) else [])

        if channels:
            channel = self._channel_combo.currentText() or "A"
            if self._channel_combo.currentIndex() < 0 or channel not in channels:
                self._show_warning_dialog(
                    "채널 미선택",
                    f"이 장치는 다중 채널을 지원합니다.\n"
                    f"사용할 채널을 선택해 주세요. (사용 가능: {', '.join(channels)})",
                )
                self.statusBar().showMessage("채널을 선택하세요")
                return
        else:
            channel = "A"

        success = self._ftdi.open_device(serial, channel)
        if success:
            ch_label = f" CH-{channel}" if channel else ""
            self.statusBar().showMessage(f"연결됨: {serial}{ch_label}")
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
        # 다채널 장치 (FT2232, FT4232) 또는 단일 채널 포함
        self._channel_combo.addItems(channels)
        if current in channels:
            self._channel_combo.setCurrentText(current)
        self._channel_combo.setEnabled(True)
        self._channel_combo.setToolTip("사용할 FTDI 채널을 선택하세요")

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
                    logger.error(f"모듈 on_channel_changed 오류: {e}")
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
        msg.setWindowTitle("채널 전환 확인")
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setText(f"제어 채널을 {new_channel}로 변경하시겠습니까?")
        msg.setInformativeText("현재 동작 중인 채널이 변경됩니다.")
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
            self.statusBar().showMessage(f"활성 채널 변경: {new_channel}")
            # Restart communications after switching
            for module in self._modules:
                try:
                    module.start_communication()
                except Exception:
                    pass
            self._log_channel_switch(current, new_channel)
        else:
            self.statusBar().showMessage("채널 변경 실패")

    @Slot()
    def _on_disconnect(self) -> None:
        """FTDI 장치 연결 해제"""
        self._last_connected_serial = self._ftdi.serial_number or "-"
        for module in self._modules:
            try:
                module.stop_communication()
            except Exception:
                pass
        self._ftdi.close_device()

    @Slot(str)
    def _on_hw_connected(self, info: str) -> None:
        """연결 성공 시 UI 업데이트"""
        self._status_led.setStyleSheet("color: #33cc33; font-size: 16px;")
        self._conn_info_label.setText(info)
        self._conn_info_label.setStyleSheet("color: #88cc88; font-style: normal;")
        self._connect_btn.setEnabled(False)
        self._disconnect_btn.setEnabled(True)
        self._device_combo.setEnabled(False)
        self._scan_btn.setEnabled(False)

        # 모든 모듈에 연결 알림
        for module in self._modules:
            try:
                module.on_device_connected()
            except Exception as e:
                logger.error(f"모듈 on_device_connected 오류: {e}")

        # 활성 채널 동기화
        try:
            active_ch = self._ftdi.channel
            for module in self._modules:
                try:
                    module.on_channel_changed(active_ch)
                except Exception as e:
                    logger.error(f"모듈 on_channel_changed 오류: {e}")
        except Exception:
            pass

        QApplication.beep()
        self._show_connection_dialog(info)

    @Slot()
    def _on_hw_disconnected(self) -> None:
        """연결 해제 시 UI 업데이트"""
        self._status_led.setStyleSheet("color: #cc3333; font-size: 16px;")
        self._conn_info_label.setText("연결 안됨")
        self._conn_info_label.setStyleSheet("color: #6a7088; font-style: italic;")
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._device_combo.setEnabled(True)
        self._scan_btn.setEnabled(True)
        self._channel_combo.setEnabled(True)
        self.statusBar().showMessage("연결 해제됨")
        self._show_disconnection_dialog(self._last_connected_serial)

        # 모든 모듈에 해제 알림
        for module in self._modules:
            try:
                module.on_device_disconnected()
            except Exception as e:
                logger.error(f"모듈 on_device_disconnected 오류: {e}")

    @Slot(str)
    def _on_hw_error(self, error_msg: str) -> None:
        """하드웨어 오류 표시"""
        self._status_led.setStyleSheet("color: #cccc33; font-size: 16px;")
        self.statusBar().showMessage(f"오류: {error_msg}")

    @Slot(str)
    def _on_active_channel_changed(self, channel: str) -> None:
        for module in self._modules:
            try:
                module.on_channel_changed(channel)
            except Exception as e:
                logger.error(f"모듈 on_channel_changed 오류: {e}")
        if hasattr(self, "_active_channel_badge"):
            self._active_channel_badge.setText(f"ACTIVE: {channel}")

    def _log_channel_switch(self, prev: str, new: str) -> None:
        logger.info(f"Active channel switch: {prev} -> {new}")
        self.statusBar().showMessage(f"채널 전환: {prev} → {new}")

    # ── 탭 전환 핸들러 ──

    @Slot(int)
    def _on_tab_changed(self, index: int) -> None:
        """탭 전환 시 모듈 활성화/비활성화"""
        if 0 <= self._active_tab_index < len(self._modules):
            self._modules[self._active_tab_index].on_tab_deactivated()

        self._active_tab_index = index
        if 0 <= index < len(self._modules):
            self._modules[index].on_tab_activated()

    def closeEvent(self, event) -> None:
        """종료 시 확인 후 모든 모듈 통신 중지 및 FTDI 해제"""
        box = QMessageBox(self)
        box.setWindowTitle("종료 확인")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("Universal Device Studio를 종료하시겠습니까?")
        if self._ftdi.is_connected:
            box.setInformativeText(
                f"FTDI 장치(SN: {self._ftdi.serial_number})가 연결되어 있습니다.\n"
                "종료하면 연결이 자동으로 해제됩니다."
            )
        else:
            box.setInformativeText("진행 중인 작업이 있다면 저장 후 종료하세요.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("종료")
        box.button(QMessageBox.StandardButton.No).setText("취소")
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
    """애플리케이션 메인 함수"""
    # 프로젝트 루트를 sys.path에 추가 (어디서 실행해도 임포트 정상 동작)
    project_root = str(Path(__file__).parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = QApplication(sys.argv)

    # Ctrl+C로 깔끔하게 종료
    signal.signal(signal.SIGINT, lambda *_: app.quit())

    # 다크 테마 로드
    qss_path = Path(__file__).parent / "assets" / "dark_theme.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    # 기본 폰트
    default_font = QFont("Segoe UI", 10)
    default_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(default_font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
