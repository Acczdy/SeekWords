"""Exercise actual Tk scrolling, filters, row numbers and column dragging."""
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from scan_keywords_app.core import Hit
from scan_keywords_app.display import create_root
from scan_keywords_app.gui import App


class ResultTableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="seekwords-results-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for name in ("scan_keywords_app.gui.data_dir", "scan_keywords_app.core.data_dir"):
            patcher = patch(name, return_value=self.folder)
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            self.root = create_root()
        except tk.TclError:
            self.skipTest("Tk display unavailable (run under xvfb-run on Linux)")
        self.addCleanup(self.root.destroy)
        self.app = App(self.root)
        self.app.toggle_preview()
        self.root.update()

    def pump(self):
        deadline = time.monotonic() + 5
        while True:
            self.root.update()
            if not self.app.rebuild_id:
                break
            if time.monotonic() > deadline:
                self.fail("result rebuild did not finish")
            time.sleep(.003)

    def populate(self, count=2100):
        app = self.app
        app.results = {str(index): Hit(str(self.folder / f"row{index:04}.txt"), "文件", "ORBIT",
                       snippet="临时文件" if index % 2 else "保留内容") for index in range(count)}
        app.rebuild()
        self.pump()

    def test_delete_at_row_1678_preserves_view_and_selects_neighbor(self):
        self.populate()
        app, tree = self.app, self.app.tree
        tree.selection_set("1677")
        tree.focus("1677")
        tree.yview_moveto(1668 / 2100)
        tree.xview_moveto(.12)
        self.root.update()
        old_rows, top, horizontal, _focus = app.capture_result_view()
        anchor = old_rows[top]
        hit = app.results["1677"]
        app.finish_operation(dict(mode="delete", successes=[(hit.path, "")], failures=[]))
        self.pump()
        rows, current_top, current_horizontal, _ = app.capture_result_view()
        self.assertEqual(rows[current_top], anchor)
        self.assertAlmostEqual(current_horizontal, horizontal, places=3)
        self.assertNotIn("1677", app.results)
        self.assertEqual(tree.selection(), ("1678",))
        self.assertEqual(int(tree.set("1678", "number")), 1678)
        self.assertEqual(int(tree.set(rows[-1], "number")), 2099)

    def test_deleting_top_anchor_and_last_rows_keeps_nearest_survivor(self):
        self.populate()
        app, tree = self.app, self.app.tree
        tree.yview_moveto(1668 / 2100)
        self.root.update()
        rows, top, _, _ = app.capture_result_view()
        anchor = rows[top]
        following = rows[top + 1]
        tree.selection_set(anchor)
        app.remove_result_rows([anchor])
        self.pump()
        new_rows, new_top, _, _ = app.capture_result_view()
        self.assertEqual(new_rows[new_top], following)
        tree.yview_moveto(1)
        self.root.update()
        last = tree.get_children()[-1]
        tree.selection_set(last)
        app.remove_result_rows([last])
        self.pump()
        self.assertGreater(tree.yview()[0], .9)
        self.assertEqual(tree.selection(), (tree.get_children()[-1],))

    def test_filter_sort_and_streamed_hits_have_contiguous_numbers(self):
        self.populate(25)
        app, tree = self.app, self.app.tree
        app.exclude_query.set("临时")
        app.rebuild()
        self.pump()
        self.assertEqual(len(tree.get_children()), 13)
        self.assertEqual(len(app.results), 25)
        app.sort("path")
        self.pump()
        rows = tree.get_children()
        self.assertEqual(rows[0], "24")
        self.assertEqual([int(tree.set(row, "number")) for row in rows], list(range(1, 14)))
        app.events.put(("hits", [Hit(str(self.folder / "next.txt"), "文件", "ORBIT")]))
        app.next_id = 25
        self.root.after_cancel(app.poll_id)
        app.poll()
        self.assertEqual(int(tree.set("25", "number")), 14)
        app.reset_filters()
        app.rebuild()
        self.pump()
        self.assertEqual(len(tree.get_children()), 26)

    def test_drag_column_width_survives_release_filter_sort_and_window_resize(self):
        self.populate(20)
        app, tree = self.app, self.app.tree
        # Drag the boundary between the path and origin columns.
        columns = tree["columns"]
        boundary = sum(tree.column(column, "width") for column in columns[:3])
        heading_y = next(y for y in range(1, 60) if tree.identify_region(boundary, y) == "separator")
        before = tree.column("path", "width")
        tree.event_generate("<ButtonPress-1>", x=boundary, y=heading_y)
        tree.event_generate("<B1-Motion>", x=boundary + 100, y=heading_y)
        tree.event_generate("<ButtonRelease-1>", x=boundary + 100, y=heading_y)
        self.pump()
        dragged = tree.column("path", "width")
        self.assertGreater(dragged, before + 50)
        app.sort("path")
        app.exclude_query.set("not present")
        app.rebuild()
        self.root.geometry("1100x740")
        self.pump()
        self.assertEqual(tree.column("path", "width"), dragged)
        self.assertTrue(app.save_config())
        settings = json.loads(app.config_path.read_text(encoding="utf-8"))
        self.assertAlmostEqual(settings["result_column_widths"]["path"], dragged / app.display_scale)

    def test_registry_delete_button_and_exact_result_removal(self):
        app, tree = self.app, self.app.tree
        path = r"HKCU\Software\Fixture"
        app.results = {
            "default": Hit(path + r"\(默认)", "注册表", "ORBIT", registry_kind="value", registry_key=path),
            "literal": Hit(path + r"\(默认)", "注册表", "ORBIT", registry_kind="value", registry_key=path,
                           registry_value="(默认)"),
        }
        app.rebuild()
        self.pump()
        tree.selection_set("default")
        app.selection_changed()
        self.assertFalse(app.file_buttons[3].instate(["disabled"]))
        self.assertTrue(app.file_buttons[2].instate(["disabled"]))
        app.finish_operation(dict(mode="registry_delete", successes=[(path + r"\(默认)", "")],
                                  removed_keys=["default"], failures=[], permanent=True))
        self.pump()
        self.assertEqual(tree.get_children(), ("literal",))
        self.assertEqual(int(tree.set("literal", "number")), 1)
