"""Explicit deletion of registry targets recorded by the scanner."""
from __future__ import annotations


def target(hit):
    if hit.kind != "注册表" or hit.registry_kind not in ("key", "value"):
        raise ValueError("结果没有完整的注册表目标信息，请重新扫描后操作")
    hive, separator, relative = hit.registry_key.partition("\\")
    hive = hive.upper()
    if hive not in ("HKCU", "HKLM", "HKU") or not separator or not relative:
        raise ValueError("不能删除注册表根键，或注册表路径无效")
    if any(not part for part in relative.split("\\")):
        raise ValueError("注册表路径无效，请重新扫描")
    return hive, relative


def covers(parent, child):
    """Compare structured targets, never split a display path/value name."""
    if parent.kind != "注册表" or child.kind != "注册表":
        return False
    if not parent.registry_key or not child.registry_key:
        return False
    parent_key, child_key = parent.registry_key.casefold(), child.registry_key.casefold()
    if parent.registry_kind == "key":
        return child_key == parent_key or child_key.startswith(parent_key + "\\")
    return (parent.registry_kind == child.registry_kind == "value" and parent_key == child_key
            and parent.registry_value.casefold() == child.registry_value.casefold())


def selected(hits):
    unique = []
    for hit in hits:
        target(hit)
        if any(covers(parent, hit) for parent in unique):
            continue
        unique = [child for child in unique if not covers(hit, child)]
        unique.append(hit)
    return unique


def delete(hit, api=None):
    """Delete a single value or a key tree in the scanner's native view.

    Enumerate the entire tree before deleting any key. Access errors propagate;
    they must never be interpreted as the end of an enumeration.
    """
    hive_name, relative = target(hit)
    if api is None:
        import winreg as api
    hive = {"HKCU": api.HKEY_CURRENT_USER, "HKLM": api.HKEY_LOCAL_MACHINE,
            "HKU": api.HKEY_USERS}[hive_name]
    if hit.registry_kind == "value":
        with api.OpenKey(hive, relative, 0, api.KEY_SET_VALUE) as key:
            api.DeleteValue(key, hit.registry_value)
    else:
        pending, ordered = [relative], []
        while pending:
            path = pending.pop()
            with api.OpenKey(hive, path, 0, api.KEY_READ | 0x00010000) as key:
                count = api.QueryInfoKey(key)[0]
                pending.extend(path + "\\" + api.EnumKey(key, index) for index in range(count))
            ordered.append(path)
        for path in reversed(ordered):
            # DeleteKeyEx defaults to the 64-bit view; 0 follows OpenKey's
            # native view, including when SeekWords runs in 32-bit Python.
            api.DeleteKeyEx(hive, path, access=0)
    return ""
