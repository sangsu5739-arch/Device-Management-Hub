# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
python main.py
```

The project root is automatically added to `sys.path` in `main()`. Run from any directory.

## Dependencies

```bash
pip install PySide6 pyqtgraph ftd2xx pyserial
```

**Critical**: Multiple Python versions may coexist. Verify packages are installed for the active interpreter:

```bash
where python
python -m pip install pyqtgraph ftd2xx pyserial
```

If a module tab disappears silently on startup, a dependency is likely missing for the active interpreter. Check the console for `ModuleNotFoundError`. `pyserial` is needed for UART port scanning in the FTDI Verifier module.

## Architecture

### Plugin System

`main.py` dynamically loads device modules at startup using `pkgutil.iter_modules()`. Each subdirectory under `modules/` with an `__init__.py` exposing `MODULE_CLASS` is automatically added as a tab in `QTabWidget`.

To add a new device module:
1. Create `modules/<device>/` directory
2. Implement `__init__.py` with `MODULE_CLASS = <YourClass>`
3. Subclass `BaseModule` and implement all abstract methods

### BaseModule (`modules/base_module.py`)

All device modules inherit from `BaseModule(QWidget)`. **Do not add `ABC` as a base** — `QWidget` uses `Shiboken.ObjectType` as its metaclass, which conflicts with `ABCMeta`. Use `@abstractmethod` decorators only.

Required abstract methods:
- `init_ui()` — called once from `__init__`
- `on_device_connected()` / `on_device_disconnected()`
- `start_communication()` / `stop_communication()`
- `update_data()`

Optional hooks: `on_tab_activated()` / `on_tab_deactivated()`

### FtdiManager (`core/ftdi_manager.py`)

Singleton managing the shared FTDI MPSSE I2C session. All modules share one instance via `FtdiManager.instance()`.

- **Thread safety**: `QMutex` serializes all I2C calls — safe to call from QThread workers
- **I2C API**: `i2c_write(addr, data)`, `i2c_read(addr, write_prefix, read_len)`, `i2c_scan(start, end)`
- **SMBus API**: `smbus_block_write()`, `smbus_block_read()` for PI6CG18201 protocol
- **GPIO API**: `read_gpio_low()` — reads ADBUS low byte in MPSSE mode, returns `Optional[int]`
- **Slave addresses**: 7-bit; the manager performs the `<< 1` shift internally
- **Device scan**: `scan_devices_with_channels()` returns `List[Tuple[str, str, List[str], str]]` — `(base_serial, description, channels, device_type)` 4-tuple
- **Device cache**: `_device_cache` dict stores scan results keyed by serial; `get_device_info(serial?)` returns cached info merged with current connection state
- **Device type inference**: `_infer_device_type(desc, channels)` deduces FT232H/FT2232H/FT4232H from description string and channel count
- **Channel property**: `channel` property returns the currently connected channel letter (A/B/C/D)
- **Signals**: `device_connected(str)`, `device_disconnected()`, `device_info_changed(object)`, `comm_error(str)`, `data_sent(str)`, `data_received(str)`, `log_message(str)`
  - `device_info_changed` emits a dict with `serial`, `channel`, `desc`, `channels`, `device_type`, `connected` on both connect and disconnect — modules use this for auto-configuration

### QThread Worker Pattern

Workers run in separate `QThread` via `moveToThread`. The UI thread starts/stops them.

```
Module (UI thread)                Worker (worker thread)
  worker.moveToThread(thread)
  thread.started → worker.run()  →  polling loop
  ← signal(data)                    read → convert → emit
  stop(): worker.stop()          →  sets _running = False
           thread.quit() + wait()
