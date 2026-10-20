"""Readable offline reports. All HTML data is escaped before insertion."""
from collections import Counter
import csv
from dataclasses import asdict
from datetime import datetime
from html import escape
import json
from pathlib import Path


def _timestamp(value):
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M") if value else "—"
    except (OSError, OverflowError, ValueError):
        return "—"


def _html(value):
    return escape(str(value), quote=True)


def _excerpt(entry):
    content = str(entry.get("text", ""))
    start, end = entry.get("highlight_start"), entry.get("highlight_end")
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(content):
        return _html(content[:start]) + "<mark>" + _html(content[start:end]) + "</mark>" + _html(content[end:])
    return _html(content)


def _metadata_summary(metadata, hits):
    progress = metadata.get("progress", {}) or {}
    coverage = metadata.get("coverage", {}) or {}
    kinds = Counter(hit.kind for hit in hits)
    return {
        "结果": len(hits), "文件": kinds.get("文件", 0), "目录": kinds.get("目录", 0),
        "程序": kinds.get("程序", 0), "已检查": progress.get("examined", 0),
        "读取内容": progress.get("content_read", 0), "未完整读取": progress.get("content_skipped", 0),
        "扫描错误": progress.get("errors", 0), "排除入口": coverage.get("excluded", 0),
    }


def _write_html(path, hits, metadata):
    summary = _metadata_summary(metadata, hits)
    limited = bool(metadata.get("cancelled"))
    progress = metadata.get("progress", {}) or {}
    error_count = progress.get("errors", 0)
    status = "已手动停止，报告仅包含部分结果" if limited else "扫描结束"
    if error_count:
        status += f" · {error_count} 项读取或访问错误"
    roots = metadata.get("roots", []) or []
    words = metadata.get("keywords", []) or []
    excludes = metadata.get("excludes", []) or []
    cards = "".join(f'<div class="metric"><strong>{_html(value)}</strong><span>{_html(label)}</span></div>'
                    for label, value in summary.items())
    chips = "".join(f"<span class=chip>{_html(word)}</span>" for word in words) or "<span class=muted>未设置</span>"
    root_lines = "".join(f"<li>{_html(root)}</li>" for root in roots) or "<li>未记录</li>"
    exclude_lines = "".join(f"<li>{_html(item)}</li>" for item in excludes) or "<li>无</li>"
    pieces = []
    for index, hit in enumerate(hits, 1):
        path_text = _html(hit.path)
        tags = "".join(f"<span class=chip>{_html(word.strip())}</span>"
                       for word in hit.keywords.split(" | ") if word.strip())
        matches = []
        for entry in hit.matches:
            matches.append(f'<div class="evidence"><div class="position">{_html(entry.get("position", ""))}</div>'
                           f'<div class="excerpt">{_excerpt(entry)}</div></div>')
        if hit.match_count > len(hit.matches):
            matches.append(f'<p class="muted">共 {_html(hit.match_count)} 处；仅保存前 {_html(len(hit.matches))} 处片段。</p>')
        if hit.content_error:
            matches.append(f'<p class="notice">内容未完整读取：{_html(hit.content_error)}</p>')
        if not matches:
            matches.append('<p class="muted">此项通过名称命中，没有内容片段。</p>')
        pieces.append(
            f'<article class="result" data-kind="{_html(hit.kind)}">'
            f'<div class="result-head"><span class="ordinal">{index:03}</span><span class="kind">{_html(hit.kind)}</span>'
            f'<span class="path">{path_text}</span></div>'
            f'<div class="meta"><span>来源：{_html(hit.match_source or "名称")}</span>'
            f'<span>大小：{_html(hit.size)} B</span><span>修改：{_html(_timestamp(hit.modified))}</span>'
            f'<span>内容命中：{_html(hit.match_count)} 处</span></div>'
            f'<div class="tags">{tags}</div>'
            f'<details><summary>查看命中位置与内容</summary>{"".join(matches)}</details></article>')
    errors = metadata.get("errors", []) or []
    error_preview = "".join(f"<li>{_html(err)}</li>" for err in errors[:20]) or "<li>无错误样例</li>"
    error_note = "" if len(errors) <= 20 else f"另有 {len(errors)-20} 条样例；完整错误清单保存在扫描日志中。"
    source_log = metadata.get("error_log", "")
    document = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>觅文 SeekWords · 扫描报告</title>
