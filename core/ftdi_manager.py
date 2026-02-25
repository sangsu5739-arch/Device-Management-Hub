"""
Universal Device Studio - FTDI MPSSE I2C 매니저 (Singleton)

FT4232H를 ftd2xx + MPSSE 방식으로 제어하여 I2C 통신을 수행합니다.
모든 디바이스 모듈이 하나의 FTDI 세션을 공유합니다.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, Signal, QMutex, QMutexLocker

logger = logging.getLogger(__name__)


class FtdiManager(QObject):
    """Singleton FTDI MPSSE I2C 매니저

    모든 모듈이 하나의 FTDI 세션을 공유하며,
    QMutex를 통해 스레드 안전한 I2C 접근을 보장합니다.

    Attributes:
        device_connected: 연결 성공 시 정보 문자열 전달
        device_disconnected: 연결 해제 시 발행
        comm_error: 통신 오류 메시지 전달
        data_sent: TX 로그 메시지
        data_received: RX 로그 메시지
        log_message: 일반 로그 메시지
    """

    device_connected = Signal(str)
    device_disconnected = Signal()
    comm_error = Signal(str)
    data_sent = Signal(str)
    data_received = Signal(str)
    log_message = Signal(str)
    device_info_changed = Signal(object)

    # ── Singleton ──
    _instance: Optional[FtdiManager] = None
    _initialized: bool = False
    _device_cache: dict = {}

    # ── FTDI ADBUS GPIO 핀맵 ──
    _PIN0_SK = 1 << 0   # AD0 — SCL
    _PIN1_DO = 1 << 1   # AD1 — SDA out
    _PIN2_DI = 1 << 2   # AD2 — SDA in
    _PIN3_CS = 1 << 3   # AD3 — CS (미사용)

    _PIN_SCL = _PIN0_SK
    _PIN_SDA = _PIN1_DO
    _PIN_SDA_IN = _PIN2_DI

    _PURGE_RXTX = 3

    # ── MPSSE Opcodes ──
    _MPSSE_SET_BITS_LOW = 0x80
    _MPSSE_READ_BITS_LOW = 0x81
    _MPSSE_SEND_IMMEDIATE = 0x87
    _MPSSE_DATA_OUT_BYTES_NEG = 0x11
    _MPSSE_DATA_OUT_BITS_POS = 0x12
    _MPSSE_DATA_IN_BYTES_POS = 0x20
    _MPSSE_DATA_IN_BITS_POS = 0x22

    # ── I2C 방향 마스크 ──
    _I2C_DIR_SDA_OUT = _PIN_SCL | _PIN_SDA  # 0x03
    _I2C_DIR_SDA_IN = _PIN_SCL               # 0x01

    def __new__(cls, parent: Optional[QObject] = None) -> FtdiManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, parent: Optional[QObject] = None) -> None:
        if self._initialized:
            return
        super().__init__(parent)
        self._ft = None
        self._is_connected: bool = False
        self._serial_number: str = ""
        self._channel: str = "A"
        self._mutex = QMutex()
        self._i2c_retry_count: int = 2
        self._i2c_retry_delay_s: float = 0.01
        FtdiManager._initialized = True

    @classmethod
    def instance(cls) -> FtdiManager:
        """싱글톤 인스턴스 반환"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Properties ──

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def serial_number(self) -> str:
        return self._serial_number

    @property
    def channel(self) -> str:
        return self._channel

    def get_device_info(self, serial: Optional[str] = None) -> dict:
        key = serial or self._serial_number
        info = FtdiManager._device_cache.get(key, {}).copy()
        if info:
            info["channel"] = self._channel
            info["connected"] = self._is_connected
        return info

    # ── 장치 관리 ──

    @staticmethod
    def _normalize_serial(serial_raw: str) -> str:
        """시리얼 번호에서 채널 접미사(A/B/C/D) 제거"""
        serial = serial_raw.strip()
        if len(serial) >= 2 and serial[-1] in ("A", "B", "C", "D"):
            return serial[:-1]
        return serial

    @staticmethod
    def _infer_device_type(desc: str, channels: List[str]) -> str:
        desc_upper = (desc or "").upper()
        if "4232" in desc_upper or len(channels) >= 4:
            return "FT4232H"
        if "2232" in desc_upper or len(channels) >= 2:
            return "FT2232H"
        if "232" in desc_upper or len(channels) <= 1:
            return "FT232H"
        return "FTDI"

    @staticmethod
    def scan_devices() -> List[Tuple[str, str]]:
        """연결된 FTDI 장치 스캔

        Returns:
            [(base_serial, description), ...] 리스트
        """
        devices: List[Tuple[str, str]] = []
        for serial, desc, _channels, _dtype in FtdiManager.scan_devices_with_channels():
            devices.append((serial, desc))
        return devices

    @staticmethod
    def scan_devices_with_channels() -> List[Tuple[str, str, List[str], str]]:
        # Scan FTDI devices with channel list
        devices_map: dict[str, dict[str, object]] = {}
        try:
            import ftd2xx

            count = ftd2xx.createDeviceInfoList()
            for i in range(count):
                info = ftd2xx.getDeviceInfoDetail(i)
                serial_raw = info.get("serial", b"")
                desc_raw = info.get("description", b"")
                serial = (
                    serial_raw.decode(errors="ignore")
                    if isinstance(serial_raw, (bytes, bytearray))
                    else str(serial_raw)
                )
                desc = (
                    desc_raw.decode(errors="ignore")
                    if isinstance(desc_raw, (bytes, bytearray))
                    else str(desc_raw)
                )
                base_serial = FtdiManager._normalize_serial(serial)
                if not base_serial:
                    continue

                channel = None
                if serial and serial[-1].upper() in ("A", "B", "C", "D"):
                    channel = serial[-1].upper()
                else:
                    desc_upper = desc.upper()
                    for ch in ("A", "B", "C", "D"):
                        if desc_upper.endswith(f" {ch}"):
                            channel = ch
                            break

                entry = devices_map.setdefault(
                    base_serial, {"desc": desc, "channels": set()}
                )
                if desc and not entry["desc"]:
                    entry["desc"] = desc
                if channel:
                    entry["channels"].add(channel)
                else:
                    entry["channels"].add("A")

        except ImportError:
            logger.warning("ftd2xx library is not installed.")
        except Exception as e:
            logger.error(f"device scan error: {e}")

        devices: List[Tuple[str, str, List[str], str]] = []
        FtdiManager._device_cache = {}
        for serial, meta in devices_map.items():
            desc = str(meta.get("desc") or "")
            channels = sorted(meta.get("channels") or ["A"])
            device_type = FtdiManager._infer_device_type(desc, channels)
            devices.append((serial, desc, channels, device_type))
            FtdiManager._device_cache[serial] = {
                "serial": serial,
                "desc": desc,
                "channels": channels,
                "device_type": device_type,
            }
        return devices

    def _find_device_index(self, serial_number: str, channel: str) -> Optional[int]:
        """시리얼 번호와 채널에 해당하는 디바이스 인덱스 검색"""
        import ftd2xx

        target_base = self._normalize_serial(serial_number)
        target_ch = channel.upper()
        count = ftd2xx.createDeviceInfoList()
        fallback_index: Optional[int] = None

        for i in range(count):
            info = ftd2xx.getDeviceInfoDetail(i)
            serial_raw = info.get("serial", b"")
            desc_raw = info.get("description", b"")
            serial = (
                serial_raw.decode(errors="ignore")
                if isinstance(serial_raw, (bytes, bytearray))
                else str(serial_raw)
            )
            desc = (
                desc_raw.decode(errors="ignore")
                if isinstance(desc_raw, (bytes, bytearray))
                else str(desc_raw)
            )
            base_serial = self._normalize_serial(serial)
            if base_serial != target_base:
                continue

            serial_ch = serial[-1].upper() if serial else ""
            desc_upper = desc.upper()
            if serial_ch == target_ch or desc_upper.endswith(f" {target_ch}"):
                return i

            if fallback_index is None:
                fallback_index = i
        return fallback_index

    # ── MPSSE 저수준 ──

    def _mpsse_write(self, data: bytes) -> None:
        if self._ft is None:
            raise RuntimeError("FTDI handle이 없습니다.")
        self._ft.write(data)

    def _mpsse_read(self, length: int) -> bytes:
        if self._ft is None:
            raise RuntimeError("FTDI handle이 없습니다.")
        return bytes(self._ft.read(length))

    def _set_lines(self, scl_high: bool, sda_high: bool) -> None:
        """GPIO로 SCL/SDA 라인 설정"""
        if self._ft is None:
            raise RuntimeError("장치가 연결되어 있지 않습니다.")
        value = 0
        if scl_high:
            value |= self._PIN_SCL
        if sda_high:
            value |= self._PIN_SDA
        cmd = bytes([self._MPSSE_SET_BITS_LOW, value & 0xFF, self._I2C_DIR_SDA_OUT])
        self._mpsse_write(cmd)

    def _configure_mpsse(self) -> None:
        """MPSSE 모드 초기화 및 I2C 클록 설정"""
        if self._ft is None:
            raise RuntimeError("FTDI handle이 없습니다.")

        self._ft.resetDevice()
        self._ft.purge(self._PURGE_RXTX)
        self._ft.setUSBParameters(65536, 65536)
        self._ft.setLatencyTimer(2)
        self._ft.setTimeouts(3000, 3000)

        self._ft.setBitMode(0x00, 0x00)
        time.sleep(0.05)
        self._ft.setBitMode(0x00, 0x02)  # MPSSE
        time.sleep(0.05)
        self._ft.purge(self._PURGE_RXTX)

        # MPSSE sync
        self._mpsse_write(b"\xAA")
        time.sleep(0.02)
        rxn = self._ft.getQueueStatus()
        if rxn > 0:
            resp = self._mpsse_read(rxn)
            if b"\xFA\xAA" not in resp:
                self._log(f"[경고] MPSSE sync 응답 비정상: {resp.hex(' ')}")
        else:
            self._log("[경고] MPSSE sync 응답 없음 (계속 진행)")

        # 60MHz, adaptive off, 3-phase on, loopback off
        self._mpsse_write(bytes([0x8A, 0x97, 0x8C, 0x85]))

        # ~100kHz I2C clock
        div = 299
        self._mpsse_write(bytes([0x86, div & 0xFF, (div >> 8) & 0xFF]))

        self._set_lines(scl_high=True, sda_high=True)

    def open_device(self, serial_number: str, channel: str = "A") -> bool:
        """FTDI 장치 연결

        Args:
            serial_number: FTDI 시리얼 번호
            channel: 채널 (A/B/C/D)

        Returns:
            연결 성공 여부
        """
        if self._is_connected:
            self.close_device()

        self._serial_number = serial_number
        self._channel = channel.upper()

        try:
            import ftd2xx

            index = self._find_device_index(serial_number, self._channel)
            if index is None:
                raise RuntimeError(
                    f"대상 장치를 찾을 수 없습니다. SN={serial_number}, CH={self._channel}"
                )

            self._log(
                f"연결 시도: SN={serial_number}, CH={self._channel}, IDX={index}"
            )
            self._ft = ftd2xx.open(index)
            self._configure_mpsse()
            self._is_connected = True

            info = f"연결됨: SN={serial_number}, CH={self._channel}"
            self._log(info)
            self.device_connected.emit(info)
            cached = FtdiManager._device_cache.get(self._serial_number, {})
            self.device_info_changed.emit(
                {
                    "serial": self._serial_number,
                    "channel": self._channel,
                    "desc": cached.get("desc", ""),
                    "channels": cached.get("channels", []),
                    "device_type": cached.get("device_type", ""),
                    "connected": True,
                }
            )
            return True
        except ImportError:
            err = "ftd2xx 라이브러리가 설치되어 있지 않습니다."
            self._log(f"[오류] {err}")
            self.comm_error.emit(err)
            return False
        except Exception as e:
            err = f"연결 실패: {e}"
            self._log(f"[오류] {err}")
            self.comm_error.emit(err)
            try:
                if self._ft is not None:
                    self._ft.close()
            except Exception:
                pass
            self._ft = None
            self._is_connected = False
            return False

    def close_device(self) -> None:
        """FTDI 장치 연결 해제"""
        try:
            if self._ft is not None:
                try:
                    self._set_lines(scl_high=True, sda_high=True)
                except Exception:
                    pass
                self._ft.setBitMode(0x00, 0x00)
                self._ft.close()
        except Exception as e:
            logger.warning(f"연결 해제 중 오류: {e}")
        finally:
            self._ft = None
            self._is_connected = False
            self._serial_number = ""
            self._log("연결 해제됨")
            self.device_disconnected.emit()
            self.device_info_changed.emit(
                {
                    "serial": "",
                    "channel": "",
                    "desc": "",
                    "channels": [],
                    "device_type": "",
                    "connected": False,
                }
            )

    # ── I2C 프리미티브 (내부용, mutex 없음) ──

    def _i2c_start(self) -> None:
        """I2C START 조건 생성"""
        buf = bytearray()
        buf.extend([self._MPSSE_SET_BITS_LOW, self._PIN_SCL | self._PIN_SDA, self._I2C_DIR_SDA_OUT])
        buf.extend([self._MPSSE_SET_BITS_LOW, self._PIN_SCL, self._I2C_DIR_SDA_OUT])
        buf.extend([self._MPSSE_SET_BITS_LOW, 0x00, self._I2C_DIR_SDA_OUT])
        self._mpsse_write(bytes(buf))

    def _i2c_stop(self) -> None:
        """I2C STOP 조건 생성"""
        buf = bytearray()
        for _ in range(4):
            buf.extend([self._MPSSE_SET_BITS_LOW, 0x00, self._I2C_DIR_SDA_OUT])
        for _ in range(4):
            buf.extend([self._MPSSE_SET_BITS_LOW, self._PIN_SCL, self._I2C_DIR_SDA_OUT])
        for _ in range(4):
            buf.extend([self._MPSSE_SET_BITS_LOW, self._PIN_SCL | self._PIN_SDA, self._I2C_DIR_SDA_OUT])
        self._mpsse_write(bytes(buf))

    def _i2c_write_byte(self, value: int) -> bool:
        """1바이트 쓰기 + ACK 읽기

        Returns:
            ACK 수신 여부 (True=ACK, False=NACK)
        """
        buf = bytearray()
        buf.extend([self._MPSSE_SET_BITS_LOW, 0x00, self._I2C_DIR_SDA_OUT])
        buf.extend([self._MPSSE_DATA_OUT_BYTES_NEG, 0x00, 0x00, value & 0xFF])
        buf.extend([self._MPSSE_SET_BITS_LOW, 0x00, self._I2C_DIR_SDA_IN])
        buf.extend([self._MPSSE_DATA_IN_BITS_POS, 0x00])
        buf.append(self._MPSSE_SEND_IMMEDIATE)
        self._mpsse_write(bytes(buf))
        resp = self._mpsse_read(1)
        ack_bit = resp[0] & 0x01
        return ack_bit == 0

    def _i2c_read_byte(self, ack: bool) -> int:
        """1바이트 읽기 + ACK/NACK 쓰기

        Args:
            ack: True=ACK 전송, False=NACK 전송

        Returns:
            읽은 바이트 값
        """
        buf = bytearray()
        buf.extend([self._MPSSE_SET_BITS_LOW, 0x00, self._I2C_DIR_SDA_IN])
        buf.extend([self._MPSSE_DATA_IN_BYTES_POS, 0x00, 0x00])
        ack_byte = 0x00 if ack else 0xFF
        buf.extend([self._MPSSE_SET_BITS_LOW, self._PIN_SDA if not ack else 0x00, self._I2C_DIR_SDA_OUT])
        buf.extend([self._MPSSE_DATA_OUT_BITS_POS, 0x00, ack_byte])
        buf.append(self._MPSSE_SEND_IMMEDIATE)
        self._mpsse_write(bytes(buf))
        resp = self._mpsse_read(1)
        return resp[0]

    # ── I2C 고수준 트랜잭션 (스레드 안전) ──

    def i2c_write(self, slave_addr: int, data: bytes) -> bool:
        # I2C write transaction (thread-safe)
        if not self._is_connected or self._ft is None:
            self.comm_error.emit("Device not connected.")
            return False

        locker = QMutexLocker(self._mutex)
        attempts = self._i2c_retry_count + 1
        for attempt in range(attempts):
            try:
                addr_w = (slave_addr << 1) | 0
                self._i2c_start()
                if not self._i2c_write_byte(addr_w):
                    self._i2c_stop()
                    raise RuntimeError(f"Address NACK: 0x{slave_addr:02X}")
                for b in data:
                    if not self._i2c_write_byte(b):
                        self._i2c_stop()
                        raise RuntimeError(f"Data NACK: 0x{b:02X}")
                self._i2c_stop()

                hex_str = " ".join(f"{b:02X}" for b in data)
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                self.data_sent.emit(f"[{timestamp}] TX -> [0x{slave_addr:02X}] {hex_str}")
                return True
            except Exception as e:
                if attempt < attempts - 1:
                    time.sleep(self._i2c_retry_delay_s)
                    continue
                err = f"I2C write error: {e}"
                self._log(f"[Error] {err}")
                self.comm_error.emit(err)
                return False
    def i2c_read(self, slave_addr: int, write_prefix: bytes, read_len: int) -> Optional[bytes]:
        # I2C read transaction (thread-safe)
        if not self._is_connected or self._ft is None:
            self.comm_error.emit("Device not connected.")
            return None
        if read_len <= 0:
            return b""
    
        locker = QMutexLocker(self._mutex)
        attempts = self._i2c_retry_count + 1
        for attempt in range(attempts):
            try:
                addr_w = (slave_addr << 1) | 0
                addr_r = (slave_addr << 1) | 1
    
                # Write phase
                self._i2c_start()
                if not self._i2c_write_byte(addr_w):
                    self._i2c_stop()
                    raise RuntimeError(f"Address NACK(Write): 0x{slave_addr:02X}")
                for b in write_prefix:
                    if not self._i2c_write_byte(b):
                        self._i2c_stop()
                        raise RuntimeError(f"Prefix NACK: 0x{b:02X}")
    
                # Repeated Start + Read phase
                self._i2c_start()
                if not self._i2c_write_byte(addr_r):
                    self._i2c_stop()
                    raise RuntimeError(f"Address NACK(Read): 0x{slave_addr:02X}")
    
                out = bytearray()
                for i in range(read_len):
                    out.append(self._i2c_read_byte(ack=(i < read_len - 1)))
                self._i2c_stop()
    
                hex_str = " ".join(f"{b:02X}" for b in out)
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                self.data_received.emit(f"[{timestamp}] RX <- [0x{slave_addr:02X}] {hex_str}")
                return bytes(out)
            except Exception as e:
                if attempt < attempts - 1:
                    time.sleep(self._i2c_retry_delay_s)
                    continue
                err = f"I2C read error: {e}"
                self._log(f"[Error] {err}")
                self.comm_error.emit(err)
                return None
    def i2c_scan(self, addr_start: int = 0x08, addr_end: int = 0x77) -> List[int]:
        """I2C 버스 스캔 (스레드 안전)
    
        Args:
            addr_start: 스캔 시작 7비트 주소
            addr_end: 스캔 종료 7비트 주소 (포함)
    
        Returns:
            ACK 응답이 있는 7비트 주소 리스트
        """
        if not self._is_connected or self._ft is None:
            return []
    
        locker = QMutexLocker(self._mutex)
        found: List[int] = []
        for addr in range(addr_start, addr_end + 1):
            try:
                addr_w = (addr << 1) | 0
                self._i2c_start()
                ack = self._i2c_write_byte(addr_w)
                self._i2c_stop()
                if ack:
                    found.append(addr)
            except Exception:
                try:
                    self._i2c_stop()
                except Exception:
                    pass
        return found

    def read_gpio_low(self) -> Optional[int]:
        """Read low GPIO bits (ADBUS) in MPSSE mode."""
        if not self._is_connected or self._ft is None:
            return None
        locker = QMutexLocker(self._mutex)
        try:
            self._mpsse_write(bytes([self._MPSSE_READ_BITS_LOW, self._MPSSE_SEND_IMMEDIATE]))
            resp = self._mpsse_read(1)
            return resp[0] if resp else None
        except Exception as e:
            self._log(f"[오류] GPIO 읽기 실패: {e}")
            return None
    
    # ── SMBus 프로토콜 (PI6CG18201 호환) ──

    def smbus_block_write(self, slave_addr: int, command: int, data: bytes) -> bool:
        """SMBus Block Write (스레드 안전)

        형식: [slave_addr_w, command, byte_count, data...]

        Args:
            slave_addr: 7비트 슬레이브 주소
            command: 커맨드 바이트 (시작 레지스터)
            data: 쓸 데이터

        Returns:
            성공 여부
        """
        if len(data) == 0 or len(data) > 0x20:
            raise ValueError(f"SMBus block write 길이 오류: {len(data)}")
        payload = bytes([command, len(data)]) + bytes(data)
        return self.i2c_write(slave_addr, payload)

    def smbus_block_read(self, slave_addr: int, command: int, length: int) -> Optional[bytes]:
        """SMBus Block Read (스레드 안전)

        Args:
            slave_addr: 7비트 슬레이브 주소
            command: 커맨드 바이트 (시작 레지스터)
            length: 읽을 데이터 바이트 수

        Returns:
            읽은 데이터 (byte_count 제외) 또는 None
        """
        if not (1 <= length <= 0x20):
            raise ValueError(f"SMBus block read 길이 오류: {length}")

        raw = self.i2c_read(slave_addr, bytes([command]), length + 1)
        if raw is None or len(raw) < 1:
            return None

        count = raw[0]
        data = raw[1:]
        if count != len(data):
            self._log(
                f"[경고] SMBus count({count}) != 수신 데이터 길이({len(data)})"
            )
        return data[:length]

    # ── 로그 ──

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        full_msg = f"[{timestamp}] {message}"
        logger.info(full_msg)
        self.log_message.emit(full_msg)
