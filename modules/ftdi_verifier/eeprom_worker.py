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

    def _enter_prog_mode(self, ft) -> None:
        """Safely exit MPSSE/bitbang and enter normal D2XX mode for EEPROM access.

        A clean transition is critical: eeRead/eeProgram called while the
        MPSSE engine is active (or with stale USB data) can crash the
        ftd2xx C library.  The sequence mirrors what set_protocol_mode("PROG")
        does, but operates directly on the handle since the mutex is held.
        """
        import time
        # 1. Purge stale MPSSE/bitbang data from USB buffers
        try:
            ft.purge(3)  # PURGE_RX | PURGE_TX
        except Exception:
            pass
        # 2. Drain any residual RX bytes
        try:
            queued = ft.getQueueStatus()
            if queued > 0:
                ft.read(queued)
        except Exception:
            pass
        # 3. Reset bit mode to normal D2XX (exit MPSSE/bitbang)
        try:
            ft.setBitMode(0x00, 0x00)
        except Exception:
            pass
        # 4. Settle time for USB interface to stabilize
        time.sleep(0.05)

    def _restore_mpsse_mode(self) -> None:
        """Request MPSSE re-init after EEPROM operation.

        We cannot call set_protocol_mode here because the mutex is
        already held.  Instead, mark the channel mode as empty so that
        the next I2C/SPI operation triggers a full re-init.
        """
        ch = self._ftdi._active_channel
        self._ftdi._channel_modes[ch] = ""

    @Slot()
    def read_eeprom(self) -> None:
        """Reads EEPROM parameters using ftd2xx"""
        locker, ft = self._get_ft_locked()
        if ft is None:
            self.operation_finished.emit(False, "Device not connected or handle unavailable")
            return

        self.log_message.emit("[EEPROM] Reading parameters...")
        try:
            self._enter_prog_mode(ft)
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
            self._restore_mpsse_mode()
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
            self._enter_prog_mode(ft)
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
            self._restore_mpsse_mode()
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
            self._enter_prog_mode(ft)
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
