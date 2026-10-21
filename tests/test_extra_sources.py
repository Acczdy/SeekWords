"""Local caches, registry values and OCR remain read-only and attributable."""
from contextlib import closing
import os
from pathlib import Path
import plistlib
import queue
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch

from scan_keywords_app.content import iter_content
from scan_keywords_app.core import Scanner
from scan_keywords_app.core import Hit, export_report
from scan_keywords_app.extra_sources import discover_extra_roots, trash_metadata, windows_trash_metadata
from scan_keywords_app.ocr import ocr_lines
from scan_keywords_app.registry_sources import classify_registry_path, iter_registry


class FakeReader:
    def __init__(self):
        self.options = []

    def read(self, path, keywords, checkpoint, progress, **options):
        self.options.append(options)
        return {"matched": [keywords[0]], "location": "第 1 行", "snippet": keywords[0],
                "matches": [{"position": "第 1 行", "text": keywords[0]}], "match_count": 1}


class ExtraSourceTests(unittest.TestCase):
    @staticmethod
    def next_hit(events):
        while not events.empty():
            event, payload = events.get_nowait()
            if event == "hits":
                return payload[0]
        raise AssertionError("no hit was emitted")

    @unittest.skipIf(os.name == "nt", "XDG trash uses POSIX paths")
    def test_discovery_and_xdg_trash_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            cache = home / ".cache"
            cache.mkdir()
            config = home / ".config"
            config.mkdir()
            trash = home / ".local/share/Trash"
            (trash / "files").mkdir(parents=True)
            (trash / "info").mkdir()
            item = trash / "files" / "notes.txt"
            item.write_text("ORBIT", encoding="utf-8")
            (trash / "info/notes.txt.trashinfo").write_text(
                "[Trash Info]\nPath=/home/test/My%20Notes/notes.txt\nDeletionDate=2026-10-21T11:30:00\n",
                encoding="utf-8")
            roots = discover_extra_roots("Linux", home, {"TMPDIR": str(home / "missing")})
            self.assertIn((str(cache), "用户缓存"), roots)
            self.assertIn((str(config), "用户配置"), roots)
            self.assertIn((str(trash / "files"), "回收站"), roots)
            self.assertEqual(trash_metadata(item, home),
                             ("/home/test/My Notes/notes.txt", "2026-10-21T11:30:00"))

    def test_binary_plist_and_sqlite_cache_are_read_without_sidecars(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            plist = folder / "office.plist"
            plist.write_bytes(plistlib.dumps({"recent": ["ORBIT.docx"]}, fmt=plistlib.FMT_BINARY))
            self.assertIn(("配置 / recent / 1", "ORBIT.docx"), list(iter_content(plist)))
            database = folder / "cache.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE recent (name TEXT, number INTEGER)")
                connection.execute("INSERT INTO recent VALUES ('ORBIT.xlsx', 7)")
                connection.commit()
            rows = list(iter_content(database))
            self.assertIn(("表 recent · 行 1 · 列 name", "ORBIT.xlsx"), rows)
            self.assertFalse((folder / "cache.db-shm").exists())
            self.assertFalse((folder / "cache.db-wal").exists())

    def test_windows_recycle_metadata_is_read_without_changing_items(self):
        with tempfile.TemporaryDirectory() as temporary:
            recycle = Path(temporary) / "$Recycle.Bin" / "S-1-5-21-test"
            recycle.mkdir(parents=True)
            item = recycle / "$RABC123.docx"
            item.write_bytes(b"document")
            original = "C:\\Users\\Test\\ORBIT.docx"
            name = (original + "\0").encode("utf-16-le")
            (recycle / "$IABC123.docx").write_bytes(
                struct.pack("<QQQI", 2, 8, 133000000000000000, len(name) // 2) + name)
            metadata = windows_trash_metadata(item)
            self.assertEqual(metadata[0], original)
            self.assertTrue(metadata[1].startswith("2022-"))
            self.assertEqual(item.read_bytes(), b"document")

    def test_report_keeps_source_and_recycle_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "result.html"
            hit = Hit("/trash/$R001.txt", "文件", "ORBIT", detail="原路径：/work/ORBIT.txt\n删除时间：2026-10-21",
                      matched_keywords=["ORBIT"], origin="回收站")
            export_report(destination, [hit], {"version": "1.2", "extra_roots": [
                {"path": "/trash", "label": "回收站"}]})
            html = destination.read_text(encoding="utf-8")
            self.assertIn('data-origin="回收站"', html)
            self.assertIn("/work/ORBIT.txt", html)
            self.assertIn("筛选位置来源", html)

    def test_cache_text_fallback_and_ocr_have_distinct_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            unknown = folder / "snippet.cache"
            unknown.write_text("ORBIT cache note", encoding="utf-8")
            events = queue.Queue()
            scanner = Scanner([], ["ORBIT"], events, programs=False, scan_mode="both",
                              content_types=["配置与缓存"], extra_roots=[(folder, "用户缓存")])
            reader = scanner.content_reader = FakeReader()
            scanner.inspect(unknown, unknown.stat())
            scanner.flush()
            hit = self.next_hit(events)
            self.assertEqual(hit.origin, "用户缓存")
            self.assertEqual(hit.match_source, "内容")
            self.assertTrue(reader.options[0]["force_text"])
            image = folder / "capture.png"
            image.write_bytes(b"not a real image")
            scanner.ocr_engine = "rapidocr"
            scanner.ocr_language = "中英混合"
            scanner.inspect(image, image.stat())
            scanner.flush()
            image_hit = self.next_hit(events)
            self.assertEqual(image_hit.match_source, "图片OCR")
            self.assertEqual(image_hit.origin, "用户缓存")
            self.assertEqual(reader.options[1]["ocr_language"], "中英混合")

    def test_single_recent_history_file_can_be_a_scan_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary) / "recently-used.xbel"
            history.write_text("<bookmarks/>", encoding="utf-8")
            events = queue.Queue()
            scanner = Scanner([], ["recently"], events, programs=False, scan_mode="name",
                              extra_roots=[(history, "打开历史")])
            scanner.run()
            self.assertEqual(self.next_hit(events).origin, "打开历史")
            self.assertEqual(scanner.examined, 1)

    def test_nested_cache_root_is_scanned_once_with_its_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            cache = folder / "cache"
            cache.mkdir()
            (cache / "ORBIT.txt").write_text("x", encoding="utf-8")
            events = queue.Queue()
            scanner = Scanner([folder], ["ORBIT"], events, programs=False,
                              extra_roots=[(cache, "用户缓存")])
            scanner.run()
            self.assertEqual(scanner.hits, 1)
            self.assertEqual(self.next_hit(events).origin, "用户缓存")

    def test_bundled_ocr_returns_text_with_pixel_location(self):
        result = type("Result", (), {"txts": ("ORBIT plan",),
                                    "boxes": ([[10, 20], [65, 20], [65, 30], [10, 30]],)})()
        with patch("scan_keywords_app.ocr._get_engine", return_value=lambda _path: result):
            rows = list(ocr_lines("capture.png"))
        self.assertEqual(rows, [("图片第 1 处 · 像素 (10,20)–(65,30)", "ORBIT plan")])

    def test_registry_key_value_and_binary_coverage(self):
        self.assertEqual(classify_registry_path(r"HKCU\Software\Microsoft\Windows\RecentDocs"), "打开历史")
        self.assertEqual(classify_registry_path(r"HKLM\SYSTEM\CurrentControlSet\Services\bam\State"), "系统痕迹（注册表）")
        class Api:
            HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, HKEY_USERS = 1, 2, 3
            KEY_READ = 1
            REG_SZ, REG_EXPAND_SZ, REG_MULTI_SZ, REG_DWORD, REG_QWORD, REG_BINARY = range(1, 7)
            records = {(1, ""): (["Software"], []),
                       (1, "Software"): ([], [("Recent", "ORBIT.txt", REG_SZ),
                                               ("blob", b"ORBIT", REG_BINARY)]),
                       (2, ""): ([], []), (3, ""): ([], [])}

            @classmethod
            def OpenKey(cls, hive, relative, _reserved, _access):
                class Key:
                    def __enter__(self):
                        return hive, relative
                    def __exit__(self, *_args):
                        pass
                return Key()

            @classmethod
            def QueryInfoKey(cls, key):
                children, values = cls.records[key]
                return len(children), len(values), 0

            @classmethod
            def EnumKey(cls, key, index):
                return cls.records[key][0][index]

            @classmethod
            def EnumValue(cls, key, index):
                return cls.records[key][1][index]

        entries = list(iter_registry(lambda: True, lambda *_: None, Api))
        self.assertEqual(sum(entry.kind == "binary" for entry in entries), 1)
        events = queue.Queue()
        scanner = Scanner([], ["ORBIT"], events, programs=False)
        with patch("scan_keywords_app.core.iter_registry", return_value=iter(entries)):
            scanner.scan_registry()
        scanner.flush()
        hit = self.next_hit(events)
        self.assertEqual(hit.kind, "注册表")
        self.assertEqual(hit.match_source, "注册表数据")
        self.assertEqual(hit.origin, "Windows 注册表")
        self.assertEqual(scanner.registry_unreadable, 1)


if __name__ == "__main__":
    unittest.main()
