# -*- coding: utf-8 -*-
"""Margin GUI —— 深色卡片风格，生成中心带像素图案的二维码。"""
from __future__ import annotations

import os
import sys
from tkinter import StringVar, DoubleVar, IntVar, filedialog
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageTk

from . import core

# ---- 设计令牌 ----
BG = "#09090B"
CARD = "#18181B"
FIELD = "#111113"
STROKE = "#27272A"
STROKE_STRONG = "#3F3F46"
TEXT = "#FAFAFA"
SUB = "#A1A1AA"
ACCENT = "#6366F1"
ACCENT_HOVER = "#4F46E5"
OK = "#4ADE80"
WARN = "#FCA5A5"

W, H = 960, 752
PREVIEW = 380

FONT_CHOICES = [
    ("微软雅黑 粗体", r"C:\Windows\Fonts\msyhbd.ttc"),
    ("微软雅黑", r"C:\Windows\Fonts\msyh.ttc"),
    ("黑体", r"C:\Windows\Fonts\simhei.ttf"),
    ("Arial 粗体", r"C:\Windows\Fonts\arialbd.ttf"),
    ("Consolas 粗体", r"C:\Windows\Fonts\consolab.ttf"),
    ("Segoe UI 半粗", r"C:\Windows\Fonts\seguisb.ttf"),
]


