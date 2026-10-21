"""Bounded, isolated document readers. Never launch Office or execute document code."""
from __future__ import annotations

import codecs
from contextlib import closing
import multiprocessing as mp
from pathlib import Path
import plistlib
import re
import sqlite3
import struct
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from .matching import MatchCollector
from .ocr import IMAGE_SUFFIXES, ocr_lines

CONTENT_TYPES = {
    "Word 文档": ("doc", "docx", "docm", "dotx", "dotm"),
    "PPT 演示": ("ppt", "pptx", "pptm", "pps", "ppsx", "ppsm"),
    "Excel 表格": ("xls", "xlsx", "xlsm", "xltx", "xltm"),
    "文本 / 脚本": ("txt", "md", "markdown", "log", "csv", "tsv", "json", "xml", "yaml", "yml", "ini", "cfg", "conf", "py", "pyw", "ps1", "psm1", "bat", "cmd", "sh", "bash", "zsh", "js", "ts", "java", "c", "cpp", "h", "cs", "go", "rs", "sql", "html", "htm", "css", "vue", "vbs", "php", "rb", "lua"),
    "PDF 文件": ("pdf",),
    "RTF 文档": ("rtf",),
    "OpenDocument": ("odt", "ods", "odp"),
    "配置与缓存": ("plist", "desktop", "properties", "toml", "url", "webloc", "xbel", "db", "sqlite", "sqlite3"),
}
SCAN_MODES = {"文件名": "name", "文件内容": "content", "文件名 + 内容": "both"}
XML_LIMIT = 16 * 1024 * 1024
EXPANDED_LIMIT = 64 * 1024 * 1024


class ContentReadError(OSError):
    pass


class ContentCancelled(Exception):
    pass


def supports_content(path, groups):
    suffix = Path(path).suffix.lower().lstrip(".")
    return any(suffix in CONTENT_TYPES.get(group, ()) for group in groups)


def _vendor():
    folder = str(Path(__file__).resolve().parent / "_vendor")
    if folder not in sys.path:
        sys.path.insert(0, folder)


def _xml(archive, name):
    entry = archive.getinfo(name)
    if entry.file_size > XML_LIMIT:
        raise ContentReadError(f"文档 XML 超过 {XML_LIMIT // 1024**2} MB 解析上限")
    data = archive.read(entry)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ContentReadError("文档包含不支持的 XML 实体定义")
    return ET.fromstring(data)


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _texts(element):
    return "".join(node.text or "" for node in element.iter() if _tag(node) == "t")


def _paragraph_text(element):
    stack, parts = [element], []
    while stack:
        node = stack.pop()
        if node is not element and _tag(node) == "p":
            continue
        if _tag(node) in ("pPr", "rPr", "endParaRPr"):
            continue
        if _tag(node) == "t":
            parts.append(node.text or "")
        elif _tag(node) == "tab":
            parts.append("\t")
        elif _tag(node) in ("br", "cr"):
            parts.append("\n")
        else:
            stack.extend(reversed(list(node)))
    return "".join(parts)