```

**GC pitfall**: Local `QWidget` objects created in helper functions may be garbage-collected if not stored as instance attributes. Store container widgets in a list (e.g., `self._metric_containers`) to prevent deletion.

### NACK / Spike Filtering (INA228)

`INA228Worker` filters invalid readings before emitting:
- If `i2c_read` returns `None` (NACK) → skip measurement
- If raw value drops to 0 while previous valid reading was non-zero → skip (0-spike suppression)

`INA228Module._on_measurement()` also validates with `math.isfinite()` before updating the chart.

### FTDI Verifier Module

CubeIDE-style interactive hardware verifier. Key design patterns:

**Declarative chip specs** (`ftdi_chip_specs.py`): `ChipSpec`, `PinSpec`, `ChannelSpec` dataclasses define FT232H/FT2232H/FT4232H pin layouts. `PIN_COLORS` and `PROTOCOL_COLORS` dicts control rendering.

**Auto-detection**: On FTDI connect, `on_device_connected()` calls `self._ftdi.get_device_info()` and auto-applies chip model + channel via `_apply_chip_and_channel()`.

**QPainter pinout** (`pinout_widget.py`): Custom-painted chip body with interactive pins — hover, click, channel dimming, per-function color coding.

**Protocol tab system**: Left control panel has a mode selector combo (`_proto_mode_combo`) that drives a `QTabWidget` (`_proto_tabs`) switching between I2C / SPI / JTAG / UART / GPIO sub-panels. The tab bar is hidden (`tabBar().setEnabled(False)`) — switching is controlled by the combo only via `_apply_protocol_mode()`.

**I2C panel specifics**:
- `_i2c_addr_combo` — editable QComboBox; auto-populated from scan results, supports manual hex entry
- `_i2c_ack_led` — colored QLabel badge showing ACK (green) / NACK (red) / N/A state
- Scan results populate both the table and the address combo

**UART panel** (GUI-only, no hardware backend yet): COM port auto-detect via `pyserial.tools.list_ports`, baudrate/data bits/parity/stop bits/flow control combos, console QTextEdit, send QLineEdit.

**GPIO panel**:
- `_gpio_states: dict[int, bool]` tracks software-written pin states
- `_gpio_table` — 5-column table (Pin / Name / Mode / Direction / Level) refreshed by `_refresh_gpio_table()`
- `_gpio_bit_to_pin: dict[int, int]` maps MPSSE bit index to pin number for hardware read-back
- `_on_gpio_updated()` receives `GpioState` from worker and merges hardware read-back with `_gpio_states`
- GPIO polling uses `FtdiManager.read_gpio_low()` (via `VerifierWorker`)
- `_gpio_toggle_btn` label dynamically shows "GPIO: HIGH" / "GPIO: LOW"

**Channel validation**: GPIO control and pin interaction check `self._ftdi.channel != self._current_channel` to prevent cross-channel operations. FT4232H channels C/D have `supports_mpsse = False` — I2C/SPI/JTAG controls are disabled.

### MainWindow Connection Flow

1. Scan: `FtdiManager.scan_devices_with_channels()` → populate device combo with 4-tuple data
2. Select device → `_on_device_selected` populates channel combo
3. Connect: validates device + channel selection (multi-channel devices require explicit channel pick), calls `FtdiManager.open_device(serial, channel)`
4. `device_connected` signal → `_on_hw_connected` → notifies all modules via `module.on_device_connected()`
5. Disconnect: stops all module communication, calls `close_device()`, shows disconnection dialog
6. Close event: confirmation dialog (default button = "취소"), auto-disconnects if still connected

## Module Structure

```
Device Management Hub/
├── main.py                           # MainWindow, dynamic module loader, FTDI connection panel
├── core/
│   └── ftdi_manager.py               # Singleton FTDI MPSSE I2C manager
├── modules/
│   ├── base_module.py                # BaseModule(QWidget) abstract base
│   ├── pi6cg18201/                   # Clock generator module
│   │   ├── pi6cg_module.py           # PI6CGModule(BaseModule)
│   │   ├── register_map.py           # Register definitions + BitField
│   │   └── clock_visualizer.py       # pyqtgraph clock waveform
│   ├── ina228/                       # Power monitor module
│   │   ├── ina228_module.py          # INA228Module(BaseModule)
│   │   ├── ina228_registers.py       # Register enums + conversion utils
│   │   ├── ina228_worker.py          # QThread polling worker
│   │   └── power_visualizer.py       # pyqtgraph dual-chart (V + A)
│   └── ftdi_verifier/                # Hardware verifier module
│       ├── ftdi_verifier_module.py   # FtdiVerifierModule(BaseModule)
│       ├── ftdi_chip_specs.py        # Chip/Pin/Channel dataclasses + enums
│       ├── pinout_widget.py          # QPainter interactive pinout (CubeIDE style)
│       └── verifier_worker.py        # GPIO/I2C/SPI test worker
└── assets/
    └── dark_theme.qss                # Application-wide dark stylesheet
```

## Language & UI

The application UI text is in Korean. Log messages mix Korean and English. Code comments are in Korean.
