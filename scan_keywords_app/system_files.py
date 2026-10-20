"""System trash, explicit permanent deletion, and truthful move failures."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import platform
import shutil
import stat
import sys
import tempfile
import time


class ReadOnlyFileError(PermissionError):
    pass


class FileActionError(OSError):
    def __init__(self, message, source, target=None, cause=None):
        super().__init__(message)
        self.source = str(source)
        self.target = str(target) if target else None
        self.original_error = cause


def read_only_files(source, recursive=False):
    if os.name != "nt":
        return []
    source = Path(source)
    paths = [source]
    if recursive and source.is_dir():
        def failed(exc):
            raise exc
        for folder, dirs, files in os.walk(source, followlinks=False, onerror=failed):
            dirs[:] = [name for name in dirs if not (getattr((Path(folder)/name).lstat(), 'st_file_attributes', 0) & 0x400)]
            paths.extend(Path(folder) / name for name in files)
    result = []
    for path in paths:
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400) and getattr(info, "st_file_attributes", 0) & 1:
            result.append((path, info.st_mode))
    return result


def restore_attributes(records, source=None, destination=None):
    errors = []
    for path, mode in records:
        if destination is not None:
            path = Path(destination) if path == source else Path(destination) / path.relative_to(source)
        if os.path.lexists(path):
            try:
                path.chmod(mode)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
    return errors


@contextmanager
def writable_source(source, allow_readonly=False, recursive=False):
    records = read_only_files(source, recursive)
    if records and not allow_readonly:
        raise ReadOnlyFileError("文件为只读。请重新操作，并在确认窗口勾选“允许处理只读文件”。")
    changed = []
    try:
        for path, mode in records:
            path.chmod(mode | stat.S_IWRITE)
            changed.append((path, mode))
        yield changed
    except Exception as exc:
        errors = restore_attributes(changed)
        if errors:
            raise FileActionError(f"{exc}\n只读属性恢复失败：" + "；".join(errors), source, cause=exc) from exc
        raise


def send_to_system_trash(path):
    vendor = str(Path(__file__).resolve().parent / "_vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    from send2trash import send2trash
    before = _source_stamp(path)
    try:
        send2trash(str(path))
    except OSError as exc:
        # A transient Recycle Bin lock can clear quickly. Retry the same modern
        # recycle operation once, without invoking another Shell confirmation.
        if (platform.system() != "Windows" or before is None or windows_error_code(exc) != 0x80270028
                or before != _source_stamp(path)):
            raise
        try:
            time.sleep(0.2)
            if before != _source_stamp(path):
                raise FileActionError("源文件已变化，请重新扫描后操作", path)
            send2trash(str(path))
        except OSError as retry:
            raise FileActionError(
                f"系统回收第一次失败：{exc}\n重试后仍失败：{retry}", path, cause=retry) from retry
    if os.path.lexists(path):
        raise OSError("系统删除未完成或已取消，原路径仍然存在。")


def _source_stamp(path):
    try:
        info = os.lstat(path)
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    except OSError:
        return None


def windows_error_code(exc):
    for name in ("winerror", "hresult"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            value &= 0xffffffff
            # Only HRESULT_FROM_WIN32 has a Win32 code in its low word.
            return value & 0xffff if value & 0xffff0000 == 0x80070000 else value
    return None


def delete_path(source, *, permanent=False, allow_readonly=False):
    source = Path(source)
    with writable_source(source, allow_readonly, recursive=permanent):
        if permanent:
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()
        else:
            send_to_system_trash(source)
    return "彻底删除" if permanent else "系统删除"


def move_path(source, target, *, allow_readonly=False):
    source, target = Path(source), Path(target)
    if os.path.lexists(target):
        raise FileExistsError(f"目标已存在，未覆盖：{target}")
    cross_device = source.stat().st_dev != target.parent.stat().st_dev
    try:
        with writable_source(source, allow_readonly, recursive=cross_device) as attributes:
            try:
                os.rename(source, target)
            except OSError as exc:
                # Access denied must never silently turn into copy + failed source deletion.
                if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
                    raise
                _cross_device_move(source, target)
            errors = restore_attributes(attributes, source, target)
            if errors:
                raise FileActionError("已移动，但恢复目标只读属性失败：" + "；".join(errors), source, target)
    except FileActionError:
        raise
    except OSError as exc:
        raise FileActionError(str(exc), source, target, cause=exc) from exc
    return str(target)


def _cross_device_move(source, target):
    stage = Path(tempfile.mkdtemp(prefix=".scan-keywords-move-", dir=target.parent))
    staged = stage / source.name
    published = False
    try:
        before = source.stat()
        if source.is_dir():
            shutil.copytree(source, staged, symlinks=True)
        else:
            shutil.copy2(source, staged)
            after = source.stat()
            if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size):
                raise OSError("源文件在复制过程中发生变化，已停止移动，源文件保留。")
        if os.path.lexists(target):
            raise FileExistsError(f"目标已存在，未覆盖：{target}")
        os.rename(staged, target)
        published = True
        try:
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()
        except OSError as exc:
            if source.is_file():
                # A failed file unlink leaves the original intact; undo our completed copy.
                try:
                    target.unlink()
                    published = False
                except OSError:
                    pass
            raise FileActionError("跨盘复制已完成，但移除源失败。请核对下方源与目标的实际状态。", source, target, cause=exc) from exc
    except FileActionError:
        raise
    except OSError as exc:
        raise FileActionError(str(exc), source, target if published else None, cause=exc) from exc
    finally:
        try:
            shutil.rmtree(stage)
        except OSError:
            # The empty reservation is safe to remove later; never remove published targets here.
            pass


def describe_failure(path, operation, exc):
    original = getattr(exc, "original_error", None) or exc
    code = windows_error_code(original)
    if isinstance(original, ReadOnlyFileError) or code == 0x8027003f:
        advice = "文件为只读：在操作确认窗口勾选“允许处理只读文件”后重试。"
    elif code == 0x80270028:
        advice = "Windows 回收站目标被占用（0x80270028）。关闭正在访问该目录的资源管理器、终端或同步程序，稍后重试；也可打开系统回收站核对。管理员身份不能解除占用。"
    elif code in (32, 33, 0x80270027):
        advice = "文件正被其他程序占用。关闭微信、Word 或相关程序后重试；管理员身份不能解除文件占用。"
    elif code in (1223, 0x80270000):
        advice = "操作已取消。"
    elif code in (111, 206, 0x8027001d, 0x8027001e):
        advice = "路径过长，当前系统文件接口无法处理。请使用系统文件管理器核对，或缩短上层目录名称后重新扫描。"
    elif code in (0x80270023, 0x80270024):
        advice = "源路径或回收站目标路径不存在。请核对磁盘连接、原路径和系统回收站后重新扫描。"
    elif code == 0x80270032:
        advice = "目标磁盘空间不足，请释放空间后重试。"
    elif code == 0x80270002:
        advice = "Windows 要求提升权限，请通过“管理员启动”重新打开后重试。"
    elif code in (0x80270036, 0x80270037, 0x80270038, 0x8027003a):
        advice = "系统回收站无法接收此项目：可能已关闭回收功能、项目过大、路径过长或回收站不可用。请在系统文件管理器中核对回收站设置。"
    elif isinstance(original, PermissionError) or code in (5, 0x80270021, 0x80270022) or getattr(original, "errno", None) in (errno.EACCES, errno.EPERM):
        advice = "访问被拒绝。检查文件/父目录的删除或写入权限，关闭可能占用文件的程序；需要时使用管理员启动后重试。"
    else:
        advice = str(exc)
    target = getattr(exc, "target", None)
    states = [f"源路径：{path}", f"源路径状态：{'仍存在（目录内容请核对）' if os.path.lexists(path) else '不存在'}"]
    if target:
        states += [f"目标路径：{target}", f"目标路径状态：{'存在，请核对内容' if os.path.lexists(target) else '不存在'}"]
    return f"[{operation}] {advice}\n" + "\n".join(states) + f"\n原始错误：{exc}"


def is_user_cancelled(exc):
    current = exc
    for _ in range(5):
        code = windows_error_code(current)
        if code in (1223, 0x80270000):
            return True
        current = getattr(current, "original_error", None)
        if current is None:
            break
    return False
