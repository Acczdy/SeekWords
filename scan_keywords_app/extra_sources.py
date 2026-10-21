"""Optional user-level configuration, temporary and office cache locations."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import struct
import tempfile
from urllib.parse import unquote
from datetime import datetime, timedelta, timezone


def discover_extra_roots(system=None, home=None, environ=None):
    """Return existing paths with source labels, without traversing them."""
    system = system or platform.system()
    env = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    candidates = []

    def add(value, label):
        if value:
            candidates.append((Path(value).expanduser(), label))

    if system == "Windows":
        local = Path(env.get("LOCALAPPDATA") or home / "AppData/Local")
        roaming = Path(env.get("APPDATA") or home / "AppData/Roaming")
        add(env.get("TEMP") or local / "Temp", "临时文件")
        add(local / "Microsoft/Office", "Microsoft Office 缓存")
        add(local / "Microsoft/OneNote", "OneNote 缓存")
        add(roaming / "Microsoft/Office/Recent", "Office 最近使用")
        add(roaming / "Microsoft/Windows/Recent", "打开历史")
        add(local / "Microsoft/Windows/INetCache", "系统网页缓存")
        add(local / "Microsoft/Edge/User Data/Default/Cache", "Edge 缓存")
        add(local / "Google/Chrome/User Data/Default/Cache", "Chrome 缓存")
        add(local / "Mozilla/Firefox/Profiles", "Firefox 数据")
        add(local / "Tencent/WeChat", "微信数据")
        add(roaming / "Tencent/WeChat", "微信数据")
        add(home / "Documents/WeChat Files", "微信文件")
        add(home / "Documents/xwechat_files", "微信文件")
        add(local / "DingTalk", "钉钉数据")
        add(roaming / "DingTalk", "钉钉数据")
        add(roaming / "Alibaba/DingTalk", "钉钉数据")
        add(local / "Kingsoft/WPS Office", "WPS 数据")
        add(roaming / "Kingsoft/office6", "WPS 数据")
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            add(Path(f"{letter}:/$Recycle.Bin"), "回收站")
    elif system == "Darwin":
        add(home / "Library/Caches", "用户缓存")
        add(home / "Library/Preferences", "用户配置")
        add(home / "Library/Application Support/com.apple.sharedfilelist", "打开历史")
        add(home / ".Trash", "回收站")
        add(home / "Library/Group Containers/UBF8T346G9.Office", "Microsoft Office 数据")
        add(home / "Library/Application Support/Microsoft/Office", "Microsoft Office 数据")
        for app in ("Word", "Excel", "Powerpoint", "Outlook", "OneNote"):
            add(home / f"Library/Containers/com.microsoft.{app}/Data/Library/Caches", f"{app} 缓存")
        add(home / "Library/Containers/com.tencent.xinWeChat/Data/Library/Caches", "微信缓存")
        add(home / "Library/Containers/com.alibaba.DingTalkMac/Data/Library/Caches", "钉钉缓存")
        add(home / "Library/Containers/com.kingsoft.wpsoffice.mac/Data/Library/Caches", "WPS 缓存")
        add(env.get("TMPDIR"), "临时文件")
    else:
        add(env.get("XDG_CACHE_HOME") or home / ".cache", "用户缓存")
        add(env.get("XDG_CONFIG_HOME") or home / ".config", "用户配置")
        add(home / ".local/share/libreoffice", "LibreOffice 数据")
        add(home / ".local/share/onlyoffice", "ONLYOFFICE 数据")
        add(home / ".local/share/recently-used.xbel", "打开历史")
        add(home / ".local/share/Trash/files", "回收站")
        add(env.get("TMPDIR") or tempfile.gettempdir(), "临时文件")

    result, seen = [], set()
    for path, label in candidates:
        try:
            path = path.resolve(strict=True)
            if not (path.is_dir() or path.is_file()):
                continue
        except (OSError, RuntimeError):
            continue
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append((str(path), label))
    return result


def trash_metadata(path, home=None):
    """Read the XDG trash info for a file without changing its trash state."""
    home = Path.home() if home is None else Path(home)
    trash = home / ".local/share/Trash"
    try:
        relative = Path(path).relative_to(trash / "files")
        info = trash / "info" / (relative.parts[0] + ".trashinfo")
        data = info.read_bytes()[:8192].decode("utf-8", errors="replace")
    except (ValueError, IndexError, OSError):
        return None
    values = {}
    for line in data.splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            if name in ("Path", "DeletionDate"):
                values[name] = value.strip()
    if not values.get("Path"):
        return None
    original = Path(unquote(values["Path"]))
    if len(relative.parts) > 1:
        original = original.joinpath(*relative.parts[1:])
    return str(original), values.get("DeletionDate", "")


def windows_trash_metadata(path):
    """Read Windows $I metadata paired with a $R recycle-bin entry."""
    item = Path(path)
    recycled = next((part for part in reversed(item.parts)
                     if part.upper().startswith("$R") and part.casefold() != "$recycle.bin" and len(part) > 2), None)
    if recycled is None:
        return None
    parts = item.parts
    index = next((i for i, part in enumerate(parts) if part == recycled), -1)
    if index < 1 or "$recycle.bin" not in str(Path(*parts[:index])).casefold():
        return None
    info = Path(*parts[:index]) / ("$I" + recycled[2:])
    try:
        data = info.read_bytes()[:32768]
        if len(data) < 28:
            return None
        version, _size, filetime = struct.unpack_from("<QQQ", data)
        if version == 2:
            length = struct.unpack_from("<I", data, 24)[0]
            if not 0 < length <= 16384 or len(data) < 28 + length * 2:
                return None
            original = data[28:28 + length * 2].decode("utf-16-le").rstrip("\0")
        elif version == 1:
            original = data[24:24 + 520].decode("utf-16-le").split("\0", 1)[0]
        else:
            return None
        if not original:
            return None
        if len(parts) > index + 1:
            original = str(Path(original).joinpath(*parts[index + 1:]))
        date = (datetime(1601, 1, 1, tzinfo=timezone.utc) +
                timedelta(microseconds=filetime // 10)).astimezone().isoformat(timespec="seconds")
        return original, date
    except (OSError, UnicodeError, ValueError, OverflowError, struct.error):
        return None
