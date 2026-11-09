"""Installed Windows fonts and portable font-file rendering."""
from __future__ import annotations

import ctypes as ct
import os
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTCollection, TTFont, TTLibError


@dataclass(frozen=True)
class FontSpec:
    family: str
    style: str = "Regular"
    weight: int = 400
    italic: bool = False
    path: str = ""
    index: int = 0
    charset: int = 1
    bitmap: bool = False

    @property
    def label(self):
        return f"{self.family} · {self.style}"


def file_fonts(path: str) -> list[FontSpec]:
    """Read every face, including collections; never substitute a broken font."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ValueError(f"字体文件不存在：{path}")
    collection = None
    try:
        if path.lower().endswith((".ttc", ".otc")):
            collection = TTCollection(path, lazy=True)
            faces = collection.fonts
        else:
            faces = [TTFont(path, lazy=True)]
        result = []
        try:
            for index, face in enumerate(faces):
                names = face["name"]
                family = names.getBestFamilyName() or os.path.basename(path)
                style = names.getBestSubFamilyName() or "Regular"
                result.append(FontSpec(family, style, path=path, index=index))
        finally:
            if collection:
                collection.close()
            else:
                faces[0].close()
        return result
    except Exception as exc:
        raise ValueError(f"无法读取字体文件：{os.path.basename(path)}（{exc}）") from exc


@lru_cache(maxsize=64)
def _coverage(path, index, modified):
    with TTFont(path, fontNumber=index, lazy=True) as face:
        return frozenset((face.getBestCmap() or {}).keys())


def _check_chars(text, available):
    missing = sorted({c for c in text if not c.isspace() and ord(c) not in available})
    if missing:
        raise ValueError("所选字体缺少字符：" + " ".join(missing[:12]) + "，请更换字体")


class LOGFONTW(ct.Structure):
    _fields_ = [(name, ct.c_int32) for name in
                ("height", "width", "escapement", "orientation", "weight")] + [
        (name, ct.c_ubyte) for name in
        ("italic", "underline", "strikeout", "charset", "outprecision",
         "clipprecision", "quality", "pitch")
    ] + [("face", ct.c_wchar * 32)]


class ENUMLOGFONTEXW(ct.Structure):
    _fields_ = [("font", LOGFONTW), ("full", ct.c_wchar * 64),
                ("style", ct.c_wchar * 32), ("script", ct.c_wchar * 32)]


@lru_cache(maxsize=1)
def _gdi():
    if os.name != "nt":
        raise ValueError("系统字体渲染需要 Windows；请改用字体文件")
    from ctypes import wintypes as w
    g = ct.WinDLL("gdi32", use_last_error=True)
    signatures = {
        "CreateCompatibleDC": ([w.HDC], w.HDC),
        "DeleteDC": ([w.HDC], w.BOOL),
        "CreateFontIndirectW": ([ct.POINTER(LOGFONTW)], w.HFONT),
        "SelectObject": ([w.HDC, w.HGDIOBJ], w.HGDIOBJ),
        "DeleteObject": ([w.HGDIOBJ], w.BOOL),
        "GetTextExtentPoint32W": ([w.HDC, w.LPCWSTR, ct.c_int, ct.c_void_p], w.BOOL),
        "GetGlyphIndicesW": ([w.HDC, w.LPCWSTR, ct.c_int, ct.c_void_p, w.DWORD], w.DWORD),
        "CreateDIBSection": ([w.HDC, ct.c_void_p, w.UINT, ct.c_void_p, w.HANDLE, w.DWORD], w.HBITMAP),
        "TextOutW": ([w.HDC, ct.c_int, ct.c_int, w.LPCWSTR, ct.c_int], w.BOOL),
        "SetTextColor": ([w.HDC, w.DWORD], w.DWORD),
        "SetBkColor": ([w.HDC, w.DWORD], w.DWORD),
        "GdiFlush": ([], w.BOOL),
        "GetTextFaceW": ([w.HDC, ct.c_int, w.LPWSTR], ct.c_int),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(g, name)
        fn.argtypes, fn.restype = args, result
    return g


@lru_cache(maxsize=1)
def installed_fonts() -> tuple[FontSpec, ...]:
    if os.name != "nt":
        return ()
    from ctypes import wintypes as w
    g = _gdi()
    callback_type = ct.WINFUNCTYPE(ct.c_int, ct.POINTER(ENUMLOGFONTEXW),
                                   ct.c_void_p, w.DWORD, w.LPARAM)
    g.EnumFontFamiliesExW.argtypes = [w.HDC, ct.POINTER(LOGFONTW), callback_type, w.LPARAM, w.DWORD]
    g.EnumFontFamiliesExW.restype = ct.c_int
    found = {}

    @callback_type
    def collect(ptr, metrics, kind, param):
        item = ptr.contents
        lf = item.font
        if lf.face and not lf.face.startswith("@"):
            style = item.style or ("Bold" if lf.weight >= 700 else "Regular")
            spec = FontSpec(lf.face, style, lf.weight, bool(lf.italic),
                            charset=lf.charset, bitmap=bool(kind & 1))
            found[(spec.family, spec.style, spec.weight, spec.italic)] = spec
        return 1

    dc = g.CreateCompatibleDC(None)
    if not dc:
        raise ValueError("无法读取 Windows 字体列表")
    try:
        query = LOGFONTW()
        query.charset = 1  # DEFAULT_CHARSET includes per-user installed fonts.
        g.EnumFontFamiliesExW(dc, ct.byref(query), collect, 0, 0)
        for family in sorted({f.family for f in found.values()}):
            query.face = family
            g.EnumFontFamiliesExW(dc, ct.byref(query), collect, 0, 0)
    finally:
        g.DeleteDC(dc)
    return tuple(sorted(found.values(), key=lambda f: f.label.casefold()))


def default_font() -> FontSpec:
    for path in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\arialbd.ttf"):
        if os.path.exists(path):
            return file_fonts(path)[0]
    raise ValueError("未找到默认字体，请选择字体文件")


def render_text(text: str, spec: FontSpec | None = None) -> Image.Image:
    spec = spec or default_font()
    if not text.strip():
        raise ValueError("中心文字不能为空")
    if len(text) > 512:
        raise ValueError("中心文字过长，请控制在 512 字符以内")
    if spec.path:
        try:
            _check_chars(text, _coverage(spec.path, spec.index, os.path.getmtime(spec.path)))
            font = ImageFont.truetype(spec.path, 256, index=spec.index)
            draw = ImageDraw.Draw(Image.new("L", (1, 1)))
            bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=8)
            image = Image.new("L", (max(1, bbox[2] - bbox[0]) + 8,
                                    max(1, bbox[3] - bbox[1]) + 8), 255)
            ImageDraw.Draw(image).multiline_text((4 - bbox[0], 4 - bbox[1]), text,
                                                 font=font, fill=0, spacing=8)
        except (OSError, IndexError, TTLibError) as exc:
            raise ValueError(f"无法加载字体 {spec.label}：{exc}") from exc
    else:
        image = _render_native(text, spec)
    bbox = image.point(lambda p: 255 - p).getbbox()
    if bbox is None:
        raise ValueError("所选字体没有可见字形，请更换字体")
    return image.crop(bbox)


def _render_native(text, spec):
    from ctypes import wintypes as w
    g = _gdi()
    # GDI can silently substitute a deleted font; reject it before creating one.
    if spec not in installed_fonts():
        raise ValueError(f"系统字体不可用：{spec.label}")
    lf = LOGFONTW()
    # Raster fonts only exist at fixed strikes. A large requested height makes
    # Windows silently replace them with an unrelated scalable font.
    lf.height, lf.weight, lf.italic = (0 if spec.bitmap else -256), spec.weight, spec.italic
    lf.charset, lf.quality, lf.face = spec.charset, 4, spec.family
    dc, font, bitmap, old_font, old_bitmap = None, None, None, None, None
    try:
        dc = g.CreateCompatibleDC(None)
        font = g.CreateFontIndirectW(ct.byref(lf))
        if not dc or not font:
            raise ValueError(f"无法加载系统字体：{spec.label}")
        old_font = g.SelectObject(dc, font)
        actual = ct.create_unicode_buffer(128)
        if not g.GetTextFaceW(dc, len(actual), actual) or actual.value.casefold() != spec.family.casefold():
            raise ValueError(f"Windows 无法使用指定字体：{spec.label}，请更换字体")
        encoded = text.encode("utf-16-le")
        glyphs = (w.WORD * (len(encoded) // 2))()
        count = g.GetGlyphIndicesW(dc, text, len(glyphs), glyphs, 1)
        if count == 0xFFFFFFFF:
            raise ValueError("无法检查字体字符覆盖范围")
        missing = []
        offset = 0
        for char in text:
            units = len(char.encode("utf-16-le")) // 2
            if not char.isspace() and any(glyphs[offset + i] == 0xFFFF for i in range(units)):
                missing.append(char)
            offset += units
        if missing:
            raise ValueError("所选字体缺少字符：" + " ".join(dict.fromkeys(missing))[:40])
        lines = text.split("\n")
        sizes = []
        for line in lines:
            size = w.SIZE()
            sample = line or " "
            if not g.GetTextExtentPoint32W(dc, sample, len(sample.encode("utf-16-le")) // 2, ct.byref(size)):
                raise ValueError("无法测量字体")
            sizes.append((size.cx, size.cy))
        width = max(s[0] for s in sizes) + 64
        height = sum(s[1] + 8 for s in sizes) + 16
        # BITMAPINFOHEADER, top-down 32-bit BGRX DIB.
        import struct
        header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ct.c_void_p()
        bitmap = g.CreateDIBSection(dc, header, 0, ct.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise ValueError("无法创建字体画布")
        old_bitmap = g.SelectObject(dc, bitmap)
        ct.memset(bits, 255, width * height * 4)
        g.SetBkColor(dc, 0xFFFFFF)
        g.SetTextColor(dc, 0)
        y = 8
        for line, (_, h) in zip(lines, sizes):
            if line and not g.TextOutW(dc, 24, y, line, len(line.encode("utf-16-le")) // 2):
                raise ValueError("系统字体渲染失败")
            y += h + 8
        g.GdiFlush()
        return Image.frombytes("RGB", (width, height), ct.string_at(bits, width * height * 4),
                               "raw", "BGRX").convert("L")
    finally:
        if old_bitmap:
            g.SelectObject(dc, old_bitmap)
        if old_font:
            g.SelectObject(dc, old_font)
        if bitmap:
            g.DeleteObject(bitmap)
        if font:
            g.DeleteObject(font)
        if dc:
            g.DeleteDC(dc)
