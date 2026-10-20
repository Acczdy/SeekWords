"""Native ttk desktop interface for Windows, Linux and macOS."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import platform
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, VERSION
from .display import configure_fonts, create_root
from .file_filters import FILE_CATEGORIES, matches_file_filter
from .content import CONTENT_TYPES, SCAN_MODES
from .widgets import Choice, Toggle, MatchPreview, ActionMenu, ResultSplit
from .permissions import is_windows_admin, request_elevation
from .system_files import describe_failure, is_user_cancelled
from .core import (FileActions, Scanner, data_dir, default_excludes,
                   export_report, local_roots, normalize_keywords, save_json)


def size_text(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024


class App:
    def __init__(self, root, initial_paths=()):
        self.root = root
        self.font_family, self.line_height = configure_fonts(root)
        self.display_scale = max(1.0, root.winfo_fpixels("1i") / 96)
        root.title(f"{APP_NAME} · v{VERSION}")
        try:
            root.iconphoto(True, tk.PhotoImage(file=str(Path(__file__).resolve().parents[1] / "assets" / "seekwords.png")))
        except tk.TclError:
            pass
        width = min(round(1220 * self.display_scale), root.winfo_screenwidth() - 40)
        height = min(round(800 * self.display_scale), root.winfo_screenheight() - 80)
        root.geometry(f"{width}x{height}")
        root.minsize(min(round(980 * self.display_scale), width), min(round(660 * self.display_scale), height))
        self.events = queue.Queue()
        self.scanner = None
        self.thread = None
        self.operating = False
        self.closing = False
        self.results = {}
        self.next_id = 0
        self.metadata = {}
        self.error_samples = []
        self.action_log = []
        self.actions = FileActions()
        self.config_path = data_dir() / "settings.json"
        self.filter_timer = None
        self.rebuild_id = None
        self.rebuild_generation = 0
        self.sort_column = "path"
        self.sort_reverse = False
        self.config_error = ""
        self.poll_id = None
        self.sidebar_collapsed = False
        self.denied_paths = []
        self.permission_errors = 0
        self.preview_visible = True
        self.preview_ratio = .55
        self.config = self.load_config()
        try:
            self.preview_ratio = max(.2, min(.8, float(self.config.get("preview_ratio", .55))))
        except (ValueError, TypeError):
            self.preview_ratio = .55
        self.content_types = [value for value in self.config.get("content_types", CONTENT_TYPES) if value in CONTENT_TYPES]
        self.build()
        roots = initial_paths or self.config.get("roots", [])
        for path in roots:
            self.add_path(self.roots_list, path)
        for keyword in normalize_keywords(self.config.get("keywords", [])):
            self.keywords_list.insert("end", keyword)
        for path in self.config.get("excludes", default_excludes()):
            self.add_path(self.excludes_list, path)
        self.programs.set(self.config.get("programs", True))
        self.scan_mode.set(next((label for label, mode in SCAN_MODES.items() if mode == self.config.get("scan_mode")), "文件名"))
        self.content_limit.set(str(self.config.get("max_content_mb", 50)))
        self.update_content_controls()
        if self.config.get("sidebar_collapsed", False):
            self.toggle_sidebar()
        if not self.config.get("preview_visible", True):
            self.toggle_preview()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(80, self.poll)
        root.bind("<Destroy>", self.cleanup_timers, add="+")
        if self.config_error:
            root.after(150, lambda: messagebox.showwarning("设置读取失败", self.config_error, parent=root))

    @property
    def busy(self):
        return self.operating or self.scanner is not None

    def load_config(self):
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("设置格式无效")
            for key in ("roots", "keywords", "excludes", "content_types"):
                if key in data and (not isinstance(data[key], list) or
                                    not all(isinstance(p, str) for p in data[key])):
                    raise ValueError(f"{key} 格式无效")
            return data
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            self.config_error = f"已使用默认设置。原设置文件：{self.config_path}\n{exc}"
            return {}

    def save_config(self):
        try:
            save_json(self.config_path, {"roots": list(self.roots_list.get(0, "end")),
                "keywords": list(self.keywords_list.get(0, "end")),
                "excludes": list(self.excludes_list.get(0, "end")), "programs": self.programs.get(),
                "sidebar_collapsed": self.sidebar_collapsed, "scan_mode": SCAN_MODES[self.scan_mode.get()],
                "content_types": self.content_types, "max_content_mb": self.content_limit.get(),
                "preview_visible": self.preview_visible, "preview_ratio": self.preview_ratio})
            return True
        except OSError as exc:
            messagebox.showwarning("设置未保存", str(exc), parent=self.root)
            return False

    def build(self):
        s = lambda value: round(value * self.display_scale)
        bg, white, ink, muted = "#f3f5f9", "#ffffff", "#243047", "#788498"
        blue, edge = "#3662e8", "#e2e7ef"
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", background=bg, foreground=ink, font="TkDefaultFont")
        style.configure("TFrame", background=bg)
        style.configure("Card.TFrame", background=white)
        style.configure("Title.TLabel", font=(self.font_family, 18, "bold"), background=bg, foreground=ink)
        style.configure("Subtitle.TLabel", font=(self.font_family, 9), foreground=muted, background=bg)
        style.configure("Card.TLabel", background=white, foreground=ink)
        style.configure("Section.TLabel", background=white, font=(self.font_family, 11, "bold"))
        style.configure("Muted.TLabel", background=white, foreground=muted, font=(self.font_family, 9))
        style.configure("Metric.TLabel", background=white, foreground=ink, font=(self.font_family, 16, "bold"))
        style.configure("MetricAccent.TLabel", background=white, foreground=blue, font=(self.font_family, 16, "bold"))
        style.configure("TButton", background="#eaf0fb", foreground="#294675", bordercolor="#b9c9e6",
                        lightcolor="#b9c9e6", darkcolor="#b9c9e6", relief="solid", borderwidth=1,
                        padding=(s(10), s(4)), width=0, shiftrelief=0)
        style.map("TButton", background=[("disabled", "#f6f7fa"), ("pressed", "#e7ecf7"), ("active", "#f2f5fc")],
                  foreground=[("disabled", "#abb4c4")], bordercolor=[("focus", "#a8baf8")])
        style.configure("Primary.TButton", background=blue, foreground=white, bordercolor=blue, lightcolor=blue, darkcolor=blue)
        style.map("Primary.TButton", background=[("disabled", "#dce3f4"), ("pressed", "#254bc3"), ("active", "#4572f4")],
                  foreground=[("disabled", "#929fb8"), ("!disabled", white)],
                  bordercolor=[("disabled", "#dce3f4"), ("!disabled", blue)])
        style.configure("Danger.TButton", foreground="#b23448", background="#fff0f3", bordercolor="#e2a8b4", lightcolor="#e2a8b4", darkcolor="#e2a8b4")
        style.map("Danger.TButton", background=[("active", "#fcecef"), ("pressed", "#f9e1e6")])
        style.configure("Warning.TButton", foreground="#9a5d12", background="#fff3dc", bordercolor="#dfbd83", lightcolor="#dfbd83", darkcolor="#dfbd83")
        style.map("Warning.TButton", background=[("active", "#ffebc4"), ("pressed", "#ffe3af")])
        style.configure("Compact.TButton", font=(self.font_family, 9), padding=(s(8), s(3)))
        style.configure("Link.TButton", borderwidth=0, relief="flat", lightcolor=white, darkcolor=white,
                        background=white, foreground=muted, padding=(s(6), s(4)))
        style.configure("Menu.TButton", background=white, foreground=ink, borderwidth=0,
                        relief="flat", anchor="w", padding=(s(12), s(7)), shiftrelief=0)
        style.map("Menu.TButton", background=[("active", "#eaf0ff"), ("pressed", "#dae5ff")])
        style.configure("MenuDanger.TButton", background=white, foreground="#ae304a", borderwidth=0,
                        relief="flat", anchor="w", padding=(s(12), s(7)), shiftrelief=0)
        style.map("MenuDanger.TButton", background=[("active", "#fff0f3"), ("pressed", "#fbdce4")])
        style.configure("TEntry", fieldbackground=white, bordercolor=edge, lightcolor=edge, darkcolor=edge,
                        padding=(s(8), s(4)), insertcolor=ink)
        style.map("TEntry", bordercolor=[("focus", "#91a8f4")])
        style.configure("TCombobox", fieldbackground=white, background=white, bordercolor=edge,
                        lightcolor=edge, darkcolor=edge, arrowsize=s(12), padding=s(5))
        style.map("TCombobox", fieldbackground=[("readonly", white)], selectbackground=[("readonly", white)],
                  selectforeground=[("readonly", ink)])
        style.configure("Selector.TFrame", background=white, borderwidth=1, relief="solid", bordercolor="#cbd5e5")
        style.configure("Selector.TButton", background=white, foreground=ink, anchor="w", borderwidth=0,
                        relief="flat", padding=(s(10), s(4)), shiftrelief=0)
        style.map("Selector.TButton", background=[("pressed", "#edf3ff"), ("active", "#f3f7ff")],
                  foreground=[("disabled", "#9aa7ba")], relief=[("pressed", "flat")])
        style.configure("Option.TButton", background="#f7f9fd", foreground="#52627b", bordercolor="#d9e1ed", anchor="w")
        style.configure("Selected.TButton", background="#eaf0ff", foreground="#2d55ba", bordercolor="#a8bdf3", anchor="w")
        style.map("Option.TButton", background=[("active", "#edf2fc"), ("pressed", "#e6edfb")])
        style.map("Selected.TButton", background=[("active", "#e2ebff"), ("pressed", "#dbe6ff")])
        style.configure("Preview.TFrame", background="#f7f9fd")
        style.configure("PreviewTitle.TLabel", background="#f7f9fd", foreground=ink, font="TkHeadingFont")
        style.configure("PreviewHint.TLabel", background="#f7f9fd", foreground=muted, font=(self.font_family, 9))
        style.configure("TCheckbutton", background=white, foreground=ink, padding=(0, s(4)))
        style.map("TCheckbutton", background=[("active", white)])
        style.configure("TabBar.TFrame", background="#f1f4fa")
        style.configure("Tab.TButton", background="#f1f4fa", foreground=muted,
                        font="TkDefaultFont", borderwidth=0, padding=0, relief="flat")
        style.map("Tab.TButton", background=[("pressed", "#e4ebfa"), ("active", "#e9eef9")],
                  foreground=[("!disabled", muted)], relief=[("pressed", "flat")])
        style.configure("ActiveTab.TButton", background=blue, foreground=white,
                        font="TkDefaultFont", borderwidth=0, padding=0, relief="flat")
        style.map("ActiveTab.TButton", background=[("pressed", blue), ("active", blue)],
                  foreground=[("!disabled", white)], relief=[("pressed", "flat")])
        style.configure("Treeview", background=white, fieldbackground=white, foreground=ink,
                        borderwidth=0, relief="flat", font="TkDefaultFont", rowheight=self.line_height + s(8))
        style.configure("Treeview.Heading", background="#f7f9fc", foreground="#657087", relief="flat",
                        font="TkHeadingFont", padding=(s(8), s(5)), borderwidth=0)
        style.map("Treeview", background=[("selected", "#eaf0ff")], foreground=[("selected", "#2445a5")])
        style.map("Treeview.Heading", background=[("active", "#edf1f8")])
        for orientation in ("Vertical", "Horizontal"):
            style.configure(f"{orientation}.TScrollbar", background="#dce2ec", troughcolor="#f7f9fc",
                            borderwidth=0, arrowsize=s(10), relief="flat")
        style.configure("Slim.Horizontal.TProgressbar", background=blue, troughcolor="#edf1f9",
                        borderwidth=0, thickness=s(4), lightcolor=blue, darkcolor=blue)
        style.configure("Status.TEntry", fieldbackground=white, foreground=ink, borderwidth=0,
                        bordercolor=white, lightcolor=white, darkcolor=white, padding=0, font="TkHeadingFont")
        style.map("Status.TEntry", fieldbackground=[("readonly", white)], foreground=[("readonly", ink)])
        style.configure("Path.TEntry", fieldbackground=white, foreground=muted, borderwidth=0,
                        bordercolor=white, lightcolor=white, darkcolor=white, padding=0, font=(self.font_family, 9))
        style.map("Path.TEntry", fieldbackground=[("readonly", white)], foreground=[("readonly", muted)])
        self.root.configure(background=bg)

        outer = ttk.Frame(self.root, padding=s(12))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, s(8)))
        header.columnconfigure(0, weight=1)
        title = ttk.Frame(header)
        title.grid(row=0, column=0, sticky="w")
        ttk.Label(title, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(title, text=f"按文件名或内容查找  /  v{VERSION}", style="Subtitle.TLabel").pack(anchor="w", pady=(s(4), 0))
        controls = ttk.Frame(header)
        controls.grid(row=0, column=1, sticky="e")
        self.start_button = ttk.Button(controls, text="开始扫描", width=10, style="Primary.TButton", command=self.start_scan)
        self.start_button.pack(side="left", padx=(0, s(6)))
        self.pause_button = ttk.Button(controls, text="暂停", width=6, command=self.pause, state="disabled")
        self.pause_button.pack(side="left", padx=(0, s(6)))
        self.stop_button = ttk.Button(controls, text="停止", width=6, command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(0, s(18)))
        self.export_button = ttk.Button(controls, text="导出报告", command=self.export)
        self.export_button.pack(side="left", padx=(0, s(6)))
        ttk.Button(controls, text="系统回收站", command=self.open_system_trash).pack(side="left")

        body = ttk.Frame(outer)
        self.body = body
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_propagate(False)
        body.columnconfigure(0, minsize=s(270), weight=0)
        body.columnconfigure(1, weight=1, minsize=0)
        body.rowconfigure(0, weight=1)
        self.sidebar = ttk.Frame(body, style="Card.TFrame", width=s(270), padding=s(14))
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, s(14)))
        self.sidebar.grid_propagate(False)
        self.sidebar.columnconfigure(0, weight=1)
        self.sidebar.rowconfigure(2, weight=1)
        sidebar_heading = ttk.Frame(self.sidebar, style="Card.TFrame")
        sidebar_heading.grid(row=0, column=0, sticky="ew", pady=(s(2), s(14)))
        sidebar_heading.columnconfigure(0, weight=1)
        ttk.Label(sidebar_heading, text="扫描设置", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.sidebar_button = ttk.Button(sidebar_heading, text="◀", width=3, style="Compact.TButton",
                                         command=self.toggle_sidebar)
        self.sidebar_button.grid(row=0, column=1)
        self.sidebar_button.configure(takefocus=True)
        self.collapsed_sidebar = ttk.Frame(body, style="Card.TFrame", width=s(44), padding=(s(4), s(12)))
        self.collapsed_sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, s(14)))
        self.collapsed_sidebar.grid_remove()
        self.collapsed_sidebar.grid_propagate(False)
        self.expand_button = ttk.Button(self.collapsed_sidebar, text="▶", width=3, style="Compact.TButton",
                                        command=self.toggle_sidebar)
        self.expand_button.pack(anchor="n")
        tab_bar = ttk.Frame(self.sidebar, style="TabBar.TFrame", height=s(36), padding=s(2))
        tab_bar.grid(row=1, column=0, sticky="ew")
        tab_bar.grid_propagate(False)
        tab_bar.rowconfigure(0, weight=1)
        pages = ttk.Frame(self.sidebar, style="Card.TFrame", width=1, height=1)
        pages.grid(row=2, column=0, sticky="nsew")
        pages.grid_propagate(False)
        pages.columnconfigure(0, weight=1)
        pages.rowconfigure(0, weight=1)
        self.settings_pages = [ttk.Frame(pages, style="Card.TFrame", padding=(0, s(12), 0, 0)) for _ in range(3)]
        scopes, keywords, excludes = self.settings_pages
        self.settings_tabs = []
        for index, (frame, title_text) in enumerate(zip(self.settings_pages, ("目录", "关键词", "排除"))):
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(1, weight=1)
            tab_bar.columnconfigure(index, weight=1, uniform="settings_tabs")
            button = ttk.Button(tab_bar, text=title_text, width=1, style="Tab.TButton",
                                command=lambda i=index: self.select_settings_page(i))
            button.grid(row=0, column=index, sticky="nsew")
            self.settings_tabs.append(button)
        self.select_settings_page(0)
        ttk.Label(scopes, text="选择需要扫描的位置", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.roots_list = self.listbox(scopes)
        self.root_buttons = self.button_row(scopes, 2, [("添加目录", lambda: self.choose_path(self.roots_list)),
            ("移除", lambda: self.remove_list(self.roots_list))])
        self.detect_button = ttk.Button(scopes, text="全盘扫描设置", style="Primary.TButton", command=self.prepare_full_scan)
        self.detect_button.grid(row=3, column=0, sticky="ew", pady=(s(6), s(14)))
        self.programs = tk.BooleanVar(value=True)
        self.program_check = Toggle(scopes, text="检查已安装程序（名称）", variable=self.programs, width=1)
        self.program_check.grid(row=4, column=0, sticky="ew")
        mode_row = ttk.Frame(scopes, style="Card.TFrame")
        mode_row.grid(row=5, column=0, sticky="ew", pady=(s(8), 0))
        mode_row.columnconfigure(1, weight=1)
        ttk.Label(mode_row, text="扫描", style="Muted.TLabel").grid(row=0, column=0, padx=(0, s(8)))
        self.scan_mode = tk.StringVar(value="文件名")
        self.mode_choice = Choice(mode_row, self.scan_mode, tuple(SCAN_MODES), width=1)
        self.mode_choice.grid(row=0, column=1, sticky="ew")
        self.scan_mode.trace_add("write", self.update_content_controls)
        options_row = ttk.Frame(scopes, style="Card.TFrame")
        options_row.grid(row=6, column=0, sticky="ew", pady=(s(6), 0))
        options_row.columnconfigure(0, weight=1)
        self.content_type_button = ttk.Button(options_row, text="内容类型", style="Compact.TButton", command=self.choose_content_types)
        self.content_type_button.grid(row=0, column=0, sticky="w")
        ttk.Label(options_row, text="上限", style="Muted.TLabel").grid(row=0, column=1, padx=(s(5), s(4)))
        self.content_limit = tk.StringVar(value="50")
        self.content_limit_entry = ttk.Entry(options_row, textvariable=self.content_limit, width=4, justify="center")
        self.content_limit_entry.grid(row=0, column=2)
        ttk.Label(options_row, text="MB", style="Muted.TLabel").grid(row=0, column=3, padx=(s(4), 0))
        ttk.Label(scopes, text="内容匹配可查看命中片段。\n超限、加密或损坏项记录原因。", style="Muted.TLabel").grid(row=7, column=0, sticky="w", pady=(s(8), s(2)))
        ttk.Label(keywords, text="所选扫描范围包含任意词即命中", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.keywords_list = self.listbox(keywords)
        entry_row = ttk.Frame(keywords, style="Card.TFrame")
        entry_row.grid(row=2, column=0, sticky="ew")
        entry_row.columnconfigure(0, weight=1)
        self.keyword_entry = ttk.Entry(entry_row, width=1)
        self.keyword_entry.grid(row=0, column=0, sticky="ew", padx=(0, s(6)))
        self.keyword_entry.bind("<Return>", lambda _e: self.add_keywords())
        self.keyword_add_button = ttk.Button(entry_row, text="添加", style="Primary.TButton", command=self.add_keywords)
        self.keyword_add_button.grid(row=0, column=1)
        self.keyword_buttons = self.button_row(keywords, 3, [("删除选中", lambda: self.remove_list(self.keywords_list)),
            ("清空词表", self.reset_keywords)])
        self.button_row(keywords, 4, [("导入词表", self.import_keywords), ("导出词表", self.export_keywords)])
        ttk.Label(keywords, text="忽略英文大小写。\n用分号分隔可批量添加关键词。", style="Muted.TLabel",
                  wraplength=s(235)).grid(row=5, column=0, sticky="w", pady=(s(12), s(2)))
        ttk.Label(excludes, text="跳过这些目录及其所有子目录", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.excludes_list = self.listbox(excludes)
        self.exclude_buttons = self.button_row(excludes, 2, [("添加排除", lambda: self.choose_path(self.excludes_list)),
            ("移除", lambda: self.remove_list(self.excludes_list))])
        ttk.Label(excludes, text="不跟随符号链接或目录联接。\n工具数据目录始终排除。", style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(s(12), s(2)))

        right = ttk.Frame(body, style="Card.TFrame", width=1, padding=s(12))
        self.results_panel = right
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_propagate(False)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        heading = ttk.Frame(right, style="Card.TFrame")
        heading.grid(row=0, column=0, sticky="ew")
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, text="扫描结果", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.count = tk.StringVar(value="显示 0 / 0 项 · 已选 0 项")
        ttk.Label(heading, textvariable=self.count, style="Muted.TLabel", width=30, anchor="e").grid(row=0, column=1, sticky="e")
        self.preview_button = ttk.Button(heading, text="隐藏预览", style="Compact.TButton", command=self.toggle_preview)
        self.preview_button.grid(row=0, column=2, padx=(s(8), 0))
        metrics = ttk.Frame(right, style="Card.TFrame", height=s(70))
        metrics.grid(row=1, column=0, sticky="ew", pady=(s(4), s(4)))
        metrics.grid_propagate(False)
        metrics.rowconfigure(0, weight=1)
        self.examined_value = tk.StringVar(value="0")
        self.hits_value = tk.StringVar(value="0")
        self.errors_value = tk.StringVar(value="0")
        self.elapsed_value = tk.StringVar(value="00:00")
        for index, (caption, variable) in enumerate((("已检查", self.examined_value), ("命中项", self.hits_value),
                                                    ("跳过 / 错误", self.errors_value), ("耗时", self.elapsed_value))):
            metrics.columnconfigure(index, weight=1, uniform="metric")
            card = ttk.Frame(metrics, style="Card.TFrame", padding=(s(12), s(5)))
            card.grid(row=0, column=index, sticky="nsew")
            card.columnconfigure(0, weight=1)
            ttk.Label(card, text=caption, style="Muted.TLabel", width=1).grid(row=0, column=0, sticky="ew")
            ttk.Label(card, textvariable=variable, width=1,
                      style="MetricAccent.TLabel" if index == 1 else "Metric.TLabel").grid(row=1, column=0, sticky="ew", pady=(s(5), 0))
        filters = ttk.Frame(right, style="Card.TFrame")
        filters.grid(row=2, column=0, sticky="ew", pady=(0, s(8)))
        filters.columnconfigure(1, weight=1)
        ttk.Label(filters, text="搜索", style="Muted.TLabel").grid(row=0, column=0, padx=(0, s(10)))
        self.query = tk.StringVar()
        self.query_entry = ttk.Entry(filters, textvariable=self.query, width=1)
        self.query_entry.grid(row=0, column=1, sticky="ew", padx=(0, s(8)))
        for shortcut in ("<Control-f>", "<Command-f>"):
            self.root.bind(shortcut, self.focus_result_search)
        self.keyword_filter = tk.StringVar(value="全部关键词")
        self.keyword_choice = Choice(filters, self.keyword_filter, ("全部关键词",), width=13)
        self.keyword_choice.grid(row=0, column=2, padx=(0, s(8)))
        self.kind = tk.StringVar(value="全部类型")
        self.kind_choice = Choice(filters, self.kind, ("全部类型", "文件", "目录", "程序"), width=9)
        self.kind_choice.grid(row=0, column=3)
        file_filters = ttk.Frame(filters, style="Card.TFrame")
        file_filters.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(s(8), 0))
        file_filters.columnconfigure(3, weight=1)
        ttk.Label(file_filters, text="类别", style="Muted.TLabel").grid(row=0, column=0, padx=(0, s(10)))
        self.file_category = tk.StringVar(value="全部文件类别")
        self.category_choice = Choice(file_filters, self.file_category, tuple(FILE_CATEGORIES), width=13)
        self.category_choice.grid(row=0, column=1, padx=(0, s(12)))
        ttk.Label(file_filters, text="后缀", style="Muted.TLabel").grid(row=0, column=2, padx=(0, s(8)))
        self.extensions = tk.StringVar()
        ttk.Entry(file_filters, textvariable=self.extensions, width=1).grid(row=0, column=3, sticky="ew")
        ttk.Label(file_filters, text="例：doc, docx", style="Muted.TLabel").grid(row=0, column=4, padx=s(8))
        ttk.Button(file_filters, text="重置筛选", style="Compact.TButton", command=self.reset_filters).grid(row=0, column=5)
        extra_filters = ttk.Frame(filters, style="Card.TFrame")
        extra_filters.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(s(8), 0))
        extra_filters.columnconfigure(1, weight=1)
        ttk.Label(extra_filters, text="来源", style="Muted.TLabel").grid(row=0, column=0, padx=(0, s(10)))
        self.source_filter = tk.StringVar(value="全部来源")
        self.source_choice = Choice(extra_filters, self.source_filter, ("全部来源", "包含内容命中", "仅名称命中"), width=16)
        self.source_choice.grid(row=0, column=1, sticky="w")
        self.export_filtered = tk.BooleanVar(value=False)
        Toggle(extra_filters, text="报告仅含当前筛选", variable=self.export_filtered, width=21).grid(row=0, column=2, sticky="e")
        self.query.trace_add("write", self.schedule_filter)
        self.keyword_filter.trace_add("write", self.schedule_filter)
        self.kind.trace_add("write", self.schedule_filter)
        self.file_category.trace_add("write", self.schedule_filter)
        self.extensions.trace_add("write", self.schedule_filter)
        self.source_filter.trace_add("write", self.schedule_filter)
        self.results_split = ResultSplit(right, ratio=self.preview_ratio, on_ratio_change=self.remember_preview_ratio)
        self.results_split.grid(row=3, column=0, sticky="nsew")
        table = ttk.Frame(self.results_split, style="Card.TFrame")
        self.results_split.add_top(table)
        table.grid_propagate(False)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = ("kind", "path", "keywords", "match_source", "size", "modified")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="extended")
        for column, caption, width in zip(columns, ("类型", "完整路径 / 程序名", "命中关键词", "命中来源", "大小", "修改时间"), (58, 310, 130, 90, 85, 145)):
            self.tree.heading(column, text=caption, anchor="w", command=lambda c=column: self.sort(c))
            self.tree.column(column, width=s(width), minwidth=s(220 if column == "path" else 45), stretch=column == "path", anchor="w")
        ys = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        self.preview = MatchPreview(self.results_split, fullscreen=self.view_matches)
        self.results_split.add_bottom(self.preview)
        self.preview.grid_propagate(False)
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)
        self.tree.bind("<Double-1>", lambda _e: self.open_folder())
        self.tree.bind("<Button-3>", self.context_menu)
        self.tree.bind("<Control-a>", self.select_all)
        self.tree.bind("<Command-a>", self.select_all)
        self.menu = ActionMenu(self.root)
        action_bar = ttk.Frame(right, style="Card.TFrame")
        action_bar.grid(row=4, column=0, sticky="ew", pady=(s(8), 0))
        action_bar.columnconfigure(2, weight=1)
        for col, caption, command in ((0, "全选", self.select_all), (1, "清除选择", lambda: self.tree.selection_remove(self.tree.selection()))):
            ttk.Button(action_bar, text=caption, command=command, style="Link.TButton").grid(row=0, column=col, padx=(0, s(4)))
        actions = (("打开目录", self.open_folder), ("复制路径", self.copy_paths),
                   ("移动", lambda: self.operate("move")), ("删除", lambda: self.operate("delete")))
        self.file_buttons = []
        for index, (caption, command) in enumerate(actions, 3):
            button = ttk.Button(action_bar, text=caption, command=command,
                                style="Danger.TButton" if caption == "删除" else "TButton")
            button.grid(row=0, column=index, padx=(s(6), 0))
            self.file_buttons.append(button)
        detail = ttk.Frame(right, style="Card.TFrame")
        detail.grid(row=5, column=0, sticky="ew", pady=(s(8), 0))
        detail.columnconfigure(1, weight=1)
        ttk.Label(detail, text="完整路径", style="Muted.TLabel").grid(row=0, column=0, padx=(0, s(8)))
        self.selected_path = tk.StringVar()
        self.selected_path_entry = ttk.Entry(detail, textvariable=self.selected_path, style="Path.TEntry", state="readonly", width=1)
        self.selected_path_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(detail, text="命中详情", style="Compact.TButton", command=self.view_matches).grid(row=0, column=2, padx=(s(8), 0))

        # Keep the complete status area allocated, even when the activity bar is hidden.
        self.footer = ttk.Frame(outer, style="Card.TFrame", height=s(76), padding=(s(16), s(8)))
        self.footer.grid(row=2, column=0, sticky="ew", pady=(s(14), 0))
        self.footer.grid_propagate(False)
        self.footer.columnconfigure(0, weight=1, minsize=0)
        self.footer.columnconfigure(1, weight=0)
        self.status = tk.StringVar(value="就绪 · 选择目录后开始扫描")
        self.status_label = ttk.Entry(self.footer, textvariable=self.status, style="Status.TEntry", state="readonly", width=1, takefocus=False)
        self.status_label.grid(row=0, column=0, sticky="ew")
        if os.name == "nt":
            admin = is_windows_admin()
            self.admin_button = ttk.Button(self.footer, text="管理员模式" if admin else "管理员启动",
                style="Compact.TButton", command=self.open_admin, state="disabled" if admin else "normal")
            self.admin_button.grid(row=0, column=1, sticky="e", padx=(s(12), 0))
        ttk.Button(self.footer, text="扫描覆盖 / 错误", style="Compact.TButton", command=self.show_errors).grid(row=0, column=2, sticky="e", padx=(s(8), 0))
        self.current = tk.StringVar(value="添加扫描目录和关键词，点击“开始扫描”。")
        self.current_label = ttk.Entry(self.footer, textvariable=self.current, style="Path.TEntry", state="readonly", width=1, takefocus=False)
        self.current_label.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(s(6), s(8)))
        self.progress_slot = ttk.Frame(self.footer, style="Card.TFrame", height=s(4))
        self.progress_slot.grid(row=2, column=0, columnspan=3, sticky="ew")
        self.progress_slot.grid_propagate(False)
        self.progress_bar = ttk.Progressbar(self.progress_slot, style="Slim.Horizontal.TProgressbar", mode="determinate", value=0, maximum=100)

    def update_content_controls(self, *_args):
        enabled = self.scan_mode.get() != "文件名" and not self.busy
        for widget in (self.content_type_button, self.content_limit_entry):
            widget.configure(state="normal" if enabled else "disabled")

    def choose_content_types(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("选择内容扫描类型")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        panel = ttk.Frame(dialog, padding=18)
        panel.pack(fill="both", expand=True)
        selected = {}
        for row, (name, suffixes) in enumerate(CONTENT_TYPES.items()):
            selected[name] = tk.BooleanVar(value=name in self.content_types)
            Toggle(panel, text=name, variable=selected[name], width=18).grid(row=row, column=0, sticky="ew", pady=4)
            sample = ", ".join(suffixes[:5]) + (" …" if len(suffixes) > 5 else "")
            ttk.Label(panel, text=sample).grid(row=row, column=1, sticky="w", padx=(15, 0))
        ttk.Label(panel, text="蓝色表示已选；点击切换。单文件最多读取 30 秒；图片和扫描版 PDF 不执行 OCR。").grid(row=7, column=0, columnspan=2, sticky="w", pady=(12, 8))
        def save():
            values = [name for name, variable in selected.items() if variable.get()]
            if not values:
                messagebox.showinfo("选择类型", "请至少选择一种内容类型。", parent=dialog)
                return
            self.content_types = values
            dialog.destroy()
        ttk.Button(panel, text="确定", style="Primary.TButton", command=save).grid(row=8, column=1, sticky="e")
        ttk.Button(panel, text="取消", command=dialog.destroy).grid(row=8, column=0, sticky="w")
        dialog.grab_set()

    def remember_preview_ratio(self, ratio):
        self.preview_ratio = ratio

    def toggle_preview(self):
        self.preview_visible = not self.preview_visible
        self.results_split.set_preview_visible(self.preview_visible)
        self.preview_button.configure(text="隐藏预览" if self.preview_visible else "显示预览")

    def select_settings_page(self, selected):
        for index, (page, button) in enumerate(zip(self.settings_pages, self.settings_tabs)):
            if index == selected:
                page.grid()
            else:
                page.grid_remove()
            button.configure(style="ActiveTab.TButton" if index == selected else "Tab.TButton")

    def toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        if self.sidebar_collapsed:
            self.sidebar.grid_remove()
            self.collapsed_sidebar.grid()
            self.body.columnconfigure(0, minsize=round(44 * self.display_scale))
        else:
            self.collapsed_sidebar.grid_remove()
            self.sidebar.grid()
            self.body.columnconfigure(0, minsize=round(270 * self.display_scale))

    def listbox(self, parent):
        s = lambda value: round(value * self.display_scale)
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.grid(row=1, column=0, sticky="nsew", pady=(s(10), s(10)))
        result = tk.Listbox(frame, selectmode="extended", exportselection=False, width=1, height=6,
                            activestyle="none", borderwidth=0, highlightthickness=1,
                            highlightbackground="#e2e7ef", highlightcolor="#91a8f4", background="white",
                            foreground="#34415a", selectbackground="#eaf0ff", selectforeground="#2445a5",
                            font="TkDefaultFont", relief="flat")
        y = ttk.Scrollbar(frame, orient="vertical", command=result.yview)
        x = ttk.Scrollbar(frame, orient="horizontal", command=result.xview)
        result.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        result.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return result

    def button_row(self, parent, row, items):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.grid(row=row, column=0, sticky="ew", pady=(round(6 * self.display_scale), 0))
        result = []
        for index, (caption, command) in enumerate(items):
            frame.columnconfigure(index, weight=1, uniform="buttons")
            appearance = "Primary.TButton" if caption.startswith("添加") else "Danger.TButton" if caption.startswith(("删除", "移除")) else "Warning.TButton" if caption == "重置" else "TButton"
            button = ttk.Button(frame, text=caption, command=command, style=appearance)
            button.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else round(6 * self.display_scale), 0))
            result.append(button)
        return result

    def add_path(self, box, path):
        value = os.path.abspath(os.path.expanduser(path))
        existing = {os.path.normcase(p) for p in box.get(0, "end")}
        if os.path.normcase(value) not in existing:
            box.insert("end", value)

    def choose_path(self, box):
        path = filedialog.askdirectory(parent=self.root, title="选择目录（可多次添加）")
        if path:
            self.add_path(box, path)

    def remove_list(self, box):
        for index in reversed(box.curselection()):
            box.delete(index)

    def detect_roots(self):
        for path in local_roots():
            self.add_path(self.roots_list, path)
        self.status.set("已添加本机磁盘；可在扫描目录中移除不需要的范围。")

    def prepare_full_scan(self):
        if self.busy:
            return
        roots = local_roots()
        if not roots:
            messagebox.showinfo("未找到本机磁盘", "请使用“添加目录”选择扫描位置。", parent=self.root)
            return
        self.roots_list.delete(0, "end")
        for path in roots:
            self.add_path(self.roots_list, path)
        self.scan_mode.set("文件名 + 内容")
        self.content_types = list(CONTENT_TYPES)
        self.content_limit.set("256")
        self.programs.set(True)
        if self.sidebar_collapsed:
            self.toggle_sidebar()
        self.select_settings_page(0)
        self.status.set(f"已设置 {len(roots)} 个本机磁盘 · 点击“开始扫描”执行")
        self.current.set("匹配文件名与全部支持类型的内容，单文件上限 256 MB；保留排除目录，未读项见“扫描覆盖 / 错误”。")

    def add_keywords(self):
        value = self.keyword_entry.get().replace("；", ";")
        words = normalize_keywords(list(self.keywords_list.get(0, "end")) + value.split(";"))
        self.keywords_list.delete(0, "end")
        for word in words:
            self.keywords_list.insert("end", word)
        self.keyword_entry.delete(0, "end")

    def reset_keywords(self):
        if self.keywords_list.size() and messagebox.askyesno("清空词表", "清空当前所有关键词？", parent=self.root):
            self.keywords_list.delete(0, "end")

    def import_keywords(self):
        path = filedialog.askopenfilename(title="导入词表（UTF-8，每行一个关键词）", parent=self.root,
                                          filetypes=[("词表", "*.txt"), ("所有文件", "*")])
        if path:
            try:
                words = normalize_keywords(list(self.keywords_list.get(0, "end")) +
                                           Path(path).read_text(encoding="utf-8-sig").splitlines())
                self.keywords_list.delete(0, "end")
                for keyword in words:
                    self.keywords_list.insert("end", keyword)
            except (OSError, UnicodeError) as exc:
                messagebox.showerror("导入失败", str(exc), parent=self.root)

    def export_keywords(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="导出词表", defaultextension=".txt",
                                            initialfile="扫描关键词.txt")
        if path:
            try:
                Path(path).write_text("\n".join(self.keywords_list.get(0, "end")) + "\n", encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("导出失败", str(exc), parent=self.root)

    def set_running(self, running):
        self.start_button.configure(state="disabled" if running else "normal")
        self.pause_button.configure(state="normal" if self.scanner else "disabled")
        self.stop_button.configure(state="normal" if self.scanner else "disabled")
        self.export_button.configure(state="disabled" if running else "normal")
        for button in self.file_buttons[2:]:
            button.configure(state="disabled" if running else "normal")
        self.mode_choice.set_enabled(not running)
        self.detect_button.configure(state="disabled" if running else "normal")
        self.update_content_controls()
        if running:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="indeterminate", value=0, maximum=100)
            self.progress_bar.place(x=0, y=0, relwidth=1, relheight=1)
            self.progress_bar.start(15)
        else:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate", value=0, maximum=100)
            self.progress_bar.place_forget()

    def start_scan(self):
        if self.busy:
            return
        roots = list(self.roots_list.get(0, "end"))
        keywords = list(self.keywords_list.get(0, "end"))
        if not roots or not keywords:
            messagebox.showinfo("需要扫描范围和关键词", "请至少添加一个扫描目录和一个关键词。", parent=self.root)
            return
        mode = SCAN_MODES[self.scan_mode.get()]
        try:
            limit = int(self.content_limit.get())
            if not 1 <= limit <= 1024:
                raise ValueError()
        except ValueError:
            if mode != "name":
                messagebox.showinfo("内容大小上限", "请输入 1 到 1024 之间的整数（MB）。", parent=self.root)
                return
            limit = 50
        if mode != "name" and not self.content_types:
            messagebox.showinfo("内容类型", "请至少选择一种内容扫描类型。", parent=self.root)
            return
        invalid = [p for p in roots if not Path(p).is_dir()]
        if invalid and len(invalid) == len(roots):
            messagebox.showerror("目录不可用", "请移除或重新选择以下目录：\n" + "\n".join(invalid), parent=self.root)
            return
        if self.results and not messagebox.askyesno("开始新扫描", "本次将清空界面中的旧结果。需要保留时请先导出报告。继续？", parent=self.root):
            return
        self.save_config()
        self.cancel_rebuild()
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self.keyword_choice.set_values(("全部关键词", *keywords))
        self.selected_path.set("")
        self.preview.set_hit(None)
        self.next_id = 0
        self.error_samples = []
        self.denied_paths = []
        self.permission_errors = 0
        self.action_log = []
        self.metadata = {"version": VERSION, "started": datetime.now().isoformat(" ", timespec="seconds"),
            "roots": roots, "keywords": keywords, "excludes": list(self.excludes_list.get(0, "end")),
            "programs": self.programs.get(), "match": self.scan_mode.get() + "包含关键词，忽略大小写",
            "scan_mode": mode, "content_types": list(self.content_types), "max_content_mb": limit,
            "unavailable_roots": invalid,
            "error_log": str(data_dir() / "logs" / ("scan_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".jsonl"))}
        self.scanner = Scanner(roots, keywords, self.events, self.metadata["excludes"], self.programs.get(),
            scan_mode=mode, content_types=self.content_types, max_content_mb=limit, error_log=self.metadata["error_log"])
        self.thread = threading.Thread(target=self.scanner.run, daemon=True)
        self.pause_button.configure(text="暂停")
        self.status.set("正在扫描…")
        self.current.set("正在准备扫描目录…")
        self.update_metrics(dict(examined=0, hits=0, errors=0, elapsed=0))
        self.update_count()
        self.set_running(True)
        self.thread.start()

    def pause(self):
        if not self.scanner:
            return
        if self.scanner.resume.is_set():
            self.scanner.resume.clear()
            self.pause_button.configure(text="继续")
            value = self.progress_bar["value"]
            self.progress_bar.stop()
            self.progress_bar.configure(value=value)
            self.status.set("已暂停 · 可继续或停止")
        else:
            self.scanner.resume.set()
            self.pause_button.configure(text="暂停")
            self.progress_bar.start(15)
            self.status.set("正在扫描…")

    def stop(self):
        if self.scanner:
            self.scanner.stop.set()
            self.scanner.resume.set()
            self.status.set("正在停止，保留已发现的结果…")
            self.stop_button.configure(state="disabled")

    def visible(self, hit):
        query = self.query.get().casefold().strip()
        keyword = self.keyword_filter.get()
        matched_words = hit.matched_keywords or hit.keywords.split(" | ")
        source = self.source_filter.get()
        return (self.kind.get() == "全部类型" or hit.kind == self.kind.get()) and matches_file_filter(
            hit.path, hit.kind, self.file_category.get(), self.extensions.get()) and (
            keyword == "全部关键词" or any(word.casefold() == keyword.casefold() for word in matched_words)) and (
            source == "全部来源" or source == "包含内容命中" and hit.match_source in ("内容", "名称+内容") or
            source == "仅名称命中" and hit.match_source not in ("内容", "名称+内容")) and (
            not query or query in (hit.path + " " + hit.keywords + " " + hit.detail).casefold())

    def reset_filters(self):
        self.query.set("")
        self.keyword_filter.set("全部关键词")
        self.kind.set("全部类型")
        self.file_category.set("全部文件类别")
        self.extensions.set("")
        self.source_filter.set("全部来源")

    def focus_result_search(self, _event=None):
        self.query_entry.focus_set()
        self.query_entry.selection_range(0, "end")
        return "break"

    def insert_hit(self, key, hit):
        self.tree.insert("", "end", iid=key, values=(hit.kind, hit.path, hit.keywords,
            hit.match_source or ("程序名称" if hit.kind == "程序" else "文件名"),
            size_text(hit.size) if hit.kind == "文件" else "—",
            datetime.fromtimestamp(hit.modified).strftime("%Y-%m-%d %H:%M") if hit.modified else "—"))

    def update_count(self):
        self.count.set(f"显示 {len(self.tree.get_children())} / {len(self.results)} 项 · 已选 {len(self.tree.selection())} 项")

    def selection_changed(self, _event=None):
        self.update_count()
        hits = self.selected_hits()
        self.selected_path.set(hits[0].path if hits else "")
        self.selected_path_entry.xview_moveto(0)
        self.preview.set_hit(hits[0] if hits else None, selected=len(hits))

    def view_paths(self):
        hits = self.selected_hits()
        if hits:
            self.show_text("完整路径", "\n\n".join(hit.path for hit in hits))
        else:
            messagebox.showinfo("选择结果", "请先选中需要查看完整路径的结果。", parent=self.root)

    def view_matches(self):
        hits = self.selected_hits()
        if hits:
            dialog = tk.Toplevel(self.root)
            dialog.title("匹配内容与具体位置")
            dialog.geometry("920x540")
            dialog.minsize(620, 320)
            panel = ttk.Frame(dialog, padding=14)
            panel.pack(fill="both", expand=True)
            ttk.Label(panel, text=hits[0].path, wraplength=850).pack(fill="x", pady=(0, 10))
            preview = MatchPreview(panel)
            preview.pack(fill="both", expand=True)
            preview.set_hit(hits[0], selected=len(hits))
            bar = ttk.Frame(panel, padding=(0, 10, 0, 0))
            bar.pack(fill="x")
            def copy():
                value = preview.location.get() + "\n" + preview.text.get("1.0", "end-1c")
                self.root.clipboard_clear()
                self.root.clipboard_append(hits[0].path + "\n" + value)
            ttk.Button(bar, text="复制位置与内容", command=copy).pack(side="left")
            ttk.Button(bar, text="关闭", command=dialog.destroy).pack(side="right")
            dialog.preview = preview
            return dialog
        else:
            messagebox.showinfo("选择结果", "请先选中需要查看的结果。", parent=self.root)

    def update_metrics(self, payload):
        self.examined_value.set(str(payload["examined"]))
        self.hits_value.set(str(payload["hits"]))
        self.errors_value.set(str(payload["errors"]))
        elapsed = max(0, int(payload["elapsed"]))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.elapsed_value.set(f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}")

    def schedule_filter(self, *_args):
        if self.filter_timer:
            self.root.after_cancel(self.filter_timer)
        self.filter_timer = self.root.after(250, self.rebuild)

    def sort(self, column):
        self.sort_reverse = not self.sort_reverse if self.sort_column == column else False
        self.sort_column = column
        self.rebuild()

    def cancel_rebuild(self):
        self.rebuild_generation += 1
        if self.rebuild_id:
            self.root.after_cancel(self.rebuild_id)
            self.rebuild_id = None
        if self.filter_timer:
            self.root.after_cancel(self.filter_timer)
            self.filter_timer = None

    def rebuild(self, selection_paths=None):
        self.cancel_rebuild()
        generation = self.rebuild_generation
        selected = set(self.tree.selection()) if selection_paths is None else {
            key for key, hit in self.results.items() if hit.path in selection_paths}
        self.tree.delete(*self.tree.get_children())
        items = [(key, hit) for key, hit in self.results.items() if self.visible(hit)]
        items.sort(key=lambda pair: getattr(pair[1], self.sort_column), reverse=self.sort_reverse)
        def chunk(offset=0):
            if generation != self.rebuild_generation:
                return
            for key, hit in items[offset:offset+400]:
                if key in self.results and not self.tree.exists(key):
                    self.insert_hit(key, hit)
                    if key in selected:
                        self.tree.selection_add(key)
            self.update_count()
            if offset + 400 < len(items):
                self.rebuild_id = self.root.after(1, lambda: chunk(offset+400))
            else:
                self.rebuild_id = None
                self.selection_changed()
        chunk()

    def poll(self):
        deadline = time.monotonic() + 0.035
        while time.monotonic() < deadline:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "hits":
                for hit in payload:
                    key = str(self.next_id)
                    self.next_id += 1
                    self.results[key] = hit
                    if self.visible(hit):
                        self.insert_hit(key, hit)
                self.update_count()
            elif event == "progress":
                self.metadata["progress"] = payload
                self.update_metrics(payload)
                if self.scanner and self.scanner.resume.is_set() and not self.scanner.stop.is_set():
                    self.status.set("正在扫描 · 可随时暂停或停止")
                path = payload["current"]
                self.current.set(path)
                self.current_label.xview_moveto(1)
            elif event == "coverage":
                self.metadata["coverage"] = payload
            elif event == "done":
                self.scanner = None
                self.error_samples = payload["errors"]
                self.permission_errors = payload.get("permission_errors", 0)
                self.denied_paths = payload.get("denied_paths", [])
                self.metadata["finished"] = datetime.now().isoformat(" ", timespec="seconds")
                self.metadata["cancelled"] = payload["cancelled"]
                self.metadata["errors"] = self.error_samples
                self.metadata["permission_errors"] = self.permission_errors
                self.metadata["denied_paths"] = self.denied_paths
                self.set_running(False)
                summary = self.metadata.get("progress", {})
                state = "已停止（部分结果）" if payload["cancelled"] else "扫描结束"
                if summary.get("errors", 0):
                    state += "（存在访问失败或跳过，详见错误记录）"
                self.status.set(state)
                self.current.set("结果已保留。可筛选、导出、打开目录，或选择文件进行批量操作。")
                if self.metadata.get("scan_mode", "name") != "name":
                    self.current.set(f"内容已读取 {summary.get('content_read', 0)} 项 · 未完整读取 {summary.get('content_skipped', 0)} 项 · 选中结果可查看命中详情。")
                self.rebuild()
            elif event == "operation_done":
                self.finish_operation(payload)
            elif event == "action_progress":
                self.status.set(f"正在{payload['title']} · {payload['done']} / {payload['total']} 项")
                self.current.set(payload["path"])
                self.current_label.xview_moveto(1)
                self.progress_bar.configure(value=payload["done"], maximum=payload["total"])
        if self.closing and not self.busy:
            self.root.destroy()
            return
        self.poll_id = self.root.after(70, self.poll)

    def cleanup_timers(self, event):
        if event.widget is self.root:
            self.cancel_rebuild()
            if self.poll_id:
                self.root.after_cancel(self.poll_id)
                self.poll_id = None

    def select_all(self, _event=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    def selected_hits(self):
        return [self.results[key] for key in self.tree.selection() if key in self.results]

    def context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item and item not in self.tree.selection():
            self.tree.selection_set(item)
        if not item and not self.tree.selection():
            return
        groups = [
            [("⌁  打开所在目录", self.open_folder, False),
             ("▢  复制完整路径", self.copy_paths, False),
             ("↗  查看完整路径", self.view_paths, False),
             ("◈  查看匹配内容", self.view_matches, False)],
            [("➜  移动选中项", lambda: self.operate("move"), False),
             ("⌫  移入系统回收站", lambda: self.operate("delete"), True)],
        ]
        if os.name != "nt":
            groups[-1].append(("✕  彻底删除（需确认）", lambda: self.operate("delete", permanent=True), True))
        if self.actions.bin.is_dir():
            groups.append([("↶  恢复旧版文件", self.recovery, False)])
        self.menu.show(event.x_root, event.y_root, groups)

    def copy_paths(self):
        hits = self.selected_hits()
        if hits:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(h.path for h in hits))
            self.status.set(f"已复制 {len(hits)} 个路径 / 程序名")

    def open_folder(self):
        hits = self.selected_hits()
        if not hits:
            messagebox.showinfo("选择结果", "请先选中要打开的结果。", parent=self.root)
            return
        hit = hits[0]
        if hit.kind == "程序" and not Path(hit.path).is_absolute():
            messagebox.showinfo("程序清单", "此项来自系统已安装程序列表，没有可打开的目录。", parent=self.root)
            return
        path = Path(hit.path)
        folder = path if hit.kind == "目录" else path.parent
        if not folder.is_dir():
            messagebox.showwarning("目录不存在", str(folder), parent=self.root)
            return
        try:
            if os.name == "nt":
                os.startfile(str(folder))
            else:
                subprocess.Popen(["open" if platform.system() == "Darwin" else "xdg-open", str(folder)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc), parent=self.root)

    def confirm_operation(self, title, explanation, paths, *, allow_readonly=None, permanent=False):
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{APP_NAME} · {title}")
        width = min(round(760 * self.display_scale), self.root.winfo_screenwidth() - 80)
        height = min(round(460 * self.display_scale), self.root.winfo_screenheight() - 100)
        dialog.geometry(f"{width}x{height}")
        dialog.transient(self.root)
        dialog.configure(background="#f3f5f9")
        header = ttk.Frame(dialog, padding=(20, 16))
        header.pack(fill="x")
        ttk.Label(header, text=f"{title} · {len(paths)} 项", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text=explanation, wraplength=width-48, style="Subtitle.TLabel",
                  justify="left").pack(anchor="w", pady=(7, 0))
        frame = ttk.Frame(dialog, style="Card.TFrame", padding=12)
        frame.pack(fill="both", expand=True, padx=20)
        ttk.Label(frame, text="将处理的路径", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        text = tk.Text(frame, wrap="none", height=10, font="TkDefaultFont", bg="#f7f9fd",
                       fg="#243047", relief="flat", padx=9, pady=8)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", "\n".join(paths))
        text.configure(state="disabled")
        if os.name == "nt" and allow_readonly is not None:
            Toggle(dialog, text="允许处理只读文件（失败时恢复仍存在文件的原属性）",
                   variable=allow_readonly, width=52).pack(anchor="w", padx=20, pady=(10, 0))
        permanent_confirm = tk.BooleanVar(value=False)
        if permanent:
            Toggle(dialog, text="我确认彻底删除，无法从系统回收站恢复", variable=permanent_confirm, width=48).pack(
                anchor="w", padx=20, pady=(10, 0))
        accepted = [False]
        def accept():
            if permanent and not permanent_confirm.get():
                return
            accepted[0] = True
            dialog.destroy()
        bar = ttk.Frame(dialog, padding=(20, 16))
        bar.pack(fill="x")
        cancel = ttk.Button(bar, text="取消", command=dialog.destroy)
        cancel.pack(side="right")
        confirm = ttk.Button(bar, text="确认" + title, command=accept,
                             style="Danger.TButton" if "删除" in title else "Primary.TButton")
        confirm.pack(side="right", padx=8)
        if permanent:
            confirm.configure(state="disabled")
            permanent_confirm.trace_add("write", lambda *_: confirm.configure(state="normal" if permanent_confirm.get() else "disabled"))
        cancel.focus_set()
        dialog.grab_set()
        dialog.wait_window()
        return accepted[0]

    def operate(self, mode, *, permanent=False):
        if self.busy:
            messagebox.showinfo("请等待", "请先停止扫描或等待当前文件操作结束。", parent=self.root)
            return
        hits = self.selected_hits()
        if not hits:
            messagebox.showinfo("选择文件", "请先选中需要操作的文件或目录。", parent=self.root)
            return
        if any(h.kind == "程序" for h in hits):
            messagebox.showinfo("包含程序结果", "请取消选择程序项。程序只能查看，文件和目录可以移动或删除。", parent=self.root)
            return
        hits = self.actions.selected(hits)
        destination = None
        title = "移动" if mode == "move" else "彻底删除" if permanent else "删除到系统回收站"
        if mode == "move":
            destination = filedialog.askdirectory(title="选择移动目标目录（同名项不会覆盖）", parent=self.root)
            if not destination:
                return
        explanation = f"将{title}以下 {len(hits)} 项。父目录与子文件同时选中时，只操作父目录。"
        if any(h.kind == "目录" for h in hits):
            explanation += "\n目录内所有内容都会一起操作，包括未命中关键词的文件。"
        if mode == "move":
            explanation += "\n目标目录：" + destination + "\n同名目标不会覆盖。"
        elif permanent:
            explanation += "\n此操作会永久删除，不经过系统回收站，不能通过工具恢复。"
        else:
            explanation += "\n使用电脑的系统回收站，恢复请在系统回收站中操作。"
            if os.name == "nt":
                explanation += "\n回收站不可用时会保留原路径并报告失败。可取消只读文件处理选项。"
            else:
                explanation += "\n回收站不可用时会报告失败，不会自动彻底删除。右键可选择彻底删除并另行确认。"
        # One clear decision covers the whole batch. Trash deletion may need
        # the temporary attribute change on Windows; errors still keep the hit.
        allow_readonly = tk.BooleanVar(value=(os.name == "nt" and mode == "delete" and not permanent))
        if not self.confirm_operation(title, explanation, [h.path for h in hits],
                                      allow_readonly=allow_readonly, permanent=permanent):
            return
        readonly_allowed = allow_readonly.get()
        self.operating = True
        self.set_running(True)
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate", value=0, maximum=len(hits))
        self.status.set(f"正在{title} 0 / {len(hits)} 项，请等待…")
        def worker():
            successes, failures, failed_paths = [], [], []
            cancelled = False
            unprocessed = 0
            for index, hit in enumerate(hits, 1):
                try:
                    if mode == "move":
                        result = self.actions.move(hit, destination, allow_readonly=readonly_allowed)
                    else:
                        result = self.actions.delete(hit, permanent=permanent, allow_readonly=readonly_allowed)
                    successes.append((hit.path, result))
                except Exception as exc:
                    failed_paths.append(hit.path)
                    failures.append(describe_failure(hit.path, title, exc))
                    if is_user_cancelled(exc):
                        cancelled = True
                        unprocessed = len(hits) - index
                        failed_paths.extend(item.path for item in hits[index:])
                        break
                self.events.put(("action_progress", dict(title=title, path=hit.path, done=index, total=len(hits))))
            self.events.put(("operation_done", dict(mode=mode, successes=successes, failures=failures,
                failed_paths=failed_paths, permanent=permanent, cancelled=cancelled, unprocessed=unprocessed)))
        threading.Thread(target=worker, daemon=True).start()

    def select_failed_paths(self, paths):
        self.reset_filters()
        self.rebuild(selection_paths=set(paths))

    def finish_operation(self, payload):
        from .core import beneath
        self.operating = False
        successes, failures = payload["successes"], payload["failures"]
        if payload["mode"] != "restore":
            for key, hit in list(self.results.items()):
                if hit.kind in ("文件", "目录") and any(beneath(hit.path, src) for src, _target in successes):
                    del self.results[key]
            self.rebuild()
        self.action_log.append({"time": datetime.now().isoformat(" ", timespec="seconds"), **payload})
        self.set_running(False)
        state = "操作已取消" if payload.get("cancelled") else "操作结束"
        self.status.set(f"{state} · 成功 {len(successes)} 项 · 失败 {len(failures)} 项 · 未处理 {payload.get('unprocessed', 0)} 项")
        if getattr(self, "refresh_recovery", None):
            self.refresh_recovery()
        if failures:
            actions = []
            paths = payload.get("failed_paths", [])
            if paths:
                actions.append(("选中未完成项" if payload.get("cancelled") else "选中失败项", lambda: self.select_failed_paths(paths)))
            content = f"本次成功 {len(successes)} 项，失败 {len(failures)} 项，未处理 {payload.get('unprocessed', 0)} 项。失败不代表磁盘状态没有变化，请按各项记录核对。\n"
            if os.name != "nt" and payload["mode"] == "delete" and not payload.get("permanent"):
                content += "回收站不可用时可在列表右键选择“彻底删除（需确认）”，不会自动改为永久删除。\n"
            if payload["mode"] == "delete" and not payload.get("permanent"):
                actions.append(("打开系统回收站", self.open_system_trash))
            self.show_text("部分操作失败", content + "\n\n".join(failures), actions)
            self.current.set("失败项仍保留在结果列表，可选中后检查并重试。")
        elif payload["mode"] == "restore":
            self.current.set("旧版文件已恢复到原路径。重新扫描可以刷新结果。")
        elif payload["mode"] == "move":
            self.current.set("文件移动已完成。")
        elif payload.get("permanent"):
            self.current.set("已按确认彻底删除。")
        else:
            self.current.set("系统删除操作已完成；可回收的项目请到系统回收站查看和恢复。")

    def open_system_trash(self):
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer.exe", "shell:RecycleBinFolder"])
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(Path.home() / ".Trash")])
            else:
                command = ["gio", "open", "trash:///"] if shutil.which("gio") else ["xdg-open", "trash:///"]
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            messagebox.showerror("系统回收站未打开", str(exc), parent=self.root)

    def recovery(self):
        if getattr(self, "recovery_window", None) and self.recovery_window.winfo_exists():
            self.recovery_window.lift()
            return
        dialog = tk.Toplevel(self.root)
        self.recovery_window = dialog
        dialog.title("旧版回收文件恢复")
        dialog.geometry("850x480")
        ttk.Label(dialog, text="这里只恢复旧版本留下的文件；新删除使用系统回收站。原路径已有同名项时不会覆盖。", padding=10).pack(anchor="w")
        tree = ttk.Treeview(dialog, columns=("path", "time"), show="headings", selectmode="extended")
        tree.heading("path", text="原始路径")
        tree.heading("time", text="回收时间")
        tree.column("path", width=600)
        tree.column("time", width=170)
        tree.pack(fill="both", expand=True, padx=10)
        records = {}
        def refresh():
            if not dialog.winfo_exists():
                return
            tree.delete(*tree.get_children())
            records.clear()
            for entry in self.actions.records():
                records[entry["id"]] = entry
                tree.insert("", "end", iid=entry["id"], values=(entry["original"], entry["time"]))
        self.refresh_recovery = refresh
        def restore():
            if self.busy:
                messagebox.showinfo("请等待", "请先停止扫描或等待文件操作结束。", parent=dialog)
                return
            entries = [records[key] for key in tree.selection()]
            if not entries:
                return
            allow_readonly = tk.BooleanVar(value=False)
            if not self.confirm_operation("恢复", f"将恢复以下 {len(entries)} 项到原始路径，不覆盖现有文件。",
                                          [e["original"] for e in entries], allow_readonly=allow_readonly):
                return
            readonly_allowed = allow_readonly.get()
            self.operating = True
            self.set_running(True)
            self.status.set("正在恢复文件，请等待…")
            def worker():
                successes, failures = [], []
                for entry in entries:
                    try:
                        target = self.actions.restore(entry, allow_readonly=readonly_allowed)
                        successes.append((entry["stored"], target))
                    except Exception as exc:
                        failures.append(describe_failure(entry["stored"], "旧版恢复", exc))
                self.events.put(("operation_done", dict(mode="restore", successes=successes, failures=failures)))
            threading.Thread(target=worker, daemon=True).start()
        buttons = ttk.Frame(dialog, padding=10)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="恢复选中项", command=restore).pack(side="left")
        ttk.Button(buttons, text="刷新", command=refresh).pack(side="left", padx=6)
        ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(side="right")
        ttk.Label(dialog, text="旧版数据位置：" + str(self.actions.bin), padding=10, wraplength=820).pack(anchor="w")
        refresh()

    def export(self):
        if self.busy:
            return
        if not self.results and not self.metadata:
            messagebox.showinfo("暂无结果", "请先执行一次扫描。", parent=self.root)
            return
        filtered = self.export_filtered.get()
        hits = [hit for hit in self.results.values() if self.visible(hit)] if filtered else list(self.results.values())
        scope = "当前筛选结果" if filtered else "全部结果（包括筛选隐藏项）"
        path = filedialog.asksaveasfilename(parent=self.root, title=f"导出{scope}",
            initialfile="觅文报告_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".html",
            defaultextension=".html", filetypes=[("可交互网页报告", "*.html"), ("Excel 表格", "*.csv"),
                                                ("文本报告", "*.txt"), ("结构化数据", "*.json")])
        if path:
            try:
                export_report(path, hits, {**self.metadata, "actions": self.action_log,
                    "remaining_results": len(self.results), "report_scope": scope})
                self.status.set(f"已导出{scope} {len(hits)} 项：{path}")
            except (OSError, ValueError) as exc:
                messagebox.showerror("导出失败", str(exc), parent=self.root)

    def show_text(self, title, content, actions=()):
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{APP_NAME} · {title}")
        s = self.display_scale
        dialog.geometry(f"{min(round(860*s), self.root.winfo_screenwidth()-70)}x{min(round(510*s), self.root.winfo_screenheight()-90)}")
        dialog.configure(background="#f3f5f9")
        dialog.transient(self.root)
        ttk.Label(dialog, text=title, style="Title.TLabel", padding=(18, 14)).pack(anchor="w")
        frame = ttk.Frame(dialog, style="Card.TFrame", padding=12)
        frame.pack(fill="both", expand=True, padx=18)
        text = tk.Text(frame, wrap="word", font="TkDefaultFont", background="#ffffff",
                       foreground="#243047", relief="flat", padx=8, pady=8, spacing3=3)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", content)
        text.configure(state="disabled")
        bar = ttk.Frame(dialog, padding=(18, 12))
        bar.pack(fill="x")
        for caption, command in actions:
            ttk.Button(bar, text=caption, command=command).pack(side="left", padx=(0, 8))
        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
        ttk.Button(bar, text="复制内容", command=copy).pack(side="left")
        ttk.Button(bar, text="关闭", command=dialog.destroy).pack(side="right")
        return dialog

    def open_admin(self):
        if self.busy:
            messagebox.showinfo("请等待", "请先停止扫描或等待文件操作完成，再打开管理员窗口。", parent=self.root)
            return
        if not self.save_config():
            return
        try:
            request_elevation(list(self.roots_list.get(0, "end")))
            self.status.set("已请求管理员窗口 · 请在 Windows 授权窗口中确认")
            self.current.set("原窗口结果仍保留。请在新的管理员窗口中重新开始扫描。")
        except OSError as exc:
            messagebox.showerror("管理员窗口未打开", str(exc), parent=self.root)

    def exclude_denied_paths(self):
        for path in self.denied_paths:
            self.add_path(self.excludes_list, path)
        if self.sidebar_collapsed:
            self.toggle_sidebar()
        self.select_settings_page(2)
        self.save_config()
        self.status.set(f"已将 {len(self.denied_paths)} 个受限路径加入排除 · 下次扫描生效")

    def show_errors(self):
        samples = self.scanner.error_samples if self.scanner else self.error_samples
        denied = self.scanner.permission_errors if self.scanner else self.permission_errors
        if self.scanner:
            self.denied_paths = list(self.scanner.denied_paths)
        coverage = (dict(files=self.scanner.files_examined, unsupported=self.scanner.content_unsupported,
            excluded=self.scanner.excluded_entries, links=self.scanner.links_skipped) if self.scanner else self.metadata.get("coverage", {}))
        progress = (dict(content_read=self.scanner.content_read, content_skipped=self.scanner.content_skipped,
            errors=self.scanner.errors) if self.scanner else self.metadata.get("progress", {}))
        roots = self.metadata.get("roots", [])
        content = "扫描范围：\n" + ("\n".join(roots) if roots else "尚未开始扫描") + "\n\n"
        content += f"文件检查 {coverage.get('files', 0)} 项；内容已读取 {progress.get('content_read', 0)} 项；内容未完整读取 {progress.get('content_skipped', 0)} 项。\n"
        content += f"未选类型 / 不支持格式 {coverage.get('unsupported', 0)} 项；排除的文件或目录入口 {coverage.get('excluded', 0)} 项；链接 / 联接点入口 {coverage.get('links', 0)} 项。\n"
        content += f"本次模式：{self.metadata.get('match', '尚未扫描')}。未选或不支持的格式不算已完成内容扫描。图片及扫描版 PDF 不执行 OCR，压缩包内文件与嵌入对象不读取。\n\n"
        if self.metadata.get("unavailable_roots"):
            content += "开始时不可用的目录 / 磁盘，已记录后继续其余范围：\n" + "\n".join(self.metadata["unavailable_roots"]) + "\n\n"
        content += "扫描跳过用户排除目录、符号链接、Windows 联接点和工具数据目录。权限错误只影响当前项，其余继续。\n"
        content += f"权限不足：{denied} 项。最多保存前 100 条错误及受限路径样例，总错误数显示在扫描进度中。\n\n"
        actions = []
        log_path = self.metadata.get("error_log")
        if log_path and Path(log_path).is_file():
            actions.append(("保存完整错误清单", self.export_error_log))
            content += f"全部错误逐条保存在：{log_path}\n\n"
        if os.name == "nt":
            content += "WinError 5 表示当前进程没有访问权限。可在扫描停止后使用“管理员启动”，在新窗口重新扫描。\n"
            content += "管理员仍可能无法访问受系统保护或被策略限制的目录。这些目录会跳过并记录，不会修改系统权限。\n\n"
            if not is_windows_admin():
                actions.append(("以管理员身份打开", self.open_admin))
        else:
            content += "访问受限项需要当前账户获得读取和目录遍历权限；也可以将不需要扫描的受限路径加入排除。\n\n"
        if self.denied_paths:
            actions.append(("受限路径加入排除", self.exclude_denied_paths))
        content += "\n".join(samples) if samples else "本次暂无错误记录。"
        self.show_text("跳过 / 错误详情", content, actions)

    def export_error_log(self):
        source = self.metadata.get("error_log")
        if not source or not Path(source).is_file():
            return
        destination = filedialog.asksaveasfilename(parent=self.root, title="保存全部未读 / 错误记录",
            initialfile="扫描未读记录.jsonl", defaultextension=".jsonl", filetypes=[("逐项记录", "*.jsonl")])
        if destination:
            try:
                shutil.copyfile(source, destination)
            except OSError as exc:
                messagebox.showerror("保存失败", str(exc), parent=self.root)

    def close(self):
        if self.operating:
            messagebox.showinfo("文件操作进行中", "请等待当前文件操作完成后关闭，以免中断移动。", parent=self.root)
            return
        if self.scanner:
            if not messagebox.askyesno("停止并关闭", "扫描仍在进行。停止扫描并关闭窗口？未导出的结果不会保存。", parent=self.root):
                return
            self.closing = True
            self.save_config()
            self.stop()
        else:
            self.save_config()
            self.root.destroy()


def main(paths=()):
    root = create_root()
    App(root, paths)
    root.mainloop()
