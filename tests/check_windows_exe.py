"""Windows EXE smoke test. Requires psutil in the test Python, not in the app.

Runs only its own EXE process with temporary settings and closes only that process.
Usage: py -3 tests/check_windows_exe.py
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import multiprocessing as mp
import sys
import zipfile
from unittest.mock import patch

import psutil


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from scan_keywords_app import VERSION
    executable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / f"SeekWords.exe"
    with executable.open("rb") as stream:
        stream.seek(0x3c)
        pe_offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(pe_offset)
        assert stream.read(4) == b"PE\0\0"
        stream.seek(pe_offset + 24 + 68)
        subsystem = struct.unpack("<H", stream.read(2))[0]
    assert subsystem == 2, "EXE must use the Windows GUI subsystem"
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    with tempfile.TemporaryDirectory(prefix="scan-exe-smoke-") as temp:
        fixture = Path(temp) / "中文目录 a & b"
        fixture.mkdir()
        windows = Path(os.environ["SystemRoot"])
        environment = dict(os.environ, LOCALAPPDATA=str(Path(temp) / "profile"), PYTHONPATH="", PYTHONHOME="",
                           PATH=os.pathsep.join((str(windows / "System32"), str(windows))))
        process = subprocess.Popen([str(executable), str(fixture)], cwd=temp, env=environment)
        windows = []
        owned = {process.pid}
        main_window = None
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                assert process.poll() is None, "EXE exited before GUI startup"
                owned.update(child.pid for child in psutil.Process(process.pid).children(recursive=True))
                windows = []
                @callback_type
                def visit(hwnd, _):
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value in owned:
                        title, kind = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(128)
                        user32.GetWindowTextW(hwnd, title, len(title))
                        user32.GetClassNameW(hwnd, kind, len(kind))
                        windows.append((hwnd, title.value, kind.value, bool(user32.IsWindowVisible(hwnd))))
                    return True
                user32.EnumWindows(visit, 0)
                assert not any(kind == "ConsoleWindowClass" for _, _, kind, _ in windows)
                main_window = next((handle for handle, title, _, visible in windows
                                    if visible and title.startswith("觅文 SeekWords")), None)
                if main_window:
                    break
                error = next((title for _, title, kind, visible in windows if visible and kind == "#32770"), None)
                assert error is None, f"Unexpected startup dialog: {error}"
                time.sleep(0.1)
            assert main_window, f"GUI was not visible: {windows}"
            # Close our own application normally and check persisted command-line roots.
            user32.PostMessageW(main_window, 0x10, 0, 0)
            process.wait(timeout=15)
            assert process.returncode == 0
            settings = Path(environment["LOCALAPPDATA"]) / "SeekWords/settings.json"
            config = json.loads(settings.read_text(encoding="utf-8"))
            assert any(Path(value) == fixture for value in config["roots"]), config
            print(json.dumps({"subsystem": subsystem, "visible_gui": True, "console_window": False,
                              "unicode_argument": True, "normal_exit": True}))
        finally:
            if process.poll() is None:
                for pid in sorted(owned, reverse=True):
                    try:
                        psutil.Process(pid).terminate()
                    except psutil.NoSuchProcess:
                        pass
                process.wait(timeout=10)
    # Exercise the bundled readers in the EXE's real frozen worker. Do not let
    # the child inherit the test Python's import paths or execute this checker.
    sys.path.insert(0, str(root))
    from scan_keywords_app.content import ContentReader
    import multiprocessing.spawn as spawn
    original = spawn.get_preparation_data
    def preparation(name):
        data = original(name)
        for key in ("sys_path", "init_main_from_path", "init_main_from_name"):
            data.pop(key, None)
        return data
    previous_executable = spawn.get_executable()
    reader = ContentReader()
    try:
        mp.set_executable(str(executable))
        with tempfile.TemporaryDirectory(prefix="scan-exe-documents-") as temp:
            jobs = []
            examples = {
                "document.docx": ("word/document.xml", '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>ORBIT</w:t></w:r></w:p></w:body></w:document>'),
                "slides.pptx": ("ppt/slides/slide1.xml", '<p:sld xmlns:p="p" xmlns:a="a"><a:p><a:r><a:t>ORBIT</a:t></a:r></a:p></p:sld>'),
                "workbook.xlsx": ("xl/worksheets/sheet1.xml", '<worksheet><sheetData><row><c r="A1" t="inlineStr"><is><t>ORBIT</t></is></c></row></sheetData></worksheet>'),
            }
            for name, (part, content) in examples.items():
                path = Path(temp) / name
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(part, content)
                jobs.append(path)
            (Path(temp) / "document.rtf").write_text(r"{\rtf1\ansi ORBIT}", encoding="ascii")
            (Path(temp) / "notes.txt").write_text("ORBIT", encoding="utf-8")
            jobs += [Path(temp) / name for name in ("document.rtf", "notes.txt")]
            with patch.object(sys, "frozen", True, create=True), patch.object(spawn, "get_preparation_data", side_effect=preparation):
                for path in jobs:
                    result = reader.read(path, ["ORBIT"], lambda: True, lambda: None)
                    assert result["matched"] == ["ORBIT"] and not result["error"], (str(path), result)
                    assert result["match_count"] >= 1 and result["matches"], (str(path), result)
                    assert "行" in result["matches"][0]["position"] and "ORBIT" in result["matches"][0]["text"]
    finally:
        reader.close()
        mp.set_executable(previous_executable)
    print(json.dumps({"frozen_document_worker": True, "formats": ["docx", "pptx", "xlsx", "rtf", "txt"]}))


if __name__ == "__main__":
    main()
