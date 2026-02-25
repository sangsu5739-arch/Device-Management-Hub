"""
Universal Device Studio - 디바이스 모듈 추상 베이스 클래스

모든 디바이스 모듈은 이 클래스를 상속받아 구현합니다.
각 모듈은 QTabWidget의 탭으로 추가됩니다.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Optional

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Signal

from core.ftdi_manager import FtdiManager


class BaseModule(QWidget):
    """디바이스 모듈 추상 베이스 클래스

    각 모듈은 이 클래스를 상속받아 구현하며,
    QTabWidget의 탭으로 동적 로드됩니다.

    Class Attributes:
        MODULE_NAME: 탭에 표시할 모듈 이름
        MODULE_ICON: 아이콘 (이모지 또는 경로)
        MODULE_VERSION: 모듈 버전
    """

    MODULE_NAME: str = "Unknown Module"
    MODULE_ICON: str = ""
    MODULE_VERSION: str = "1.0.0"

    # Signals
    status_message = Signal(str)
    log_message = Signal(str)

    def __init__(self, ftdi_manager: FtdiManager, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ftdi = ftdi_manager
        self._is_active: bool = False
        self.init_ui()

    @abstractmethod
    def init_ui(self) -> None:
        """모듈 UI 초기화. __init__에서 1회 호출됩니다."""
        ...

    @abstractmethod
    def on_device_connected(self) -> None:
        """FTDI 장치 연결 시 호출. UI 상태를 업데이트합니다."""
        ...

    @abstractmethod
    def on_device_disconnected(self) -> None:
        """FTDI 장치 해제 시 호출. 컨트롤을 비활성화합니다."""
        ...

    @abstractmethod
    def start_communication(self) -> None:
        """주기적/연속 I2C 통신 시작 (Worker 스레드 등)."""
        ...

    @abstractmethod
    def stop_communication(self) -> None:
        """I2C 통신 중지."""
        ...

    @abstractmethod
    def update_data(self) -> None:
        """하드웨어로부터 1회 데이터 갱신."""
        ...

    def on_tab_activated(self) -> None:
        """이 모듈의 탭이 활성화될 때 호출됩니다."""
        self._is_active = True

    def on_tab_deactivated(self) -> None:
        """이 모듈의 탭이 비활성화될 때 호출됩니다."""
        self._is_active = False
