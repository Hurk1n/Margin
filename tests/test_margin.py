"""Algorithm and export regression tests; no external images or UI automation."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import CancelledError
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
import segno
from PIL import Image, ImageDraw

import app
from margin import core, fonts, svg
from margin.art import ImageOptions, detail_preview, grid_from_gray, load_image, prepare_image


def logo():
    image = Image.new("RGBA", (240, 180), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((60, 20, 180, 140), fill="black")
    draw.ellipse((85, 45, 155, 115), fill="white")
    draw.rectangle((110, 25, 130, 155), fill="black")
    return image


def photo():
    y, x = np.mgrid[:180, :240]
    pixels = np.stack((40 + x * 0.8, 30 + y, 210 - x * 0.5), axis=2)
    image = Image.fromarray(pixels.astype(np.uint8))
    draw = ImageDraw.Draw(image)
    draw.ellipse((80, 25, 160, 115), fill=(205, 150, 110))
    draw.polygon([(50, 179), (85, 110), (155, 110), (190, 179)], fill=(25, 45, 70))
    return image


class ImageTests(unittest.TestCase):
    def test_crop_transparency_and_keep_white_hole(self):
        original, prepared = prepare_image(logo(), ImageOptions())
        self.assertEqual(original.size, (240, 180))
        self.assertEqual(prepared.size, (121, 136))
        self.assertEqual(prepared.getpixel((30, 50)), 255)
        self.assertEqual(prepared.getpixel((60, 50)), 0)

    def test_near_white_crop_and_switch(self):
        image = Image.new("RGB", (120, 90), (250, 250, 250))
        ImageDraw.Draw(image).rectangle((30, 20, 89, 69), fill=(80, 90, 100))
        self.assertEqual(prepare_image(image, ImageOptions())[1].size, (60, 50))
        self.assertEqual(prepare_image(image, ImageOptions(crop=False))[1].size, image.size)
        self.assertEqual(prepare_image(image, ImageOptions(white_threshold=255))[1].size, image.size)

    def test_faint_image_with_crop_disabled(self):
        image = Image.new("RGB", (80, 80), "white")
        ImageDraw.Draw(image).ellipse((10, 10, 70, 70), fill=(249, 249, 249))
        self.assertEqual(prepare_image(image, ImageOptions(crop=False))[1].getextrema(), (0, 255))

    def test_blank_and_broken_input(self):
        for image in (Image.new("RGB", (20, 20), "white"), Image.new("RGBA", (20, 20), (0, 0, 0, 0))):
            with self.assertRaisesRegex(ValueError, "空白"):
                prepare_image(image, ImageOptions())
        with self.assertRaisesRegex(ValueError, "无法识别"):
            load_image(io.BytesIO(b"not an image"))
        with self.assertRaisesRegex(ValueError, "不存在"):
            load_image("missing-margin-image.png")

    def test_exif_orientation(self):
        image = Image.new("RGB", (40, 20), "black")
        exif = image.getexif()
        exif[274] = 6
        stream = io.BytesIO()
        image.save(stream, format="JPEG", exif=exif)
        stream.seek(0)
        self.assertEqual(load_image(stream).size, (20, 40))

    def test_formats_and_first_frame(self):
        for format in ("PNG", "JPEG", "WEBP", "BMP", "TIFF"):
            with self.subTest(format=format):
                stream = io.BytesIO()
                photo().save(stream, format=format)
                stream.seek(0)
                self.assertEqual(load_image(stream).size, (240, 180))
        stream = io.BytesIO()
        photo().save(stream, format="TIFF", save_all=True, append_images=[Image.new("RGB", (40, 20))])
        stream.seek(0)
        self.assertEqual(load_image(stream).size, (240, 180))
        stream = io.BytesIO()
        photo().save(stream, format="GIF")
        stream.seek(0)
        with self.assertRaisesRegex(ValueError, "不支持"):
            load_image(stream)

    def test_modes_and_aspect_ratio(self):
        _, prepared = prepare_image(photo(), ImageOptions())
        for mode in ("auto", "logo", "photo"):
            grid = grid_from_gray(prepared, 65, 0.62, mode)
            self.assertEqual(grid.dark.dtype, bool)
            self.assertIn(grid.mode, ("logo", "photo"))
            y, x = np.where(grid.region)
            self.assertAlmostEqual((np.ptp(x) - 1) / (np.ptp(y) - 1), 240 / 180, delta=0.1)
        for shape in ((400, 30), (30, 400)):
            im = Image.new("L", shape, 100)
            grid = grid_from_gray(im, 65, 0.62, "logo")
            rows, cols = np.where(grid.region)
            self.assertLessEqual(np.ptp(cols) + 1, 42)
            self.assertLessEqual(np.ptp(rows) + 1, 42)

    def test_manual_controls_and_inversion(self):
        _, ordinary = prepare_image(photo(), ImageOptions())
        _, inverted = prepare_image(photo(), ImageOptions(invert=True))
        np.testing.assert_array_equal(np.array(inverted), 255 - np.array(ordinary))
        low = grid_from_gray(ordinary, 65, .62, "logo", threshold=30)
        high = grid_from_gray(ordinary, 65, .62, "logo", threshold=220)
        self.assertLess(low.dark.sum(), high.dark.sum())
        self.assertNotEqual(np.asarray(ordinary).tobytes(),
                            np.asarray(prepare_image(photo(), ImageOptions(contrast=2))[1]).tobytes())

    def test_invalid_options(self):
        for options in (ImageOptions(mode="wrong"), ImageOptions(threshold=-1),
                        ImageOptions(contrast=0), ImageOptions(white_threshold=256)):
            with self.assertRaises(ValueError):
                options.validate()

    def test_high_version_uses_full_square_and_preserves_thin_ink(self):
        image = Image.new("L", (1200, 1200), 255)
        draw = ImageDraw.Draw(image)
        draw.line((601, 0, 601, 1199), fill=0, width=1)
        grid = grid_from_gray(image, 177, .62, "auto")
        self.assertEqual(grid.mode, "logo")
        rows, cols = np.where(grid.dark)
        self.assertEqual(np.ptp(rows) + 1, int(177 * .62))
        self.assertEqual(np.ptp(cols), 0)
        self.assertGreater(len(rows), 0)
        preview = detail_preview(image, "logo")
        self.assertEqual(preview.size, image.size)
        self.assertEqual(preview.getpixel((601, 500)), 0)

    def test_higher_version_recovers_separated_fine_lines(self):
        image = Image.new("L", (1000, 1000), 255)
        draw = ImageDraw.Draw(image)
        for x in range(10, 990, 12):
            draw.line((x, 0, x, 999), fill=0, width=1)
        low = grid_from_gray(image, 65, .62, "logo")
        high = grid_from_gray(image, 177, .62, "logo")
        def transitions(grid):
            middle = grid.dark[grid.dark.shape[0] // 2]
            return np.count_nonzero(middle[1:] != middle[:-1])
        self.assertGreater(transitions(high), transitions(low))

    def test_uniform_logo_and_small_pixel_art(self):
        uniform = grid_from_gray(Image.new("L", (50, 50), 80), 177, .62, "logo")
        self.assertTrue(uniform.dark.any())
        image = Image.new("L", (10, 10), 255)
        ImageDraw.Draw(image).rectangle((0, 0, 4, 9), fill=0)
        grid = grid_from_gray(image, 177, .62, "logo")
        self.assertEqual(grid.dark.sum(), 55 * 109)


class GeneratorTests(unittest.TestCase):
    def test_logo_and_photo_decode_in_both_strategies(self):
        for image in (logo(), photo()):
            for strategy in ("scan", "fidelity"):
                with self.subTest(strategy=strategy, image=image.mode):
                    _, stats = core.generate("https://example.com", image_path=image,
                                              strategy=strategy, scale=10)
                    self.assertTrue(stats.ok, stats.summary())
                    self.assertIn(stats.version, (12, 15, 18))
                    self.assertEqual(stats.decode, [stats.text])
                    self.assertGreater(stats.fidelity, .7)

    def test_function_modules_and_real_error_counts(self):
        for version in (1, 2, 6, 10, 12, 15, 40):
            for ec in ("L", "M", "Q", "H"):
                with self.subTest(version=version, ec=ec):
                    matrix, st, used, mask, changed = core.build_matrix("X", image_path=logo(),
                        version=version, ec=ec, budget=100, strategy="fidelity")
                    baseline = np.array(segno.make("X", version=version, error=ec, mask=mask,
                                                    boost_error=False, micro=False).matrix)
                    np.testing.assert_array_equal(matrix[st.fm], baseline[st.fm])
                    self.assertEqual(changed, np.count_nonzero(matrix != baseline))
                    actual = {}
                    for k, (r, c) in enumerate(st.path[:len(st.cw_owner) * 8]):
                        if matrix[r, c] != baseline[r, c]:
                            actual.setdefault(st.cw_owner[k // 8], set()).add(k // 8)
                    self.assertEqual({b: n for b, n in used.items() if n}, {b: len(c) for b, c in actual.items()})
                    self.assertTrue(all(len(c) <= st.capacity - 1 for c in actual.values()))
                    for r, c in st.path[len(st.cw_owner) * 8:]:
                        self.assertEqual(matrix[r, c], baseline[r, c])

    def test_scan_reserve_and_user_ceiling(self):
        for strategy in ("scan", "fidelity"):
            for budget in (0, 2, 100):
                _, stats = core.generate("test", image_path=logo(), version=12,
                                          budget=budget, strategy=strategy, scale=8)
                ceiling = int(stats.capacity * .7) if strategy == "scan" else stats.capacity - 1
                self.assertLessEqual(stats.budget, min(ceiling, budget))
                self.assertTrue(all(n <= stats.budget for n in stats.per_block.values()))

    def test_manual_version_mask_and_legacy_return(self):
        values = core.build_matrix("hello", "M", 12, "H", 5)
        self.assertEqual(len(values), 5)
        self.assertEqual(values[1].version, 12)
        self.assertEqual(values[3], 5)
        _, stats = core.generate("hello", image_path=logo(), version=12, mask=5, strategy="fidelity", scale=8)
        self.assertEqual((stats.version, stats.mask), (12, 5))

    def test_no_art_small_payload_uses_standard_qr(self):
        _, stats = core.generate("1", ec="L", scale=10)
        self.assertEqual(stats.version, 1)
        self.assertEqual(stats.changed_modules, 0)
        self.assertTrue(stats.ok)

    def test_high_version_image_decodes_and_keeps_source_preview_resolution(self):
        _, stats = core.generate("https://example.com", image_path=photo(), version=40,
                                  image_options=ImageOptions(mode="photo"), strategy="fidelity", scale=10)
        self.assertTrue(stats.ok, stats.summary())
        self.assertEqual(stats.version, 40)
        self.assertEqual(stats.processed.size, photo().size)

    def test_svg_uses_final_matrix_without_regeneration(self):
        _, stats = core.generate("https://example.com", image_path=logo(), strategy="fidelity", dot=1, scale=8)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(core, "generate", side_effect=AssertionError("must not regenerate")):
                output = svg.write_svg(str(Path(directory) / "out.svg"), "", result=stats)
            root = ET.fromstring(output)
            recovered = np.zeros_like(stats.matrix)
            for rect in root.findall("{http://www.w3.org/2000/svg}rect")[1:]:
                r = int(rect.attrib["y"]) // 10 - stats.quiet
                c = int(rect.attrib["x"]) // 10 - stats.quiet
                recovered[r, c] = 1
            np.testing.assert_array_equal(recovered, stats.matrix)

    def test_missing_decoder_is_explicit_and_does_not_retry(self):
        with patch.object(core, "zxingcpp", None):
            _, stats = core.generate("hello", image_path=logo(), scale=8)
        self.assertFalse(stats.verified)
        self.assertFalse(stats.ok)
        self.assertIn("未验证", stats.summary())

    def test_decode_failure_and_budget_retry(self):
        class Decoder:
            def __init__(self):
                self.calls = 0

            def read_barcodes(self, array):
                self.calls += 1
                if self.calls <= 8:
                    return []
                return [type("Barcode", (), {"text": "hello"})()]

        decoder = Decoder()
        with patch.object(core, "zxingcpp", decoder):
            _, stats = core.generate("hello", image_path=logo(), version=12, scale=4)
        self.assertTrue(stats.ok)
        self.assertLess(stats.budget, int(stats.capacity * .7))
        with patch.object(core.zxingcpp, "read_barcodes", return_value=[]):
            _, failed = core.generate("hello", image_path=logo(), version=12, mask=0, auto_retry=False, scale=4)
        self.assertTrue(failed.verified)
        self.assertFalse(failed.ok)
        self.assertIn("失败", failed.summary())

    def test_cancellation(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            core.generate("hello", image_path=logo(), cancel=cancel)

    def test_invalid_parameters(self):
        for kwargs in ({"art": "X", "image_path": logo()}, {"version": 0}, {"mask": 8},
                       {"budget": -1}, {"art_ratio": .9}, {"ec": "Z"}, {"dot": .2},
                       {"strategy": "unknown"}, {"quiet": 0}):
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(ValueError):
                    core.generate("test", **kwargs)


@unittest.skipUnless(os.name == "nt", "Windows font coverage")
class FontTests(unittest.TestCase):
    def test_installed_families_and_styles(self):
        entries = fonts.installed_fonts()
        self.assertGreater(len(entries), 10)
        self.assertTrue(all(not f.family.startswith("@") for f in entries))
        self.assertTrue(any(f.weight >= 700 for f in entries))
        self.assertTrue(any(f.italic for f in entries))
        self.assertTrue(any(any(ord(c) > 127 for c in f.family) for f in entries))
        for entry in (next(f for f in entries if f.family == "Arial" and f.italic),
                      next(f for f in entries if f.family == "微软雅黑" and f.weight >= 700)):
            self.assertGreater(fonts.render_text("Margin", entry).width, 0)

    def test_raster_font_does_not_get_replaced(self):
        entry = next(f for f in fonts.installed_fonts() if f.bitmap and f.family == "Terminal")
        rendered = fonts.render_text("Margin", entry)
        self.assertLess(rendered.height, 40)

    def test_user_installed_font(self):
        import winreg
        names = set()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                _, path, _ = winreg.EnumValue(key, i)
                if isinstance(path, str) and Path(path).suffix.lower() in (".ttf", ".otf") and Path(path).is_file():
                    names.update(f.family for f in fonts.file_fonts(path))
        candidate = next((f for f in fonts.installed_fonts() if f.family in names), None)
        self.assertIsNotNone(candidate)
        self.assertGreater(fonts.render_text("Margin", candidate).width, 0)

    def test_collection_faces_and_external_path(self):
        entries = fonts.file_fonts(r"C:\Windows\Fonts\msyh.ttc")
        self.assertGreater(len(entries), 1)
        for entry in entries:
            self.assertGreater(fonts.render_text("中文 Margin", entry).width, 0)

    def test_missing_glyph_and_missing_font(self):
        entry = next(f for f in fonts.installed_fonts() if f.family == "Arial")
        for font in (entry, fonts.FontSpec("Arial", path=r"C:\Windows\Fonts\arial.ttf")):
            with self.assertRaisesRegex(ValueError, "缺少字符"):
                fonts.render_text("汉字", font)
        with self.assertRaisesRegex(ValueError, "不存在"):
            fonts.file_fonts("missing-font.ttf")
        with tempfile.NamedTemporaryFile(suffix=".ttf") as file:
            with self.assertRaisesRegex(ValueError, "无法读取"):
                fonts.file_fonts(file.name)


class CLITests(unittest.TestCase):
    def test_image_and_export(self):
        with tempfile.TemporaryDirectory() as directory:
            source, png, vector = (str(Path(directory) / name) for name in ("logo.png", "qr.png", "qr.svg"))
            logo().save(source)
            with contextlib.redirect_stdout(io.StringIO()):
                result = app.cli(["-t", "example.com", "--image", source, "--strategy", "fidelity",
                                  "--scale", "8", "-o", png, "--svg", vector])
            self.assertEqual(result, 0)
            self.assertTrue(Path(png).is_file())
            self.assertTrue(Path(vector).is_file())
            decoded = core.zxingcpp.read_barcodes(np.array(Image.open(png)))
            self.assertEqual(decoded[0].text, "https://example.com")

    def test_mutual_exclusion(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                app.cli(["-t", "test", "-a", "X", "--image", "x.png"])
        self.assertEqual(error.exception.code, 2)

    def test_failed_decode_does_not_export(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "out.png")
            with patch.object(core.zxingcpp, "read_barcodes", return_value=[]), contextlib.redirect_stdout(io.StringIO()):
                result = app.cli(["-t", "test", "-o", output])
            self.assertEqual(result, 2)
            self.assertFalse(Path(output).exists())


if __name__ == "__main__":
    unittest.main()
