# -*- coding: utf-8 -*-
"""Margin — 用二维码的纠错余量换取中心像素图案。

核心思路：只覆写"数据模块"，让 Reed-Solomon 纠错吸收误差；
每个 RS 块可纠错的码字数量有硬上限，用预算（budget）控制，
功能图案（定位/时序/对齐/格式/版本/暗模块）永远不碰。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
import segno
import segno.consts as sconst

try:  # 可选：用于生成后的自检
    import zxingcpp
except Exception:  # pragma: no cover
    zxingcpp = None

from PIL import Image, ImageDraw, ImageFont

EC_TABLE_KEY = {"L": 1, "M": 0, "H": 2, "Q": 3}

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",     # 微软雅黑 Bold
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",     # 黑体
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\consolab.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
]


# ---------------------------------------------------------------- 内容规范化
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_DOMAIN_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}"
    r"(?::\d{1,5})?(?:[/?#].*)?$")
_PREFIXES = ("mailto:", "tel:", "sms:", "smsto:", "geo:", "wifi:",
             "begin:vcard", "begin:vevent", "bitcoin:", "market:")


def normalize_payload(text: str) -> str:
    """裸域名自动补 https://，这样扫码器才会当成网址而不是纯文本。"""
    t = (text or "").strip()
    if not t or " " in t or chr(10) in t:
        return text
    if _SCHEME_RE.match(t):
        return t
    low = t.lower()
    if low.startswith(_PREFIXES):
        return t
    if _DOMAIN_RE.match(t):
        return "https://" + t
    return text


# ---------------------------------------------------------------- QR 结构


def function_mask(version: int, n: int) -> np.ndarray:
    """功能模块（不可改写）的位置掩码。"""
    f = np.zeros((n, n), dtype=bool)
    # 三个定位图案 + 分隔符
    for r0, c0 in ((0, 0), (0, n - 8), (n - 8, 0)):
        f[r0:r0 + 8, c0:c0 + 8] = True
    # 时序图案
    f[6, :] = True
    f[:, 6] = True
    # 对齐图案
    if version >= 2:
        pos = sconst.ALIGNMENT_POS[version - 2]
        for r in pos:
            for c in pos:
                if (r < 8 and c < 8) or (r < 8 and c > n - 8) or (r > n - 8 and c < 8):
                    continue
                f[r - 2:r + 3, c - 2:c + 3] = True
    # 版本信息（v7 起）
    if version >= 7:
        f[0:6, n - 11:n - 8] = True
        f[n - 11:n - 8, 0:6] = True
    # 格式信息
    for c in range(9):
        f[8, c] = True
        f[c, 8] = True
    for c in range(n - 8, n):
        f[8, c] = True
    for r in range(n - 7, n):
        f[r, 8] = True
    # 暗模块
    f[4 * version + 9, 8] = True
    return f


def bit_path(n: int, fm: np.ndarray):
    """按标准之字形顺序返回每个数据比特对应的 (row, col)。"""
    path = []
    col = n - 1
    up = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(n - 1, -1, -1) if up else range(n)
        for r in rows:
            for c in (col, col - 1):
                if not fm[r, c]:
                    path.append((r, c))
        up = not up
        col -= 2
    return path


@dataclass
class Structure:
    version: int
    n: int
    ec: str
    fm: np.ndarray
    path: list
    cw_owner: list          # 发射顺序的码字 -> 块号
    blocks: list            # [(num_blocks, total, data)]
    ec_per_block: int
    capacity: int           # 每块最多可纠的码字错误数


    @property
    def block_count(self) -> int:
        return sum(b[0] for b in self.blocks)


