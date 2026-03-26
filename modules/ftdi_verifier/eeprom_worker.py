from __future__ import annotations

import time
from typing import Dict, Any, Optional

from PySide6.QtCore import QObject, Signal, Slot, QMutexLocker
from core.ftdi_manager import FtdiManager


class EepromWorker(QObject):
    """Worker thread for FTDI EEPROM read/write operations"""

    log_message = Signal(str)
    eeprom_data_read = Signal(dict)
    operation_finished = Signal(bool, str)  # success, message
    request_disconnect = Signal()  # ask main thread to disconnect after reset

    def __init__(self, ftdi_manager: FtdiManager, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._ftdi = ftdi_manager
        self._pending_params: dict = {}  # stored by caller before thread start

    def _get_ft_locked(self) -> tuple:
        """Acquire mutex and return (locker, ft_handle).

        Both is_connected check and _ft access happen under the same
        mutex lock to avoid TOCTOU races.  Caller must keep `locker`
        alive for the duration of the operation.
        """
        locker = QMutexLocker(self._ftdi._mutex)
        if not self._ftdi.is_connected:
            locker.unlock()
            return None, None
        ft = getattr(self._ftdi, "_ft", None)
        if ft is None:
            locker.unlock()
            return None, None
        return locker, ft

    @Slot()
    def read_eeprom(self) -> None:
        """Reads EEPROM parameters using ftd2xx"""
        locker, ft = self._get_ft_locked()
        if ft is None:
            self.operation_finished.emit(False, "Device not connected or handle unavailable")
            return

        self.log_message.emit("[EEPROM] Reading parameters...")
        try:
            ee = ft.eeRead()

            def decode_str(val: Any) -> str:
                if isinstance(val, (bytes, bytearray)):
                    return val.decode("utf-8", errors="ignore").strip("\x00")
                return str(val or "")

            data = {
                "manufacturer": decode_str(getattr(ee, "Manufacturer", "")),
                "description": decode_str(getattr(ee, "Description", "")),
                "serial": decode_str(getattr(ee, "SerialNumber", "")),
                "vid": f"{getattr(ee, 'VendorId', 0):04X}",
                "pid": f"{getattr(ee, 'ProductId', 0):04X}",
                "max_power": int(getattr(ee, "MaxPower", 0)),
                "self_powered": bool(getattr(ee, "SelfPowered", False)),
                "remote_wakeup": bool(getattr(ee, "RemoteWakeup", False))
            }

            self.eeprom_data_read.emit(data)
            self.operation_finished.emit(True, "EEPROM read completed")
            self.log_message.emit("[EEPROM] Read completed successfully.")
        except Exception as e:
            self.log_message.emit(f"[EEPROM] Read failed: {e}")
            self.operation_finished.emit(False, str(e))
        finally:
            locker.unlock()

    @Slot()
    def write_eeprom(self) -> None:
        """Writes EEPROM parameters using ftd2xx.
        Params are read from self._pending_params (set before thread start).
        """
        params = self._pending_params
        if not params:
            self.operation_finished.emit(False, "No parameters provided")
            return

        locker, ft = self._get_ft_locked()
        if ft is None:
            self.operation_finished.emit(False, "Device not connected or handle unavailable")
            return

        self.log_message.emit("[EEPROM] Writing parameters to device...")
        try:
            ee = ft.eeRead()

            # ftd2xx ctypes fields (c_char_p) require bytes, not str
            if "manufacturer" in params and params["manufacturer"]:
                ee.Manufacturer = params["manufacturer"].encode("utf-8")
            if "description" in params and params["description"]:
                ee.Description = params["description"].encode("utf-8")
            if "serial" in params and params["serial"]:
                ee.SerialNumber = params["serial"].encode("utf-8")

            vid_str = params.get("vid", "")
            pid_str = params.get("pid", "")
            if vid_str:
                ee.VendorId = int(vid_str, 16)
            if pid_str:
                ee.ProductId = int(pid_str, 16)

            if "max_power" in params:
                ee.MaxPower = int(params["max_power"])
            if "self_powered" in params:
                ee.SelfPowered = 1 if params["self_powered"] else 0
            if "remote_wakeup" in params:
                ee.RemoteWakeup = 1 if params["remote_wakeup"] else 0

            self.log_message.emit(f"[EEPROM] New VID: 0x{vid_str or 'Keep'}, PID: 0x{pid_str or 'Keep'}, Max Power: {getattr(ee, 'MaxPower', 0)} mA")

            ft.eeProgram(ee)
            self.operation_finished.emit(True, "EEPROM written successfully")
            self.log_message.emit("[EEPROM] Write completed successfully. (A reset/replug might be needed)")
        except Exception as e:
            self.log_message.emit(f"[EEPROM] Write failed: {e}")
            self.operation_finished.emit(False, str(e))
        finally:
            locker.unlock()

    @Slot()
    def reset_device(self) -> None:
        """Resets the connected FTDI device using ftd2xx.

        After cyclePort() the USB handle is invalidated, so we signal
        the main thread to run close_device() to keep FtdiManager in sync.
        """
        locker, ft = self._get_ft_locked()
        if ft is None:
            self.operation_finished.emit(False, "Device not connected or handle unavailable")
            return

        self.log_message.emit("[EEPROM] Resetting device...")
        try:
            ft.resetDevice()
            ft.cyclePort()
            self.log_message.emit("[EEPROM] Device reset complete. Re-enumeration generated.")
            self.operation_finished.emit(True, "Device reset successfully")
        except Exception as e:
            self.log_message.emit(f"[EEPROM] Reset failed: {e}")
            self.operation_finished.emit(False, str(e))
        finally:
            locker.unlock()
            # Handle is now invalid after cyclePort — request disconnect
            self.request_disconnect.emit()