def resource_path(*parts) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def rounded_polygon(cv: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def rounded_image(img: Image.Image, radius: int) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


class Pill(tk.Canvas):
    """自绘圆角按钮。"""

    def __init__(self, master, text, command, kind="ghost", width=None,
                 height=38, font=("Microsoft YaHei UI", 10)):
        self.kind = kind
        self.font = font
        self.command = command
        self._text = text
        tmp = tkfont.Font(font=self.font)
        w = width or (tmp.measure(text) + 42)
        super().__init__(master, width=w, height=height, bg=BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._bw, self._bh = w, height
        self._paint()

    def _colors(self, state):
        if self.kind == "primary":
            return (ACCENT_HOVER if state == "hover" else ACCENT,
                    ACCENT_HOVER if state == "hover" else ACCENT, "#FFFFFF")
        if state == "hover":
            return (STROKE, STROKE_STRONG, TEXT)
        return (CARD, STROKE, TEXT)

    def _paint(self, state="idle"):
        self.delete("all")
        fill, edge, fg = self._colors(state)
        rounded_polygon(self, 1, 1, self._bw - 1, self._bh - 1, 9,
                        fill=fill, outline=edge, width=1)
        self.create_text(self._bw / 2, self._bh / 2, text=self._text,
                         fill=fg, font=self.font)

    def _bind_events(self):
        self.bind("<Enter>", lambda e: self._paint("hover"))
        self.bind("<Leave>", lambda e: self._paint("idle"))
        self.bind("<Button-1>", lambda e: self._paint("press"))
        self.bind("<ButtonRelease-1>", self._click)

    def _click(self, event):
        self._paint("hover")
        if self.command:
            self.command()



class MiniSlider(tk.Canvas):
    """自绘滑块：圆角轨道 + 圆点手柄。"""

    PAD = 8
    CY = 12

    def __init__(self, master, width=110, var=None, lo=0.0, hi=1.0, jump=1.0,
                 command=None, bg=CARD):
        super().__init__(master, width=width, height=24, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._bw = width
        self.var = var
        self.lo, self.hi, self.jump = lo, hi, jump
        self.command = command
        self._dragging = False
        self._draw()
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_move)
        self.bind("<ButtonRelease-1>", self._on_release)
        if var is not None:
            var.trace_add("write", lambda *a: self._draw())

    def _ratio(self):
        span = (self.hi - self.lo) or 1
        try:
            v = float(self.var.get())
        except Exception:
            v = self.lo
        return max(0.0, min(1.0, (v - self.lo) / span))

    def _draw(self):
        self.delete("all")
        x0 = self.PAD
        x1 = self._bw - self.PAD
        self.create_line(x0, self.CY, x1, self.CY, width=4,
                         fill=STROKE, capstyle="round")
        cx = x0 + (x1 - x0) * self._ratio()
        if cx > x0:
            self.create_line(x0, self.CY, cx, self.CY, width=4,
                             fill=ACCENT, capstyle="round")
        self.create_oval(cx - 7, self.CY - 7, cx + 7, self.CY + 7,
                         fill="#FAFAFA", outline=ACCENT, width=2)

    def _value_at(self, x):
        x0 = self.PAD
        x1 = self._bw - self.PAD
        r = max(0.0, min(1.0, (x - x0) / max(1, x1 - x0)))
        v = self.lo + r * (self.hi - self.lo)
        if self.jump:
            v = round(v / self.jump) * self.jump
        v = max(self.lo, min(self.hi, v))
        if isinstance(self.var, IntVar) or (self.jump >= 1 and float(v).is_integer()):
            return int(round(v))
        return round(v, 2)

    def _on_press(self, event):
        self._dragging = True
        self.var.set(self._value_at(event.x))
        self._draw()

    def _on_move(self, event):
        if not self._dragging:
            return
        self.var.set(self._value_at(event.x))
        self._draw()

    def _on_release(self, event):
        if not self._dragging:
            return
        self._dragging = False
        if self.command:
            self.command()


class App(tk.Tk):
    def __init__(self):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Hurkin.Margin.QR.1")
        except Exception:
            pass
        super().__init__()
        self.title("Margin")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.geometry(f"{W}x{H}+{max(0,(self.winfo_screenwidth()-W)//2)}+"
                      f"{max(0,(self.winfo_screenheight()-H)//2-20)}")
        self._set_icon()
        self._dark_titlebar()

        self.text = StringVar(value="www.hurkin.top")
        self.art = StringVar(value="Hurkin")
        self.version = StringVar(value="自动")
        self.ec = StringVar(value="H")
        self.budget = IntVar(value=10)
        self.dot = DoubleVar(value=0.66)
        self.font_choice = StringVar(value=FONT_CHOICES[0][0])

        self._img = None
        self._photo = None
        self._stats = None

        self._styles()
        self._build()
        self.after(150, self.on_generate)

    # ------------------------------------------------------------ 外观
    def _set_icon(self):
        ico = resource_path("assets", "margin.ico")
        try:
            if os.path.exists(ico):
                self.iconbitmap(ico)
        except Exception:
            pass
        try:
            png = Image.open(resource_path("assets", "margin.png")).convert("RGBA")
            self._icons = [ImageTk.PhotoImage(png.resize((s, s), Image.LANCZOS))
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
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Dark.TCombobox",
                     fieldbackground=FIELD, background=CARD, foreground=TEXT,
                     arrowcolor=SUB, bordercolor=STROKE, lightcolor=STROKE,
                     darkcolor=STROKE, selectbackground=FIELD,
                     selectforeground=TEXT, padding=4)
        st.map("Dark.TCombobox",
               fieldbackground=[("readonly", FIELD)],
               foreground=[("readonly", TEXT)],
               bordercolor=[("focus", ACCENT)])
        self.option_add("*TCombobox*Listbox.background", CARD)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        self.option_add("*TCombobox*Listbox.borderWidth", 0)

    # ------------------------------------------------------------ 布局
    def _label(self, x, y, text, color=SUB, size=9, bold=False, anchor="w"):
        return self.cv.create_text(x, y, text=text, fill=color, anchor=anchor,
                                   font=("Microsoft YaHei UI", size,
                                         "bold" if bold else "normal"))

    def _entry(self, x, y, w, var, font_size=10, h=32):
        holder = tk.Frame(self.cv, bg=STROKE)
        e = tk.Entry(holder, textvariable=var, bg=FIELD, fg=TEXT,
                     insertbackground=ACCENT, relief="flat", bd=0,
                     font=("Microsoft YaHei UI", font_size),
                     highlightthickness=0)
        e.pack(padx=1, pady=1, fill="both", expand=True)
        self.cv.create_window(x, y, window=holder, anchor="nw", width=w, height=h)
        return e

    def _combo(self, x, y, w, var, values, h=32):
        cb = ttk.Combobox(self.cv, textvariable=var, values=values,
                          state="readonly", style="Dark.TCombobox",
                          font=("Microsoft YaHei UI", 9))
        self.cv.create_window(x, y, window=cb, anchor="nw", width=w, height=h)
        return cb

    def _scale(self, x, y, w, var, lo, hi, jump=1):
        s = MiniSlider(self.cv, width=w, var=var, lo=lo, hi=hi, jump=jump,
                       command=self.on_generate)
        self.cv.create_window(x, y, window=s, anchor="w")
        return s

    def _build(self):
        cv = tk.Canvas(self, width=W, height=H, bg=BG, highlightthickness=0, bd=0)
        cv.place(x=0, y=0)
        self.cv = cv

        cv.create_text(24, 26, text="Margin", fill=TEXT, anchor="w",
                       font=("Segoe UI", 20, "bold"))

        # 卡片一：设置
        rounded_polygon(cv, 24, 60, W - 24, 224, 14, fill=CARD, outline=STROKE)
        self._label(44, 94, "扫码后显示的内容")
        self._entry(160, 78, 700, self.text)

        self._label(44, 138, "中心图案")
        e_art = self._entry(160, 122, 186, self.art)
        e_art.bind("<KeyRelease>", lambda e: self.on_generate())
        self._label(370, 138, "字体")
        cb_font = self._combo(412, 122, 190, self.font_choice,
                              [n for n, _ in FONT_CHOICES])
        cb_font.bind("<<ComboboxSelected>>", lambda e: self.on_generate())

        # 第三行：版本 / 纠错 / 每块预算 / 点径
        self._label(44, 194, "版本")
        cb_ver = self._combo(88, 178, 76, self.version,
                             ["自动"] + [str(v) for v in range(6, 26)])
        cb_ver.bind("<<ComboboxSelected>>", lambda e: self.on_generate())

        self._label(186, 194, "纠错")
        cb_ec = self._combo(226, 178, 62, self.ec, ["H", "Q", "M"])
        cb_ec.bind("<<ComboboxSelected>>", lambda e: self.on_generate())

        self._label(310, 194, "每块预算")
        sc1 = self._scale(388, 194, 108, self.budget, 2, 14, 1)
        self._budget_text = cv.create_text(
            508, 192, text=str(self.budget.get()), fill=TEXT, anchor="w",
            font=("Segoe UI", 10, "bold"))
        self.budget.trace_add(
            "write", lambda *a: cv.itemconfigure(self._budget_text,
                                                 text=str(self.budget.get())))
        sc1.bind("<ButtonRelease-1>", lambda e: self.on_generate())

        self._label(556, 194, "点径")
        sc2 = self._scale(594, 194, 108, self.dot, 0.40, 1.0, 0.02)
        self._dot_text = cv.create_text(
            712, 192, text=f"{self.dot.get():.2f}", fill=TEXT, anchor="w",
            font=("Segoe UI", 10, "bold"))
        self.dot.trace_add(
            "write", lambda *a: cv.itemconfigure(self._dot_text,
                                                 text=f"{self.dot.get():.2f}"))
        sc2.bind("<ButtonRelease-1>", lambda e: self.on_generate())

        # 卡片二：预览
        rounded_polygon(cv, 24, 236, W - 24, 648, 14, fill=CARD, outline=STROKE)
        self.preview = tk.Label(cv, bg=CARD, bd=0, highlightthickness=0)
        cv.create_window(W / 2, 442, window=self.preview, anchor="center")

        # 底部按钮
        self.btn_gen = Pill(cv, "生成", self.on_generate, kind="primary", height=40)
        cv.create_window(24, 668, window=self.btn_gen, anchor="nw")
        x = 24 + self.btn_gen._bw + 10
        b2 = Pill(cv, "保存 PNG", self.on_save, height=40)
        cv.create_window(x, 668, window=b2, anchor="nw")
        x += b2._bw + 10
        b3 = Pill(cv, "保存 SVG", self.on_save_svg, height=40)
        cv.create_window(x, 668, window=b3, anchor="nw")
        x += b3._bw + 10
        b4 = Pill(cv, "复制参数", self.copy_stats, height=40)
        cv.create_window(x, 668, window=b4, anchor="nw")
        for b in (self.btn_gen, b2, b3, b4):
            b._bind_events()

        self.hint_text = cv.create_text(26, 730, text="", fill=OK, anchor="w",
                                        font=("Microsoft YaHei UI", 9))

    # ------------------------------------------------------------ 行为
    def _font_path(self):
        for name, path in FONT_CHOICES:
            if name == self.font_choice.get():
                return path
        return None

    def _set_hint(self, msg, color=OK):
        self.cv.itemconfigure(self.hint_text, text=msg, fill=color)

    def on_generate(self):
        ver = None if self.version.get() == "自动" else int(self.version.get())
        raw = self.text.get().strip()
        try:
            img, stats = core.generate(
                self.text.get().strip(), self.art.get().strip(),
                version=ver, ec=self.ec.get(), budget=int(self.budget.get()),
                dot=float(self.dot.get()), scale=20, quiet=4,
                font_path=self._font_path())
        except Exception:
            self._img = None
            self.preview.configure(image="")
            self._photo = None
            self._set_hint("内容放不进这个版本，换个版本或缩短内容", WARN)
            return

        self._img = img
        self._stats = stats
        thumb = img.convert("RGBA").resize((PREVIEW, PREVIEW), Image.NEAREST)
        thumb = rounded_image(thumb, 18)
        plate = Image.new("RGBA", (PREVIEW + 8, PREVIEW + 8), (0, 0, 0, 0))
        plate.paste(thumb, (4, 4), thumb)
        self._photo = ImageTk.PhotoImage(plate)
        self.preview.configure(image=self._photo, width=PREVIEW + 8, height=PREVIEW + 8)

        fixed = stats.text if stats.text != raw else ""
        tail = f"（已自动补全为 {stats.text}）" if fixed else ""
        if not stats.decode:
            self._set_hint("已生成 · 未做扫码自检" + tail, SUB)
        elif stats.ok:
            self._set_hint("已生成 · 自检可扫描" + tail, OK)
        else:
            self._set_hint("自检未通过，已自动降低预算，可再调小预算", WARN)

    def on_save(self):
        if self._img is None:
            self._set_hint("先生成一张二维码", WARN)
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png", filetypes=[("PNG 图片", "*.png")],
            initialfile="margin.png")
        if path:
            self._img.save(path)
            self._set_hint(f"已保存 {os.path.basename(path)}", OK)

    def on_save_svg(self):
        if self._stats is None:
            self._set_hint("先生成一张二维码", WARN)
            return
        from . import svg
        path = filedialog.asksaveasfilename(
            defaultextension=".svg", filetypes=[("SVG 矢量图", "*.svg")],
            initialfile="margin.svg")
        if path:
            svg.write_svg(path, self.text.get().strip(), self.art.get().strip(),
                          None if self.version.get() == "自动" else int(self.version.get()),
                          self.ec.get(), int(self.budget.get()),
                          float(self.dot.get()), self._font_path())
            self._set_hint(f"已保存 {os.path.basename(path)}", OK)

    def copy_stats(self):
        self.clipboard_clear()
        self.clipboard_append(
            f"内容: {self.text.get()}\n图案: {self.art.get()}\n"
            f"版本: {self.version.get()} 纠错: {self.ec.get()} "
            f"预算: {self.budget.get()} 点径: {self.dot.get()}")
        self._set_hint("参数已复制到剪贴板", SUB)


def main():
    App().mainloop()
