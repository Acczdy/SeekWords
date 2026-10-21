"""Build the public report example from fictional, reproducible records."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scan_keywords_app import VERSION
from scan_keywords_app.core import Hit, export_report


def example_hit(path, origin, source, location, excerpt, start, *, kind="文件"):
    return Hit(path, kind, "ORBIT", 88 if kind == "文件" else 0, 0, (),
               f"命中来源：{source}\n位置：{location}\n内容片段：{excerpt}",
               source, location, excerpt,
               [{"position": location, "text": excerpt,
                 "highlight_start": start, "highlight_end": start + 5}],
               1, False, "", ["ORBIT"], origin)


def main():
    hits = [
        example_hit("/示例项目/合同与方案/会议纪要.md", "普通文件", "内容",
                    "文本正文 · 第 3 行，第 4 列", "关于 ORBIT 项目的资料归档。", 3),
        example_hit("/示例项目/缓存/最近项目.plist", "Office 缓存", "内容",
                    "配置 / 最近项目 / 1", "ORBIT 方案.docx", 0),
        example_hit("/示例项目/截图/进度.png", "普通文件", "图片OCR",
                    "图片第 1 处 · 像素 (10,20)–(65,30)", "ORBIT 已完成评审", 0),
    ]
    metadata = {
        "version": VERSION, "started": "2026-10-21 09:30:00", "finished": "2026-10-21 09:30:02",
        "roots": ["/示例项目/合同与方案", "/示例项目/截图"], "keywords": ["ORBIT"],
        "excludes": [], "match": "文件名 + 内容", "report_scope": "全部结果",
        "extra_roots": [{"path": "/示例项目/缓存", "label": "Office 缓存"}],
        "ocr_images": True, "ocr_language": "中英混合",
        "progress": {"examined": 3, "content_read": 3, "content_skipped": 0, "errors": 0},
        "coverage": {"files": 3, "ocr_examined": 1, "excluded": 0},
    }
    target = Path(__file__).resolve().parents[1] / "assets/example-report.html"
    export_report(target, hits, metadata)
    print(target)


if __name__ == "__main__":
    main()
