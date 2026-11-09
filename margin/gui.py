# -*- coding: utf-8 -*-
"""Margin desktop editor: text, installed fonts, and image art."""
from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import CancelledError, ThreadPoolExecutor
import tkinter as tk
from tkinter import ttk, filedialog

from PIL import Image, ImageTk, ImageOps

from . import core, fonts, svg
from .art import ImageOptions

BG, CARD, FIELD = "#09090B", "#18181B", "#111113"
STROKE, TEXT, SUB = "#27272A", "#FAFAFA", "#A1A1AA"
ACCENT, OK, WARN = "#6366F1", "#4ADE80", "#FCA5A5"


def resource_path(*parts):
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(__file__))), *parts)


class NumberControl(ttk.Frame):
    """A precise text field and a stepped slider sharing one value."""

    def __init__(self, parent, label, variable, low, high, step, *, enabled=None):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.variable, self.low, self.high, self.step = variable, low, high, step
        self._syncing = False
        self._position = tk.DoubleVar(value=low)
        ttk.Label(self, text=label, style="Sub.TLabel").grid(row=0, column=0, sticky="w")
        self.entry = ttk.Entry(self, textvariable=variable, width=8, justify="right")
        self.entry.grid(row=0, column=1, sticky="e", padx=(10, 0))
        self.slider = ttk.Scale(self, from_=low, to=high, variable=self._position,
                                 command=self._from_slider)
        self.slider.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        for key, direction in (("Left", -1), ("Down", -1), ("Right", 1), ("Up", 1)):
            self.slider.bind(f"<{key}>", lambda event, d=direction: self._keyboard_step(d))
        self._trace = variable.trace_add("write", self._from_entry)
        self._enabled = enabled
        self._enabled_trace = enabled.trace_add("write", self._update_enabled) if enabled is not None else None
        self._from_entry()
        self._update_enabled()
        self.bind("<Destroy>", self._cleanup)

    def _from_slider(self, value):
        if self._syncing:
            return
        number = round(float(value) / self.step) * self.step
        number = max(self.low, min(self.high, number))
        self.variable.set(int(round(number)) if self.step >= 1 else round(number, 2))

    def _from_entry(self, *args):
        try:
            value = float(self.variable.get())
        except (tk.TclError, ValueError):
            return  # Allow partially typed numbers; generation validates the complete value.
        self._syncing = True
        try:
            self._position.set(max(self.low, min(self.high, value)))
        finally:
            self._syncing = False

    def _keyboard_step(self, direction):
        if str(self.slider.cget("state")) != "disabled":
            self._from_slider(str(self._position.get() + self.step * direction))
        return "break"

    def _update_enabled(self, *args):
        state = "normal" if self._enabled is None or self._enabled.get() else "disabled"
        self.entry.configure(state=state)
        self.slider.configure(state=state)

    def _cleanup(self, event):
        if event.widget is self:
            self.variable.trace_remove("write", self._trace)
            if self._enabled_trace:
                self._enabled.trace_remove("write", self._enabled_trace)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Margin")
        self.configure(bg=BG)
        self.geometry("1100x820")
        self.minsize(960, 700)
        self._set_icon()
        self._dark_titlebar()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._cancel = threading.Event()
        self._revision = 0
        self._job = None
        self._timer = None
        self._stats = self._img = None
        self._photos = {}
        self._font_specs = []
        self._closed = False
        self.text = tk.StringVar(value="www.hurkin.top")
        self.art = tk.StringVar(value="Hurkin")
        self.source = tk.StringVar(value="文字")
        self.family = tk.StringVar()
        self.face = tk.StringVar()
        self.image_path = tk.StringVar()
        self.image_mode = tk.StringVar(value="自动")
        self.threshold = tk.IntVar(value=128)
        self.manual_threshold = tk.BooleanVar(value=False)
        self.contrast = tk.DoubleVar(value=1.0)
        self.invert = tk.BooleanVar(value=False)
        self.crop = tk.BooleanVar(value=True)
        self.white_threshold = tk.IntVar(value=245)
        self.ratio = tk.DoubleVar(value=0.62)
        self.version = tk.StringVar(value="自动")
        self.ec = tk.StringVar(value="H")
        self.mask = tk.StringVar(value="自动")
        self.budget = tk.IntVar(value=10)
        self.dot = tk.DoubleVar(value=0.66)
        self.strategy = tk.StringVar(value="扫码优先")
        self.advanced = tk.BooleanVar(value=False)
        self._styles()
        self._build()
        for var in (self.text, self.art, self.source, self.family, self.face, self.image_path,
                    self.image_mode, self.threshold, self.manual_threshold, self.contrast, self.invert, self.crop,
                    self.white_threshold, self.ratio, self.version, self.ec, self.mask,
                    self.budget, self.dot, self.strategy):
            var.trace_add("write", self.schedule)
        self._font_job = self._executor.submit(fonts.installed_fonts)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._poll_timer = self.after(50, self._poll)
        self.bind("<Control-Return>", lambda e: self.on_generate())

    def _set_icon(self):
        ico = resource_path("assets", "margin.ico")
        try:
            if os.path.exists(ico):
                self.iconbitmap(ico)
        except Exception:
            pass
        try:
            png = Image.open(resource_path("assets", "margin.png")).convert("RGBA")
            self._icons = [ImageTk.PhotoImage(png.resize((s, s), Image.Resampling.LANCZOS))
                           for s in (256, 128, 64, 48, 32, 16)]
            self.iconphoto(True, *self._icons)
        except Exception:
            pass

    def _dark_titlebar(self):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        self.after(120, self._apply_dark_titlebar)

    def _apply_dark_titlebar(self):
        try:
            import ctypes
            self.update_idletasks()
            u32 = ctypes.windll.user32
            hwnd = u32.GetAncestor(self.winfo_id(), 2)  # GA_ROOT
            if not hwnd:
                hwnd = u32.GetParent(self.winfo_id())
            if not hwnd:
                hwnd = self.winfo_id()
            v = ctypes.c_int(1)
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
            # 让 Windows 重绘标题栏
            u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
        except Exception:
            pass
    def _styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=CARD)
        style.configure("Root.TFrame", background=BG)
        style.configure("TLabel", background=CARD, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Sub.TLabel", foreground=SUB, font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background=BG, font=("Segoe UI", 22, "bold"))
        style.configure("TButton", background=STROKE, foreground=TEXT, padding=(12, 8),
                        font=("Microsoft YaHei UI", 10), borderwidth=0)
        style.map("TButton", background=[("active", "#3F3F46"), ("disabled", FIELD)],
                  foreground=[("disabled", "#71717A")])
        style.configure("Primary.TButton", background=ACCENT)
        style.map("Primary.TButton", background=[("active", "#4F46E5")])
        style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT,
                        padding=7, bordercolor=STROKE)
        style.configure("TCombobox", fieldbackground=FIELD, background=STROKE, foreground=TEXT,
                        arrowcolor=SUB, padding=6, bordercolor=STROKE)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)])
        style.configure("TCheckbutton", background=CARD, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("TRadiobutton", background=CARD, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        for name in ("TCheckbutton", "TRadiobutton"):
            style.map(name, background=[("active", CARD)])
        style.configure("TSpinbox", fieldbackground=FIELD, foreground=TEXT, padding=6,
                        arrowcolor=TEXT, background=STROKE)
        style.configure("Vertical.TScrollbar", background=STROKE, troughcolor=CARD, borderwidth=0)
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=FIELD, borderwidth=0)
        style.configure("Horizontal.TScale", background=CARD, troughcolor=FIELD)
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))

    def _label(self, parent, text, row, col=0):
        ttk.Label(parent, text=text, style="Sub.TLabel").grid(row=row, column=col, sticky="w", pady=(10, 4))

    def _entry(self, parent, var, row):
        widget = ttk.Entry(parent, textvariable=var)
        widget.grid(row=row, column=0, sticky="ew")
        return widget

    def _combo(self, parent, var, values, row, editable=False):
        widget = ttk.Combobox(parent, textvariable=var, values=values,
                              state="normal" if editable else "readonly")
        widget.grid(row=row, column=0, sticky="ew")
        return widget

    def _number(self, parent, label, var, low, high, step, row, **options):
        control = NumberControl(parent, label, var, low, high, step, **options)
        control.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        return control

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        header = ttk.Frame(self, style="Root.TFrame", padding=(24, 14))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Margin", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="用纠错余量，让图案融入二维码", background=BG,
                  foreground=SUB).pack(side="left", padx=20)
        body = ttk.Frame(self, style="Root.TFrame", padding=(24, 0, 24, 10))
        body.grid(row=1, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=2, minsize=350)
        body.columnconfigure(1, weight=3, minsize=470)
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        canvas = tk.Canvas(left, bg=CARD, highlightthickness=0, width=360)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)
        settings = ttk.Frame(canvas, padding=18)
        window = canvas.create_window(0, 0, window=settings, anchor="nw")
        settings.columnconfigure(0, weight=1)
        settings.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        def wheel(event):
            if str(event.widget).startswith(str(left)):
                canvas.yview_scroll(-int(event.delta / 120), "units")
        self.bind_all("<MouseWheel>", wheel, add="+")
        self._label(settings, "扫码后显示的内容", 0)
        self._entry(settings, self.text, 1)
        row = ttk.Frame(settings)
        row.grid(row=2, column=0, sticky="w", pady=(18, 6))
        ttk.Label(row, text="中心图案", style="Sub.TLabel").pack(side="left", padx=(0, 18))
        for value in ("文字", "图片"):
            ttk.Radiobutton(row, text=value, variable=self.source, value=value,
                            command=self._show_source).pack(side="left", padx=(0, 18))
        self.text_panel = ttk.Frame(settings)
        self.text_panel.grid(row=3, column=0, sticky="ew")
        self.text_panel.columnconfigure(0, weight=1)
        self._entry(self.text_panel, self.art, 0)
        self._label(self.text_panel, "字体 · 可输入名称搜索", 1)
        self.font_combo = self._combo(self.text_panel, self.family, [], 2, editable=True)
        self.font_combo.bind("<KeyRelease>", self._filter_fonts)
        self.font_combo.bind("<<ComboboxSelected>>", self._family_selected)
        self._label(self.text_panel, "字形", 3)
        self.face_combo = self._combo(self.text_panel, self.face, [], 4)
        ttk.Button(self.text_panel, text="选择字体文件…", command=self.choose_font).grid(
            row=5, column=0, sticky="ew", pady=(10, 0))
        self.font_hint = ttk.Label(self.text_panel, text="正在读取系统字体…", style="Sub.TLabel", wraplength=300)
        self.font_hint.grid(row=6, column=0, sticky="w", pady=(6, 0))
        self.image_panel = ttk.Frame(settings)
        self.image_panel.columnconfigure(0, weight=1)
        ttk.Button(self.image_panel, text="选择图片…", command=self.choose_image).grid(row=0, column=0, sticky="ew")
        ttk.Label(self.image_panel, textvariable=self.image_path, style="Sub.TLabel", wraplength=290).grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self._label(self.image_panel, "黑白处理", 2)
        self._combo(self.image_panel, self.image_mode, ["自动", "Logo", "照片"], 3)
        ttk.Checkbutton(self.image_panel, text="裁掉外围白边 / 透明边", variable=self.crop).grid(
            row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Checkbutton(self.image_panel, text="反色", variable=self.invert).grid(row=5, column=0, sticky="w")
        ttk.Checkbutton(self.image_panel, text="图片高级设置", variable=self.advanced,
                         command=self._show_advanced).grid(row=6, column=0, sticky="w", pady=(8, 0))
        self.advanced_panel = ttk.Frame(self.image_panel)
        self.advanced_panel.columnconfigure(0, weight=1)
        ttk.Checkbutton(self.advanced_panel, text="手动黑白阈值（关闭时自动）",
                         variable=self.manual_threshold).grid(row=0, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self._number(self.advanced_panel, "黑白阈值", self.threshold, 0, 255, 1, 1,
                     enabled=self.manual_threshold)
        self._number(self.advanced_panel, "对比度", self.contrast, 0.2, 4, 0.1, 2)
        self._number(self.advanced_panel, "白边阈值", self.white_threshold, 1, 255, 1, 3)
        ttk.Button(self.advanced_panel, text="重置图片设置", command=self.reset_image).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._label(settings, "生成策略", 4)
        self._combo(settings, self.strategy, ["扫码优先", "还原优先"], 5)
        ttk.Label(settings, text="扫码优先保留更多纠错余量；还原优先会比较更高版本。",
                  style="Sub.TLabel", wraplength=300).grid(row=6, column=0, sticky="w", pady=(6, 4))
        parameters = ttk.Frame(settings)
        parameters.grid(row=7, column=0, sticky="ew")
        parameters.columnconfigure(0, weight=1)
        self._label(parameters, "二维码版本", 0)
        ttk.Combobox(parameters, textvariable=self.version, values=["自动"] + list(range(1, 41)),
                      state="readonly", width=10).grid(row=0, column=1, sticky="e", pady=(10, 4))
        self._label(parameters, "纠错等级", 1)
        ttk.Combobox(parameters, textvariable=self.ec, values=["H", "Q", "M", "L"],
                      state="readonly", width=10).grid(row=1, column=1, sticky="e", pady=(10, 4))
        self._number(parameters, "每块预算上限", self.budget, 0, 15, 1, 2)
        self._number(parameters, "图案宽度占比", self.ratio, 0.1, 0.8, 0.02, 3)
        self._number(parameters, "点径", self.dot, 0.4, 1.0, 0.02, 4)
        self._label(parameters, "掩码", 5)
        ttk.Combobox(parameters, textvariable=self.mask, values=["自动"] + list(range(8)),
                      state="readonly", width=10).grid(row=5, column=1, sticky="e", pady=(10, 4))
        preview_panel = ttk.Frame(body, padding=20)
        preview_panel.grid(row=0, column=1, sticky="nsew")
        preview_panel.columnconfigure(0, weight=1)
        preview_panel.rowconfigure(2, weight=1)
        self.previews = ttk.Frame(preview_panel)
        for i, (name, label) in enumerate((("original", "原图"), ("target", "黑白处理 · 保留原图细节"))):
            self.previews.columnconfigure(i, weight=1)
            panel = ttk.Frame(self.previews)
            panel.grid(row=0, column=i, sticky="nsew", padx=6)
            ttk.Label(panel, text=label, style="Sub.TLabel").pack(anchor="w", pady=(0, 8))
            widget = tk.Label(panel, bg=FIELD, fg=SUB, text="选择图片后显示", width=20, height=6)
            widget.pack(fill="both", expand=True)
            setattr(self, name + "_preview", widget)
        ttk.Label(preview_panel, text="二维码预览", style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 8))
        self.preview = tk.Label(preview_panel, bg=CARD, fg=SUB, text="正在准备…")
        self.preview.grid(row=2, column=0, sticky="nsew")
        self.preview.bind("<Configure>", self._resize_preview)
        self.details = ttk.Label(preview_panel, text="", style="Sub.TLabel", wraplength=430, justify="left")
        self.details.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        footer = ttk.Frame(self, style="Root.TFrame", padding=(24, 0, 24, 16))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        actions = ttk.Frame(footer, style="Root.TFrame")
        actions.grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="生成", style="Primary.TButton", command=self.on_generate).pack(side="left", padx=(0, 8))
        self.save_png = ttk.Button(actions, text="保存 PNG", command=self.on_save, state="disabled")
        self.save_png.pack(side="left", padx=(0, 8))
        self.save_svg = ttk.Button(actions, text="保存 SVG", command=self.on_save_svg, state="disabled")
        self.save_svg.pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="复制参数", command=self.copy_stats).pack(side="left")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=110)
        self.progress.grid(row=0, column=1, sticky="e")
        self.status = ttk.Label(footer, text="读取字体中…", background=BG, foreground=SUB, wraplength=960)
        self.status.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))

    def _show_source(self):
        image = self.source.get() == "图片"
        if image:
            self.text_panel.grid_remove()
            self.image_panel.grid(row=3, column=0, sticky="ew")
            self.previews.grid(row=0, column=0, sticky="ew")
        else:
            self.image_panel.grid_remove()
            self.text_panel.grid()
            self.previews.grid_remove()

    def _show_advanced(self):
        if self.advanced.get():
            self.advanced_panel.grid(row=7, column=0, sticky="ew")
        else:
            self.advanced_panel.grid_remove()

    def _filter_fonts(self, event=None):
        query = self.family.get().casefold()
        values = sorted({s.family for s in self._font_specs if query in s.family.casefold()}, key=str.casefold)
        self.font_combo.configure(values=values)
        self._family_selected()

    def _family_selected(self, event=None):
        faces = list(dict.fromkeys(s.style for s in self._font_specs if s.family == self.family.get()))
        self.face_combo.configure(values=faces)
        if self.face.get() not in faces:
            self.face.set(faces[0] if faces else "")

    def choose_font(self):
        path = filedialog.askopenfilename(title="选择字体文件", filetypes=[("字体文件", "*.ttf *.otf *.ttc *.otc")])
        if not path:
            return
        try:
            imported = fonts.file_fonts(path)
        except ValueError as exc:
            self._set_hint(str(exc), WARN)
            return
        self._font_specs = imported + self._font_specs
        self.family.set(imported[0].family)
        self._filter_fonts()
        self.face.set(imported[0].style)
        self.font_hint.configure(text=f"已载入 {os.path.basename(path)} · {len(imported)} 个字形")

    def choose_image(self):
        path = filedialog.askopenfilename(title="选择中心图案", filetypes=[
            ("图片文件", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"), ("所有文件", "*.*")])
        if path:
            self.image_path.set(path)

    def reset_image(self):
        for var, value in ((self.image_mode, "自动"), (self.threshold, 128), (self.manual_threshold, False),
                           (self.contrast, 1.0), (self.invert, False), (self.crop, True),
                           (self.white_threshold, 245), (self.ratio, 0.62)):
            var.set(value)

    def _set_hint(self, text, color=SUB):
        self.status.configure(text=text, foreground=color)

    def schedule(self, *args):
        self._revision += 1
        self._cancel.set()
        self._stats = self._img = None
        self.save_png.configure(state="disabled")
        self.save_svg.configure(state="disabled")
        self.preview.configure(image="", text="等待生成…")
        self.details.configure(text="")
        self._set_hint("参数已更新，等待生成…")
        self._show_source()
        if self._timer:
            self.after_cancel(self._timer)
        self._timer = self.after(350, self.on_generate)

    def _arguments(self):
        image = self.source.get() == "图片"
        if image and not self.image_path.get():
            raise ValueError("请先选择一张中心图案图片")
        spec = None
        if not image and self.art.get().strip():
            spec = next((s for s in self._font_specs if s.family == self.family.get() and s.style == self.face.get()), None)
            if spec is None:
                raise ValueError("请选择列表中的字体及字形，或导入字体文件")
        try:
            def integer(variable):
                raw = str(self.getvar(variable._name))
                return int(raw)

            options = ImageOptions()
            budget = integer(self.budget)
            if not 0 <= budget <= 15:
                raise ValueError("预算需在 0–15 之间")
            if image:
                options = ImageOptions(
                    {"自动": "auto", "Logo": "logo", "照片": "photo"}[self.image_mode.get()],
                    integer(self.threshold) if self.manual_threshold.get() else None,
                    self.contrast.get(), self.invert.get(), self.crop.get(), integer(self.white_threshold))
            return dict(text=self.text.get().strip(), art="" if image else self.art.get().strip(),
                        image_path=self.image_path.get() if image else None, font_spec=spec,
                        version=None if self.version.get() == "自动" else int(self.version.get()),
                        ec=self.ec.get(), mask=None if self.mask.get() == "自动" else int(self.mask.get()),
                        budget=budget, dot=self.dot.get(), art_ratio=self.ratio.get(),
                        strategy="scan" if self.strategy.get() == "扫码优先" else "fidelity",
                        image_options=options)
        except (ValueError, tk.TclError) as exc:
            raise ValueError("请检查数值输入，预算与阈值需为整数，其余数值需在滑块范围内") from exc

    def on_generate(self):
        if self._timer:
            self.after_cancel(self._timer)
            self._timer = None
        if self._font_job is not None:
            return
        self._cancel.set()
        self._cancel = threading.Event()
        self._revision += 1
        self._stats = self._img = None
        self.save_png.configure(state="disabled")
        self.save_svg.configure(state="disabled")
        self.details.configure(text="")
        if self._job:
            self._job[1].cancel()
        try:
            args = self._arguments()
        except ValueError as exc:
            self._set_hint(str(exc), WARN)
            self.progress.stop()
            self.preview.configure(image="", text="检查设置后重新生成")
            return
        self._job = (self._revision, self._executor.submit(core.generate, **args, cancel=self._cancel))
        self.progress.start(12)
        self.preview.configure(image="", text="正在生成并检查扫码…")
        self._set_hint("正在比较图案和掩码，界面可继续操作…")

    def _poll(self):
        self._poll_timer = None
        if self._closed:
            return
        if self._font_job is not None and self._font_job.done():
            try:
                self._font_specs.extend(self._font_job.result())
                if not self.family.get():
                    default = fonts.default_font()
                    self._font_specs.append(default)
                    self.family.set(default.family)
                    self._filter_fonts()
                    self.face.set(default.style)
                self.font_hint.configure(text=f"{len({f.family for f in self._font_specs})} 种字体 · 支持系统及当前用户字体")
            except Exception as exc:
                self._set_hint(f"读取字体失败：{exc}；可选择字体文件", WARN)
            self._font_job = None
            self.schedule()
        if self._job and self._job[1].done():
            revision, job = self._job
            self._job = None
            if revision == self._revision:
                self.progress.stop()
                try:
                    self._img, self._stats = job.result()
                    self._show_result()
                except CancelledError:
                    pass
                except Exception as exc:
                    self._stats = self._img = None
                    self.preview.configure(image="", text="生成失败")
                    self._set_hint(str(exc), WARN)
        self._poll_timer = self.after(60, self._poll)

    def _thumbnail(self, name, image, size, nearest=False):
        thumb = ImageOps.contain(image.convert("RGB"), size,
                                 Image.Resampling.NEAREST if nearest else Image.Resampling.LANCZOS)
        plate = Image.new("RGB", size, "white")
        plate.paste(thumb, ((size[0] - thumb.width) // 2, (size[1] - thumb.height) // 2))
        photo = ImageTk.PhotoImage(plate)
        self._photos[name] = photo
        widget = self.preview if name == "qr" else getattr(self, name + "_preview")
        widget.configure(image=photo, text="", width=0, height=0)

    def _resize_preview(self, event=None):
        if self._img is not None:
            side = max(100, min(480, self.preview.winfo_width() - 8, self.preview.winfo_height() - 8))
            self._thumbnail("qr", self._img, (side, side), True)

    def _show_result(self):
        stats = self._stats
        self._resize_preview()
        if stats.original is not None:
            self._thumbnail("original", stats.original, (180, 120))
            self._thumbnail("target", stats.processed, (180, 120))
        self.details.configure(text=stats.summary())
        can_save = stats.ok or not stats.verified
        for button in (self.save_png, self.save_svg):
            button.configure(state="normal" if can_save else "disabled")
        if stats.ok:
            self._set_hint(f"自检可扫描 · 有效预算 {stats.budget}/{stats.capacity} · 图案一致度 {stats.fidelity:.0%} · {stats.text}", OK)
        elif not stats.verified:
            self._set_hint("已生成，但未安装 zxing-cpp，扫码能力未验证", WARN)
        else:
            self._set_hint("扫码自检失败，无法保存。请增大点径、提高版本或缩小图案。", WARN)

    def _save(self, extension):
        if self._stats is None or (self._stats.verified and not self._stats.ok):
            return
        path = filedialog.asksaveasfilename(defaultextension=extension,
            filetypes=[(extension[1:].upper(), "*" + extension)], initialfile="margin" + extension)
        if path:
            try:
                if extension == ".svg":
                    svg.write_svg(path, "", result=self._stats)
                else:
                    self._img.save(path)
                self._set_hint(f"已保存 {os.path.basename(path)}", OK)
            except OSError as exc:
                self._set_hint(f"保存失败：{exc}", WARN)

    def on_save(self):
        self._save(".png")

    def on_save_svg(self):
        self._save(".svg")

    def copy_stats(self):
        try:
            values = self._arguments()
        except ValueError as exc:
            self._set_hint(str(exc), WARN)
            return
        lines = [f"{key}: {value}" for key, value in values.items() if value is not None]
        if self._stats:
            lines.append("实际生成结果：\n" + self._stats.summary())
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self._set_hint("参数已复制到剪贴板")

    def close(self):
        self._closed = True
        self._cancel.set()
        if self._timer:
            self.after_cancel(self._timer)
        if self._poll_timer:
            self.after_cancel(self._poll_timer)
        self.progress.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def main():
    App().mainloop()
