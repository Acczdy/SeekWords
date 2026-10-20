"""Optional Windows elevation, invoked only by an explicit GUI or launcher action."""
import ctypes
from pathlib import Path
import platform
import subprocess
import sys


def is_windows_admin():
    if platform.system() != "Windows":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def request_elevation(paths):
    if platform.system() != "Windows":
        raise OSError("管理员启动入口仅适用于 Windows")
    entry = Path(__file__).resolve().parent.parent / "seekwords.py"
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        executable = executable.resolve()
        entry = executable
        argv = list(map(str, paths))
    else:
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.is_file():
            executable = pythonw
        argv = [str(entry), *map(str, paths)]
    # ShellExecute receives the executable and its arguments separately: no shell code.
    arguments = subprocess.list2cmdline(argv)
    launch = ctypes.windll.shell32.ShellExecuteW
    launch.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                       ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    launch.restype = ctypes.c_ssize_t
    result = launch(None, "runas", str(executable), arguments, str(entry.parent), 1)
    if result <= 32:
        raise OSError(f"未能打开管理员窗口（错误代码 {result}）。授权可能已取消，或当前账户不允许提升权限。")
