"""Build SeekWords on the current platform; PyInstaller is not a cross-compiler."""
import os
from pathlib import Path
import platform
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "scan_keywords_app" / "_vendor"
WORK = ROOT / ".build"


def main():
    system = platform.system()
    if system not in ("Windows", "Linux", "Darwin"):
        raise SystemExit(f"Unsupported build platform: {system}")
    icon = ROOT / "assets" / ("seekwords.ico" if system == "Windows" else "seekwords.icns")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--windowed", "--name", "SeekWords", "--distpath", str(ROOT / "dist"),
            "--workpath", str(WORK / "work"), "--specpath", str(WORK),
            "--paths", str(VENDOR), "--icon", str(icon),
            "--add-data", f"{VENDOR}{os.pathsep}scan_keywords_app/_vendor",
            "--add-data", f"{ROOT / 'assets'}{os.pathsep}assets",
            "--add-data", f"{ROOT / 'docs/OCR_COMPONENTS.md'}{os.pathsep}docs",
            "--add-data", f"{ROOT / 'docs/third_party'}{os.pathsep}docs/third_party",
            "--collect-data", "rapidocr",
            "--hidden-import", "rapidocr", "--hidden-import", "onnxruntime",
            "--hidden-import", "olefile", "--hidden-import", "xlrd",
            "--hidden-import", "pypdf", "--hidden-import", "striprtf.striprtf"]
    if system == "Darwin":
        args += ["--onedir", "--osx-bundle-identifier", "io.github.acczdy.seekwords",
                 "--hidden-import", "send2trash.mac.modern", "--hidden-import", "Foundation"]
    else:
        args.append("--onefile")
    if system == "Windows":
        args += ["--hidden-import", "send2trash.win.modern",
                 "--hidden-import", "send2trash.win.IFileOperationProgressSink",
                 "--hidden-import", "win32com.shell.shell", "--hidden-import", "pythoncom"]
    else:
        args += ["--hidden-import", "send2trash.plat_gio", "--hidden-import", "send2trash.plat_other"]
    args.append(str(ROOT / "seekwords.py"))
    subprocess.run(args, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
