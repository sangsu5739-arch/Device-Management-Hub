from __future__ import annotations

import ctypes as c
import logging
import time
from typing import Any, Optional

from PySide6.QtCore import QObject, Signal, Slot, QMutexLocker
from core.ftdi_manager import FtdiManager

logger = logging.getLogger(__name__)


def _ee_read_to_dict(ft) -> dict:
    """Read EEPROM and immediately extract all values into a Python dict.

    The ftd2xx library's eeRead() returns a ctypes struct whose string
    pointer fields (Manufacturer, Description, etc.) reference local
    buffers that can be garbage-collected at any time.  By extracting
    everything into native Python types *before* returning, we eliminate
    the dangling-pointer crash entirely.
    """
    ee = ft.eeRead()

    def _s(val: Any) -> str:
        if isinstance(val, (bytes, bytearray)):
            return val.decode("utf-8", errors="ignore").strip("\x00")
        if val is None:
            return ""
        return str(val)

    return {
        "Manufacturer": _s(getattr(ee, "Manufacturer", b"")),
        "ManufacturerId": _s(getattr(ee, "ManufacturerId", b"")),
        "Description": _s(getattr(ee, "Description", b"")),
        "SerialNumber": _s(getattr(ee, "SerialNumber", b"")),
        "VendorId": int(getattr(ee, "VendorId", 0)),
        "ProductId": int(getattr(ee, "ProductId", 0)),
        "MaxPower": int(getattr(ee, "MaxPower", 0)),
        "PnP": int(getattr(ee, "PnP", 0)),
        "SelfPowered": int(getattr(ee, "SelfPowered", 0)),
        "RemoteWakeup": int(getattr(ee, "RemoteWakeup", 0)),
        "SerNumEnable": int(getattr(ee, "SerNumEnable", 0)),
    }
    # ee (ctypes struct with dangling pointers) is discarded here — safe.


def _ee_write(ft, params: dict) -> None:
    """Build a fresh ft_program_data struct and program the EEPROM.

    Instead of read-modify-write on the same ctypes struct (which risks
    dangling pointers), we read current values into a Python dict, merge
    user params, then construct a brand-new struct for eeProgram().
    """
    # 1. Read current EEPROM into safe Python dict
    current = _ee_read_to_dict(ft)

    # 2. Merge user params over current values
    if params.get("manufacturer"):
        current["Manufacturer"] = params["manufacturer"]
    if params.get("description"):
        current["Description"] = params["description"]
    if params.get("serial"):
        current["SerialNumber"] = params["serial"]
    vid_str = params.get("vid", "")
    if vid_str:
        current["VendorId"] = int(vid_str, 16)
    pid_str = params.get("pid", "")
    if pid_str:
        current["ProductId"] = int(pid_str, 16)
    if "max_power" in params:
        current["MaxPower"] = int(params["max_power"])
    if "self_powered" in params:
        current["SelfPowered"] = 1 if params["self_powered"] else 0
    if "remote_wakeup" in params:
        current["RemoteWakeup"] = 1 if params["remote_wakeup"] else 0

    # 3. Build fresh struct — all string buffers are owned by this scope
    buf_mfg = c.create_string_buffer(current["Manufacturer"].encode("utf-8"), 256)
    buf_mfg_id = c.create_string_buffer(current["ManufacturerId"].encode("utf-8"), 256)
    buf_desc = c.create_string_buffer(current["Description"].encode("utf-8"), 256)
    buf_serial = c.create_string_buffer(current["SerialNumber"].encode("utf-8"), 256)

    ft.eeProgram(
        Manufacturer=c.cast(buf_mfg, c.c_char_p),
        ManufacturerId=c.cast(buf_mfg_id, c.c_char_p),
        Description=c.cast(buf_desc, c.c_char_p),
        SerialNumber=c.cast(buf_serial, c.c_char_p),
        VendorId=current["VendorId"],
        ProductId=current["ProductId"],
        MaxPower=current["MaxPower"],
        PnP=current["PnP"],
        SelfPowered=current["SelfPowered"],
        RemoteWakeup=current["RemoteWakeup"],
        SerNumEnable=current["SerNumEnable"],
    )
    # buf_mfg, buf_mfg_id, buf_desc, buf_serial are alive until here — safe.


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
        """Safely exit MPSSE/bitbang and enter normal D2XX mode for EEPROM access."""
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
        time.sleep(0.1)

    def _restore_mpsse_mode(self) -> None:
        """Mark channel mode as empty so the next I2C/SPI operation triggers re-init."""
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
            raw = _ee_read_to_dict(ft)

            data = {
                "manufacturer": raw["Manufacturer"],
                "description": raw["Description"],
                "serial": raw["SerialNumber"],
                "vid": f"{raw['VendorId']:04X}",
                "pid": f"{raw['ProductId']:04X}",
                "max_power": raw["MaxPower"],
                "self_powered": bool(raw["SelfPowered"]),
                "remote_wakeup": bool(raw["RemoteWakeup"]),
            }

            self.eeprom_data_read.emit(data)
            self.operation_finished.emit(True, "EEPROM read completed")
            self.log_message.emit("[EEPROM] Read completed successfully.")
        except Exception as e:
            logger.exception("EEPROM read failed")
            self.log_message.emit(f"[EEPROM] Read failed: {e}")
            self.operation_finished.emit(False, str(e))
        finally:
            self._restore_mpsse_mode()
            locker.unlock()

    @Slot()
    def write_eeprom(self) -> None:
        """Writes EEPROM parameters using ftd2xx."""
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
            _ee_write(ft, params)
            self.operation_finished.emit(True, "EEPROM written successfully")
            self.log_message.emit("[EEPROM] Write completed successfully. (A reset/replug might be needed)")
        except Exception as e:
            logger.exception("EEPROM write failed")
            self.log_message.emit(f"[EEPROM] Write failed: {e}")
            self.operation_finished.emit(False, str(e))
        finally:
            self._restore_mpsse_mode()
            locker.unlock()

    @Slot()
    def reset_device(self) -> None:
        """Resets the connected FTDI device using ftd2xx."""
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
            logger.exception("EEPROM reset failed")
            self.log_message.emit(f"[EEPROM] Reset failed: {e}")
            self.operation_finished.emit(False, str(e))
        finally:
            locker.unlock()
            # Handle is now invalid after cyclePort — request disconnect
            self.request_disconnect.emit()
