"""
GPIO controller for FTDI Verifier.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QColor

from modules.ftdi_verifier.ftdi_chip_specs import PinFunction

if TYPE_CHECKING:
    from modules.ftdi_verifier.ftdi_verifier_module import FtdiVerifierModule


class GpioController:
    def __init__(self, module: "FtdiVerifierModule") -> None:
        self._m = module

    def toggle_selected(self, high: bool) -> None:
        if self._m._ftdi.is_connected and self._m._ftdi.channel != self._m._current_channel:
            self._m._gpio_toggle_btn.setChecked(False)
            self._m._append_log("Channel mismatch: GPIO control is only available on the connected channel.")
            return
        pin_num = self._m._pinout.get_selected_pin()
        if pin_num < 0:
            return
        pin = self._m._current_chip.pins.get(pin_num) if self._m._current_chip else None
        if pin is None or pin.mpsse_bit is None:
            self._m._append_log("GPIO write failed: invalid pin mapping.")
            return
        # GPIO tab: auto switch backend per pin (low byte -> bitbang, high byte -> mpsse)
        if pin.name.startswith(("AC", "BC")):
            if not self._m._ftdi.set_gpio_backend("mpsse"):
                self._m._append_log("GPIO write failed: MPSSE not available on this channel.")
                return
            self._m._set_gpio_backend_label("MPSSE")
        else:
            self._m._ftdi.set_gpio_backend("bitbang")
            self._m._set_gpio_backend_label("BITBANG")
        if self._m._gpio_poll_btn.isChecked():
            self._m._gpio_poll_btn.setChecked(False)
        state_str = "HIGH" if high else "LOW"
        self._m._pinout.set_pin_state(pin_num, high)
        self._m._gpio_states[pin_num] = high
        try:
            if pin.name.startswith(("AC", "BC")):
                mask = (1 << pin.mpsse_bit)
                value = mask if high else 0
                self._m._ftdi.set_gpio_high_masked(mask, value)
            else:
                self._m._ftdi.set_gpio_low(pin.mpsse_bit, high)
        except Exception:
            pass
        readback = self._m._ftdi.read_gpio_high() if pin.name.startswith(("AC", "BC")) else self._m._ftdi.read_gpio_low()
        if readback is not None:
            actual = bool(readback & (1 << pin.mpsse_bit))
            if actual != high:
                self._m._append_log(
                    f"[GPIO] Readback mismatch on D{pin_num}: set {state_str}, "
                    f"read {'HIGH' if actual else 'LOW'}"
                )
            else:
                self._m._append_log(f"[GPIO] Readback OK on D{pin_num}: {state_str}")
        self._m._gpio_toggle_btn.setText("GPIO: HIGH" if high else "GPIO: LOW")
        self.refresh_controls(pin_selected=True, is_gpio=True)
        self._m._pin_desc_label.setText(
            (self._m._pin_desc_label.text().split("\n")[0]) + f"\nCurrent status: {'HIGH' if high else 'LOW'}"
        )
        name = pin.name if pin else f"#{pin_num}"
        self._m._append_log(f"[GPIO] {name} -> {state_str}")
        self._m._refresh_gpio_table()

    def on_gpio_updated(self, state: object) -> None:
        if self._m._gpio_table is None or self._m._current_chip is None:
            return
        pin_states = getattr(state, "pin_states", None) or {}
        mapped = {}
        if hasattr(self._m, "_gpio_bit_to_pin"):
            for bit, val in pin_states.items():
                pin_num = self._m._gpio_bit_to_pin.get(bit)
                if pin_num is not None:
                    mapped[pin_num] = val
        for row in range(self._m._gpio_table.rowCount()):
            pin_num_item = self._m._gpio_table.item(row, 0)
            if pin_num_item is None:
                continue
            try:
                pin_num = int(pin_num_item.text().lstrip("D"))
            except ValueError:
                continue
            level = mapped.get(pin_num, self._m._gpio_states.get(pin_num, False))
            level_item = self._m._gpio_table.item(row, 4)
            if level_item:
                level_item.setText("1" if level else "0")
                if self._m._gpio_poll_btn.isChecked():
                    level_item.setForeground(QColor("#66ff99"))
                else:
                    level_item.setForeground(QColor("#c8d2f0"))
            if pin_num in mapped:
                self._m._pinout.set_pin_state(pin_num, mapped[pin_num])
                self._m._gpio_states[pin_num] = mapped[pin_num]
                if pin_num == self._m._pinout.get_selected_pin():
                    self._m._gpio_toggle_btn.blockSignals(True)
                    self._m._gpio_toggle_btn.setChecked(mapped[pin_num])
                    self._m._gpio_toggle_btn.setText("GPIO: HIGH" if mapped[pin_num] else "GPIO: LOW")
                    self._m._gpio_toggle_btn.blockSignals(False)

    def refresh_controls(self, pin_selected: bool | None = None, is_gpio: bool | None = None) -> None:
        if self._m._current_chip is None:
            self._m._gpio_toggle_btn.setEnabled(False)
            self._m._gpio_poll_btn.setEnabled(False)
            self._m._set_bitbang_controls_enabled(False)
            return

        ch_spec = self._m._current_chip.channels.get(self._m._current_channel)
        has_mpsse = bool(ch_spec and ch_spec.supports_mpsse)
        connected = self._m._ftdi.is_connected
        ch_match = (not connected) or (self._m._ftdi.channel == self._m._current_channel)

        if pin_selected is None:
            pin_selected = self._m._pinout.get_selected_pin() >= 0

        if is_gpio is None:
            is_gpio = False
            if pin_selected:
                pin_num = self._m._pinout.get_selected_pin()
                if self._m._current_chip and pin_num in self._m._current_chip.pins:
                    pin = self._m._current_chip.pins[pin_num]
                    active = self._m._pinout._pin_active_funcs.get(pin.number, pin.default_function)
                    is_gpio = active in (PinFunction.GPIO_OUT, PinFunction.GPIO_IN)

        mode = self._m._proto_mode_combo.currentText() if hasattr(self._m, "_proto_mode_combo") else ""
        base_ok = connected and ch_match and (has_mpsse or mode == "GPIO")
        poll_checked = self._m._gpio_poll_btn.isChecked()

        if not base_ok:
            if poll_checked:
                self._m._gpio_poll_btn.blockSignals(True)
                self._m._gpio_poll_btn.setChecked(False)
                self._m._gpio_poll_btn.blockSignals(False)
                self._m._gpio_poll_btn.setText("Start Polling")
                self._m._stop_worker()
            self._m._gpio_toggle_btn.setEnabled(False)
            self._m._gpio_poll_btn.setEnabled(False)
            self._m._set_bitbang_controls_enabled(False)
            return

        if poll_checked:
            self._m._gpio_poll_btn.setEnabled(True)
            self._m._gpio_toggle_btn.setEnabled(False)
            self._m._set_bitbang_controls_enabled(mode == "GPIO")
            return

        allow = pin_selected and (is_gpio or mode == "GPIO")
        self._m._gpio_toggle_btn.setEnabled(allow)
        self._m._gpio_poll_btn.setEnabled(base_ok)
        self._m._set_bitbang_controls_enabled(mode == "GPIO")
