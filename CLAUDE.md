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

Optional hooks: `on_tab_activated()` / `on_tab_deactivated()` / `on_channel_changed(channel: str)`

Class-level attributes for capability declaration:
- `REQUIRED_MODE: Optional[str] = None` — protocol mode required by this module (e.g. `"SPI"`, `"I2C"`)
- `REQUIRE_MPSSE: bool = False` — set `True` to disable the module on non-MPSSE channels (FT4232H C/D)

### FtdiManager (`core/ftdi_manager.py`)

Singleton managing the shared FTDI session. All modules share one instance via `FtdiManager.instance()`. Acts as a **facade** coordinating protocol mode switching and channel management — protocol implementations are delegated to `core/i2c_controller.py`, `core/spi_controller.py`, and `core/ftdi_bitbang.py` (all inherit from `core/mpsse_base.py`).

- **Thread safety**: `QMutex` serializes all I2C calls — safe to call from QThread workers
- **Protocol mode**: `set_protocol_mode(mode)` switches between `"I2C"`, `"SPI"`, `"JTAG"`, `"GPIO"`, `"UART"`. A 300ms mode-switch guard prevents I2C operations immediately after switching.
- **I2C API**: `i2c_write(addr, data)`, `i2c_read(addr, write_prefix, read_len)`, `i2c_scan(start, end)`
- **SMBus API**: `smbus_block_write()`, `smbus_block_read()` for PI6CG18201 protocol
- **GPIO API (low byte / ADBUS)**: `read_gpio_low()` → `Optional[int]`, `set_gpio_low(bit, high)`, `set_gpio_masked(mask, value)`
- **GPIO API (high byte / ACBUS)**: `read_gpio_high()` → `Optional[int]`, `set_gpio_high_masked(mask, value)` — accumulates direction & value across calls to preserve state
- **I2C hold policy**: `set_i2c_hold(mask, value)` / `clear_i2c_hold()` — holds D4~D7 at specific values during MPSSE I2C transactions to avoid interference
- **Slave addresses**: 7-bit; the manager performs the `<< 1` shift internally
- **Device scan**: `scan_devices_with_channels()` returns `List[Tuple[str, str, List[str], str]]` — `(base_serial, description, channels, device_type)` 4-tuple
- **Device cache**: `_device_cache` dict stores scan results keyed by serial; `get_device_info(serial?)` returns cached info merged with current connection state
- **Device type inference**: `_infer_device_type(desc, channels)` deduces FT232H/FT2232H/FT4232H from description string and channel count
- **Channel property**: `channel` property returns the currently connected channel letter (A/B/C/D); `set_active_channel(channel)` switches between open channel handles
- **Signals**: `device_connected(str)`, `device_disconnected()`, `device_info_changed(object)`, `active_channel_changed(str)`, `comm_error(str)`, `data_sent(str)`, `data_received(str)`, `log_message(str)`
  - `device_info_changed` emits a dict with `serial`, `channel`, `desc`, `channels`, `device_type`, `connected` on both connect and disconnect — modules use this for auto-configuration
  - `active_channel_changed` emits the new channel letter when `set_active_channel()` succeeds

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

### NACK / Spike Filtering (INA228, INA3221)

Workers filter invalid readings before emitting:
- If `i2c_read` returns `None` (NACK) → skip measurement
- If raw value drops to 0 while previous valid reading was non-zero → skip (0-spike suppression)
- Module `_on_measurement()` also validates with `math.isfinite()` before updating the chart.

### Packaging

`DeviceManagementHub.spec` is a PyInstaller config (windowed app, `res/logo.ico`). Build with:
```bash
pyinstaller DeviceManagementHub.spec
```

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
device-management-hub/
├── main.py                           # MainWindow, dynamic module loader, FTDI connection panel
├── core/
│   ├── ftdi_manager.py               # Singleton FTDI facade (protocol mode + channel mgmt)
│   ├── i2c_controller.py             # MPSSE I2C protocol implementation
│   ├── spi_controller.py             # MPSSE SPI full-duplex + chip-select management
│   ├── mpsse_base.py                 # MpsseBaseController base class (MPSSE sync, init)
│   ├── ftdi_bitbang.py               # Bitbang GPIO controller
│   └── data_recorder.py             # CSV measurement recorder → ~/Documents/DeviceHub/
├── modules/
│   ├── base_module.py                # BaseModule(QWidget) abstract base
│   ├── adc_group/                    # [ACTIVE] Composite tab: ADS1018 + INA228 + INA3221
│   ├── clock_group/                  # [ACTIVE] Composite tab: PI6CG18201 + sidebar nav
│   ├── ftdi_verifier/                # [ACTIVE] Hardware verifier module
│   │   ├── ftdi_verifier_module.py   # FtdiVerifierModule(BaseModule)
│   │   ├── ftdi_chip_specs.py        # Chip/Pin/Channel dataclasses + enums
│   │   ├── pinout_widget.py          # QPainter interactive pinout (CubeIDE style)
│   │   ├── pinmap_controller.py      # Pin mapping utilities
│   │   ├── gpio_controller.py        # GPIO state tracking
│   │   ├── jtag_sequencer_panel.py   # JTAG .csv sequence file import & execution
│   │   ├── jtag_tap_diagram.py       # TAP state machine diagram visualization
│   │   └── verifier_worker.py        # GPIO/I2C/SPI test worker
│   ├── ads1018/                      # [DISABLED] 4-channel SPI ADC
│   ├── ina228/                       # [DISABLED] Power monitor (now inside adc_group)
│   ├── ina3221/                      # [DISABLED] 3-channel power monitor
│   └── pi6cg18201/                   # [DISABLED] Clock generator (now inside clock_group)
└── assets/
    └── dark_theme.qss                # Application-wide dark stylesheet
```

**Disabled modules**: `ads1018`, `ina228`, `ina3221`, `pi6cg18201` have `MODULE_CLASS` commented out in their `__init__.py` — they are instantiated as children inside the composite group modules instead.

### Composite Module (Group) Pattern

`AdcGroupModule` and `ClockGroupModule` use a macOS-style sidebar navigation:
- `QListWidget` sidebar drives a `QStackedWidget` content area
- Sub-modules are instantiated as children; `on_tab_activated/deactivated()`, `on_device_connected/disconnected()`, and `on_channel_changed()` are forwarded to the currently active child (and all children for connect/disconnect events)
- `_on_sidebar_row_changed(row)` deactivates the old module and activates the new one

### DataRecorder (`core/data_recorder.py`)

Saves timestamped CSV measurements to `~/Documents/DeviceHub/`:
- Auto-naming: `{ModuleName}_{YYYYMMDD_HHMMSS}.csv`
- Columns: `[Timestamp, Elapsed_s, ...headers]`; flushes every 100 samples

### MainWindow Signal Notes

- `_on_device_info_changed(info: dict)` in `main.py` extracts `info["channel"]` and forwards it to modules via `module.on_channel_changed(channel)`.
- For multi-channel devices (FT2232H), both channel handles are pre-opened during `open_device()` to maintain persistent handles. `set_active_channel()` switches between them without re-enumeration.

## Language & UI

The application UI text is in Korean. Log messages mix Korean and English. Code comments are in Korean.

**Korean string encoding**: Source files on this project are prone to Korean text corruption when edited on Windows (CP949/EUC-KR editors). If Korean characters appear garbled (e.g. `?꾨젰`, `紐⑤땲??`), use Unicode escape sequences (`\uXXXX`) instead of raw Korean literals in string values to prevent re-corruption. Raw Korean in comments and docstrings is acceptable.
