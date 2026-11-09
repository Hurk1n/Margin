# -*- coding: utf-8 -*-
"""SVG 输出 —— 矢量，适合印刷。"""
from __future__ import annotations

from typing import Optional

from . import core


def matrix_to_svg(M, st, dot: float = 0.66, quiet: int = 4) -> str:
    n = st.n
    unit = 10
    size = (n + 2 * quiet) * unit
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" shape-rendering="geometricPrecision">',
        f'<rect width="{size}" height="{size}" fill="#ffffff"/>',
    ]
    r_pt = unit * dot / 2.0
    for r in range(n):
        for c in range(n):
            if not M[r, c]:
                continue
            x0 = (c + quiet) * unit
            y0 = (r + quiet) * unit
            if st.fm[r, c] or dot >= 0.999:
                parts.append(f'<rect x="{x0}" y="{y0}" width="{unit}" height="{unit}" fill="#000000"/>')
            else:
                cx = x0 + unit / 2.0
                cy = y0 + unit / 2.0
                parts.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r_pt:.2f}" fill="#000000"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def write_svg(path: str, text: str, art: str = "", version: Optional[int] = None,
              ec: str = "H", budget: int = 10, dot: float = 0.66,
              font_path: Optional[str] = None, quiet: int = 4, *, result=None,
              **options) -> str:
    if result is None:
        _, result = core.generate(text, art, version=version, ec=ec, budget=budget,
                                  dot=dot, quiet=quiet, font_path=font_path, **options)
    svg = matrix_to_svg(result.matrix, result.structure, dot=result.dot, quiet=result.quiet)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return svg
