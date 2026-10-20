#!/usr/bin/env python3
"""Launch the keyword scanner GUI. Optional arguments preselect scan directories."""
import argparse
import sys


def startup_error(message):
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    else:
        # Windowed executables and pythonw have no stderr.
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "觅文 SeekWords 无法启动", 0x10)


def main():
    parser = argparse.ArgumentParser(description="觅文 SeekWords 文件关键词扫描 GUI（Python 3.10+ / Tk）")
    parser.add_argument("paths", nargs="*", help="预先添加的扫描目录；不自动开始扫描")
    options = parser.parse_args()
    try:
        from scan_keywords_app.gui import main as run
    except ImportError as exc:
        startup_error(f"GUI 组件不可用：{exc}\n请安装带 Tk 的 Python 3.10+。Linux 可安装 python3-tk；macOS 推荐 python.org 安装包。")
        return 1
    try:
        run(options.paths)
    except Exception as exc:
        startup_error(f"GUI 无法启动：{exc}\n请在带图形桌面的会话中运行；SSH/WSL 需要可用的图形显示。")
        return 1
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
