"""Image cleanup and conversion at the QR module resolution."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError


@dataclass(frozen=True)
class ImageOptions:
    mode: str = "auto"
    threshold: int | None = None
    contrast: float = 1.0
    invert: bool = False
    crop: bool = True
    white_threshold: int = 245

    def validate(self):
        if self.mode not in ("auto", "logo", "photo"):
            raise ValueError("图片处理模式必须为 auto、logo 或 photo")
        if self.threshold is not None and (not isinstance(self.threshold, int) or not 0 <= self.threshold <= 255):
            raise ValueError("黑白阈值必须在 0–255 之间")
        if not 0.2 <= self.contrast <= 4:
            raise ValueError("对比度必须在 0.2–4 之间")
        if not isinstance(self.white_threshold, int) or not 1 <= self.white_threshold <= 255:
            raise ValueError("白边阈值必须在 1–255 之间")


@dataclass
class ArtGrid:
    dark: np.ndarray
    gray: np.ndarray
    region: np.ndarray
    weights: np.ndarray
    mode: str = "text"


def load_image(source) -> Image.Image:
    try:
        if isinstance(source, Image.Image):
            image = ImageOps.exif_transpose(source).convert("RGBA")
        else:
            with Image.open(source) as opened:
                if opened.format not in ("PNG", "JPEG", "WEBP", "BMP", "TIFF"):
                    raise ValueError("不支持此图片格式，请使用 PNG、JPEG、WebP、BMP 或 TIFF")
                opened.seek(0)
                image = ImageOps.exif_transpose(opened).convert("RGBA")
        background = Image.new("RGBA", image.size, "white")
        return Image.alpha_composite(background, image).convert("RGB")
    except FileNotFoundError as exc:
        raise ValueError("图片文件不存在，请重新选择") from exc
    except UnidentifiedImageError as exc:
        raise ValueError("无法识别图片，文件可能损坏或格式不受支持") from exc
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"无法读取图片：{exc}") from exc


def prepare_image(source, options: ImageOptions) -> tuple[Image.Image, Image.Image]:
    options.validate()
    original = load_image(source)
    pixels = np.asarray(original)
    content = np.any(pixels < (options.white_threshold if options.crop else 255), axis=2)
    if not content.any():
        raise ValueError("图片为空白或白边阈值过低，请调高白边阈值")
    if options.crop:
        rows, cols = np.where(content)
        cropped = original.crop((int(cols.min()), int(rows.min()),
                                 int(cols.max()) + 1, int(rows.max()) + 1))
    else:
        cropped = original
    gray = ImageOps.grayscale(cropped)
    # Percentile clipping can erase fine strokes occupying <0.5% of an image.
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(options.contrast)
    if options.invert:
        gray = ImageOps.invert(gray)
    return original, gray


def otsu(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    levels = np.flatnonzero(hist)
    if len(levels) == 1:
        return min(int(levels[0]), 254)
    prob = hist / hist.sum()
    weight = np.cumsum(prob)
    mean = np.cumsum(prob * np.arange(256))
    score = (mean[-1] * weight - mean) ** 2 / np.maximum(weight * (1 - weight), 1e-12)
    return int(np.argmax(score))


def _ordered_dither(gray, threshold=None):
    height, width = gray.shape
    bayer = np.array([[0, 8, 2, 10], [12, 4, 14, 6],
                      [3, 11, 1, 9], [15, 7, 13, 5]], dtype=float)
    screen = np.tile((bayer + 0.5) * 16, ((height + 3) // 4, (width + 3) // 4))[:height, :width]
    values = gray.astype(float)
    if threshold is not None:
        values = np.clip(values + 128 - threshold, 0, 255)
    return values < screen


def detail_preview(image: Image.Image, mode: str, threshold=None) -> Image.Image:
    """Source-resolution preview; never enlarge a tiny QR grid as the source art."""
    gray = np.asarray(image)
    dark = (_ordered_dither(gray, threshold) if mode == "photo" else
            gray <= (threshold if threshold is not None else otsu(gray)))
    return Image.fromarray(np.where(dark, 0, 255).astype(np.uint8))


def grid_from_gray(image: Image.Image, n: int, ratio: float,
                   mode="text", threshold=None, max_height_ratio=None) -> ArtGrid:
    height_ratio = max_height_ratio if max_height_ratio is not None else ratio * (0.72 if mode == "text" else 1)
    max_w, max_h = max(1, int(n * ratio)), max(1, int(n * height_ratio))
    factor = min(max_w / image.width, max_h / image.height)
    width, height = max(1, round(image.width * factor)), max(1, round(image.height * factor))
    small = image.resize((width, height), Image.Resampling.LANCZOS)
    source_gray = np.asarray(small, dtype=np.uint8)
    full_gray = np.asarray(image)
    cut = threshold if threshold is not None else otsu(full_gray)
    logo_gray = np.asarray(image.resize((width, height), Image.Resampling.NEAREST))
    if image.width >= width and image.height >= height and mode != "text":
        # Preserve sub-cell ink instead of averaging a thin line into white.
        # More QR modules create smaller cells and therefore recover finer detail.
        xs = np.arange(width) * image.width // width
        ys = np.arange(height) * image.height // height
        logo_gray = np.minimum.reduceat(np.minimum.reduceat(full_gray, xs, axis=1), ys, axis=0)
    logo = logo_gray <= cut
    photo = _ordered_dither(source_gray, threshold)
    selected = mode
    if mode == "auto":
        # Tone comparison only selects the mode; it never blurs the source art.
        comparison_size = (max(1, width // 4), max(1, height // 4))
        reference = np.asarray(small.resize(comparison_size, Image.Resampling.BOX), dtype=float)
        def loss(dark):
            rendered = Image.fromarray(np.where(dark, 0, 255).astype(np.uint8))
            return np.mean((np.asarray(rendered.resize(comparison_size, Image.Resampling.BOX), dtype=float) - reference) ** 2)
        binary_source = np.mean((full_gray <= 24) | (full_gray >= 231)) >= 0.9
        selected = "logo" if binary_source else ("photo" if loss(photo) < loss(logo) else "logo")
    dark = (source_gray < 128) if mode == "text" else (photo if selected == "photo" else logo)
    gray = np.full((n, n), 255.0, dtype=np.float32)
    target = np.zeros((n, n), dtype=bool)
    region = np.zeros((n, n), dtype=bool)
    top, left = (n - height) // 2, (n - width) // 2
    sl = np.s_[top:top + height, left:left + width]
    gray[sl], target[sl], region[sl] = source_gray, dark, True
    # A one-module white halo separates the art from surrounding QR noise.
    region[max(0, top - 1):min(n, top + height + 1),
           max(0, left - 1):min(n, left + width + 1)] = True
    edge = np.asarray(Image.fromarray((target * 255).astype(np.uint8)).filter(ImageFilter.FIND_EDGES)) > 0
    weights = np.where(edge, 4.0, np.where(target, 2.0, 1.0)).astype(np.float32)
    return ArtGrid(target, gray, region, weights, selected)
