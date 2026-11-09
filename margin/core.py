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
from typing import Optional
from concurrent.futures import CancelledError

from .art import ImageOptions, detail_preview, grid_from_gray, prepare_image
from .fonts import FontSpec, render_text

import numpy as np
import segno
import segno.consts as sconst

try:  # 可选：用于生成后的自检
    import zxingcpp
except Exception:  # pragma: no cover
    zxingcpp = None

from PIL import Image, ImageDraw

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
    if font_path:
        if not os.path.isfile(font_path):
            raise ValueError(f"字体文件不存在：{font_path}")
        return font_path
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def art_grid(art: str, n: int, font_path: Optional[str] = None,
             max_width_ratio: float = 0.62, max_height_ratio: float = 0.42):
    """Compatibility helper returning the text target and its grayscale grid."""
    if not art:
        return np.zeros((n, n), dtype=bool), np.full((n, n), 255, np.float32)
    spec = FontSpec("", path=font_path) if font_path else None
    image = render_text(art, spec)
    grid = grid_from_gray(image, n, max_width_ratio, max_height_ratio=max_height_ratio)
    return grid.dark, grid.gray


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
    verified: bool = False
    fidelity: float = 1.0
    strategy: str = "scan"
    image_mode: str = ""
    matrix: Optional[np.ndarray] = field(default=None, repr=False)
    structure: Optional[Structure] = field(default=None, repr=False)
    target: Optional[np.ndarray] = field(default=None, repr=False)
    original: Optional[Image.Image] = field(default=None, repr=False)
    processed: Optional[Image.Image] = field(default=None, repr=False)
    dot: float = 0.66
    quiet: int = 4

    def summary(self) -> str:
        blocks = " ".join(str(v) for _, v in sorted(self.per_block.items()))
        dec = "成功" if self.ok else ("失败" if self.verified else "未验证（未安装 zxing-cpp）")
        strategy = "扫码优先" if self.strategy == "scan" else "还原优先"
        processing = f" · 图片模式 {self.image_mode}" if self.image_mode else ""
        return (f"v{self.version} · {self.modules}×{self.modules} · {self.ec}级 · mask{self.mask}\n"
                f"改写 {self.changed_modules} 个模块，占用 {self.error_codewords} 个纠错码字\n"
                f"各块用量 {blocks}（容量 {self.capacity} / 有效预算 {self.budget}）\n"
                f"预算占比 {self.ratio:.0%} · 图案一致度 {self.fidelity:.0%}\n"
                f"{strategy}{processing}\n"
                f"扫码自检：{dec}")


def _validate(text, art, image_path, ec, budget, ratio, strategy, version, mask):
    if not text or not text.strip():
        raise ValueError("扫码内容不能为空")
    if art and image_path is not None:
        raise ValueError("中心文字与图片只能选择一种")
    if ec not in EC_TABLE_KEY:
        raise ValueError("纠错等级必须为 H、Q、M 或 L")
    if not isinstance(budget, int) or budget < 0:
        raise ValueError("纠错预算必须为非负整数")
    if not 0.1 <= ratio <= 0.8:
        raise ValueError("图案尺寸占比必须在 0.1–0.8 之间")
    if strategy not in ("scan", "fidelity"):
        raise ValueError("生成策略必须为 scan 或 fidelity")
    if version is not None and (not isinstance(version, int) or not 1 <= version <= 40):
        raise ValueError("二维码版本必须在 1–40 之间")
    if mask is not None and (not isinstance(mask, int) or not 0 <= mask <= 7):
        raise ValueError("掩码必须在 0–7 之间")


def _seed(text, ec):
    return segno.make(text, error=ec, boost_error=False, micro=False)


def _qr_candidates(text, ec, version, mask):
    qr = segno.make(text, error=ec, version=version, mask=mask,
                     boost_error=False, micro=False)
    if mask is not None:
        return [qr]
    # Keep the QR library's standard penalty winner when art scores tie.
    return [qr] + [segno.make(text, error=ec, version=version, mask=m,
                              boost_error=False, micro=False)
                    for m in range(8) if m != qr.mask]


