"""Consistent selectors and accessible literal-match previews for Tk."""
import tkinter as tk
from tkinter import font as tkfont, ttk

from .matching import match_ranges


class Choice(ttk.Frame):
    def __init__(self, parent, variable, values, *, width=12):
        super().__init__(parent, style="Selector.TFrame", width=width * 8, height=34, padding=1)
        self.variable, self.values = variable, tuple(values)
        self.popup = None
        self.enabled = True
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.button = ttk.Button(self, textvariable=variable, style="Selector.TButton", width=width, command=self.open)
        self.button.grid(row=0, column=0, sticky="nsew")
        scale = max(1, self.winfo_fpixels("1i") / 96)
        arrow = tk.Canvas(self, width=round(22 * scale), height=round(24 * scale), bg="#ffffff", highlightthickness=0)
        chevron = arrow.create_line(0, 0, 0, 0, 0, 0, fill="#65758e", width=round(2 * scale))
        def draw(event):
            x, y, size = event.width / 2, event.height / 2, 4 * scale
            arrow.coords(chevron, x - size, y - size / 2, x, y + size / 2, x + size, y - size / 2)
        arrow.bind("<Configure>", draw)
        arrow.grid(row=0, column=1, sticky="ns")
        arrow.bind("<Button-1>", lambda _: self.open())
        for sequence in ("<Down>", "<space>", "<Return>"):
            self.button.bind(sequence, lambda _: self.open())
        self.bind("<Destroy>", lambda event: self.close() if event.widget is self else None, add="+")

    def set_enabled(self, value):
        self.enabled = value
        self.button.state(["!disabled"] if value else ["disabled"])
        if not value:
            self.close()

    def set_values(self, values):
        self.close()
        self.values = tuple(values)
        if self.variable.get() not in self.values and self.values:
            self.variable.set(self.values[0])

    def open(self):
        if not self.enabled:
            return "break"
        if self.popup is not None:
            self.close()
            return "break"
        window = self.popup = tk.Toplevel(self)
        window.withdraw()
        window.overrideredirect(True)
        window.transient(self.winfo_toplevel())
        box = ttk.Frame(window, style="Selector.TFrame", padding=5)
        box.pack(fill="both", expand=True)
        selected = tk.Listbox(box, height=min(9, len(self.values)), width=1, activestyle="none",
            font="TkDefaultFont", background="#ffffff", foreground="#243047", borderwidth=0,
            highlightthickness=0, selectbackground="#e8efff", selectforeground="#244dbb", exportselection=False)
        for value in self.values:
            selected.insert("end", "  " + value)
        selected.pack(side="left", fill="both", expand=True)
        if len(self.values) > 9:
            scroll = ttk.Scrollbar(box, orient="vertical", command=selected.yview)
            scroll.pack(side="right", fill="y")
            selected.configure(yscrollcommand=scroll.set)
        index = self.values.index(self.variable.get()) if self.variable.get() in self.values else 0
        selected.selection_set(index)
        selected.activate(index)
        selected.see(index)
        def choose(_=None):
            choices = selected.curselection()
            if choices:
                self.variable.set(self.values[choices[0]])
            self.close()
            self.button.focus_set()
            return "break"
        selected.bind("<ButtonRelease-1>", choose)
        selected.bind("<Return>", choose)
        window.bind("<Escape>", lambda _: self.close())
        window.bind("<FocusOut>", lambda _: window.after_idle(self.close_if_unfocused))
        window.bind("<Button-1>", lambda event: self.close() if event.widget is window else None)
        window.update_idletasks()
        scale = max(1, self.winfo_fpixels("1i") / 96)
        text_width = max((tkfont.nametofont("TkDefaultFont").measure(value) for value in self.values), default=0)
        width = min(self.winfo_screenwidth() - 16,
                    max(self.winfo_width(), round(190 * scale), text_width + round(46 * scale)))
        height = window.winfo_reqheight()
        x = min(self.winfo_rootx(), max(0, self.winfo_screenwidth() - width - 8))
        y = self.winfo_rooty() + self.winfo_height() + 4
        if y + height > self.winfo_screenheight() - 8:
            y = max(0, self.winfo_rooty() - height - 4)
        window.geometry(f"{width}x{height}{int(x):+d}{int(y):+d}")
        window.deiconify()
        window.grab_set()
        selected.focus_set()
        self.options = selected
        return "break"

    def close_if_unfocused(self):
        if self.popup is not None:
            focus = self.focus_get()
            if focus is None or focus.winfo_toplevel() is not self.popup:
                self.close()

    def close(self):
        if self.popup is not None:
            popup, self.popup = self.popup, None
            try:
                popup.grab_release()
                popup.destroy()
            except tk.TclError:
                pass