def _office_xml(path):
    with path.open("rb") as stream:
        header = stream.read(8)
    if header == bytes.fromhex("d0cf11e0a1b11ae1"):
        _vendor()
        import olefile
        with olefile.OleFileIO(str(path)) as ole:
            if ole.exists("EncryptedPackage") or ole.exists("EncryptionInfo"):
                raise ContentReadError("Office 文件需要密码，无法读取")
        raise ContentReadError("文件扩展名与实际格式不符，请另存为对应 Office 格式后重试")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > EXPANDED_LIMIT:
            raise ContentReadError("文档解压后超过 64 MB 或内部条目过多，已跳过")
        names = set(archive.namelist())
        suffix = path.suffix.lower()
        if suffix in (".docx", ".docm", ".dotx", ".dotm"):
            parts = [name for name in names if re.fullmatch(r"word/(document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml", name)]
            if "word/document.xml" not in names:
                raise ContentReadError("缺少 Word 正文，文件格式不正确或文件已加密")
            for part in sorted(parts):
                label = {"document": "正文", "footnotes": "脚注", "endnotes": "尾注", "comments": "批注"}.get(Path(part).stem)
                if label is None:
                    label = ("页眉 " if "header" in part else "页脚 ") + "".join(re.findall(r"\d+", part))
                for index, paragraph in enumerate((p for p in _xml(archive, part).iter() if _tag(p) == "p"), 1):
                    yield f"{label} · 段落 {index}", _paragraph_text(paragraph)
        elif suffix in (".pptx", ".pptm", ".ppsx", ".ppsm"):
            slides = sorted((name for name in names if re.fullmatch(r"ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml", name)),
                            key=lambda n: ("notesSlides" in n, int(re.search(r"(\d+)\.xml$", n).group(1))))
            if not slides:
                raise ContentReadError("缺少 PPT 幻灯片，文件格式不正确或文件已加密")
            for part in slides:
                number = re.search(r"(\d+)\.xml$", part).group(1)
                for index, paragraph in enumerate((p for p in _xml(archive, part).iter() if _tag(p) == "p"), 1):
                    yield f"{'备注' if 'notesSlides' in part else '幻灯片'} {number} · 段落 {index}", _paragraph_text(paragraph)
        elif suffix in (".xlsx", ".xlsm", ".xltx", ".xltm"):
            strings = [_texts(node) for node in _xml(archive, "xl/sharedStrings.xml")] if "xl/sharedStrings.xml" in names else []
            sheets = {}
            if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
                import posixpath
                links = {node.attrib.get("Id"): posixpath.normpath("xl/" + node.attrib.get("Target", ""))
                         if not node.attrib.get("Target", "").startswith("/") else node.attrib["Target"].lstrip("/")
                         for node in _xml(archive, "xl/_rels/workbook.xml.rels")}
                for node in _xml(archive, "xl/workbook.xml").iter():
                    if _tag(node) == "sheet":
                        relation = next((v for k, v in node.attrib.items() if k.endswith("}id")), None)
                        sheets[links.get(relation)] = node.attrib.get("name", "工作表")
            parts = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
            if not parts:
                raise ContentReadError("缺少 Excel 工作表，文件格式不正确或文件已加密")
            for part in parts:
                for cell in _xml(archive, part).iter():
                    if _tag(cell) != "c":
                        continue
                    value = next((c.text or "" for c in cell if _tag(c) == "v"), "")
                    kind = cell.attrib.get("t")
                    if kind == "s":
                        value = strings[int(value)] if value else ""
                    elif kind == "inlineStr":
                        value = _texts(cell)
                    # Read cached formula values, never execute spreadsheet formulas.
                    yield f"{sheets.get(part, part.rsplit('/', 1)[-1])}!{cell.attrib.get('r', '?')}", value
        else:
            document = _xml(archive, "content.xml")
            for index, node in enumerate((n for n in document.iter() if _tag(n) in ("p", "h")), 1):
                yield f"段落 {index}", "".join(node.itertext())


def _doc_pieces(word, table):
    if len(word) < 34 or word[:2] != b"\xec\xa5":
        raise ContentReadError("不支持此 Word 二进制格式，请另存为 DOCX 后扫描")
    flags = struct.unpack_from("<H", word, 10)[0]
    if flags & 0x8100:
        raise ContentReadError("Word 文件已加密，无法读取")
    # Locate variable-length FIB sections instead of assuming a fixed Word version.
    offset = 32
    csw = struct.unpack_from("<H", word, offset)[0]
    offset += 2 + csw * 2
    cslw = struct.unpack_from("<H", word, offset)[0]
    offset += 2 + cslw * 4
    count = struct.unpack_from("<H", word, offset)[0]
    if count <= 33:
        raise ContentReadError("Word 版本过旧，请另存为 DOCX 后扫描")
    start, length = struct.unpack_from("<II", word, offset + 2 + 33 * 8)
    clx = table[start:start + length]
    pos = 0
    while pos < len(clx) and clx[pos] == 1:
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos + 5 > len(clx) or clx[pos] != 2:
        raise ContentReadError("Word 正文索引不可用，请另存为 DOCX 后扫描")
    size = struct.unpack_from("<I", clx, pos + 1)[0]
    data = clx[pos + 5:pos + 5 + size]
    if len(data) != size or size < 4 or (size - 4) % 12:
        raise ContentReadError("Word 正文索引损坏")
    pieces = (size - 4) // 12
    positions = struct.unpack_from(f"<{pieces + 1}I", data)
    for index in range(pieces):
        fc = struct.unpack_from("<I", data, (pieces + 1) * 4 + index * 8 + 2)[0]
        compressed = bool(fc & 0x40000000)
        begin = (fc & 0x3fffffff) // (2 if compressed else 1)
        amount = (positions[index + 1] - positions[index]) * (1 if compressed else 2)
        if amount < 0 or begin + amount > len(word):
            raise ContentReadError("Word 正文位置超出文件范围")
        yield "Word 正文", word[begin:begin + amount].decode("cp1252" if compressed else "utf-16-le", errors="replace")