def _start_version(seed, art, image_path, version):
    if version is not None:
        if version < seed.version:
            raise ValueError("内容放不进这个版本，请提高版本或缩短内容")
        return version
    minimum = 12 if image_path is not None else (15 if len(art) > 8 else 12 if len(art) > 4 else 10)
    return max(seed.version, minimum) if art or image_path is not None else seed.version


def _source(art, image_path, image_options, font_path, font_index, font_spec):
    if font_index < 0:
        raise ValueError("字体索引不能为负数")
    if image_path is not None:
        original, prepared = prepare_image(image_path, image_options)
        return original, prepared
    if art:
        spec = font_spec or (FontSpec("", path=font_path, index=font_index) if font_path else None)
        return None, render_text(art, spec)
    return None, None


def _grid(prepared, n, ratio, options, is_image, st):
    if prepared is None:
        return None
    grid = grid_from_gray(prepared, n, ratio, options.mode if is_image else "text",
                          options.threshold if is_image else None)
    _, _, shift = shift_art(grid.dark, grid.gray, st)
    if shift != (0, 0):
        for name in ("dark", "gray", "region", "weights"):
            setattr(grid, name, np.roll(getattr(grid, name), shift, axis=(0, 1)))
    return grid


def _effective_budget(st, requested, strategy):
    ceiling = int(st.capacity * 0.7) if strategy == "scan" else max(0, st.capacity - 1)
    return min(requested, ceiling)


