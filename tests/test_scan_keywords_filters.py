import unittest

from scan_keywords_app.file_filters import matches_file_filter, parse_extensions


class ResultFilterTests(unittest.TestCase):
    def test_word_text_script_and_presentation_categories(self):
        for name, category in (("a.DOCX", "Word 文档"), ("a.md", "文本文件"),
                               ("a.PS1", "脚本文件"), ("a.pptm", "PPT 演示"), ("a.xlsx", "Excel 表格")):
            self.assertTrue(matches_file_filter("C:\\a.ppt\\" + name, "文件", category))
            self.assertFalse(matches_file_filter(name + ".exe", "文件", category))

    def test_custom_extensions_match_whole_suffixes_and_intersect_category(self):
        self.assertEqual(parse_extensions(" DOC,*.docx；.docm .DOC "), (".doc", ".docx", ".docm"))
        self.assertTrue(matches_file_filter("/a/doc/name.DOC", "文件", extensions="doc;docx"))
        self.assertFalse(matches_file_filter("/a.doc/name.docx", "文件", extensions="doc"))
        self.assertFalse(matches_file_filter("a.doc", "文件", "PPT 演示", "doc"))
        self.assertTrue(matches_file_filter("a.tar.gz", "文件", extensions="*.tar.gz"))

    def test_file_filters_hide_directories_and_programs_and_handle_extensionless(self):
        for kind in ("目录", "程序"):
            self.assertTrue(matches_file_filter("a.doc", kind))
            self.assertFalse(matches_file_filter("a.doc", kind, "Word 文档"))
            self.assertFalse(matches_file_filter("a.doc", kind, extensions="doc"))
        self.assertTrue(matches_file_filter("/folder.v1/README", "文件", "无后缀文件"))
        self.assertTrue(matches_file_filter(".gitignore", "文件", "无后缀文件"))
        self.assertFalse(matches_file_filter("a.txt", "文件", "无后缀文件"))
