"""Literal, case-insensitive result filters; no filesystem access."""
from functools import lru_cache
import re


FILE_CATEGORIES = {
    "全部文件类别": (),
    "Word 文档": ("doc", "docx", "docm", "dot", "dotx", "dotm", "odt", "rtf", "wps"),
    "文本文件": ("txt", "md", "markdown", "log", "csv", "tsv", "ini", "cfg", "conf", "yaml", "yml", "json", "xml"),
    "脚本文件": ("py", "pyw", "ps1", "psm1", "psd1", "bat", "cmd", "sh", "bash", "zsh", "fish", "js", "mjs", "cjs", "ts", "vbs", "vbe", "wsf", "lua", "pl", "rb", "php"),
    "PPT 演示": ("ppt", "pptx", "pptm", "pps", "ppsx", "ppsm", "pot", "potx", "potm", "odp", "dps", "key"),
    "Excel 表格": ("xls", "xlsx", "xlsm", "xlsb", "xlt", "xltx", "xltm", "ods", "et", "csv", "tsv"),
    "PDF 文件": ("pdf",),
    "图片文件": ("png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "tif", "tiff", "ico", "heic"),
    "压缩文件": ("zip", "7z", "rar", "tar", "gz", "bz2", "xz", "tgz", "tar.gz", "tar.bz2", "tar.xz"),
    "音视频文件": ("mp3", "wav", "flac", "aac", "m4a", "mp4", "mkv", "avi", "mov", "wmv", "webm"),
    "源代码": ("c", "h", "cpp", "hpp", "cs", "java", "go", "rs", "swift", "kt", "html", "css", "sql", "vue", "jsx", "tsx"),
    "无后缀文件": (),
}


@lru_cache(maxsize=128)
def parse_extensions(value):
    return tuple(dict.fromkeys("." + part.casefold().removeprefix("*.").lstrip(".")
                              for part in re.split(r"[\s,;，；、]+", value.strip())
                              if part.strip("*.") and "/" not in part and "\\" not in part))


def matches_file_filter(path, kind, category="全部文件类别", extensions=""):
    suffixes = parse_extensions(extensions)
    if category == "全部文件类别" and not suffixes:
        return True
    if kind != "文件":
        return False
    name = re.split(r"[/\\]", path)[-1].casefold()
    if category == "无后缀文件":
        category_match = "." not in name.lstrip(".")
    else:
        values = FILE_CATEGORIES.get(category, ())
        category_match = not values or name.endswith(tuple("." + value for value in values))
    return category_match and (not suffixes or name.endswith(suffixes))