class ResultSplit(ttk.Frame):
    """A compact, accessible divider between results and match preview."""

    def __init__(self, parent, ratio=.55, on_ratio_change=None):
        super().__init__(parent, style="Card.TFrame")
        self.ratio = ratio
        self.on_ratio_change = on_ratio_change
        self.preview_visible = True
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=round(ratio * 100))
        self.rowconfigure(2, weight=round((1 - ratio) * 100))
        self.grip = tk.Canvas(self, height=16, background="#ffffff", borderwidth=0,
                              highlightthickness=0, cursor="sb_v_double_arrow")
        self.grip.grid(row=1, column=0, sticky="ew")
        self.grip.bind("<Configure>", self._draw)
        self.grip.bind("<ButtonPress-1>", self._drag)
        self.grip.bind("<B1-Motion>", self._drag)

    def add_top(self, widget):
        widget.grid(row=0, column=0, sticky="nsew")

    def add_bottom(self, widget):
        self.bottom = widget
        widget.grid(row=2, column=0, sticky="nsew")

    def set_preview_visible(self, visible):
        self.preview_visible = visible
        if visible:
            self.grip.grid()
            self.bottom.grid()
            self.set_ratio(self.ratio)
        else:
            self.grip.grid_remove()
            self.bottom.grid_remove()
            self.rowconfigure(0, weight=1)
            self.rowconfigure(2, weight=0)

    def set_ratio(self, ratio):
        self.ratio = max(.2, min(.8, ratio))
        if self.preview_visible:
            self.rowconfigure(0, weight=round(self.ratio * 100))
            self.rowconfigure(2, weight=round((1 - self.ratio) * 100))
        if self.on_ratio_change:
            self.on_ratio_change(self.ratio)

    def _drag(self, event):
        height = self.winfo_height() - self.grip.winfo_height()
        if height > 100:
            y = event.y_root - self.winfo_rooty()
            self.set_ratio(y / height)

    def _draw(self, event):
        canvas = self.grip
        canvas.delete("all")
        mid = event.height / 2
        canvas.create_line(0, mid, event.width, mid, fill="#e7edf6")
        left = event.width / 2 - 30
        canvas.create_rectangle(left, 2, left + 60, event.height - 2,
                                fill="#f5f8ff", outline="#cbd8ed")
        for offset in (-9, 0, 9):
            x = event.width / 2 + offset
            canvas.create_oval(x - 1.5, mid - 1.5, x + 1.5, mid + 1.5,
                               fill="#6382ba", outline="")


class Toggle(ttk.Button):
    def __init__(self, parent, text, variable, **kwargs):
        self.variable = variable
        self.caption = text
        self.label = tk.StringVar(master=parent)
        super().__init__(parent, textvariable=self.label, command=lambda: variable.set(not variable.get()), **kwargs)
        self.token = variable.trace_add("write", self.refresh)
        self.refresh()
        self.bind("<Destroy>", self.cleanup, add="+")

    def refresh(self, *_):
        self.label.set(("✓  " if self.variable.get() else "○  ") + self.caption)
        self.configure(style="Selected.TButton" if self.variable.get() else "Option.TButton")

    def cleanup(self, event):
        if event.widget is self:
            self.variable.trace_remove("write", self.token)


class ActionMenu:
    """Small themed action popover instead of the platform's unstyled Tk menu."""
    def __init__(self, root):
        self.root = root
        self.popup = None

    def show(self, x, y, groups):
        self.close()
        popup = self.popup = tk.Toplevel(self.root)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self.root)
        popup.configure(bg="#dbe3ef")
        frame = ttk.Frame(popup, style="Card.TFrame", padding=5)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        for index, group in enumerate(groups):
            if index:
                ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=4)
            for label, command, danger in group:
                def invoke(callback=command):
                    self.close()
                    callback()
                ttk.Button(frame, text=label, style="MenuDanger.TButton" if danger else "Menu.TButton",
                           command=invoke, width=25).pack(fill="x", pady=1)
        popup.update_idletasks()
        width, height = popup.winfo_reqwidth(), popup.winfo_reqheight()
        x = min(x, max(0, popup.winfo_screenwidth() - width - 8))
        y = min(y, max(0, popup.winfo_screenheight() - height - 8))
        popup.geometry(f"{width}x{height}{int(x):+d}{int(y):+d}")
        popup.bind("<Escape>", lambda _: self.close())
        popup.bind("<FocusOut>", lambda _: popup.after_idle(self.close_if_unfocused))
        popup.deiconify()
        popup.focus_set()

    def close_if_unfocused(self):
        if self.popup is not None:
            focus = self.root.focus_get()
            if focus is None or focus.winfo_toplevel() is not self.popup:
                self.close()

    def close(self):
        if self.popup is not None:
            popup, self.popup = self.popup, None
            try:
                popup.destroy()
            except tk.TclError:
                pass


