"""Release behavior: no bundled words, safe reports, readable binary skips."""
import csv
from pathlib import Path
import tempfile
import unittest
import zipfile

from scan_keywords_app.core import DEFAULT_KEYWORDS, Hit, export_report
from scan_keywords_app.content import ContentReadError, _text_file, iter_content
from scan_keywords_app.matching import MatchCollector


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="seekwords-release-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_no_bundled_keywords_and_escaped_readable_report(self):
        self.assertEqual(DEFAULT_KEYWORDS, [])
        hit = Hit(str(self.root / '<script>alert(1)</script>.txt'), "文件", "ORBIT",
                  match_source="内容", match_count=1,
                  matches=[{"position": "文本正文 · 第 2 行，第 1 列", "text": "<script>ORBIT</script>",
                            "highlight_start": 8, "highlight_end": 13}])
        report = self.root / "report.html"
        export_report(report, [hit], {"version": "1.0", "keywords": ["ORBIT"], "roots": [str(self.root)]})
        document = report.read_text(encoding="utf-8")
        self.assertIn("<mark>ORBIT</mark>", document)
        self.assertIn("&lt;script&gt;", document)
        self.assertNotIn("<script>alert(1)</script>", document)
        self.assertIn("data-filter=", document)
        self.assertIn("扫描范围", document)

    def test_csv_formula_is_literal_and_has_single_row_per_hit(self):
        report = self.root / "report.csv"
        hit = Hit("=1+1", "文件", "ORBIT", match_source="文件名")
        export_report(report, [hit], {})
        with report.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], "'=1+1")

    def test_binary_with_script_extension_is_a_readable_skip(self):
        path = self.root / "fake.ps1"
        path.write_bytes(bytes([0x80, 0x81, 0x82, 0xff]) * 100)
        with self.assertRaises(ContentReadError) as exc:
            list(_text_file(path))
        self.assertIn("编码无法识别", str(exc.exception))

    def test_docx_pptx_xlsx_extract_locations(self):
        examples = {
            "letter.docx": ("word/document.xml", '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>ORBIT</w:t></w:r></w:p></w:body></w:document>', "正文"),
            "slides.pptx": ("ppt/slides/slide1.xml", '<p:sld xmlns:p="p" xmlns:a="a"><a:p><a:r><a:t>ORBIT</a:t></a:r></a:p></p:sld>', "幻灯片"),
            "table.xlsx": ("xl/worksheets/sheet1.xml", '<worksheet><sheetData><row><c r="B2" t="inlineStr"><is><t>ORBIT</t></is></c></row></sheetData></worksheet>', "B2"),
        }
        for name, (part, content, location) in examples.items():
            with self.subTest(name=name):
                path = self.root / name
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(part, content)
                collector = MatchCollector(["ORBIT"])
                for where, text in iter_content(path):
                    collector.feed(where, text)
                match = collector.result()
                self.assertEqual(match["matched"], ["ORBIT"])
                self.assertIn(location, match["location"])


if __name__ == "__main__":
    unittest.main()
