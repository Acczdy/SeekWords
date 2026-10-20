"""Elevation API tests never trigger a real UAC prompt."""
import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

from scan_keywords_app.permissions import is_windows_admin, request_elevation


class PermissionLaunchTests(unittest.TestCase):
    def test_elevation_uses_separate_executable_and_quoted_arguments(self):
        paths = ['C:\\资料 a & b\\', r'D:\scan folder']
        with patch('scan_keywords_app.permissions.platform.system', return_value='Windows'), patch(
                'scan_keywords_app.permissions.ctypes.windll', create=True) as native:
            native.shell32.ShellExecuteW.return_value = 33
            request_elevation(paths)
            args = native.shell32.ShellExecuteW.call_args.args
            self.assertEqual(args[1], 'runas')
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            self.assertEqual(args[2], str(pythonw) if pythonw.is_file() else sys.executable)
            self.assertTrue(args[3].endswith(subprocess.list2cmdline(paths)))
            self.assertNotIn('powershell', args[3].lower())

    def test_cancelled_elevation_is_reported(self):
        with patch('scan_keywords_app.permissions.platform.system', return_value='Windows'), patch(
                'scan_keywords_app.permissions.ctypes.windll', create=True) as native:
            native.shell32.ShellExecuteW.return_value = 5
            with self.assertRaisesRegex(OSError, '未能打开管理员窗口'):
                request_elevation([])

    def test_frozen_elevation_launches_exe_without_python_script(self):
        with patch('scan_keywords_app.permissions.platform.system', return_value='Windows'), patch(
                'scan_keywords_app.permissions.sys.frozen', True, create=True), patch(
                'scan_keywords_app.permissions.ctypes.windll', create=True) as native:
            native.shell32.ShellExecuteW.return_value = 33
            request_elevation(['D:\\资料 a\\'])
            args = native.shell32.ShellExecuteW.call_args.args
            self.assertEqual(args[2], str(Path(sys.executable).resolve()))
            self.assertEqual(args[3], subprocess.list2cmdline(['D:\\资料 a\\']))
            self.assertNotIn('seekwords.py', args[3])

    def test_non_windows_does_not_request_windows_elevation(self):
        with patch('scan_keywords_app.permissions.platform.system', return_value='Linux'):
            self.assertFalse(is_windows_admin())
            with self.assertRaises(OSError):
                request_elevation([])


if __name__ == '__main__':
    unittest.main()
