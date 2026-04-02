from __future__ import annotations

import ctypes as c
import logging
import time
from typing import Any, Optional

from PySide6.QtCore import QObject, Signal, Slot, QMutexLocker
from core.ftdi_manager import FtdiManager

logger = logging.getLogger(__name__)

# FTDI EEPROM struct Version per device type
# Source: ftd2xx.h FT_PROGRAM_DATA documentation
#   0 = FT232BM   1 = FT2232C   2 = FT232R
#   3 = FT2232H   4 = FT4232H   5 = FT232H
_DEVICE_EE_VERSIONS = {
    "FT232H": 5,
    "FT4232H": 4,
    "FT2232H": 3,
    "FT232R": 2,
    "FT2232C": 1,
    "FT232BM": 0,
}
_DEFAULT_EE_VERSION = 5  # safe default (covers all fields up to FT232H)


def _get_ee_version(ftdi_mgr: FtdiManager) -> int:
    """Determine the correct EEPROM struct version for the connected device."""
    info = ftdi_mgr.get_device_info()
    dtype = (info.get("device_type") or "").upper()
    for key, ver in _DEVICE_EE_VERSIONS.items():
        if key.upper() in dtype:
            return ver
    return _DEFAULT_EE_VERSION


def _safe_ee_read(ft, version: int) -> tuple[dict, dict]:
    """Read EEPROM via direct FT_EE_Read, keeping string buffers alive.

    Returns (ui_data, raw_fields) where:
    - ui_data: display-friendly dict for the UI
    - raw_fields: all int/str fields for reconstruction during write

    String buffers are local variables that stay alive until this
    function returns — no dangling pointer risk.
    """
    import ftd2xx._ftd2xx as _ft
    from ftd2xx.ftd2xx import call_ft
    from ftd2xx import defines

    # Allocate string buffers — these MUST outlive the FT_EE_Read call
    buf_mfg = c.create_string_buffer(defines.MAX_DESCRIPTION_SIZE)
    buf_mfg_id = c.create_string_buffer(defines.MAX_DESCRIPTION_SIZE)
    buf_desc = c.create_string_buffer(defines.MAX_DESCRIPTION_SIZE)
    buf_serial = c.create_string_buffer(defines.MAX_DESCRIPTION_SIZE)

    progdata = _ft.ft_program_data(
        Signature1=0,
        Signature2=0xFFFFFFFF,
        Version=version,
        Manufacturer=c.cast(buf_mfg, c.c_char_p),
        ManufacturerId=c.cast(buf_mfg_id, c.c_char_p),
        Description=c.cast(buf_desc, c.c_char_p),
        SerialNumber=c.cast(buf_serial, c.c_char_p),
    )

    call_ft(_ft.FT_EE_Read, ft.handle, c.byref(progdata))

    # Extract ALL values into Python types NOW, while buffers are alive
    def _s(buf) -> str:
        try:
            return buf.value.decode("utf-8", errors="ignore").strip("\x00")
        except Exception:
            return ""

    raw = {}
    for field_name, _ in _ft.ft_program_data._fields_:
        if field_name in ("Manufacturer", "ManufacturerId", "Description", "SerialNumber"):
            continue  # handled separately from buffers
        raw[field_name] = int(getattr(progdata, field_name, 0))

    raw["Manufacturer"] = _s(buf_mfg)
    raw["ManufacturerId"] = _s(buf_mfg_id)
    raw["Description"] = _s(buf_desc)
    raw["SerialNumber"] = _s(buf_serial)

    ui_data = {
        "manufacturer": raw["Manufacturer"],
        "description": raw["Description"],
        "serial": raw["SerialNumber"],
        "vid": f"{raw.get('VendorId', 0):04X}",
        "pid": f"{raw.get('ProductId', 0):04X}",
        "max_power": raw.get("MaxPower", 0),
        "self_powered": bool(raw.get("SelfPowered", 0)),
        "remote_wakeup": bool(raw.get("RemoteWakeup", 0)),
    }
    return ui_data, raw
    # buf_mfg, buf_mfg_id, buf_desc, buf_serial freed here — safe.


def _safe_ee_program(ft, raw_fields: dict, version: int) -> None:
    """Write EEPROM via direct FT_EE_Program with correct Version.

    The ftd2xx library's eeProgram() hardcodes Version=2, which is
    WRONG for FT232H (needs 4) and FT2232H (needs 3).  Passing the
    wrong version causes FT_EE_Program to misinterpret the struct
    layout → C-level crash.

    This function calls FT_EE_Program directly with the correct version.
    """
    import ftd2xx._ftd2xx as _ft
    from ftd2xx.ftd2xx import call_ft

    # String buffers — must stay alive until FT_EE_Program returns
    buf_mfg = c.create_string_buffer(raw_fields["Manufacturer"].encode("utf-8"), 256)
    buf_mfg_id = c.create_string_buffer(raw_fields.get("ManufacturerId", "").encode("utf-8"), 256)
    buf_desc = c.create_string_buffer(raw_fields["Description"].encode("utf-8"), 256)
    buf_serial = c.create_string_buffer(raw_fields["SerialNumber"].encode("utf-8"), 256)

    # Build struct with ALL fields from the read + user modifications
    progdata = _ft.ft_program_data(
        Signature1=0,
        Signature2=0xFFFFFFFF,
        Version=version,
        Manufacturer=c.cast(buf_mfg, c.c_char_p),
        ManufacturerId=c.cast(buf_mfg_id, c.c_char_p),
        Description=c.cast(buf_desc, c.c_char_p),
        SerialNumber=c.cast(buf_serial, c.c_char_p),
    )

    # Copy all non-string fields from raw_fields
    skip = {"Signature1", "Signature2", "Version",
            "Manufacturer", "ManufacturerId", "Description", "SerialNumber"}
    for field_name, _ in _ft.ft_program_data._fields_:
        if field_name in skip:
            continue
        if field_name in raw_fields:
            try:
                setattr(progdata, field_name, int(raw_fields[field_name]))
            except (ValueError, TypeError):
                pass

    call_ft(_ft.FT_EE_Program, ft.handle, c.byref(progdata))
    # buf_mfg, buf_mfg_id, buf_desc, buf_serial freed here — safe.


