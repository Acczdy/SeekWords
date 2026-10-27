"""Registry deletions use exact structured targets and respect access errors."""
from contextlib import contextmanager
import os
import unittest
import uuid

from scan_keywords_app.core import Hit
from scan_keywords_app import registry_actions


def key(path):
    return Hit(path, "注册表", "ORBIT", registry_kind="key", registry_key=path)


def value(path, name=""):
    return Hit(path + "\\" + (name or "(默认)"), "注册表", "ORBIT", registry_kind="value",
               registry_key=path, registry_value=name)


class RegistryApi:
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, HKEY_USERS = 1, 2, 3
    KEY_SET_VALUE, KEY_READ, DELETE = 2, 8, 16

    def __init__(self):
        self.records = {r"Software\Fixture": {"": "default", "(默认)": "literal", r"a\b": "named"},
                        r"Software\Fixture\child": {"x": "y"},
                        r"Software\FixtureOther": {"keep": "yes"}}
        self.deleted = []
        self.denied = None

    @contextmanager
    def OpenKey(self, hive, relative, _reserved, _access):
        if relative == self.denied:
            raise PermissionError("access denied")
        if relative not in self.records:
            raise FileNotFoundError(relative)
        yield relative

    def children(self, path):
        return [item[len(path) + 1:] for item in self.records
                if item.startswith(path + "\\") and "\\" not in item[len(path) + 1:]]

    def QueryInfoKey(self, path):
        return len(self.children(path)), len(self.records[path]), 0

    def EnumKey(self, path, index):
        return self.children(path)[index]

    def DeleteValue(self, path, name):
        del self.records[path][name]

    def DeleteKeyEx(self, hive, path, *, access):
        assert access == 0, "must use the same native view as scanning"
        assert not self.children(path), "children must be removed before parents"
        del self.records[path]
        self.deleted.append(path)


class RegistryActionTests(unittest.TestCase):
    def test_default_literal_default_and_backslash_names_are_distinct(self):
        api = RegistryApi()
        registry_actions.delete(value(r"HKCU\Software\Fixture"), api)
        self.assertNotIn("", api.records[r"Software\Fixture"])
        self.assertEqual(api.records[r"Software\Fixture"]["(默认)"], "literal")
        registry_actions.delete(value(r"HKCU\Software\Fixture", r"a\b"), api)
        self.assertNotIn(r"a\b", api.records[r"Software\Fixture"])
        self.assertIn(r"Software\Fixture\child", api.records)
        self.assertFalse(registry_actions.covers(value(r"HKCU\Software\Fixture"), value(r"HKCU\Software\Fixture", "(默认)")))

    def test_key_delete_includes_descendants_but_not_prefix_neighbors(self):
        api = RegistryApi()
        registry_actions.delete(key(r"HKCU\Software\Fixture"), api)
        self.assertEqual(api.deleted, [r"Software\Fixture\child", r"Software\Fixture"])
        self.assertIn(r"Software\FixtureOther", api.records)

    def test_selection_collapses_only_real_descendants_case_insensitively(self):
        parent = key(r"HKCU\Software\Fixture")
        neighbor = key(r"HKCU\Software\FixtureOther")
        self.assertEqual(registry_actions.selected([value(r"hkcu\software\fixture", "a"),
            key(r"HKCU\Software\Fixture\child"), neighbor, parent, parent]), [neighbor, parent])
        self.assertFalse(registry_actions.covers(parent, neighbor))

    def test_denied_child_aborts_before_any_key_is_deleted(self):
        api = RegistryApi()
        api.denied = r"Software\Fixture\child"
        with self.assertRaises(PermissionError):
            registry_actions.delete(key(r"HKCU\Software\Fixture"), api)
        self.assertEqual(api.deleted, [])

    def test_hive_and_legacy_display_only_hits_are_rejected(self):
        for hit in (key("HKCU"), key("HKCU\\"), key(r"OTHER\Software"),
                    Hit(r"HKCU\Software\Fixture", "注册表", "ORBIT")):
            with self.assertRaises(ValueError):
                registry_actions.delete(hit, RegistryApi())

    @unittest.skipUnless(os.name == "nt", "native Windows registry integration")
    def test_native_registry_deletes_only_its_own_temporary_fixture(self):
        import winreg
        relative = "Software\\SeekWordsTest_" + uuid.uuid4().hex
        full = "HKCU\\" + relative
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, relative) as handle:
                for name in ("", "(默认)", r"a\b", "keep"):
                    winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, "ORBIT")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, relative + "\\child"):
                pass
            registry_actions.delete(value(full))
            registry_actions.delete(value(full, r"a\b"))
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, relative) as handle:
                with self.assertRaises(FileNotFoundError):
                    winreg.QueryValueEx(handle, "")
                self.assertEqual(winreg.QueryValueEx(handle, "keep")[0], "ORBIT")
                self.assertEqual(winreg.QueryValueEx(handle, "(默认)")[0], "ORBIT")
            registry_actions.delete(key(full))
            with self.assertRaises(FileNotFoundError):
                winreg.OpenKey(winreg.HKEY_CURRENT_USER, relative)
        finally:
            for path in (relative + "\\child", relative):
                try:
                    winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, path, access=0)
                except FileNotFoundError:
                    pass