def _ppt_atoms(data, begin=0, end=None, depth=0):
    end = len(data) if end is None else end
    if depth > 64:
        raise ContentReadError("PPT 结构嵌套过深")
    while begin + 8 <= end:
        version, kind, size = struct.unpack_from("<HHI", data, begin)
        body, following = begin + 8, begin + 8 + size
        if following > end:
            raise ContentReadError("PPT 记录已损坏或加密")
        if version & 15 == 15:
            yield from _ppt_atoms(data, body, following, depth + 1)
        else:
            yield kind, body, following
        begin = following


def _ppt_current(data, user):
    # Resolve the current persist directory, so deleted slides and old saved
    # revisions in the stream do not become false content matches.
    edit = struct.unpack_from("<I", user, 16)[0]
    mapping, seen, document_id = {}, set(), None
    while edit:
        if edit in seen or len(seen) > 10000:
            raise ContentReadError("PPT 编辑索引损坏")
        seen.add(edit)
        previous, directory, current_id = struct.unpack_from("<III", data, edit + 16)
        document_id = current_id if document_id is None else document_id
        _, kind, size = struct.unpack_from("<HHI", data, directory)
        if kind != 6002 or directory + 8 + size > len(data):
            raise ContentReadError("PPT 持久对象索引损坏或已加密")
        pos, end = directory + 8, directory + 8 + size
        while pos < end:
            entry = struct.unpack_from("<I", data, pos)[0]
            first, count = entry & 0xfffff, entry >> 20
            if not count or pos + 4 + count * 4 > end:
                raise ContentReadError("PPT 持久对象索引不完整")
            for index, address in enumerate(struct.unpack_from(f"<{count}I", data, pos + 4)):
                mapping.setdefault(first + index, address)
            pos += 4 + count * 4
        edit = previous
    if document_id not in mapping:
        raise ContentReadError("PPT 当前文档索引不可用，请另存为 PPTX 后扫描")
    pending, used, text_index = [document_id], set(), 0
    while pending:
        persist = pending.pop()
        if persist in used:
            continue
        used.add(persist)
        begin = mapping[persist]
        version, kind, size = struct.unpack_from("<HHI", data, begin)
        if version & 15 != 15 or begin + 8 + size > len(data):
            raise ContentReadError("PPT 当前对象损坏或已加密")
        for kind, body, end in _ppt_atoms(data, begin, begin + 8 + size):
            if kind in (4000, 4008):
                text_index += 1
                yield f"PPT 文本片段 {text_index}", data[body:end].decode("utf-16-le" if kind == 4000 else "cp1252", errors="replace")
            elif kind == 1011:
                reference = struct.unpack_from("<I", data, body)[0]
                if reference not in mapping:
                    raise ContentReadError("PPT 幻灯片引用缺失")
                pending.append(reference)


def _legacy_office(path):
    _vendor()
    import olefile
    with olefile.OleFileIO(str(path)) as ole:
        if ole.exists("EncryptedPackage") or ole.exists("EncryptionInfo"):
            raise ContentReadError("Office 文件已加密，无法读取")
        if path.suffix.lower() == ".doc":
            word = ole.openstream("WordDocument").read()
            flags = struct.unpack_from("<H", word, 10)[0]
            table = ole.openstream("1Table" if flags & 0x200 else "0Table").read()
            yield from _doc_pieces(word, table)
        else:
            yield from _ppt_current(ole.openstream("PowerPoint Document").read(), ole.openstream("Current User").read())


