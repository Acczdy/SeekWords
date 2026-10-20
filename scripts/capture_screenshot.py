"""Capture only an isolated demo window under Xvfb for the README."""
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scan_keywords_app.display import create_root
from scan_keywords_app.gui import App


def main():
    with tempfile.TemporaryDirectory(prefix="SeekWords-Demo-") as temporary:
        base = Path(temporary)
        folder = base / "示例项目" / "合同与方案"
        folder.mkdir(parents=True)
        (folder / "项目记录.txt").write_text("项目进展\nORBIT 已完成阶段评审，下一步整理归档材料。\n", encoding="utf-8")
        (folder / "会议纪要.md").write_text("# 会议纪要\n关于 ORBIT 项目的资料归档。\n", encoding="utf-8")
        with patch("scan_keywords_app.gui.data_dir", return_value=base / "settings"), patch(
                "scan_keywords_app.core.data_dir", return_value=base / "settings"):
            root = create_root()
            try:
                app = App(root, (str(folder),))
                app.select_settings_page(1)
                app.keyword_entry.insert(0, "ORBIT")
                app.add_keywords()
                app.scan_mode.set("文件名 + 内容")
                app.programs.set(False)
                app.start_scan()
                deadline = time.monotonic() + 30
                while (app.scanner or app.thread and app.thread.is_alive()) and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.02)
                root.update()
                assert app.scanner is None and app.results, "demo scan did not complete"
                first = app.tree.get_children()[0]
                app.tree.selection_set(first)
                app.tree.see(first)
                root.update()
                root.update_idletasks()
                time.sleep(0.1)
                x, y = root.winfo_rootx(), root.winfo_rooty()
                screenshot = ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height()),
                                            xdisplay=root.tk.call("winfo", "screen", root._w))
                target = Path(__file__).resolve().parents[1] / "assets" / "screenshot.png"
                screenshot.save(target, optimize=True)
                print(target)
            finally:
                root.destroy()


if __name__ == "__main__":
    main()
