"""Only temporary fixtures are deleted. Native Windows trash is mocked."""
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scan_keywords_app.core import FileActions, Hit, fingerprint
from scan_keywords_app.system_files import FileActionError, ReadOnlyFileError, describe_failure
from scan_keywords_app.system_files import send_to_system_trash, is_user_cancelled, windows_error_code


class SystemFileTests(unittest.TestCase):
    @staticmethod
    def shell_error(code):
        error = OSError("fixture Shell error")
        error.winerror = code - 0x100000000 if code > 0x7fffffff else code
        return error

    def test_shell_hresult_is_not_misclassified_as_win32_error(self):
        error = self.shell_error(0x80270028)
        self.assertEqual(windows_error_code(error), 0x80270028)
        self.assertIn("回收站目标被占用", describe_failure(self.source, "系统回收站", error))
        self.assertEqual(windows_error_code(self.shell_error(0x80070005)), 5)
        self.assertTrue(is_user_cancelled(self.shell_error(0x80270000)))
        self.assertTrue(is_user_cancelled(self.shell_error(0x800704c7)))
        self.assertFalse(is_user_cancelled(error))

    @unittest.skipUnless(os.name == "nt", "Windows modern Shell API")
    def test_modern_trash_zero_result_with_aborted_flag_is_cancelled(self):
        from send2trash.win import modern
        with patch.object(modern.pythoncom, "CoCreateInstance") as create, patch.object(
                modern.pythoncom, "CoInitialize"), patch.object(modern.pythoncom, "CoUninitialize"), patch.object(
                modern.shell, "SHCreateItemFromParsingName"), patch.object(modern, "create_sink"):
            create.return_value.PerformOperations.return_value = 0
            create.return_value.GetAnyOperationsAborted.return_value = True
            with self.assertRaises(OSError) as result:
                modern.send2trash(str(self.source))
        self.assertTrue(is_user_cancelled(result.exception))
        self.assertTrue(self.source.exists())

    def test_destination_sharing_error_retries_only_system_trash_once(self):
        from scan_keywords_app import system_files
        vendor = str(Path(system_files.__file__).parent / "_vendor")
        if vendor not in sys.path:
            sys.path.insert(0, vendor)
        calls = []
        def retry(path):
            calls.append(path)
            if len(calls) == 1:
                raise self.shell_error(0x80270028)
            shutil.move(path, str(self.destination / Path(path).name))
        with patch("send2trash.send2trash", side_effect=retry), patch(
                "scan_keywords_app.system_files.platform.system", return_value="Windows"), patch(
                "scan_keywords_app.system_files.time.sleep") as pause:
            send_to_system_trash(self.source)
        self.assertEqual(calls, [str(self.source), str(self.source)])
        pause.assert_called_once()
        self.assertFalse(self.source.exists())
        self.assertEqual((self.destination / self.source.name).read_text(), "original fixture")

    def test_cancel_or_changed_source_never_retries(self):
        import send2trash
        def changed(path):
            self.source.write_text("changed source")
            raise self.shell_error(0x80270028)
        for error in (self.shell_error(0x80270000), self.shell_error(5), changed):
            with patch.object(send2trash, "send2trash", side_effect=error), patch(
                    "scan_keywords_app.system_files.platform.system", return_value="Windows"), patch(
                    "scan_keywords_app.system_files.time.sleep") as pause:
                with self.assertRaises(OSError):
                    send_to_system_trash(self.source)
            pause.assert_not_called()
            self.assertTrue(self.source.exists())

    def setUp(self):
        vendor = str(Path(__file__).resolve().parents[1] / "scan_keywords_app" / "_vendor")
        if vendor not in sys.path:
            sys.path.insert(0, vendor)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / "ORBIT-fixture.txt"
        self.source.write_text("original fixture", encoding="utf-8")
        self.destination = self.base / "destination"
        self.destination.mkdir()
        self.actions = FileActions(self.base / "app-data")

    def hit(self):
        return Hit(str(self.source), "文件", "ORBIT", identity=fingerprint(self.source.lstat()))

    def test_system_trash_never_creates_private_recovery(self):
        trash = self.base / "trash-fixture"
        trash.mkdir()
        with patch("scan_keywords_app.system_files.send_to_system_trash", side_effect=lambda p: shutil.move(str(p), str(trash/p.name))):
            self.actions.delete(self.hit())
        self.assertFalse(self.source.exists())
        self.assertTrue((trash/self.source.name).exists())
        self.assertFalse(self.actions.bin.exists())

    def test_failed_trash_does_not_fall_back_to_permanent_delete(self):
        with patch("scan_keywords_app.system_files.send_to_system_trash", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.actions.delete(self.hit())
        self.assertEqual(self.source.read_text(), "original fixture")

    def test_explicit_permanent_delete(self):
        self.actions.delete(self.hit(), permanent=True)
        self.assertFalse(self.source.exists())
        self.assertFalse(self.actions.bin.exists())

    def test_access_denied_rename_does_not_copy(self):
        with patch("scan_keywords_app.system_files.os.rename", side_effect=PermissionError(errno.EACCES, "fixture denied")), patch(
                "scan_keywords_app.system_files.shutil.copy2") as copy:
            with self.assertRaises(FileActionError) as result:
                self.actions.move(self.hit(), self.destination)
        copy.assert_not_called()
        self.assertTrue(self.source.exists())
        self.assertFalse((self.destination/self.source.name).exists())
        self.assertIn("访问被拒绝", describe_failure(self.source, "移动", result.exception))

    def test_cross_volume_file_unlink_failure_rolls_back_copy(self):
        original_rename, original_unlink = os.rename, Path.unlink
        def rename(source, destination):
            if Path(source) == self.source:
                raise OSError(errno.EXDEV, "fixture cross device")
            return original_rename(source, destination)
        def unlink(path, *args, **kwargs):
            if path == self.source:
                raise PermissionError(errno.EACCES, "fixture source delete denied")
            return original_unlink(path, *args, **kwargs)
        with patch("scan_keywords_app.system_files.os.rename", side_effect=rename), patch.object(Path, "unlink", unlink):
            with self.assertRaises(FileActionError) as result:
                self.actions.move(self.hit(), self.destination)
        self.assertEqual(self.source.read_text(), "original fixture")
        self.assertFalse((self.destination/self.source.name).exists())
        self.assertEqual(list(self.destination.iterdir()), [])
        detail = describe_failure(self.source, "移动", result.exception)
        self.assertIn("源路径状态：仍存在", detail)
        self.assertIn("目标路径状态：不存在", detail)

    def test_cross_volume_copy_detects_source_changes(self):
        real_rename, real_copy = os.rename, shutil.copy2
        def rename(source, destination):
            if Path(source) == self.source:
                raise OSError(errno.EXDEV, "fixture cross device")
            return real_rename(source, destination)
        def changing_copy(source, destination):
            real_copy(source, destination)
            self.source.write_text("changed during copy")
        with patch("scan_keywords_app.system_files.os.rename", side_effect=rename), patch(
                "scan_keywords_app.system_files.shutil.copy2", side_effect=changing_copy):
            with self.assertRaisesRegex(FileActionError, "发生变化"):
                self.actions.move(self.hit(), self.destination)
        self.assertEqual(self.source.read_text(), "changed during copy")
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_cross_volume_directory_partial_delete_keeps_complete_target(self):
        folder = self.base / "ORBIT-folder"
        folder.mkdir()
        (folder/"first.txt").write_text("one")
        (folder/"second.txt").write_text("two")
        real_rename, real_remove = os.rename, shutil.rmtree
        def rename(source, target):
            if Path(source) == folder:
                raise OSError(errno.EXDEV, "fixture cross device")
            return real_rename(source, target)
        def remove(path, *args, **kwargs):
            if Path(path) == folder:
                (folder/"first.txt").unlink()
                raise PermissionError(errno.EACCES, "fixture partial source removal")
            return real_remove(path, *args, **kwargs)
        hit = Hit(str(folder), "目录", "ORBIT", identity=fingerprint(folder.lstat()))
        with patch("scan_keywords_app.system_files.os.rename", side_effect=rename), patch(
                "scan_keywords_app.system_files.shutil.rmtree", side_effect=remove):
            with self.assertRaises(FileActionError) as result:
                self.actions.move(hit, self.destination)
        target = self.destination/folder.name
        self.assertFalse((folder/"first.txt").exists())
        self.assertEqual((target/"first.txt").read_text(), "one")
        self.assertEqual((target/"second.txt").read_text(), "two")
        self.assertIn("目标路径状态：存在", describe_failure(folder, "移动", result.exception))

    @unittest.skipUnless(os.name == "nt", "Windows readonly attribute")
    def test_readonly_requires_opt_in_and_failed_operation_restores_attribute(self):
        self.source.chmod(stat.S_IREAD)
        self.addCleanup(lambda: self.source.chmod(stat.S_IWRITE) if self.source.exists() else None)
        with self.assertRaises(ReadOnlyFileError):
            self.actions.delete(self.hit(), permanent=True)
        self.assertTrue(self.source.exists())
        with patch("scan_keywords_app.system_files.send_to_system_trash", side_effect=PermissionError("fixture denied")):
            with self.assertRaises(PermissionError):
                self.actions.delete(self.hit(), allow_readonly=True)
        self.assertTrue(self.source.stat().st_file_attributes & 1)
        self.actions.delete(self.hit(), permanent=True, allow_readonly=True)
        self.assertFalse(self.source.exists())

    @unittest.skipUnless(os.name == "nt", "Windows shell API")
    def test_windows_trash_uses_silent_modern_recycle_flags(self):
        # Mock the Shell. No real user file or system Recycle Bin is touched.
        vendor = str(Path(__file__).resolve().parents[1]/"scan_keywords_app/_vendor")
        if vendor not in sys.path:
            sys.path.insert(0, vendor)
        from send2trash.win import modern
        with patch.object(modern.pythoncom, "CoCreateInstance") as create, patch.object(
                modern.pythoncom, "CoInitialize"), patch.object(modern.pythoncom, "CoUninitialize"), patch.object(
                modern.shell, "SHCreateItemFromParsingName"), patch.object(modern, "create_sink"):
            create.return_value.PerformOperations.return_value = 0
            create.return_value.GetAnyOperationsAborted.return_value = False
            modern.send2trash(str(self.source))
            flags = create.return_value.SetOperationFlags.call_args.args[0]
        self.assertTrue(flags & 0x00080000)  # FOFX_RECYCLEONDELETE
        self.assertFalse(flags & 0x4000)      # no extra nuke warning dialog
        self.assertTrue(self.source.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "isolated Linux freedesktop trash")
    def test_linux_trash_in_isolated_profile(self):
        profile = self.base / "isolated-profile"
        profile.mkdir()
        env = dict(os.environ, XDG_DATA_HOME=str(profile))
        code = "from scan_keywords_app.system_files import send_to_system_trash; import sys; send_to_system_trash(sys.argv[1])"
        result = subprocess.run([sys.executable, "-c", code, str(self.source)], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.source.exists())
        self.assertEqual(len(list((profile/"Trash/files").iterdir())), 1)
        self.assertEqual(len(list((profile/"Trash/info").glob("*.trashinfo"))), 1)


if __name__ == "__main__":
    unittest.main()