class EepromWorker(QObject):
    """Worker thread for FTDI EEPROM read/write operations"""

    log_message = Signal(str)
    eeprom_data_read = Signal(dict)
    operation_finished = Signal(bool, str)  # success, message
    request_disconnect = Signal()  # ask main thread to disconnect after reset

    def __init__(self, ftdi_manager: FtdiManager, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._ftdi = ftdi_manager
        self._pending_params: dict = {}
        self._last_raw_fields: dict = {}  # preserve all fields for write

    def _get_ft_locked(self) -> tuple:
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

        Uses resetDevice() instead of just setBitMode(0,0) — this is what
        FTDI's own FT_Prog utility does before EEPROM operations.
        resetDevice() fully resets the USB endpoint state, not just the
        bit mode, which prevents intermittent crashes from stale USB state.
        """
        # 1. Purge stale MPSSE/bitbang data
        try:
            ft.purge(3)
        except Exception:
            pass
        try:
            queued = ft.getQueueStatus()
            if queued > 0:
                ft.read(queued)
        except Exception:
            pass
        # 2. Full USB-level device reset (not just bit mode)
        try:
            ft.resetDevice()
        except Exception:
            pass
        # 3. Exit any special mode
        try:
            ft.setBitMode(0x00, 0x00)
        except Exception:
            pass
        # 4. Settle — EEPROM needs time after USB reset
        time.sleep(0.2)

    def _restore_mpsse_mode(self) -> None:
        ch = self._ftdi._active_channel
        self._ftdi._channel_modes[ch] = ""

    @Slot()
    def read_eeprom(self) -> None:
        locker, ft = self._get_ft_locked()
        if ft is None:
            self.operation_finished.emit(False, "Device not connected or handle unavailable")
            return

        self.log_message.emit("[EEPROM] Reading parameters...")
        try:
            self._enter_prog_mode(ft)
            version = _get_ee_version(self._ftdi)
            self.log_message.emit(f"[EEPROM] Device EE version={version}")
            ui_data, raw_fields = _safe_ee_read(ft, version)
            self._last_raw_fields = raw_fields

            self.eeprom_data_read.emit(ui_data)
            self.operation_finished.emit(True, "EEPROM read completed")
            self.log_message.emit("[EEPROM] Read completed successfully.")
        except Exception as e:
            logger.exception("EEPROM read failed")
            self.log_message.emit(f"[EEPROM] Read failed: {e}")
            self.operation_finished.emit(False, str(e))
        finally:
            # Post-EEPROM settle: allow EEPROM internal cycle to complete
            # before any subsequent operation (read, write, or MPSSE re-init)
            time.sleep(0.2)
            self._restore_mpsse_mode()
            locker.unlock()

    @Slot()
    def write_eeprom(self) -> None:
        params = self._pending_params
        if not params:
            self.operation_finished.emit(False, "No parameters provided")
            return
        if not self._last_raw_fields:
            self.operation_finished.emit(False, "Read EEPROM first before writing")
            return

        locker, ft = self._get_ft_locked()
        if ft is None:
            self.operation_finished.emit(False, "Device not connected or handle unavailable")
            return

        self.log_message.emit("[EEPROM] Writing parameters to device...")
        try:
            self._enter_prog_mode(ft)
            version = _get_ee_version(self._ftdi)

            # Merge user params into the complete raw field set
            raw = dict(self._last_raw_fields)
            if params.get("manufacturer"):
                raw["Manufacturer"] = params["manufacturer"]
            if params.get("description"):
                raw["Description"] = params["description"]
            if params.get("serial"):
                raw["SerialNumber"] = params["serial"]
            vid_str = params.get("vid", "")
            if vid_str:
                raw["VendorId"] = int(vid_str, 16)
            pid_str = params.get("pid", "")
            if pid_str:
                raw["ProductId"] = int(pid_str, 16)
            if "max_power" in params:
                raw["MaxPower"] = int(params["max_power"])
            if "self_powered" in params:
                raw["SelfPowered"] = 1 if params["self_powered"] else 0
            if "remote_wakeup" in params:
                raw["RemoteWakeup"] = 1 if params["remote_wakeup"] else 0

            self.log_message.emit(
                f"[EEPROM] Version={version}, VID=0x{raw.get('VendorId', 0):04X}, "
                f"PID=0x{raw.get('ProductId', 0):04X}, MaxPower={raw.get('MaxPower', 0)}mA"
            )

            _safe_ee_program(ft, raw, version)
            # Post-write settle: EEPROM write cycle ~5ms/word, verify read follows
            time.sleep(0.3)
            self.operation_finished.emit(True, "EEPROM written successfully")
            self.log_message.emit("[EEPROM] Write completed. (A reset/replug might be needed)")
        except Exception as e:
            logger.exception("EEPROM write failed")
            self.log_message.emit(f"[EEPROM] Write failed: {e}")
            self.operation_finished.emit(False, str(e))
        finally:
            time.sleep(0.2)
            self._restore_mpsse_mode()
            locker.unlock()

    @Slot()
    def reset_device(self) -> None:
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
            self.request_disconnect.emit()
