"""
Windows Per-Monitor V2 DPI awareness and scaling helpers.
Enables Per-Monitor V2 DPI awareness (Windows 10 Creators Update 1703+)
with graceful fallbacks for older Windows versions, plus utilities for
querying per-window/system DPI and multi-monitor work areas.
"""

import ctypes
from ctypes import wintypes
from typing import Tuple, Optional, Union

# Win32 DPI awareness context handles (SetProcessDpiAwarenessContext)
DPI_AWARENESS_CONTEXT_UNAWARE = -1
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = -2
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3      # Per-Monitor V1 (Win 8.1+)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4   # Per-Monitor V2 (Win 10 1703+)
DPI_AWARENESS_CONTEXT_UNAWARE_GDISCALED = -5

# Win32 Process DPI awareness modes (SetProcessDpiAwareness in Shcore.dll)
PROCESS_DPI_UNAWARE = 0
PROCESS_SYSTEM_DPI_AWARE = 1
PROCESS_PER_MONITOR_DPI_AWARE = 2

# Win32 constants
DEFAULT_DPI = 96
MONITOR_DEFAULTTONEAREST = 2
SPI_GETWORKAREA = 0x0030


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def enable_per_monitor_dpi_v2() -> str:
    """
    Enable Per-Monitor V2 DPI awareness for the process.
    
    Tries user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    first. If unsupported (pre-Windows 10 1703), falls back in order to:
    1. shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)  [Per-Monitor V1]
    2. shcore.SetProcessDpiAwareness(PROCESS_SYSTEM_DPI_AWARE)       [System DPI]
    3. user32.SetProcessDPIAware()                                  [Legacy System DPI]

    Returns a string indicating the awareness mode successfully enabled:
    'per_monitor_v2', 'per_monitor_v1', 'system_aware', or 'unaware'.
    """
    # 1. Per-Monitor V2 (Windows 10 Creators Update 1703+)
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        ):
            return "per_monitor_v2"
    except (AttributeError, OSError):
        pass

    # 2. Per-Monitor V1 (Windows 8.1+)
    try:
        shcore = ctypes.windll.shcore
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return "per_monitor_v1"
    except (AttributeError, OSError):
        pass

    # 3. System DPI Aware (Windows Vista+)
    try:
        shcore = ctypes.windll.shcore
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(PROCESS_SYSTEM_DPI_AWARE) == 0:
            return "system_aware"
    except (AttributeError, OSError):
        pass

    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system_aware"
    except (AttributeError, OSError):
        pass

    return "unaware"


def is_per_monitor_v2_active() -> bool:
    """Check whether Per-Monitor V2 DPI awareness is currently active for this thread."""
    try:
        user32 = ctypes.windll.user32
        if hasattr(user32, "GetThreadDpiAwarenessContext") and hasattr(user32, "AreDpiAwarenessContextsEqual"):
            user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            user32.AreDpiAwarenessContextsEqual.restype = wintypes.BOOL
            ctx = user32.GetThreadDpiAwarenessContext()
            return bool(user32.AreDpiAwarenessContextsEqual(ctx, ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))
    except (AttributeError, OSError):
        pass
    return False


def get_dpi_for_system() -> int:
    """Return the system DPI (typically 96 at 100% display scaling)."""
    try:
        user32 = ctypes.windll.user32
        if hasattr(user32, "GetDpiForSystem"):
            user32.GetDpiForSystem.restype = wintypes.UINT
            val = user32.GetDpiForSystem()
            if val > 0:
                return int(val)
    except (AttributeError, OSError):
        pass
    return DEFAULT_DPI


def get_dpi_for_window(hwnd: Optional[int] = None) -> int:
    """
    Return the DPI for the specified window handle under Per-Monitor DPI awareness.
    If the window handle is None, 0, or invalid, falls back to the system DPI.
    """
    if hwnd:
        try:
            user32 = ctypes.windll.user32
            if hasattr(user32, "GetDpiForWindow"):
                user32.GetDpiForWindow.argtypes = [wintypes.HWND]
                user32.GetDpiForWindow.restype = wintypes.UINT
                val = user32.GetDpiForWindow(hwnd)
                if val > 0:
                    return int(val)
        except (AttributeError, OSError):
            pass
    return get_dpi_for_system()


def get_dpi_scale_for_window(hwnd: Optional[int] = None) -> float:
    """Return the scaling multiplier for a window (e.g. 1.0 for 100%, 1.25 for 125%, 1.5 for 150%)."""
    return get_dpi_for_window(hwnd) / DEFAULT_DPI


def get_system_metrics_for_dpi(n_index: int, dpi: Optional[int] = None) -> int:
    """
    Call GetSystemMetricsForDpi if available (Windows 10 1607+),
    otherwise fall back to standard GetSystemMetrics.
    """
    try:
        user32 = ctypes.windll.user32
        if dpi is not None and hasattr(user32, "GetSystemMetricsForDpi"):
            user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
            user32.GetSystemMetricsForDpi.restype = ctypes.c_int
            return int(user32.GetSystemMetricsForDpi(n_index, dpi))
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        return int(user32.GetSystemMetrics(n_index))
    except (AttributeError, OSError):
        return 0


def get_work_area_for_point_or_window(
    pt_or_hwnd: Optional[Union[Tuple[int, int], int]] = None
) -> Tuple[int, int, int, int]:
    """
    Get the multi-monitor work area (left, top, right, bottom)
    for the monitor where the point or window is currently situated.
    Falls back to SystemParametersInfoW(SPI_GETWORKAREA) if monitor APIs fail.
    """
    try:
        user32 = ctypes.windll.user32
        hmon = None
        if isinstance(pt_or_hwnd, tuple) and len(pt_or_hwnd) >= 2:
            pt = wintypes.POINT(int(pt_or_hwnd[0]), int(pt_or_hwnd[1]))
            user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            user32.MonitorFromPoint.restype = wintypes.HMONITOR
            hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
        elif isinstance(pt_or_hwnd, int) and pt_or_hwnd > 0:
            user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            user32.MonitorFromWindow.restype = wintypes.HMONITOR
            hmon = user32.MonitorFromWindow(pt_or_hwnd, MONITOR_DEFAULTTONEAREST)

        if hmon:
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                return (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom)
    except Exception:
        pass

    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return (rect.left, rect.top, rect.right, rect.bottom)
