"""Create release assets from an already built native application."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=("windows-x64", "linux-x64", "macos-x64", "macos-arm64"))
    target = parser.parse_args().target
    out = ROOT / "release"
    out.mkdir(exist_ok=True)
    if target == "windows-x64":
        shutil.copy2(ROOT / "dist" / "SeekWords.exe", out / "SeekWords-windows-x64.exe")
    elif target == "linux-x64":
        executable = ROOT / "dist" / "SeekWords"
        if not executable.is_file():
            raise SystemExit("Linux executable not found")
        with tarfile.open(out / "SeekWords-linux-x64.tar.gz", "w:gz") as archive:
            archive.add(executable, arcname="SeekWords/SeekWords")
            archive.add(ROOT / "README.md", arcname="SeekWords/README.md")
            archive.add(ROOT / "LICENSE", arcname="SeekWords/LICENSE")
    else:
        app = ROOT / "dist" / "SeekWords.app"
        if not app.is_dir():
            raise SystemExit("macOS app bundle not found")
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        str(app), str(out / f"SeekWords-{target}.zip")], check=True)


if __name__ == "__main__":
    main()
