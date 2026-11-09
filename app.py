# -*- coding: utf-8 -*-
"""Margin 入口：无参数 -> 图形界面；带参数 -> 命令行。"""
from __future__ import annotations

import argparse
import os
import sys


def cli(argv):
    from margin import core
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(prog="Margin", description="纠错余量二维码生成器")
    ap.add_argument("-t", "--text", required=True, help="扫码后显示的内容")
    ap.add_argument("-a", "--art", default="", help="中心像素图案（文字）")
    ap.add_argument("-o", "--out", default="margin.png", help="输出 PNG 路径")
    ap.add_argument("--svg", default="", help="额外输出 SVG 路径")
    ap.add_argument("--version", type=int, default=None, help="版本 6-40，默认自动")
    ap.add_argument("--ec", default="H", help="纠错等级 H/Q/M（默认 H）")
    ap.add_argument("--mask", type=int, default=None, help="掩码 0-7，默认自动")
    ap.add_argument("--budget", type=int, default=10, help="每块纠错码字预算（默认 10）")
    ap.add_argument("--dot", type=float, default=0.66, help="点径 0.4-1.0")
    ap.add_argument("--scale", type=int, default=20, help="每模块像素")
    ap.add_argument("--quiet", type=int, default=4, help="静区模块数")
    ap.add_argument("--font", default="", help="字体文件路径")
    ap.add_argument("--ratio", type=float, default=0.62, help="图案最大宽度占比")
    args = ap.parse_args(argv)

    img, stats = core.generate(
        args.text, args.art, version=args.version, ec=args.ec, mask=args.mask,
        budget=args.budget, dot=args.dot, scale=args.scale, quiet=args.quiet,
        font_path=args.font or None, art_ratio=args.ratio)
    for _p in (args.out, args.svg):
        if _p:
            d = os.path.dirname(os.path.abspath(_p))
            os.makedirs(d, exist_ok=True)
    img.save(args.out)
    print(f"已保存: {os.path.abspath(args.out)}")
    if args.svg:
        from margin import svg
        svg.write_svg(args.svg, args.text, args.art, args.version, args.ec,
                      args.budget, args.dot, args.font or None, args.quiet)
        print(f"已保存: {os.path.abspath(args.svg)}")
    print(stats.summary())
    return 0 if (stats.ok or not stats.decode) else 2


def selftest():
    from margin import core
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cases = [
        ("https://example.com", "LCS", {"budget": 10}),
        ("https://example.com", "YG", {"budget": 12}),
        ("Margin 纠错余量二维码", "M", {"budget": 10, "version": 12}),
        ("tel:13800138000", "88", {"budget": 10}),
        ("www.hurkin.top", "Hurkin", {"budget": 10}),
    ]
    bad = 0
    for text, art, kw in cases:
        img, st = core.generate(text, art, **kw)
        ok = "OK " if st.ok else "FAIL"
        if not st.ok:
            bad += 1
        print(f"[{ok}] {text[:24]:26s} art={art:4s} v{st.version} "
              f"cw={st.error_codewords:3d} mod={st.changed_modules:4d} "
              f"点阵={st.ratio:.0%}")
    print("selftest", "passed" if bad == 0 else f"{bad} failed")
    return 0 if bad == 0 else 1


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--selftest":
        raise SystemExit(selftest())
    if argv:
        raise SystemExit(cli(argv))
    from margin import gui
    gui.main()


if __name__ == "__main__":
    main()
