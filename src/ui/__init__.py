"""UI package for TwinRails."""
from .theme import *
from .icon_renderer import render_tray_icon
from .custom_progress import CustomProgressBar
from .flyout_widget import FlyoutWidget
from .taskbar_bar import TaskbarBarWidget
from .settings_window import SettingsWindow
from .tray_widget import TrayWidget
from .dpi import (
    enable_per_monitor_dpi_v2,
    is_per_monitor_v2_active,
    get_dpi_for_system,
    get_dpi_for_window,
    get_dpi_scale_for_window,
    get_system_metrics_for_dpi,
    get_work_area_for_point_or_window,
)

__all__ = [
    "render_tray_icon",
    "CustomProgressBar",
    "FlyoutWidget",
    "TaskbarBarWidget",
    "SettingsWindow",
    "TrayWidget",
    "enable_per_monitor_dpi_v2",
    "is_per_monitor_v2_active",
    "get_dpi_for_system",
    "get_dpi_for_window",
    "get_dpi_scale_for_window",
    "get_system_metrics_for_dpi",
    "get_work_area_for_point_or_window",
]
