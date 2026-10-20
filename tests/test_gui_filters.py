"""Result filters should use exact matched words and readable counters."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scan_keywords_app.core import Hit
from scan_keywords_app.gui import App


class Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class GuiFilterTests(unittest.TestCase):
    def make_app(self):
        app = App.__new__(App)
        app.query = Variable("")
        app.keyword_filter = Variable("全部关键词")
        app.kind = Variable("全部类型")
        app.file_category = Variable("全部文件类别")
        app.extensions = Variable("")
        app.source_filter = Variable("全部来源")
        return app

    def test_keyword_filter_matches_complete_words_case_insensitively(self):
        app = self.make_app()
        app.keyword_filter.set("orbit")
        self.assertTrue(app.visible(Hit("/a.txt", "文件", "ORBIT | Alpha", matched_keywords=["ORBIT", "Alpha"])))
        self.assertFalse(app.visible(Hit("/b.txt", "文件", "ORBITAL")))
        self.assertTrue(app.visible(Hit("program", "程序", "ORBIT")))

    def test_source_filter_includes_mixed_content_matches(self):
        app = self.make_app()
        content = Hit("/a.txt", "文件", "ORBIT", match_source="内容")
        mixed = Hit("/b.txt", "文件", "ORBIT", match_source="名称+内容")
        filename = Hit("/c.txt", "文件", "ORBIT", match_source="文件名")
        app.source_filter.set("包含内容命中")
        self.assertTrue(app.visible(content))
        self.assertTrue(app.visible(mixed))
        self.assertFalse(app.visible(filename))
        app.source_filter.set("仅名称命中")
        self.assertFalse(app.visible(content))
        self.assertTrue(app.visible(filename))

    def test_checked_count_has_no_grouping_punctuation(self):
        app = self.make_app()
        app.examined_value = Variable("")
        app.hits_value = Variable("")
        app.errors_value = Variable("")
        app.elapsed_value = Variable("")
        app.update_metrics(dict(examined=668888, hits=12000, errors=1000, elapsed=1))
        self.assertEqual(app.examined_value.get(), "668888")
        self.assertEqual(app.hits_value.get(), "12000")
        self.assertEqual(app.errors_value.get(), "1000")

    def test_export_can_include_only_current_keyword_filter(self):
        app = self.make_app()
        app.operating = False
        app.scanner = None
        app.root = None
        app.results = {
            "1": Hit("/a.txt", "文件", "ORBIT"),
            "2": Hit("/b.txt", "文件", "Alpha"),
        }
        app.metadata = {"version": "1.1"}
        app.action_log = []
        app.status = Variable("")
        app.export_filtered = Variable(True)
        app.keyword_filter.set("ORBIT")
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.json"
            with patch("scan_keywords_app.gui.filedialog.asksaveasfilename", return_value=str(target)):
                app.export()
            data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual([row["path"] for row in data["results"]], ["/a.txt"])
        self.assertEqual(data["scan"]["report_scope"], "当前筛选结果")


if __name__ == "__main__":
    unittest.main()
