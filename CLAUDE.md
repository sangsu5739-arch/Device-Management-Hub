# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
python main.py
```

The project root must be in `sys.path` (handled automatically in `main()` via `Path(__file__).parent`). Run from any directory.

## Dependencies

```bash
pip install PySide6 pyqtgraph ftd2xx
```

**Critical**: Both Python 3.9 and 3.12 may be installed. Verify that `pyqtgraph` and `ftd2xx` are installed for the Python interpreter actually used by your environment:

```bash
where python          # check which interpreter is active
python -m pip install pyqtgraph ftd2xx
```

If a module tab disappears silently on startup, pyqtgraph is likely missing for the active interpreter. Check the console for `ModuleNotFoundError`.

## Architecture

### Plugin System

`main.py` dynamically loads device modules at startup using `pkgutil.iter_modules()`. Each subdirectory under `modules/` that has an `__init__.py` exposing `MODULE_CLASS` is automatically added as a tab in `QTabWidget`.

To add a new device module:
1. Create `modules/<device>/` directory
2. Implement `__init__.py` with `MODULE_CLASS = <YourClass>`
3. Implement `<YourClass>` as a subclass of `BaseModule`

### BaseModule (`modules/base_module.py`)

All device modules inherit from `BaseModule(QWidget)`. **Do not add `ABC` as a base** — `QWidget` uses `Shiboken.ObjectType` as its metaclass, which conflicts with `ABCMeta`. Use `@abstractmethod` decorators only.

Required abstract methods:
- `init_ui()` — called once from `__init__`
- `on_device_connected()` / `on_device_disconnected()`
- `start_communication()` / `stop_communication()`
- `update_data()`

Optional hooks: `on_tab_activated()` / `on_tab_deactivated()`

### FtdiManager (`core/ftdi_manager.py`)

Singleton managing the shared FT232H MPSSE I2C session. All modules share one instance via `FtdiManager.instance()`.

- **Thread safety**: `QMutex` serializes all I2C calls — safe to call from QThread workers
- **I2C API**: `i2c_write(addr, data)`, `i2c_read(addr, write_prefix, read_len)`, `i2c_scan(start, end)`
- **SMBus API**: `smbus_block_write()`, `smbus_block_read()` for PI6CG18201 protocol
- **Signals**: `device_connected(str)`, `device_disconnected()`, `comm_error(str)`, `data_sent(str)`, `data_received(str)`, `log_message(str)`
- Slave addresses are 7-bit; the manager performs the `<< 1` shift internally

### QThread Worker Pattern (INA228)

`INA228Worker` runs in a separate `QThread` (via `moveToThread`). The UI thread starts/stops it through `INA228Module.start_communication()` / `stop_communication()`.

```
INA228Module (UI thread)          INA228Worker (worker thread)
  worker.moveToThread(thread)
  thread.started → worker.run()  →  polling loop
  ← measurement_ready(m)            read → convert → emit
  stop(): worker.stop()          →  sets _running = False
           thread.quit() + wait()
```

**GC pitfall**: Local `QWidget` objects created in helper functions may be garbage-collected if not stored as instance attributes. Store container widgets in a list (e.g., `self._metric_containers`) to prevent deletion.

### NACK / Spike Filtering

`INA228Worker` filters invalid readings before emitting:
- If `i2c_read` returns `None` (NACK) → skip measurement, do not emit
- If raw value drops to 0 while the previous valid reading was non-zero → skip (0-spike suppression)

`INA228Module._on_measurement()` also validates with `math.isfinite()` before updating the chart.

## Module Structure Reference

```
Device Management Hub/
├── main.py                     # MainWindow, dynamic module loader
├── core/
│   └── ftdi_manager.py         # Singleton FTDI MPSSE I2C manager
├── modules/
│   ├── base_module.py          # BaseModule(QWidget) abstract base
│   ├── pi6cg18201/             # Clock generator module
│   │   ├── pi6cg_module.py     # PI6CGModule(BaseModule)
│   │   ├── register_map.py     # Register definitions + BitField
│   │   └── clock_visualizer.py # pyqtgraph clock waveform
│   └── ina228/                 # Power monitor module
│       ├── ina228_module.py    # INA228Module(BaseModule)
│       ├── ina228_registers.py # Register enums + conversion utils
│       ├── ina228_worker.py    # QThread polling worker
│       └── power_visualizer.py # pyqtgraph dual-chart (V + A)
└── assets/
    └── dark_theme.qss          # Application-wide dark stylesheet
```