def _text_file(path):
    # Validate UTF-8 incrementally, then fall back to Chinese Windows text encoding.
    with path.open("rb") as stream:
        head = stream.read(4096)
    if head.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    elif head and head[1::2].count(0) > len(head[1::2]) * .6:
        encoding = "utf-16-le"
    elif head and head[::2].count(0) > len(head[::2]) * .6:
        encoding = "utf-16-be"
    else:
        encoding = "utf-8-sig"
        try:
            with path.open("r", encoding=encoding) as stream:
                while stream.read(65536):
                    pass
        except UnicodeDecodeError:
            encoding = "gb18030"
    try:
        with path.open("r", encoding=encoding, errors="strict") as stream:
            while text := stream.read(65536):
                if "\0" in text:
                    raise ContentReadError("文件包含二进制数据，不作为文本匹配")
                yield "文本正文", text
    except UnicodeDecodeError as exc:
        raise ContentReadError("文本编码无法识别，或文件虽有文本后缀但实际为二进制数据；此文件内容已跳过") from exc


def looks_like_text(path):
    """Conservatively sniff otherwise unsupported files in selected cache roots."""
    with Path(path).open("rb") as stream:
        head = stream.read(4096)
    if not head:
        return False
    if head.startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE,
                        codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return True
    if head.startswith((b"SQLite format 3\0", b"PK\x03\x04", b"%PDF", b"\xd0\xcf\x11\xe0")):
        return False
    if b"\0" in head:
        even, odd = head[::2].count(0), head[1::2].count(0)
        return max(even, odd) > len(head) * .25 and min(even, odd) < len(head) * .05
    for encoding in ("utf-8", "gb18030"):
        try:
            text = head.decode(encoding)
        except UnicodeDecodeError:
            continue
        if sum(not (char.isprintable() or char in "\r\n\t") for char in text) <= max(1, len(text) // 100):
            return True
    return False


def _plist(path):
    try:
        with path.open("rb") as stream:
            value = plistlib.load(stream)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ContentReadError("配置文件无法解析") from exc
    pending = [("配置", value, 0)]
    while pending:
        location, item, depth = pending.pop()
        if depth > 64:
            raise ContentReadError("配置文件嵌套过深")
        if isinstance(item, dict):
            for key, child in reversed(list(item.items())):
                pending.append((f"{location} / {key}", child, depth + 1))
        elif isinstance(item, list):
            for index in range(len(item) - 1, -1, -1):
                pending.append((f"{location} / {index + 1}", item[index], depth + 1))
        elif isinstance(item, (str, int, float, bool)):
            yield location, str(item)


def _sqlite(path):
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\0":
            raise ContentReadError("缓存文件不是 SQLite 数据库")
    # immutable avoids creating WAL/shared-memory files alongside the source.
    # Uncheckpointed WAL pages may therefore not be represented in the result.
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection:
            connection.execute("PRAGMA query_only=ON")
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            for (table,) in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                cursor = connection.execute(f"SELECT * FROM {quoted}")
                columns = [column[0] for column in cursor.description]
                for row_number, row in enumerate(cursor, 1):
                    for column, value in zip(columns, row):
                        if isinstance(value, (str, int, float)):
                            text = str(value)
                            if len(text) > 1024 * 1024:
                                raise ContentReadError("数据库单元格超过 1 MB 上限")
                            yield f"表 {table} · 行 {row_number} · 列 {column}", text
    except sqlite3.Error as exc:
        raise ContentReadError("SQLite 数据库无法只读解析：" + str(exc)) from exc


def iter_content(path, *, force_text=False, ocr_engine=None, ocr_language=""):
    path = Path(path)
    suffix = path.suffix.lower()
    if ocr_engine and suffix in IMAGE_SUFFIXES:
        yield from ocr_lines(path, ocr_engine, ocr_language)
    elif force_text:
        yield from _text_file(path)
    elif suffix == ".plist":
        yield from _plist(path)
    elif suffix in (".db", ".sqlite", ".sqlite3"):
        yield from _sqlite(path)
    elif suffix in (".docx", ".docm", ".dotx", ".dotm", ".pptx", ".pptm", ".ppsx", ".ppsm", ".xlsx", ".xlsm", ".xltx", ".xltm", ".odt", ".ods", ".odp"):
        yield from _office_xml(path)
    elif suffix in (".doc", ".ppt", ".pps"):
        with path.open("rb") as stream:
            header = stream.read(8)
        if header.startswith(b"{\\rtf"):
            yield from _rtf(path)
        else:
            yield from _legacy_office(path)
    elif suffix == ".xls":
        _vendor()
        import xlrd
        with xlrd.open_workbook(str(path), on_demand=True) as book:
            for sheet in book.sheets():
                for row in range(sheet.nrows):
                    for column, cell in enumerate(sheet.row(row)):
                        if cell.ctype != xlrd.XL_CELL_EMPTY:
                            yield f"{sheet.name}!{xlrd.formula.colname(column)}{row + 1}", str(cell.value)
    elif suffix == ".pdf":
        _vendor()
        from pypdf import PdfReader, overwrite_configuration
        overwrite_configuration(zlib_maximum_output_length=EXPANDED_LIMIT)
        reader = PdfReader(str(path))
        try:
            if reader.is_encrypted and not reader.decrypt(""):
                raise ContentReadError("PDF 文件需要密码，无法读取")
            found = False
            for index, page in enumerate(reader.pages, 1):
                content = page.get_contents()
                if content and len(content.get_data()) > XML_LIMIT:
                    raise ContentReadError("PDF 页面内容流超过 16 MB 解析上限")
                text = page.extract_text() or ""
                found = found or bool(text.strip())
                yield f"PDF 第 {index} 页", text
            if not found:
                raise ContentReadError("PDF 未提取到可读取文字，可能为扫描图片；本版不执行 OCR")
        finally:
            reader.close()
    elif suffix == ".rtf":
        yield from _rtf(path)
    else:
        yield from _text_file(path)


def _rtf(path):
    _vendor()
    from striprtf.striprtf import rtf_to_text
    yield "RTF 正文", rtf_to_text(path.read_bytes().decode("latin-1"), errors="strict")


def _worker(connection):
    while True:
        try:
            job = connection.recv()
        except EOFError:
            return
        if job is None:
            return
        path, keywords, force_text, ocr_engine, ocr_language = job
        collector = MatchCollector(keywords)
        error, permission = "", False
        total = 0
        try:
            for location, text in iter_content(path, force_text=force_text,
                                               ocr_engine=ocr_engine, ocr_language=ocr_language):
                total += len(text)
                if total > EXPANDED_LIMIT:
                    raise ContentReadError("提取文字超过 64 MB 上限，文件未完整扫描")
                collector.feed(location, text)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            permission = isinstance(exc, PermissionError)
        result = {**collector.result(), "error": error, "permission": permission}
        try:
            connection.send(result)
        except (BrokenPipeError, EOFError, OSError):
            return


class ContentReader:
    """One reusable child process per scan; timeouts and cancellation terminate it."""
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.process = self.connection = None

    def read(self, path, keywords, checkpoint, progress, *, force_text=False,
             ocr_engine=None, ocr_language=""):
        if self.process is None:
            context = mp.get_context("spawn")
            self.connection, child = context.Pipe()
            self.process = context.Process(target=_worker, args=(child,), daemon=True)
            self.process.start()
            child.close()
        try:
            self.connection.send((str(path), keywords, force_text, ocr_engine, ocr_language))
        except (OSError, EOFError) as exc:
            self.close()
            raise ContentReadError("内容读取进程无法接收文件，已跳过") from exc
        deadline = time.monotonic() + self.timeout
        while True:
            started = time.monotonic()
            if not checkpoint():
                self.close()
                raise ContentCancelled()
            deadline += time.monotonic() - started
            progress()
            if time.monotonic() >= deadline:
                self.close()
                raise ContentReadError(f"内容读取超过 {self.timeout} 秒，已跳过此文件")
            if self.connection.poll(.1):
                try:
                    return self.connection.recv()
                except (EOFError, OSError):
                    self.close()
                    raise ContentReadError("内容读取进程意外退出，已跳过此文件")
            if not self.process.is_alive() or time.monotonic() > deadline:
                self.close()
                raise ContentReadError(f"内容读取异常退出或超过 {self.timeout} 秒，已跳过此文件")

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=2)
            self.process.close()
            self.process = None
