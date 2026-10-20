"""Filesystem scanning and recoverable file operations, independent of the GUI."""
from __future__ import annotations

import csv
import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .system_files import delete_path, move_path
from .content import CONTENT_TYPES, ContentReader, ContentReadError, ContentCancelled, supports_content

DEFAULT_KEYWORDS = []


def data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "SeekWords"


def normalize_keywords(values):
    result, seen = [], set()
    for value in values:
        value = str(value).strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            result.append(value)
    return result


def canonical(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def beneath(path, parent):
    try:
        return os.path.commonpath([canonical(path), canonical(parent)]) == canonical(parent)
    except ValueError:
        return False


def compact_paths(paths):
    result = []
    for path in sorted({canonical(p) for p in paths}, key=len):
        if not any(beneath(path, parent) for parent in result):
            result.append(path)
    return result


def local_roots():
    if os.name == "nt":
        return [f"{chr(letter)}:\\" for letter in range(65, 91)
                if ctypes.windll.kernel32.GetDriveTypeW(f"{chr(letter)}:\\") in (2, 3)]
    roots = ["/"]
    if platform.system() == "Linux":
        try:
            for line in Path("/proc/self/mountinfo").read_text().splitlines():
                left, right = line.split(" - ", 1)
                fields, fs = left.split(), right.split()
                mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
                if fs[1].startswith("/dev/") and not mount.startswith(("/snap/", "/boot/")):
                    roots.append(mount)
        except (OSError, ValueError, IndexError):
            pass
    elif platform.system() == "Darwin":
        try:
            roots.extend(str(p) for p in Path("/Volumes").iterdir() if p.is_dir())
        except OSError:
            pass
    return list(dict.fromkeys(roots))


def default_excludes():
    if platform.system() == "Linux":
        return ["/proc", "/sys", "/dev", "/run", "/snap"]
    if platform.system() == "Darwin":
        return ["/dev", "/System/Volumes", "/private/var/vm"]
    return []


@dataclass(frozen=True)
class Hit:
    path: str
    kind: str
    keywords: str
    size: int = 0
    modified: float = 0
    identity: tuple = ()
    detail: str = ""
    match_source: str = ""
    location: str = ""
    snippet: str = ""
    matches: list = field(default_factory=list)
    match_count: int = 0
    matches_limited: bool = False
    content_error: str = ""
    matched_keywords: list = field(default_factory=list)


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)


def is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


