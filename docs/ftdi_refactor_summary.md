# FTDI Control Refactor Summary

## Overview
This refactor separates MPSSE and Bitbang control into dedicated modules and keeps `FtdiManager` as a facade that coordinates mode switching, device/channel state, and delegates IO operations.

## File Roles

### `core/ftdi_mpsse.py`
**Purpose:** MPSSE-only logic and I2C implementation.

**Key responsibilities:**
- MPSSE initialization and synchronization
- Safe reads with queue wait
- GPIO read/write (low/high byte)
- I2C sequences and transactions:
  - START/STOP
  - read/write byte
  - `i2c_write`, `i2c_read`, `i2c_scan`

### `core/ftdi_bitbang.py`
**Purpose:** Bitbang-only logic.

**Key responsibilities:**
- Enable/disable bitbang mode
- Read pin latch

### `core/ftdi_manager.py`
**Purpose:** Facade and state manager.

**Key responsibilities:**
- Device enumeration and open/close
- Channel selection
- Mode switching (GPIO -> Bitbang, I2C/SPI/JTAG -> MPSSE)
- Guards (mode switch, bitbang I2C block)
- Delegates:
  - I2C ¡æ MPSSE controller
  - Bitbang ¡æ Bitbang controller
  - GPIO writes ¡æ MPSSE low/high or Bitbang
- I2C hold policy for D4~D7 during MPSSE I2C

## Current Call Flow

- **I2C**
  - `FtdiManager.i2c_*()` ¡æ `MpsseController.i2c_*()`

- **GPIO (Bitbang)**
  - `FtdiManager.set_protocol_mode("GPIO")` ¡æ `BitbangController.enable()`

- **GPIO (MPSSE)**
  - `FtdiManager.set_gpio_low()` / `set_gpio_high_masked()` ¡æ `MpsseController.set_bits_low/high()`

## Notes
- FT2232H ACBUS/BCBUS corrected to 8 pins.
- FT232H ACBUS corrected to 8 pins.
- High-byte GPIO writes are supported via `set_bits_high()`.

## Recommendation: Further Encapsulation
It is better to keep **all I2C mechanics inside `MpsseController`** and let `FtdiManager` delegate. This reduces coupling and makes protocol handling more cohesive.

If desired, the remaining I2C guard/checks in `FtdiManager` can be reduced to:
- connection status
- current mode (block if bitbang)
- MPSSE support check
and then delegate to `MpsseController` for the actual transaction.