def build_structure(version: int, ec: str) -> Structure:
    n = 17 + 4 * version
    fm = function_mask(version, n)
    path = bit_path(n, fm)
    key = EC_TABLE_KEY[ec]
    spec = sconst.ECC[version][key]
    blocks = [(b.num_blocks, b.num_total, b.num_data) for b in spec]
    ec_per_block = blocks[0][1] - blocks[0][2]
    data_counts = []
    for nb, tot, dat in blocks:
        data_counts += [dat] * nb
    # 发射顺序：数据码字交织 -> 纠错码字交织
    cw_owner = []
    for i in range(max(data_counts)):
        for bi, d in enumerate(data_counts):
            if i < d:
                cw_owner.append(bi)
    for i in range(ec_per_block):
        for bi in range(len(data_counts)):
            cw_owner.append(bi)
    return Structure(version, n, ec, fm, path, cw_owner, blocks, ec_per_block,
                     ec_per_block // 2)


# ---------------------------------------------------------------- 图案


def pick_font(font_path: Optional[str] = None) -> Optional[str]:
    if font_path and os.path.exists(font_path):
        return font_path
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def art_grid(art: str, n: int, font_path: Optional[str] = None,
             max_width_ratio: float = 0.62, max_height_ratio: float = 0.42):
    """把文字渲染成 n x n 的模块网格（True = 深色）。"""
    if not art:
        return np.zeros((n, n), dtype=bool), np.zeros((n, n), np.float32)
    S = 48
    H = n * S
    img = Image.new("L", (H, H), 255)
    d = ImageDraw.Draw(img)
    fpath = pick_font(font_path)
    # 自适应字号：先按宽度上限，再按高度上限
    lo, hi = 4, H
    best = None
    for _ in range(28):
        fs = (lo + hi) // 2
        font = ImageFont.truetype(fpath, fs) if fpath else ImageFont.load_default()
        try:
            bbox = d.textbbox((0, 0), art, font=font, spacing=0)
        except Exception:
            bbox = (0, 0, fs * len(art), fs)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw <= H * max_width_ratio and th <= H * max_height_ratio:
            best = (fs, bbox, tw, th)
            lo = fs + 1
        else:
            hi = fs - 1
        if lo > hi:
            break
    if best is None:
        fs = max(6, int(H * 0.18))
        font = ImageFont.truetype(fpath, fs) if fpath else ImageFont.load_default()
        bbox = d.textbbox((0, 0), art, font=font)
    else:
        fs, bbox, tw, th = best
        font = ImageFont.truetype(fpath, fs) if fpath else ImageFont.load_default()
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (H - tw) / 2 - bbox[0]
    y = (H - th) / 2 - bbox[1]
    d.text((x, y), art, font=font, fill=0)

    small = img.resize((n, n), Image.LANCZOS)
    gray = np.array(small).astype(np.float32)
    thr = 128
    return gray < thr, gray



def alignment_mask(st: Structure) -> np.ndarray:
    """对齐图案区域（5x5），用于让中心图案避开它们。"""
    n = st.n
    a = np.zeros((n, n), dtype=bool)
    if st.version < 2:
        return a
    pos = sconst.ALIGNMENT_POS[st.version - 2]
    for r in pos:
        for c in pos:
            if (r < 8 and c < 8) or (r < 8 and c > n - 8) or (r > n - 8 and c < 8):
                continue
            a[r - 2:r + 3, c - 2:c + 3] = True
    return a


def shift_art(P: np.ndarray, gray: np.ndarray, st: Structure, max_shift: int = 10):
    """轻移图案，尽量让字形避开对齐图案，同时保持居中且不越界。"""
    n = st.n
    a = alignment_mask(st)
    if not a.any() or not P.any():
        return P, gray, (0, 0)
    rows = np.where(P.any(axis=1))[0]
    cols = np.where(P.any(axis=0))[0]
    if rows.size == 0:
        return P, gray, (0, 0)
    r0, r1 = int(rows[0]), int(rows[-1])
    c0, c1 = int(cols[0]), int(cols[-1])
    best = None
    for dr in range(-max_shift, max_shift + 1):
        for dc in range(-max_shift, max_shift + 1):
            # 越界与安全区约束
            if r0 + dr < 9 or r1 + dr > n - 10 or c0 + dc < 9 or c1 + dc > n - 10:
                continue
            Q = np.roll(np.roll(P, dr, axis=0), dc, axis=1)
            cost = int(np.count_nonzero(Q & a))
            key = (cost, abs(dr) + abs(dc))
            if best is None or key < best[0]:
                g = np.roll(np.roll(gray, dr, axis=0), dc, axis=1)
                best = (key, Q, g, (dr, dc))
    if best is None:
        return P, gray, (0, 0)
    return best[1], best[2], best[3]


# ---------------------------------------------------------------- 生成


@dataclass
class Stats:
    text: str = ""
    art: str = ""
    version: int = 0
    modules: int = 0
    ec: str = "H"
    mask: int = 0
    budget: int = 0
    capacity: int = 0
    changed_modules: int = 0
    error_codewords: int = 0
    per_block: dict = field(default_factory=dict)
    decode: list = field(default_factory=list)
    ok: bool = False
    ratio: float = 0.0

    def summary(self) -> str:
        blocks = " ".join(f"{v}" for _, v in sorted(self.per_block.items()))
        dec = "成功" if self.ok else ("未验证" if not self.decode else "失败")
        return (f"v{self.version} · {self.modules}×{self.modules} · {self.ec}级 · mask{self.mask}\n"
                f"改写 {self.changed_modules} 个模块，占用 {self.error_codewords} 个纠错码字\n"
                f"各块用量 {blocks}（上限 {self.capacity}）· 预算占比 {self.ratio:.0%}\n"
                f"扫码自检：{dec}")


def build_matrix(text: str, art: str = "", version: Optional[int] = None,
                 ec: str = "H", mask: Optional[int] = None, budget: int = 10,
                 font_path: Optional[str] = None, art_ratio: float = 0.62):
    """构造最终模块矩阵（含中心图案改写）。

    返回 (M, Structure, used, mask, changed_modules)
    """
    if not text:
        raise ValueError("内容不能为空")
    text = normalize_payload(text)
    ec = ec.upper()
    seed = segno.make(text, error=ec, boost_error=False)
    if version is None:
        if art:
            # 图案越长，需要越多模块来画字：4 字符内 v10，5-8 字符 v12，更长 v15
            need = 12 if len(art) > 4 else 10
            if len(art) > 8:
                need = 15
            version = max(seed.version, need)
        else:
            version = seed.version
    st = build_structure(version, ec)

    qr = (segno.make(text, error=ec, version=version, mask=mask, boost_error=False)
          if mask is not None else
          segno.make(text, error=ec, version=version, boost_error=False))
    M = np.array([[1 if v else 0 for v in row] for row in qr.matrix], dtype=np.uint8)
    n = st.n
    pos_index = {rc: k for k, rc in enumerate(st.path)}
    used = {}
    applied = 0

    if art:
        P, gray = art_grid(art, n, font_path,
                           max_width_ratio=art_ratio,
                           max_height_ratio=art_ratio * 0.72)
        P, gray, _shift = shift_art(P, gray, st)
        cand = np.argwhere((~st.fm) & (P.astype(bool) != M.astype(bool)))
        # 距离场：触摸到笔画的可能性（膨胀 + 模糊），让"该变白"的点也有优先级
        from PIL import ImageFilter
        base = Image.fromarray((P * 255).astype(np.uint8))
        near = base
        for _ in range(3):
            near = near.filter(ImageFilter.MaxFilter(5))
        near = near.filter(ImageFilter.GaussianBlur(2))
        near = np.array(near).astype(np.float32)
        # 笔画内（P=1）绝对优先，其次才是靠近笔画的空白
        score = np.where(P, 300.0 + (255.0 - gray), near * 0.9)
        order = sorted(((score[r, c], int(r), int(c)) for r, c in cand), reverse=True)
        changed = {}
        for _s, r, c in order:
            k = pos_index[(r, c)]
            cw = k // 8
            bi = st.cw_owner[cw]
            cur = changed.get(bi, set())
            if len(cur | {cw}) > budget:
                continue
            changed[bi] = cur | {cw}
            M[r, c] = P[r, c]
            applied += 1
        used = {bi: len(v) for bi, v in changed.items()}
    return M, st, used, qr.mask, applied


def generate(text: str, art: str = "", version: Optional[int] = None,
             ec: str = "H", mask: Optional[int] = None, budget: int = 10,
             dot: float = 0.66, scale: int = 20, quiet: int = 4,
             font_path: Optional[str] = None,
             auto_retry: bool = True, art_ratio: float = 0.62):
    """生成中心带像素图案的二维码。返回 (PIL.Image, Stats)。"""
    text = normalize_payload(text)
    M, st, used, used_mask, applied = build_matrix(
        text, art, version, ec, mask, budget, font_path, art_ratio)
    img = render(M, st, dot=dot, scale=scale, quiet=quiet)

    stats = Stats(text=text, art=art, version=st.version, modules=st.n, ec=st.ec,
                  mask=used_mask, budget=budget, capacity=st.capacity,
                  changed_modules=applied,
                  error_codewords=sum(used.values()),
                  per_block={i: used.get(i, 0) for i in range(st.block_count)},
                  ratio=(sum(used.values()) / (st.capacity * st.block_count))
                  if st.capacity else 0.0)

    if zxingcpp is not None:
        stats.decode = [b.text for b in
                        zxingcpp.read_barcodes(np.array(img.convert("L")))]
        stats.ok = stats.text in stats.decode

    # 自检失败 -> 自动降预算重试，取第一个能扫出来的
    if auto_retry and art and not stats.ok and budget > 4:
        for b in (budget - 2, budget - 4, 6, 4):
            if b <= 0 or b >= budget:
                continue
            im2, st2 = generate(text, art, version=version, ec=ec, mask=mask, budget=b,
                                dot=dot, scale=scale, quiet=quiet, font_path=font_path,
                                auto_retry=False, art_ratio=art_ratio)
            if st2.ok:
                return im2, st2
    return img, stats


def render(M: np.ndarray, st: Structure, dot: float = 0.66, scale: int = 20,
           quiet: int = 4) -> Image.Image:
    n = st.n
    size = (n + 2 * quiet) * scale
    img = Image.new("RGB", (size, size), "white")
    dr = ImageDraw.Draw(img)
    for r in range(n):
        for c in range(n):
            if not M[r, c]:
                continue
            x0 = (c + quiet) * scale
            y0 = (r + quiet) * scale
            if st.fm[r, c] or dot >= 0.999:
                # 功能图案必须满格实心，否则识别率会崩
                dr.rectangle([x0, y0, x0 + scale - 1, y0 + scale - 1], fill="black")
            else:
                pad = scale * (1.0 - dot) / 2.0
                dr.ellipse([x0 + pad, y0 + pad, x0 + scale - pad, y0 + scale - pad],
                           fill="black")
    return img

