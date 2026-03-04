"""
ThemeManager \u2014 singleton dark/light theme controller.

Provides semantic color lookups via ``color(key)`` and emits
``theme_changed(str)`` when the active palette switches.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QApplication


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

DARK_PALETTE: Dict[str, str] = {
    # Backgrounds
    "bg_window":        "#1a1c24",
    "bg_panel":         "#22242e",
    "bg_control":       "#2d3040",
    "bg_input":         "#2d3040",
    "bg_titlebar":      "#2a3040",
    "bg_card":          "#22242e",
    "bg_deep":          "#1a1c22",
    "bg_table":         "#1e2028",
    "bg_table_alt":     "#242630",
    "bg_header":        "#282c38",
    "bg_tab":           "#282c38",
    "bg_tab_selected":  "#2a3448",
    "bg_statusbar":     "#1a1c24",
    "bg_hover":         "#3a3f55",
    "bg_pressed":       "#444960",
    "bg_disabled":      "#1e2130",
    "bg_waveform":      "#0e1018",
    "bg_console":       "#1a1c22",
    "bg_bar":           "#1e2130",
    "bg_bar_fill":      "#3b4458",

    # Text
    "text_primary":     "#c8cdd8",
    "text_secondary":   "#8090aa",
    "text_muted":       "#6a7088",
    "text_label":       "#88a0cc",
    "text_heading":     "#88c0ff",
    "text_accent":      "#c8d2f0",
    "text_disabled":    "#383d50",
    "text_hint":        "#7f8aa4",
    "text_console":     "#a0b0c8",
    "text_info":        "#505870",
    "text_tag":         "#9aa4b8",
    "text_desc":        "#8899bb",

    # Borders
    "border_primary":   "#3a3f50",
    "border_control":   "#4a506a",
    "border_subtle":    "#2a2e3a",
    "border_hover":     "#6a7090",
    "border_titlebar":  "#3a4560",
    "border_deep":      "#1e2030",

    # Accent / Semantic
    "accent_blue":      "#88c0ff",
    "accent_cyan":      "#5ab8d0",
    "accent_brand":     "#4a8898",
    "separator":        "#3a3f50",
    "pipe":             "#3a4058",

    # Status (same in both themes)
    "status_connected":   "#33cc33",
    "status_disconnected":"#cc3333",
    "status_warning":     "#d4a84b",
    "status_scanning":    "#cccc33",

    # Title bar text
    "title_text":       "#b0bcd0",
    "title_version":    "#8fa0b8",
    "title_channel":    "#606880",
    "title_winbtn":     "#6878a0",
    "title_winbtn_hover":"#a0b0d0",
    "title_winbtn_bg_hover": "#2a2e42",

    # Connection bar
    "conn_bar_bg":      "#22262e",
    "conn_bar_border":  "#3a3f50",
    "conn_device_bg":   "#252838",
    "conn_device_border":"#3a3f50",
    "conn_device_text": "#c8cdd8",
    "conn_info":        "#505870",
    "conn_info_active": "#607870",
    "conn_info_scanning":"#607050",

    # Connect/Disconnect buttons
    "btn_connect_bg":     "#0e4a5a",
    "btn_connect_text":   "#a0e8f8",
    "btn_connect_border": "#1a7090",
    "btn_connect_hover":  "#18607a",
    "btn_disconnect_bg":  "#5a1e20",
    "btn_disconnect_text":"#f0a0a0",
    "btn_disconnect_border":"#a03030",
    "btn_disconnect_hover":"#7a2830",

    # Channel buttons
    "btn_ch_bg":          "#252838",
    "btn_ch_text":        "#7888a8",
    "btn_ch_border":      "#3a3f50",
    "btn_ch_active_bg":   "#1d3a4a",
    "btn_ch_active_text": "#70c8e8",
    "btn_ch_active_border":"#2a6880",
    "btn_ch_disabled_bg": "#1e2130",
    "btn_ch_disabled_text":"#383d50",
    "btn_ch_disabled_border":"#2a2e3a",

    # Scan button
    "btn_scan_bg":        "#1e2838",
    "btn_scan_text":      "#70a8c8",
    "btn_scan_border":    "#2a4868",
    "btn_scan_hover":     "#28385a",

    # ACK/NACK LEDs
    "led_ack":          "#2ecc71",
    "led_nack":         "#e74c3c",
    "led_na":           "#6a7088",
    "led_off":          "#2a303b",
    "led_on":           "#80c890",

    # Hold button
    "btn_hold_bg":        "#2a303b",
    "btn_hold_text":      "#cbd5e1",
    "btn_hold_border":    "#3b4458",
    "btn_hold_hover":     "#343d4a",
    "btn_hold_checked_bg":"#1a2d20",
    "btn_hold_checked_text":"#80c890",
    "btn_hold_checked_border":"#2a5a38",
    "btn_hold_checked_hover":"#203828",

    # Graph / pyqtgraph
    "graph_bg":         "#1a1c24",
    "graph_fg":         "#c8cdd8",
    "graph_grid":       "#3a3f50",
    "graph_axis":       "#4a5068",
    "graph_axis_text":  "#8890a0",

    # Message box
    "msgbox_bg":        "#22242e",
    "msgbox_text":      "#c8d0e0",
    "msgbox_btn_bg":    "#1d2d3a",
    "msgbox_btn_text":  "#90d0e8",
    "msgbox_btn_border":"#4a6880",
    "msgbox_btn_hover": "#2a4050",

    # SPI panel
    "spi_waveform_bg":  "#0e1018",
    "spi_waveform_text":"#a0b0c8",
    "spi_waveform_border":"#1e2030",
    "spi_btn_bg":       "#22303e",
    "spi_btn_text":     "#80c0e0",
    "spi_btn_border":   "#2a5070",
    "spi_btn_hover":    "#2a405a",
    "spi_input_bg":     "#1a2030",
    "spi_input_text":   "#b0c8e0",
    "spi_input_border": "#2a4060",
    "spi_result_idle_bg":"#1a2028",
    "spi_result_idle_text":"#6f7a8e",
    "spi_result_idle_border":"#2a3040",
    "spi_result_pass_bg":"#1a2e1a",
    "spi_result_pass_text":"#70e080",
    "spi_result_pass_border":"#2a5a2a",
    "spi_result_fail_bg":"#2e1a1a",
    "spi_result_fail_text":"#f08080",
    "spi_result_fail_border":"#5a2a2a",

    # UART panel
    "uart_console_bg":  "#0e1018",
    "uart_console_text":"#b0c0d0",
    "uart_console_border":"#1e2030",
    "uart_input_bg":    "#1a2028",
    "uart_input_text":  "#c8d8e8",
    "uart_input_border":"#2a3848",
    "uart_send_bg":     "#1a3048",
    "uart_send_text":   "#80c8e8",
    "uart_send_border": "#2a5878",
    "uart_send_hover":  "#224060",
    "uart_open_connected_bg":  "#1a3020",
    "uart_open_connected_text":"#80d890",
    "uart_open_connected_border":"#2a5838",
    "uart_open_disconnected_bg":  "#301a1a",
    "uart_open_disconnected_text":"#d08080",
    "uart_open_disconnected_border":"#582a2a",

    # GPIO panel
    "gpio_poll_bg":     "#22303e",
    "gpio_poll_text":   "#80c0e0",
    "gpio_poll_border": "#2a5070",
    "gpio_backend_mpsse_bg": "#1a2838",
    "gpio_backend_mpsse_text":"#60a0d0",
    "gpio_backend_mpsse_border":"#2a4060",
    "gpio_backend_bitbang_bg":"#2a2818",
    "gpio_backend_bitbang_text":"#c0a860",
    "gpio_backend_bitbang_border":"#5a4828",
    "gpio_poll_running_bg":  "#1a2e1a",
    "gpio_poll_running_text":"#60c880",
    "gpio_poll_running_border":"#2a5a2a",
    "gpio_poll_stopped_bg":  "#2a1a1a",
    "gpio_poll_stopped_text":"#c08080",
    "gpio_poll_stopped_border":"#5a2a2a",
    "gpio_toggle_bg":   "#1e2838",
    "gpio_toggle_text": "#80b8d0",
    "gpio_toggle_border":"#2a4868",
    "gpio_toggle_hover":"#28385a",
    "gpio_pin_name":    "#00d2ff",

    # JTAG
    "jtag_btn_bg":      "#22303e",
    "jtag_btn_text":    "#80c0e0",
    "jtag_btn_border":  "#2a5070",
    "jtag_btn_hover":   "#2a405a",

    # I2C buttons
    "i2c_scan_bg":      "#22303e",
    "i2c_scan_text":    "#80c0e0",
    "i2c_scan_border":  "#2a5070",
    "i2c_scan_hover":   "#2a405a",
    "i2c_test_bg":      "#22303e",
    "i2c_test_text":    "#80c0e0",
    "i2c_test_border":  "#2a5070",
    "i2c_test_hover":   "#2a405a",
    "i2c_ack_bg":       "#1a2028",
    "i2c_ack_border":   "#2a3040",
    "i2c_ack_text":     "#6f7a8e",

    # Checkbox special
    "cb_live_bg":         "#2d3040",
    "cb_live_border":     "#4a506a",
    "cb_live_checked_bg": "#2a8c5a",
    "cb_live_checked_border":"#3aac6a",

    # Auto-range button
    "btn_auto_bg":      "#1a2838",
    "btn_auto_text":    "#70a8c8",
    "btn_auto_border":  "#2a4868",
    "btn_auto_hover":   "#28385a",
    "btn_auto_checked_bg":"#1a3a2a",
    "btn_auto_checked_text":"#80d898",
    "btn_auto_checked_border":"#2a6838",

    # Metric container
    "metric_bg":        "#22242e",

    # ADS1018 specific
    "ads_ch_frame_bg":  "#1a1c28",
    "ads_ch_frame_border":"#2a2e40",
    "ads_vi_btn_bg":    "#282a3a",
    "ads_vi_btn_text":  "#8890a0",
    "ads_vi_btn_border":"#3a4060",
    "ads_config_bg":    "#0e1018",
    "ads_config_text":  "#c8d0e0",
    "ads_config_border":"#1e2030",
    "ads_log_bg":       "#0e1018",
    "ads_log_text":     "#8890a0",
    "ads_log_border":   "#1e2030",
    "ads_led_off_bg":   "#2a303b",
    "ads_led_off_border":"#1a1c28",

    # SS readback badge (pi6cg)
    "ss_badge_bg":      "#2a303b",
    "ss_badge_text":    "#9aa4b8",
    "ss_badge_border":  "#3a3f50",

    # Pinout widget
    "pinout_bg":        "#2a3040",
    "pinout_chip_top":  "#1A1A1A",
    "pinout_chip_mid":  "#222222",
    "pinout_chip_bot":  "#2A2A2A",
    "pinout_chip_border":"#3a3a3a",
    "pinout_notch":     "#252c3a",
    "pinout_chip_text": "#d0d8e8",
    "pinout_pin_text_dimmed":"#808898",
    "pinout_pin_line":  "#606878",
    "pinout_tooltip_key":"#aabbdd",
    "pinout_tooltip_val":"#ffffff",
}


LIGHT_PALETTE: Dict[str, str] = {
    # ── Backgrounds ──────────────────────────────────────────
    # Layered system: window → panel → card → control → input
    "bg_window":        "#e8ecf2",      # Warm blue-gray base
    "bg_panel":         "#f4f6f9",      # Slightly off-white panels
    "bg_control":       "#dce2eb",      # Buttons / interactive controls
    "bg_input":         "#ffffff",      # Editable fields (crisp white)
    "bg_titlebar":      "#d0d8e5",      # Title bar - deeper
    "bg_card":          "#f0f3f7",      # Card containers
    "bg_deep":          "#d5dbe6",      # Deep nested / inset areas
    "bg_table":         "#fafbfd",      # Clean table background
    "bg_table_alt":     "#f0f3f7",      # Alternating row
    "bg_header":        "#d5dbe6",      # Table headers - distinct
    "bg_tab":           "#dce2eb",      # Inactive tabs
    "bg_tab_selected":  "#f4f6f9",      # Active tab
    "bg_statusbar":     "#d5dbe6",
    "bg_hover":         "#c8d0de",      # Hover state
    "bg_pressed":       "#b0bace",      # Press state
    "bg_disabled":      "#e0e4ea",
    "bg_waveform":      "#f4f6f9",      # Chart background
    "bg_console":       "#f4f6f9",      # Console/log area
    "bg_bar":           "#d0d8e5",
    "bg_bar_fill":      "#8898b0",

    # ── Text ─────────────────────────────────────────────────
    "text_primary":     "#1e2a3a",      # Near-black for readability
    "text_secondary":   "#445566",      # Medium contrast labels
    "text_muted":       "#607080",      # Dimmed / placeholders
    "text_label":       "#2d5a8e",      # Blue-accent labels
    "text_heading":     "#1e3a5f",      # Strong section titles
    "text_accent":      "#1a3050",      # Darkest brand accent
    "text_disabled":    "#98a8b8",
    "text_hint":        "#607080",
    "text_console":     "#2a3848",
    "text_info":        "#445566",
    "text_tag":         "#3a5070",
    "text_desc":        "#445566",

    # ── Borders ──────────────────────────────────────────────
    "border_primary":   "#b8c4d4",      # Standard border
    "border_control":   "#94a4b8",      # Button/input outlines (visible)
    "border_subtle":    "#d0d8e2",      # Soft dividers
    "border_hover":     "#6880a0",      # Hovered control border
    "border_titlebar":  "#a0aec0",
    "border_deep":      "#b8c4d4",

    # ── Accent / Semantic ────────────────────────────────────
    "accent_blue":      "#2d6caa",
    "accent_cyan":      "#0090a8",
    "accent_brand":     "#2a4060",
    "separator":        "#b8c4d4",
    "pipe":             "#94a4b8",

    # ── Status ───────────────────────────────────────────────
    "status_connected":   "#2f855a",
    "status_disconnected":"#c53030",
    "status_warning":     "#c85000",
    "status_scanning":    "#b8860b",

    # ── Title bar ────────────────────────────────────────────
    "title_text":       "#2a3848",
    "title_version":    "#607080",
    "title_channel":    "#445566",
    "title_winbtn":     "#445566",
    "title_winbtn_hover":"#1e2a3a",
    "title_winbtn_bg_hover": "#c0c8d8",

    # ── Connection bar ───────────────────────────────────────
    "conn_bar_bg":      "#d5dbe6",
    "conn_bar_border":  "#b8c4d4",
    "conn_device_bg":   "#f4f6f9",
    "conn_device_border":"#94a4b8",
    "conn_device_text": "#1e2a3a",
    "conn_info":        "#445566",
    "conn_info_active": "#286848",
    "conn_info_scanning":"#8a6010",

    # ── Connect / Disconnect buttons ─────────────────────────
    "btn_connect_bg":     "#d8f5ee",
    "btn_connect_text":   "#1a4040",
    "btn_connect_border": "#68d0b8",
    "btn_connect_hover":  "#b0eadb",
    "btn_disconnect_bg":  "#fde8e8",
    "btn_disconnect_text":"#6a2020",
    "btn_disconnect_border":"#e88888",
    "btn_disconnect_hover":"#fbc8c8",

    # ── Channel buttons ──────────────────────────────────────
    "btn_ch_bg":          "#dce2eb",
    "btn_ch_text":        "#445566",
    "btn_ch_border":      "#b8c4d4",
    "btn_ch_active_bg":   "#a8d0f0",
    "btn_ch_active_text": "#1a3858",
    "btn_ch_active_border":"#5098d0",
    "btn_ch_disabled_bg": "#e0e4ea",
    "btn_ch_disabled_text":"#98a8b8",
    "btn_ch_disabled_border":"#d0d8e2",

    # ── Scan button ──────────────────────────────────────────
    "btn_scan_bg":        "#d0e4f8",
    "btn_scan_text":      "#1e4878",
    "btn_scan_border":    "#78a8d8",
    "btn_scan_hover":     "#b8d4f0",

    # ── ACK/NACK LEDs ────────────────────────────────────────
    "led_ack":          "#2f855a",
    "led_nack":         "#c53030",
    "led_na":           "#94a4b8",
    "led_off":          "#d0d8e2",
    "led_on":           "#38a060",

    # ── Hold button ──────────────────────────────────────────
    "btn_hold_bg":        "#dce2eb",
    "btn_hold_text":      "#3a5070",
    "btn_hold_border":    "#b8c4d4",
    "btn_hold_hover":     "#c8d0de",
    "btn_hold_checked_bg":"#b8e8c8",
    "btn_hold_checked_text":"#1a4830",
    "btn_hold_checked_border":"#58b878",
    "btn_hold_checked_hover":"#98d8a8",

    # ── Graph / pyqtgraph ────────────────────────────────────
    "graph_bg":         "#f4f6f9",
    "graph_fg":         "#1e2a3a",
    "graph_grid":       "#d0d8e2",
    "graph_axis":       "#94a4b8",
    "graph_axis_text":  "#445566",

    # ── Message box ──────────────────────────────────────────
    "msgbox_bg":        "#f0f3f7",
    "msgbox_text":      "#2a3848",
    "msgbox_btn_bg":    "#dce2eb",
    "msgbox_btn_text":  "#1e4878",
    "msgbox_btn_border":"#b8c4d4",
    "msgbox_btn_hover": "#c8d0de",

    # ── SPI panel ────────────────────────────────────────────
    "spi_waveform_bg":  "#f0f3f7",
    "spi_waveform_text":"#1e2a3a",
    "spi_waveform_border":"#b8c4d4",
    "spi_btn_bg":       "#d0e4f8",
    "spi_btn_text":     "#1e4878",
    "spi_btn_border":   "#78a8d8",
    "spi_btn_hover":    "#b8d4f0",
    "spi_input_bg":     "#ffffff",
    "spi_input_text":   "#1e2a3a",
    "spi_input_border": "#94a4b8",
    "spi_result_idle_bg":"#e8ecf2",
    "spi_result_idle_text":"#607080",
    "spi_result_idle_border":"#d0d8e2",
    "spi_result_pass_bg":"#d8f0e0",
    "spi_result_pass_text":"#1a4830",
    "spi_result_pass_border":"#70c888",
    "spi_result_fail_bg":"#f8e0e0",
    "spi_result_fail_text":"#6a2020",
    "spi_result_fail_border":"#d88080",

    # ── UART panel ───────────────────────────────────────────
    "uart_console_bg":  "#fafbfd",
    "uart_console_text":"#1e2a3a",
    "uart_console_border":"#b8c4d4",
    "uart_input_bg":    "#ffffff",
    "uart_input_text":  "#1e2a3a",
    "uart_input_border":"#94a4b8",
    "uart_send_bg":     "#d0e4f8",
    "uart_send_text":   "#1e4878",
    "uart_send_border": "#78a8d8",
    "uart_send_hover":  "#b8d4f0",
    "uart_open_connected_bg":  "#d8f0e0",
    "uart_open_connected_text":"#1a4830",
    "uart_open_connected_border":"#58b878",
    "uart_open_disconnected_bg":  "#f8e0e0",
    "uart_open_disconnected_text":"#6a2020",
    "uart_open_disconnected_border":"#d08080",

    # ── GPIO panel ───────────────────────────────────────────
    "gpio_poll_bg":     "#d0e4f8",
    "gpio_poll_text":   "#1e4878",
    "gpio_poll_border": "#78a8d8",
    "gpio_backend_mpsse_bg": "#d0e4f8",
    "gpio_backend_mpsse_text":"#1e4878",
    "gpio_backend_mpsse_border":"#78a8d8",
    "gpio_backend_bitbang_bg":"#f8f0d0",
    "gpio_backend_bitbang_text":"#705810",
    "gpio_backend_bitbang_border":"#c8a840",
    "gpio_poll_running_bg":  "#d8f0e0",
    "gpio_poll_running_text":"#1a4830",
    "gpio_poll_running_border":"#58b878",
    "gpio_poll_stopped_bg":  "#f8e0e0",
    "gpio_poll_stopped_text":"#6a2020",
    "gpio_poll_stopped_border":"#d08080",
    "gpio_toggle_bg":   "#dce2eb",
    "gpio_toggle_text": "#1e4878",
    "gpio_toggle_border":"#b8c4d4",
    "gpio_toggle_hover":"#c8d0de",
    "gpio_pin_name":    "#2d6caa",

    # ── JTAG ─────────────────────────────────────────────────
    "jtag_btn_bg":      "#d0e4f8",
    "jtag_btn_text":    "#1e4878",
    "jtag_btn_border":  "#78a8d8",
    "jtag_btn_hover":   "#b8d4f0",

    # ── I2C buttons ──────────────────────────────────────────
    "i2c_scan_bg":      "#d0e4f8",
    "i2c_scan_text":    "#1e4878",
    "i2c_scan_border":  "#78a8d8",
    "i2c_scan_hover":   "#b8d4f0",
    "i2c_test_bg":      "#d0e4f8",
    "i2c_test_text":    "#1e4878",
    "i2c_test_border":  "#78a8d8",
    "i2c_test_hover":   "#b8d4f0",
    "i2c_ack_bg":       "#e8ecf2",
    "i2c_ack_border":   "#d0d8e2",
    "i2c_ack_text":     "#607080",

    # ── Checkbox special (live mode) ─────────────────────────
    "cb_live_bg":         "#dce2eb",
    "cb_live_border":     "#b8c4d4",
    "cb_live_checked_bg": "#2f855a",
    "cb_live_checked_border":"#267848",

    # ── Auto-range button ────────────────────────────────────
    "btn_auto_bg":      "#dce2eb",
    "btn_auto_text":    "#1e4878",
    "btn_auto_border":  "#b8c4d4",
    "btn_auto_hover":   "#c8d0de",
    "btn_auto_checked_bg":"#b8e8c8",
    "btn_auto_checked_text":"#1a4830",
    "btn_auto_checked_border":"#58b878",

    # ── Metric container ─────────────────────────────────────
    "metric_bg":        "#edf0f5",

    # ── ADS1018 specific ─────────────────────────────────────
    "ads_ch_frame_bg":  "#edf0f5",
    "ads_ch_frame_border":"#d0d8e2",
    "ads_vi_btn_bg":    "#dce2eb",
    "ads_vi_btn_text":  "#3a5070",
    "ads_vi_btn_border":"#b8c4d4",
    "ads_config_bg":    "#f4f6f9",
    "ads_config_text":  "#1e2a3a",
    "ads_config_border":"#b8c4d4",
    "ads_log_bg":       "#f4f6f9",
    "ads_log_text":     "#445566",
    "ads_log_border":   "#b8c4d4",
    "ads_led_off_bg":   "#d0d8e2",
    "ads_led_off_border":"#b8c4d4",

    # ── SS readback badge (pi6cg) ────────────────────────────
    "ss_badge_bg":      "#dce2eb",
    "ss_badge_text":    "#3a5070",
    "ss_badge_border":  "#b8c4d4",

    # ── Pinout widget (original dark chip for contrast) ─────
    "pinout_bg":        "#2a3040",      # Original dark background
    "pinout_chip_top":  "#1A1A1A",
    "pinout_chip_mid":  "#222222",      # Original black chip body
    "pinout_chip_bot":  "#2A2A2A",
    "pinout_chip_border":"#3a3a3a",     # Original border
    "pinout_notch":     "#252c3a",
    "pinout_chip_text": "#D1D1D1",      # Light text on black chip
    "pinout_pin_text_dimmed":"#3a3f50",
    "pinout_pin_line":  "#606878",
    "pinout_tooltip_key":"#aabbdd",
    "pinout_tooltip_val":"#ffffff",
}


class ThemeManager(QObject):
    """Singleton theme manager for dark / light switching."""

    theme_changed = Signal(str)  # "dark" | "light"

    _instance: Optional["ThemeManager"] = None

    @classmethod
    def instance(cls) -> "ThemeManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        self._settings = QSettings("UniversalDeviceStudio", "Theme")
        self._current: str = self._settings.value("theme", "dark")
        self._palettes = {"dark": DARK_PALETTE, "light": LIGHT_PALETTE}

    # -- Public API ---------------------------------------------------------

    @property
    def current_theme(self) -> str:
        return self._current

    def is_dark(self) -> bool:
        return self._current == "dark"

    def color(self, key: str) -> str:
        """Return the hex color for *key* in the active palette."""
        return self._palettes[self._current].get(key, "#ff00ff")

    def toggle(self) -> None:
        self.set_theme("light" if self._current == "dark" else "dark")

    def set_theme(self, theme: str) -> None:
        if theme == self._current:
            return
        self._current = theme
        self._settings.setValue("theme", theme)
        self._apply_qss()
        self.theme_changed.emit(theme)

    def initial_apply(self) -> None:
        """Load QSS for the persisted theme.  Call once at startup."""
        self._apply_qss()

    # -- Internal -----------------------------------------------------------

    def _apply_qss(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        filename = "dark_theme.qss" if self._current == "dark" else "light_theme.qss"
        qss_path = Path(__file__).parent.parent / "assets" / filename
        if qss_path.exists():
            app.setStyleSheet(qss_path.read_text(encoding="utf-8"))
