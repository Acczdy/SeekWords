"""Configure native DPI rendering before Tk creates any Windows windows."""
import ctypes
import os
import platform
import tkinter as tk
from tkinter import font as tkfont


def enable_high_dpi():
    if os.name != "nt":
        return
    # https://learn.microsoft.com/windows/win32/hidpi/setting-the-default-dpi-awareness-for-a-process
    try:
        set_context = ctypes.windll.user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_bool
        if set_context(ctypes.c_void_p(-4)):  # Per-monitor v2, Windows 10+
            return
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def create_root():
    enable_high_dpi()
    root = tk.Tk()
    if os.name == "nt":
        try:
            get_dpi = ctypes.windll.user32.GetDpiForWindow
            get_dpi.argtypes = [ctypes.c_void_p]
            get_dpi.restype = ctypes.c_uint
            root.update_idletasks()
            dpi = get_dpi(root.winfo_id())
            if dpi:
                root.tk.call("tk", "scaling", dpi / 72)
        except (AttributeError, OSError):
            pass
    return root


def configure_fonts(root):
    available = set(tkfont.families(root))
    choices = {
        "Windows": ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"),
        "Darwin": ("PingFang SC", "Helvetica Neue"),
    }.get(platform.system(), ("Noto Sans CJK SC", "WenQuanYi Micro Hei", "Droid Sans Fallback"))
    family = next((name for name in choices if name in available),
                  tkfont.nametofont("TkDefaultFont", root=root).actual("family"))
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        font = tkfont.nametofont(name, root=root)
        font.configure(family=family, size=10, weight="bold" if name == "TkHeadingFont" else "normal")
    return family, tkfont.nametofont("TkDefaultFont", root=root).metrics("linespace")