class Scanner:
    def __init__(self, roots, keywords, events, excludes=(), programs=True, *, scan_mode="name",
                 content_types=None, max_content_mb=50, content_timeout=30, error_log=None):
        self.roots = list(dict.fromkeys(canonical(p) for p in roots))
        self.keywords = normalize_keywords(keywords)
        self.events = events
        self.excludes = [canonical(p) for p in excludes] + [canonical(data_dir())]
        self.programs = programs
        if scan_mode not in ("name", "content", "both"):
            raise ValueError("未知扫描模式")
        self.scan_mode = scan_mode
        self.content_types = list(CONTENT_TYPES) if content_types is None else list(content_types)
        self.max_content_bytes = int(max_content_mb) * 1024**2
        self.content_reader = ContentReader(content_timeout)
        self.content_read = self.content_skipped = 0
        self.files_examined = self.content_unsupported = self.excluded_entries = self.links_skipped = 0
        self.error_log_path = Path(error_log) if error_log else None
        self.error_stream = None
        self.stop = threading.Event()
        self.resume = threading.Event()
        self.resume.set()
        self.examined = self.hits = self.errors = self.directories = 0
        self.last = 0
        self.batch = []
        self.started = time.monotonic()
        self.current = ""
        self.error_samples = []
        self.permission_errors = 0
        self.denied_paths = []

    def checkpoint(self):
        while not self.resume.wait(0.1):
            if self.stop.is_set():
                return False
        return not self.stop.is_set()

    def match(self, name):
        folded = name.casefold()
        return " | ".join(k for k in self.keywords if k.casefold() in folded)

    def excluded(self, path):
        return any(beneath(path, p) for p in self.excludes)

    def error(self, path, exc):
        self.errors += 1
        denied = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 5 or getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM)
        if denied:
            self.permission_errors += 1
            if len(self.denied_paths) < 100 and str(path) not in self.denied_paths:
                self.denied_paths.append(str(path))
        if len(self.error_samples) < 100:
            category = "权限不足" if denied else "内容读取" if isinstance(exc, ContentReadError) else "访问错误"
            self.error_samples.append(f"[{category}] {path}: {exc}")
        if self.error_stream is not None:
            try:
                self.error_stream.write(json.dumps({"path": str(path), "reason": str(exc),
                    "category": "权限不足" if denied else "内容读取" if isinstance(exc, ContentReadError) else "访问错误"}, ensure_ascii=False) + "\n")
                self.error_stream.flush()
            except OSError:
                try:
                    self.error_stream.close()
                except OSError:
                    pass
                self.error_stream = None
                self.error_samples.append("完整错误清单写入失败，请检查工具数据目录的空间和权限。")

    def emit_hit(self, hit):
        self.hits += 1
        self.batch.append(hit)
        if len(self.batch) >= 100:
            self.flush()

    def flush(self):
        if self.batch:
            self.events.put(("hits", self.batch))
            self.batch = []

    def progress(self, force=False):
        now = time.monotonic()
        if force or now - self.last >= 0.2:
            self.flush()
            self.events.put(("progress", dict(examined=self.examined, hits=self.hits,
                errors=self.errors, directories=self.directories, current=self.current,
                permission_errors=self.permission_errors, elapsed=now-self.started,
                content_read=self.content_read, content_skipped=self.content_skipped)))
            self.events.put(("coverage", dict(files=self.files_examined, unsupported=self.content_unsupported,
                excluded=self.excluded_entries, links=self.links_skipped)))
            self.last = now

    def run(self):
        visited = set()
        try:
            if self.error_log_path:
                try:
                    self.error_log_path.parent.mkdir(parents=True, exist_ok=True)
                    self.error_stream = self.error_log_path.open("w", encoding="utf-8")
                except OSError as exc:
                    self.error(self.error_log_path, exc)
            # Global directory identities prevent duplicate traversal of nested roots/bind mounts.
            for root in self.roots:
                stack = [root]
                while stack and self.checkpoint():
                    current = stack.pop()
                    self.current = current
                    if self.excluded(current):
                        self.excluded_entries += 1
                        continue
                    try:
                        info = os.lstat(current)
                        if is_link(info):
                            self.links_skipped += 1
                            continue
                        identity = (info.st_dev, info.st_ino)
                        if identity in visited:
                            continue
                        visited.add(identity)
                        self.inspect(current, info)
                        self.directories += 1
                        with os.scandir(current) as entries:
                            for entry in entries:
                                if not self.checkpoint():
                                    break
                                self.examined += 1
                                try:
                                    if entry.is_symlink():
                                        self.links_skipped += 1
                                        continue
                                    if self.excluded(entry.path):
                                        self.excluded_entries += 1
                                        continue
                                    # Windows DirEntry.stat can report zero dev/ino; use the
                                    # same lstat API as later file-operation validation.
                                    info = os.lstat(entry.path) if os.name == "nt" else entry.stat(follow_symlinks=False)
                                    self.current = entry.path
                                    if is_link(info):
                                        self.links_skipped += 1
                                        continue
                                    if stat.S_ISDIR(info.st_mode):
                                        stack.append(entry.path)
                                    else:
                                        self.inspect(entry.path, info)
                                except OSError as exc:
                                    self.error(entry.path, exc)
                                self.progress()
                    except OSError as exc:
                        self.error(current, exc)
                    self.progress()
            if self.programs and self.checkpoint():
                self.current = "检查已安装程序"
                self.progress(True)
                self.scan_programs()
        except ContentCancelled:
            pass
        except Exception as exc:
            self.error(self.current, exc)
        finally:
            self.content_reader.close()
            if self.error_stream is not None:
                try:
                    self.error_stream.close()
                except OSError:
                    self.error_samples.append("完整错误清单保存失败，请检查工具数据目录的空间和权限。")
                self.error_stream = None
            self.progress(True)
            self.events.put(("done", dict(cancelled=self.stop.is_set(), errors=self.error_samples,
                permission_errors=self.permission_errors, denied_paths=self.denied_paths)))

    def inspect(self, path, info):
        regular, directory = stat.S_ISREG(info.st_mode), stat.S_ISDIR(info.st_mode)
        if not regular and not directory:
            return
        if regular:
            self.files_examined += 1
        name_words = [k for k in self.keywords if k.casefold() in Path(path).name.casefold()] if self.scan_mode != "content" else []
        content = dict(matched=[], location="", snippet="")
        content_error = ""
        if regular and self.scan_mode != "name" and not supports_content(path, self.content_types):
            self.content_unsupported += 1
        if regular and self.scan_mode != "name" and supports_content(path, self.content_types):
            self.current = "读取内容：" + str(path)
            self.progress(True)
            try:
                if info.st_size > self.max_content_bytes:
                    raise ContentReadError(f"文件超过 {self.max_content_bytes // 1024**2} MB 内容扫描上限")
                content = self.content_reader.read(path, self.keywords, self.checkpoint, self.progress)
                if fingerprint(os.lstat(path)) != fingerprint(info):
                    content = dict(matched=[], location="", snippet="")
                    raise ContentReadError("文件在读取时发生变化，请重新扫描")
                if content.get("error"):
                    error_type = PermissionError if content.get("permission") else ContentReadError
                    raise error_type(content["error"])
                self.content_read += 1
            except OSError as exc:
                content_error = str(exc)
                self.content_skipped += 1
                self.error(path, exc)
        matched = [k for k in self.keywords if k in name_words or k in content["matched"]]
        if matched:
            source = "名称+内容" if name_words and content["matched"] else "内容" if content["matched"] else "文件名"
            detail = f"命中来源：{source}"
            if content["location"]:
                detail += f"\n位置：{content['location']}\n内容片段：{content['snippet']}"
            if content_error:
                detail += "\n内容未完整读取：" + content_error
            self.emit_hit(Hit(str(path), "目录" if directory else "文件", " | ".join(matched),
                info.st_size if regular else 0, info.st_mtime, fingerprint(info), detail,
                source, content["location"], content["snippet"], content.get("matches", []),
                content.get("match_count", 0), content.get("matches_limited", False), content_error, matched))

    def scan_programs(self):
        if os.name == "nt":
            import winreg
            seen = set()
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                                            0, winreg.KEY_READ | view) as parent:
                            for index in range(winreg.QueryInfoKey(parent)[0]):
                                if not self.checkpoint():
                                    return
                                try:
                                    with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as key:
                                        name = winreg.QueryValueEx(key, "DisplayName")[0]
                                        matched = self.match(name)
                                        if matched:
                                            details = []
                                            for field in ("DisplayVersion", "Publisher", "InstallLocation"):
                                                try:
                                                    details.append(str(winreg.QueryValueEx(key, field)[0]))
                                                except OSError:
                                                    pass
                                            identity = (name, tuple(details))
                                            if identity not in seen:
                                                seen.add(identity)
                                                self.emit_hit(Hit(name, "程序", matched, detail=" | ".join(details)))
                                except OSError:
                                    continue
                    except OSError:
                        continue
        elif platform.system() == "Darwin":
            for folder in (Path("/Applications"), Path.home()/"Applications", Path("/System/Applications")):
                if not folder.exists():
                    continue
                try:
                    for current, dirs, _files in os.walk(folder, followlinks=False):
                        if not self.checkpoint():
                            return
                        for name in dirs[:]:
                            if name.endswith(".app"):
                                dirs.remove(name)
                                matched = self.match(name)
                                if matched:
                                    self.emit_hit(Hit(str(Path(current)/name), "程序", matched,
                                                      detail="macOS 应用；仅展示"))
                except OSError as exc:
                    self.error(str(folder), exc)
        else:
            for command in (["dpkg-query", "-W", "-f=${Package}\\n"], ["rpm", "-qa"],
                            ["snap", "list"], ["flatpak", "list", "--columns=application,name"]):
                if not shutil.which(command[0]) or not self.checkpoint():
                    continue
                self.current = "检查已安装程序：" + command[0]
                self.progress(True)
                try:
                    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, errors="replace") as proc:
                        deadline = time.monotonic() + 20
                        while True:
                            try:
                                output, errors = proc.communicate(timeout=0.2)
                                break
                            except subprocess.TimeoutExpired:
                                if self.stop.is_set() or time.monotonic() > deadline:
                                    proc.kill()
                                    proc.communicate()
                                    if not self.stop.is_set():
                                        self.error(command[0], "程序清单查询超时")
                                    output = ""
                                    break
                        if proc.returncode and not self.stop.is_set():
                            self.error(command[0], "程序清单查询失败")
                        for name in output.splitlines():
                            if not self.checkpoint():
                                return
                            matched = self.match(name)
                            if matched:
                                self.emit_hit(Hit(name.strip(), "程序", matched, detail=command[0]))
                except OSError as exc:
                    self.error(command[0], exc)