class MatchPreview(ttk.Frame):
    def __init__(self, parent, *, fullscreen=None):
        super().__init__(parent, style="Preview.TFrame", padding=(10, 7))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.excerpts, self.keywords, self.position = [], [], 0
        bar = ttk.Frame(self, style="Preview.TFrame")
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="匹配内容", style="PreviewTitle.TLabel").grid(row=0, column=0)
        self.count = tk.StringVar(value="选择结果查看内容")
        ttk.Label(bar, textvariable=self.count, style="PreviewHint.TLabel", width=1).grid(row=0, column=1, sticky="ew", padx=10)
        self.previous = ttk.Button(bar, text="上一处", style="Compact.TButton", command=lambda: self.move(-1))
        self.previous.grid(row=0, column=2, padx=(0, 4))
        self.next = ttk.Button(bar, text="下一处", style="Compact.TButton", command=lambda: self.move(1))
        self.next.grid(row=0, column=3)
        if fullscreen:
            ttk.Button(bar, text="展开", style="Compact.TButton", command=fullscreen).grid(row=0, column=4, padx=(4, 0))
        self.location = tk.StringVar(value="页码、段落、单元格或文本行列将在这里显示")
        self.location_label = ttk.Label(self, textvariable=self.location, style="PreviewHint.TLabel", width=1)
        self.location_label.grid(row=1, column=0, sticky="ew", pady=(4, 3))
        area = ttk.Frame(self, style="Preview.TFrame")
        area.grid(row=2, column=0, sticky="nsew")
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        self.text = tk.Text(area, height=3, width=1, wrap="word", font="TkDefaultFont", bg="#f7f9fd",
            fg="#243047", relief="flat", borderwidth=0, highlightthickness=0, padx=4, pady=4,
            selectbackground="#c9d9ff", cursor="xterm", spacing1=3, spacing3=3)
        self.text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.tag_configure("match", background="#ffe39b", foreground="#713e00")
        self.text.tag_configure("current", background="#ffbd54", foreground="#5b3400")
        self.compact = False
        self.bind("<Configure>", self.adapt)
        self.set_hit(None)

    def adapt(self, event):
        compact = event.height < round(110 * max(1, self.winfo_fpixels("1i") / 96))
        if compact != self.compact:
            self.compact = compact
            if compact:
                self.location_label.grid_remove()
            else:
                self.location_label.grid()
            if self.excerpts:
                self.render()

    def set_hit(self, hit, selected=1):
        self.position = 0
        self.excerpts = list(getattr(hit, "matches", [])) if hit else []
        if hit and not self.excerpts and hit.snippet:
            self.excerpts = [{"position": hit.location, "text": hit.snippet}]
        self.keywords = (getattr(hit, "matched_keywords", []) or hit.keywords.split(" | ")) if hit else []
        self.total = getattr(hit, "match_count", 0) or len(self.excerpts)
        self.warning = getattr(hit, "content_error", "") if hit else ""
        self.selection_note = f" · 已选 {selected} 项，预览首项" if selected > 1 else ""
        if not self.excerpts:
            self.keywords = []
            message = "选择一条内容命中结果，查看高亮片段与具体位置。" if hit is None else (
                "此项按名称命中，没有内容片段。" if not self.warning else "此项内容未完整读取：\n" + self.warning)
            self.location.set(hit.path if hit else "页码、段落、单元格或文本行列将在这里显示")
            self.count.set(("文件名命中" if hit else "选择结果查看内容") + self.selection_note)
            self.render_text(message)
        else:
            self.render()
        self.update_buttons()

    def render_text(self, content, active_start=None, active_end=None):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.tag_remove("match", "1.0", "end")
        self.text.tag_remove("current", "1.0", "end")
        for start, end in match_ranges(content, self.keywords):
            self.text.tag_add("match", f"1.0+{start}c", f"1.0+{end}c")
        if isinstance(active_start, int) and isinstance(active_end, int) and 0 <= active_start < active_end <= len(content):
            self.text.tag_add("current", f"1.0+{active_start}c", f"1.0+{active_end}c")
            self.text.tag_raise("current")
        self.text.configure(state="disabled")
        self.text.yview_moveto(0)
        ranges = self.text.tag_ranges("current") or self.text.tag_ranges("match")
        if ranges:
            self.text.see(ranges[0])

    def render(self):
        entry = self.excerpts[self.position]
        note = f" · 展示前 {len(self.excerpts)} 处" if self.total > len(self.excerpts) else ""
        if self.warning:
            note += " · 内容未完整读取"
        self.location.set(entry.get("position") or entry.get("location", ""))
        self.count.set(f"{self.position + 1} / {len(self.excerpts)} · " + (
            ("未完整读取 · " if self.warning else "") + f"共 {self.total} 处 · " + self.location.get()
            if self.compact else f"共 {self.total} 处{note}{self.selection_note}"))
        self.render_text(entry.get("text", ""), entry.get("highlight_start"), entry.get("highlight_end"))
        self.update_buttons()

    def move(self, delta):
        if self.excerpts:
            self.position = max(0, min(len(self.excerpts) - 1, self.position + delta))
            self.render()

    def update_buttons(self):
        self.previous.state(["!disabled"] if self.position > 0 else ["disabled"])
        self.next.state(["!disabled"] if self.position + 1 < len(self.excerpts) else ["disabled"])
