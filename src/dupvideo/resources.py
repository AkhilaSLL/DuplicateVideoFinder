"""Locating bundled assets, both from source and inside a PyInstaller bundle."""

from __future__ import annotations

import os
import sys

APP_ID = "AkhilaSLL.DuplicateVideoFinder"

# DWM window attributes (dwmapi.h).  20 replaced 19 in Windows 10 build 18985;
# the colour attributes need Windows 11 22000+.
DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36


def _base_dir() -> str:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return str(bundle)
    # src/dupvideo/resources.py -> repository root
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def asset_path(name: str) -> str:
    """Absolute path to ``assets/<name>``; may not exist."""
    return os.path.join(_base_dir(), "assets", name)


def set_windows_app_id() -> None:
    """
    Give the process its own taskbar identity on Windows.

    Without this the window groups under the generic Python icon instead of
    showing the app icon.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _colorref(hex_color: str) -> int:
    """'#rrggbb' -> Win32 COLORREF (0x00bbggrr)."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (b << 16) | (g << 8) | r


def apply_dark_titlebar(window, caption: str, text: str, border: str) -> None:
    """
    Make the window's title bar match the app's dark theme.

    Tk draws only the client area - the caption is the OS's - so the colours
    have to be requested through DWM.  Every attribute is best-effort: older
    Windows builds reject the newer ones and simply keep the default frame.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        window.update_idletasks()
        # Tk's own HWND is a child; the frame that owns the caption is its parent.
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            return

        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD]

        for attribute, value in (
            (DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
            (DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY, 1),
            (DWMWA_CAPTION_COLOR, _colorref(caption)),
            (DWMWA_TEXT_COLOR, _colorref(text)),
            (DWMWA_BORDER_COLOR, _colorref(border)),
        ):
            payload = ctypes.c_int(value)
            set_attribute(hwnd, attribute, ctypes.byref(payload),
                          ctypes.sizeof(payload))
    except Exception:
        pass


def enable_dpi_awareness() -> float:
    """
    Opt into DPI awareness so Tk renders crisply instead of being bitmap
    stretched by Windows.  Returns the scale factor relative to 96 DPI, which
    callers use to scale pixel constants.
    """
    if sys.platform != "win32":
        return 1.0
    try:
        import ctypes

        try:
            # PROCESS_PER_MONITOR_DPI_AWARE
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return max(1.0, dpi / 96.0)
    except Exception:
        return 1.0