def save_json(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def export_report(path, hits, metadata):
    from .reporting import export_report as write_report
    return write_report(path, hits, metadata)


class FileActions:
    """Validated file actions; existing recovery manifests remain readable for migration."""
    def __init__(self, storage=None):
        self.storage = Path(storage) if storage else data_dir()
        self.bin = self.storage / "recovery"

    def selected(self, hits):
        hits = [h for h in hits if h.kind in ("文件", "目录")]
        paths = compact_paths(h.path for h in hits)
        return [h for h in hits if canonical(h.path) in paths]

    def validate(self, hit):
        path = Path(hit.path)
        if hit.kind not in ("文件", "目录"):
            raise ValueError("已安装程序仅供查看，请使用系统的卸载功能")
        # Protect scan tool, recovery storage, filesystem roots and home directory.
        protected = [Path(path.anchor), Path.home(), Path(__file__).resolve().parent,
                     Path(__file__).resolve().parent.parent / "seekwords.py", self.storage]
        if getattr(sys, "frozen", False):
            protected.append(Path(sys.executable).resolve())
        for value in protected:
            if beneath(value, path):
                raise ValueError("此路径包含系统根目录、用户主目录或工具数据，禁止操作")
        resolved = path.resolve()
        if canonical(resolved) != canonical(path):
            raise ValueError("路径包含符号链接，已拒绝操作；请重新扫描真实路径")
        info = path.lstat()
        if is_link(info):
            raise ValueError("不操作符号链接或 Windows 重解析点")
        if hit.identity and tuple(hit.identity) != fingerprint(info):
            raise ValueError("文件或目录已在扫描后发生变化，请重新扫描后操作")
        return path

    def move(self, hit, destination, *, allow_readonly=False):
        source = self.validate(hit)
        folder = Path(destination).resolve(strict=True)
        if not folder.is_dir():
            raise ValueError("目标必须是目录")
        target = folder / source.name
        if beneath(folder, source) or beneath(folder, self.storage):
            raise ValueError("目标不能是源目录内部或工具数据目录")
        if os.path.lexists(target):
            raise FileExistsError(f"目标已存在，未覆盖：{target}")
        return move_path(source, target, allow_readonly=allow_readonly)

    def delete(self, hit, *, permanent=False, allow_readonly=False):
        source = self.validate(hit)
        return delete_path(source, permanent=permanent, allow_readonly=allow_readonly)

    def records(self):
        result = []
        for path in self.bin.glob("*.json"):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                # Keep old interrupted copies visible; restore still refuses existing originals.
                if os.path.lexists(entry["stored"]):
                    result.append(entry)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(result, key=lambda e: e["time"], reverse=True)

    def restore(self, entry, *, allow_readonly=False):
        # Read the manifest again instead of accepting arbitrary paths from a stale UI.
        record = self.bin / (entry["id"] + ".json")
        entry = json.loads(record.read_text(encoding="utf-8"))
        source, target = Path(entry["stored"]), Path(entry["original"])
        if not beneath(source, self.bin / entry["id"]):
            raise ValueError("回收记录的路径无效")
        if os.path.lexists(target):
            raise FileExistsError(f"原路径已存在，未覆盖：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        move_path(source, target, allow_readonly=allow_readonly)
        record.unlink()
        try:
            source.parent.rmdir()
        except OSError:
            pass
        return str(target)