<style>
:root{{--bg:#f3f6fb;--ink:#203049;--muted:#63748a;--line:#dde5ef;--blue:#385fdb;--white:#fff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);font-family:-apple-system,BlinkMacSystemFont,"Microsoft YaHei UI","PingFang SC",sans-serif;color:var(--ink);line-height:1.6}}
main{{max-width:1160px;margin:auto;padding:36px 22px 80px}}header{{background:linear-gradient(125deg,#172849,#3353a4);color:white;border-radius:20px;padding:30px 34px}}
h1{{font-size:27px;margin:0 0 4px}}header p{{margin:4px 0;opacity:.86}}.status{{margin-top:16px;border-radius:8px;padding:8px 12px;background:#ffffff20;display:inline-block}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(115px,1fr));gap:11px;margin:20px 0}}
.metric,.panel,.result{{background:white;border:1px solid var(--line);border-radius:13px;box-shadow:0 2px 9px #18386608}}.metric{{padding:14px 17px}}
.metric strong{{display:block;font-size:23px;color:var(--blue);line-height:1.2}}.metric span{{color:var(--muted);font-size:12px}}
.panel{{padding:22px;margin-bottom:18px}}h2{{font-size:17px;margin:0 0 12px}}.scope{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
ul{{margin:4px 0 0;padding-left:20px;overflow-wrap:anywhere}}.chips,.tags{{display:flex;gap:7px;flex-wrap:wrap}}.chip{{border-radius:6px;background:#e9efff;color:#3155b6;padding:2px 8px;font-size:12px}}
.toolbar{{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin:20px 0 12px}}input,button{{font:inherit;border-radius:8px;border:1px solid #cdd8e8;padding:8px 11px;background:white;color:var(--ink)}}
input{{flex:1;min-width:240px}}button{{cursor:pointer}}button.active,button:hover{{background:#e9efff;color:#2c51bf;border-color:#aebff2}}.result{{padding:15px 18px;margin:9px 0}}
.result-head{{display:flex;gap:9px;align-items:baseline;flex-wrap:wrap}}.ordinal{{font-size:11px;color:var(--muted)}}.kind{{border-radius:5px;background:#e8f2fb;padding:1px 7px;font-size:12px}}
.path{{font-weight:600;overflow-wrap:anywhere;user-select:text}}.meta{{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin:5px 0 9px}}
details{{border-top:1px solid var(--line);margin-top:12px;padding-top:8px}}summary{{cursor:pointer;color:var(--blue);font-size:13px;font-weight:600}}
.evidence{{background:#f6f8fc;margin:10px 0;border-radius:8px;padding:10px 12px}}.position{{font-size:12px;color:var(--muted)}}.excerpt{{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:5px}}
mark{{background:#ffcf75;border-radius:3px;padding:0 2px}}.muted{{color:var(--muted)}}.notice{{background:#fff5ed;color:#8b4817;border-radius:6px;padding:8px}}
footer{{font-size:12px;color:var(--muted);margin-top:28px}}@media(max-width:640px){{main{{padding:12px}}.scope{{grid-template-columns:1fr}}header{{padding:22px}}}}
</style></head><body><main>
<header><h1>觅文 SeekWords</h1><p>文件关键词扫描报告 · v{_html(metadata.get("version", "1.0"))}</p>
<p>开始：{_html(metadata.get("started", "—"))}　结束：{_html(metadata.get("finished", "—"))}</p><div class="status">{_html(status)}</div></header>
<section class="metrics">{cards}</section>
<section class="panel scope"><div><h2>扫描范围</h2><ul>{root_lines}</ul><h2 style="margin-top:18px">排除目录</h2><ul>{exclude_lines}</ul></div>
<div><h2>匹配设置</h2><p>模式：{_html(metadata.get("match", "—"))}</p><p>关键词：</p><div class="chips">{chips}</div>
<p class="muted">内容扫描只覆盖已选且支持的可读取格式；受限、占用、超限或未选格式请结合覆盖统计与错误清单核对。</p></div></section>
<section><h2>结果 <span id="shown">{len(hits)}</span> / {len(hits)}</h2>
<div class="toolbar"><input id="query" type="search" aria-label="筛选路径或关键词" placeholder="搜索路径、关键词或内容…">
<button class="active" data-filter="全部">全部</button><button data-filter="文件">文件</button><button data-filter="目录">目录</button><button data-filter="程序">程序</button></div>
<div id="results">{"".join(pieces) or '<div class="panel muted">没有匹配结果。</div>'}</div></section>
<section class="panel" style="margin-top:24px"><h2>扫描错误与跳过</h2><ul>{error_preview}</ul><p class="muted">{_html(error_note)}</p>
<p class="muted">完整错误日志：{_html(source_log or "未生成")}</p></section>
<footer>本报告保存在本机，可离线打开。数据仅来自扫描时保存的结果与片段。文件操作后的报告只包含当前保留的结果。</footer>
</main><script>
const q=document.querySelector('#query'), rows=[...document.querySelectorAll('.result')], buttons=[...document.querySelectorAll('[data-filter]')];
let kind='全部';function filter(){{const term=q.value.trim().toLocaleLowerCase();let shown=0;for(const row of rows){{let visible=(kind==='全部'||row.dataset.kind===kind)&&row.textContent.toLocaleLowerCase().includes(term);row.hidden=!visible;if(visible)shown++;}}document.querySelector('#shown').textContent=shown;}}
q.addEventListener('input',filter);buttons.forEach(button=>button.addEventListener('click',()=>{{kind=button.dataset.filter;buttons.forEach(b=>b.classList.toggle('active',b===button));filter();}}));
</script></body></html>'''
    Path(path).write_text(document, encoding="utf-8")


def _csv_cell(value):
    text = str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text


def export_report(path, hits, metadata):
    """Export every retained hit; caller decides whether filtered hits are included."""
    hits = list(hits)
    suffix = Path(path).suffix.lower()
    if suffix in (".html", ".htm"):
        _write_html(path, hits, metadata)
    elif suffix == ".json":
        from .core import save_json
        save_json(path, {"scan": metadata, "results": [asdict(hit) for hit in hits]})
    elif suffix == ".csv":
        with open(path, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["类型", "完整路径", "关键词", "命中来源", "内容命中处数", "内容位置", "命中片段", "大小(B)", "修改时间", "内容读取提醒"])
            for hit in hits:
                writer.writerow([_csv_cell(hit.kind), _csv_cell(hit.path), _csv_cell(hit.keywords),
                    _csv_cell(hit.match_source), hit.match_count,
                    _csv_cell("；".join(str(m.get("position", "")) for m in hit.matches)),
                    _csv_cell("\n\n".join(str(m.get("text", "")) for m in hit.matches)),
                    hit.size, _timestamp(hit.modified), _csv_cell(hit.content_error)])
    elif suffix == ".txt":
        summary = _metadata_summary(metadata, hits)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("觅文 SeekWords · 文件关键词扫描报告\n" + "="*42 + "\n")
            stream.write(f"开始：{metadata.get('started', '—')}\n结束：{metadata.get('finished', '—')}\n")
            stream.write("状态：" + ("已停止，仅部分结果" if metadata.get("cancelled") else "扫描结束") + "\n")
            stream.write("概览：" + "  ·  ".join(f"{k} {v}" for k, v in summary.items()) + "\n")
            stream.write("扫描范围：\n" + "\n".join("  " + str(root) for root in metadata.get("roots", [])) + "\n")
            stream.write("关键词：" + "、".join(metadata.get("keywords", [])) + "\n\n")
            for index, hit in enumerate(hits, 1):
                stream.write(f"{index}. [{hit.kind}] {hit.path}\n")
                stream.write(f"   关键词：{hit.keywords}  ·  来源：{hit.match_source or '名称'}  ·  内容 {hit.match_count} 处\n")
                for entry in hit.matches:
                    stream.write(f"   {entry.get('position', '')}\n   {entry.get('text', '')}\n")
                if hit.content_error:
                    stream.write(f"   未完整读取：{hit.content_error}\n")
                stream.write("\n")
            if metadata.get("errors"):
                stream.write("扫描错误样例\n" + "\n".join(str(err) for err in metadata["errors"]) + "\n")
    else:
        raise ValueError("报告格式仅支持 HTML、CSV、JSON 和 TXT")
