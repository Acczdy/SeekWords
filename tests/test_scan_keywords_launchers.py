"""Verify platform launchers with a harmless stub instead of opening the GUI."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import time


class LauncherTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows windowless source launcher")
    def test_windowless_launcher_preserves_unicode_and_trailing_backslash_arguments(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="scan launch ") as temp:
            folder = Path(temp) / "测试目录 (GUI)"
            folder.mkdir()
            marker = folder / "args.json"
            (folder / "seekwords.pyw").write_text(
                "import json,sys,os\nfrom pathlib import Path\n"
                "marker=Path(__file__).with_name('args.json')\n"
                "os.chdir(Path(__file__).anchor)\n"
                "pending=marker.with_suffix('.tmp')\n"
                "pending.write_text(json.dumps(sys.argv[1:]),encoding='utf-8')\n"
                "os.replace(pending,marker)\n", encoding="utf-8")
            shutil.copyfile(source / "seekwords.vbs", folder / "seekwords.vbs")
            arguments = [str(folder), 'D:\\目录 a & b\\', 'E:\\']
            script = Path(os.environ['SystemRoot']) / 'System32/wscript.exe'
            # WScript parses quoted arguments itself; it does not use CRT's
            # doubling of backslashes before a closing quote.
            command = ' '.join('"' + value + '"' for value in
                               [str(script), '//B', str(folder / 'seekwords.vbs'), *arguments])
            result = subprocess.run(command, timeout=15)
            self.assertEqual(result.returncode, 0)
            deadline = time.monotonic() + 15
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), 'windowless Python launcher did not start')
            self.assertEqual(json.loads(marker.read_text(encoding='utf-8')), arguments)

    def test_platform_launcher_starts_python_from_unicode_directory(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="scan launcher ") as temp:
            folder = Path(temp) / "测试目录 (GUI)"
            folder.mkdir()
            (folder / "seekwords.py").write_text(
                "import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
            if os.name == "nt":
                shutil.copyfile(source / "seekwords.ps1", folder / "seekwords.ps1")
                powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
                command = [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(folder / "seekwords.ps1"), "-Path", str(folder)]
            else:
                shutil.copyfile(source / "seekwords.sh", folder / "seekwords.sh")
                command = ["bash", str(folder / "seekwords.sh"), str(folder)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout), [str(folder)])


if __name__ == "__main__":
    unittest.main()