def _apply(qr, st, grid, budget):
    matrix = np.array(qr.matrix, dtype=np.uint8)
    if grid is None:
        return matrix, {}, 0, 1.0
    # A changed codeword costs one error regardless of how many of its bits change.
    # Rank entire codewords by recoverable visual detail, independently per RS block.
    groups = {}
    for k, (r, c) in enumerate(st.path[:len(st.cw_owner) * 8]):
        if grid.region[r, c] and bool(matrix[r, c]) != grid.dark[r, c]:
            groups.setdefault(k // 8, []).append((r, c))
    ranked = {}
    for cw, cells in groups.items():
        score = sum(float(grid.weights[r, c]) for r, c in cells)
        ranked.setdefault(st.cw_owner[cw], []).append((score, cw, cells))
    used, applied = {}, 0
    for block, choices in ranked.items():
        chosen = sorted(choices, key=lambda item: (-item[0], item[1]))[:budget]
        used[block] = len(chosen)
        for _, _, cells in chosen:
            for r, c in cells:
                matrix[r, c] = grid.dark[r, c]
                applied += 1
    weights = grid.weights * grid.region
    fidelity = float(np.sum(weights * (matrix == grid.dark)) / max(float(weights.sum()), 1))
    return matrix, used, applied, fidelity


def build_matrix(text: str, art: str = "", version: Optional[int] = None,
                 ec: str = "H", mask: Optional[int] = None, budget: int = 10,
                 font_path: Optional[str] = None, art_ratio: float = 0.62, *,
                 image_path=None, image_options=None, font_index=0, font_spec=None,
                 strategy="scan"):
    """Build a budget-limited matrix. The legacy five-item return is preserved."""
    text, ec = normalize_payload(text), ec.upper()
    _validate(text, art, image_path, ec, budget, art_ratio, strategy, version, mask)
    options = image_options or ImageOptions()
    options.validate()
    seed = _seed(text, ec)
    version = _start_version(seed, art, image_path, version)
    st = build_structure(version, ec)
    _, prepared = _source(art, image_path, options, font_path, font_index, font_spec)
    grid = _grid(prepared, st.n, art_ratio, options, image_path is not None, st)
    effective = _effective_budget(st, budget, strategy)
    candidates = []
    for qr in _qr_candidates(text, ec, version, mask):
        matrix, used, applied, fidelity = _apply(qr, st, grid, effective)
        candidates.append((fidelity, matrix, used, applied, qr.mask))
    _, matrix, used, applied, chosen_mask = max(candidates, key=lambda item: item[0])
    return matrix, st, used, chosen_mask, applied


def generate(text: str, art: str = "", version: Optional[int] = None,
             ec: str = "H", mask: Optional[int] = None, budget: int = 10,
             dot: float = 0.66, scale: int = 20, quiet: int = 4,
             font_path: Optional[str] = None,
             auto_retry: bool = True, art_ratio: float = 0.62, *,
             image_path=None, image_options=None, font_index=0, font_spec=None,
             strategy="scan", cancel=None):
    """Return (PNG image, Stats), including the exact final matrix for SVG export."""
    text, ec = normalize_payload(text), ec.upper()
    _validate(text, art, image_path, ec, budget, art_ratio, strategy, version, mask)
    if (not 0.4 <= dot <= 1 or not isinstance(scale, int) or scale < 1
            or not isinstance(quiet, int) or quiet < 4):
        raise ValueError("点径需为 0.4–1，像素需大于 0，静区至少为 4")
    options = image_options or ImageOptions()
    options.validate()
    seed = _seed(text, ec)
    first = _start_version(seed, art, image_path, version)
    versions = sorted({min(40, first + step) for step in (0, 3, 6)}) if image_path is not None and version is None else [first]
    if cancel is not None and cancel.is_set():
        raise CancelledError()
    original, prepared = _source(art, image_path, options, font_path, font_index, font_spec)
    previews = {}
    best, fallback = None, None
    for current in versions:
        st = build_structure(current, ec)
        grid = _grid(prepared, st.n, art_ratio, options, image_path is not None, st)
        processed = None
        if grid is not None and image_path is not None:
            if grid.mode not in previews:
                previews[grid.mode] = detail_preview(prepared, grid.mode, options.threshold)
            processed = previews[grid.mode]
        limit = _effective_budget(st, budget, strategy)
        budgets = [limit]
        if auto_retry and grid is not None and zxingcpp is not None:
            budgets += list(range(limit - 2, -1, -2))
            if 0 not in budgets:
                budgets.append(0)
        qr_candidates = _qr_candidates(text, ec, current, mask)
        for effective in budgets:
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            candidates = []
            for qr in qr_candidates:
                matrix, used, applied, fidelity = _apply(qr, st, grid, effective)
                candidates.append((fidelity, qr, matrix, used, applied))
            candidates.sort(key=lambda item: item[0], reverse=True)
            passed = False
            for fidelity, qr, matrix, used, applied in candidates:
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
                img = render(matrix, st, dot=dot, scale=scale, quiet=quiet)
                stats = Stats(text=text, art=art, version=current, modules=st.n, ec=ec,
                              mask=qr.mask, budget=effective, capacity=st.capacity,
                              changed_modules=applied, error_codewords=sum(used.values()),
                              per_block={i: used.get(i, 0) for i in range(st.block_count)},
                              ratio=sum(used.values()) / (st.capacity * st.block_count),
                              fidelity=fidelity, strategy=strategy,
                              image_mode=grid.mode if grid is not None and image_path is not None else "",
                              matrix=matrix, structure=st,
                              target=grid.dark if grid is not None else None,
                              original=original, processed=processed, dot=dot, quiet=quiet)
                if zxingcpp is not None:
                    stats.verified = True
                    stats.decode = [b.text for b in zxingcpp.read_barcodes(np.array(img.convert("L")))]
                    stats.ok = text in stats.decode
                result = (img, stats)
                if fallback is None or fidelity > fallback[1].fidelity:
                    fallback = result
                if stats.ok or not stats.verified:
                    passed = True
                    if best is None or fidelity > best[1].fidelity:
                        best = result
                    # All masks were compared above. Decode the highest-fidelity passing one.
                    break
            if passed:
                break  # Lower budgets cannot recover more of the target.
        if best is not None and strategy == "scan":
            break
    return best or fallback


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

