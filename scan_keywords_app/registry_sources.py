"""Read-only enumeration of Windows registry keys and readable values."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegistryEntry:
    kind: str
    key_path: str
    name: str = ""
    text: str = ""
    value_type: int = 0


def classify_registry_path(path):
    folded = path.casefold()
    if any(part in folded for part in ("\\recentdocs", "\\runmru", "\\typedpaths",
                                       "\\opensavepidlmru", "\\lastvisitedpidlmru", "\\wordwheelquery")):
        return "打开历史"
    if any(part in folded for part in ("\\bam\\", "\\dam\\", "\\bagmru", "\\shell\\bags",
                                       "\\appcompatflags\\", "\\amcache")):
        return "系统痕迹（注册表）"
    return "Windows 注册表"


def readable_value(value, value_type, api):
    if value_type in (api.REG_SZ, api.REG_EXPAND_SZ):
        return value if isinstance(value, str) and len(value) <= 1024 * 1024 else None
    if value_type == api.REG_MULTI_SZ:
        if isinstance(value, list) and sum(len(part) for part in value) <= 1024 * 1024:
            return "\n".join(value)
        return None
    if value_type in (api.REG_DWORD, api.REG_QWORD):
        return f"{value} (0x{value:X})" if isinstance(value, int) else None
    return None


def iter_registry(checkpoint, on_error, api=None):
    """Yield keys and values from loaded user and machine hives.

    HKCR and HKCC are merged/alias views of these hives and are omitted to
    avoid returning the same values again under different paths.
    """
    if api is None:
        import winreg as api
    hives = (("HKCU", api.HKEY_CURRENT_USER), ("HKLM", api.HKEY_LOCAL_MACHINE),
             ("HKU", api.HKEY_USERS))
    for label, hive in hives:
        pending = [""]
        while pending and checkpoint():
            relative = pending.pop()
            key_path = label + ("\\" + relative if relative else "")
            try:
                with api.OpenKey(hive, relative, 0, api.KEY_READ) as key:
                    subkeys, values, _modified = api.QueryInfoKey(key)
                    yield RegistryEntry("key", key_path, relative.rsplit("\\", 1)[-1] if relative else label)
                    for index in range(values):
                        if not checkpoint():
                            return
                        try:
                            name, data, value_type = api.EnumValue(key, index)
                            text = readable_value(data, value_type, api)
                            yield RegistryEntry("value" if text is not None else "binary",
                                                key_path, name, text or "", value_type)
                        except OSError as exc:
                            on_error(key_path, exc)
                    for index in range(subkeys - 1, -1, -1):
                        if not checkpoint():
                            return
                        try:
                            child = api.EnumKey(key, index)
                            pending.append(relative + "\\" + child if relative else child)
                        except OSError as exc:
                            on_error(key_path, exc)
            except OSError as exc:
                on_error(key_path, exc)
