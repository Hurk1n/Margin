"""Tk component/event-loop checks, without desktop or mouse automation."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from margin import core
from margin.gui import App, NumberControl


@unittest.skipUnless(os.name == "nt", "Windows desktop editor")
class EditorTests(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.app.withdraw()
        self.wait_for(lambda: self.app._stats is not None)
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "logo.png")
        image = Image.new("RGB", (160, 160), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((20, 20, 140, 140), fill="black")
        draw.ellipse((50, 50, 110, 110), fill="white")
        image.save(self.path)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def wait_for(self, predicate, timeout=12):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail("Timed out: " + self.app.status.cget("text"))

    def test_sliders_and_fields_share_values(self):
        def controls(widget):
            for child in widget.winfo_children():
                if isinstance(child, NumberControl):
                    yield child
                yield from controls(child)
        numbers = list(controls(self.app))
        self.assertEqual(len(numbers), 6)
        for control in numbers:
            with self.subTest(variable=control.variable._name):
                value = control.low + (control.high - control.low) * .6
                control._from_slider(value)
                self.assertAlmostEqual(float(control.variable.get()), control._position.get())
                typed = control.low + control.step
                control.entry.delete(0, "end")
                # The automatic threshold field is disabled until opted in.
                if str(control.entry.cget("state")) == "disabled":
                    self.app.manual_threshold.set(True)
                control.entry.delete(0, "end")
                control.entry.insert(0, str(int(typed) if control.step >= 1 else round(typed, 2)))
                self.assertAlmostEqual(control._position.get(), typed)
                control._keyboard_step(1)
                self.assertAlmostEqual(float(control.variable.get()), typed + control.step)
        self.app.manual_threshold.set(False)
        threshold = next(c for c in numbers if c.variable is self.app.threshold)
        self.assertEqual(str(threshold.slider.cget("state")), "disabled")

    def test_image_switch_reset_and_exact_export(self):
        self.app.source.set("图片")
        self.app.image_path.set(self.path)
        self.app.contrast.set(1.5)
        self.app.manual_threshold.set(True)
        self.app.threshold.set(100)
        self.wait_for(lambda: self.app._stats is not None)
        self.assertTrue(self.app._stats.ok)
        self.assertIsNotNone(self.app._stats.processed)
        self.assertEqual(str(self.app.save_svg.cget("state")), "normal")
        output = str(Path(self.temp.name) / "out.svg")
        with patch("margin.gui.filedialog.asksaveasfilename", return_value=output), \
             patch.object(core, "generate", side_effect=AssertionError("must not regenerate")):
            self.app.on_save_svg()
        self.assertTrue(Path(output).is_file())
        self.app.reset_image()
        self.assertEqual(self.app.contrast.get(), 1)
        self.assertFalse(self.app.manual_threshold.get())
        self.assertIsNone(self.app._stats)
        self.assertEqual(str(self.app.save_png.cget("state")), "disabled")

    def test_pending_and_failed_generation_disable_export(self):
        self.app.source.set("图片")
        self.app.image_path.set(str(Path(self.temp.name) / "missing.png"))
        self.assertIsNone(self.app._stats)
        self.assertEqual(str(self.app.save_svg.cget("state")), "disabled")
        self.wait_for(lambda: "不存在" in self.app.status.cget("text"))
        self.assertIsNone(self.app._stats)
        self.assertEqual(str(self.app.save_png.cget("state")), "disabled")
        self.app.source.set("文字")
        self.app.setvar(self.app.budget._name, "2.5")
        self.app.on_generate()
        self.assertIn("整数", self.app.status.cget("text"))

    def test_search_fonts_and_change_style(self):
        self.app.family.set("Arial")
        self.app._filter_fonts()
        self.assertTrue(all("arial" in v.casefold() for v in self.app.font_combo.cget("values")))
        self.app.face.set("Italic")
        self.wait_for(lambda: self.app._stats is not None)
        self.assertTrue(self.app._stats.ok)

    def test_latest_change_wins_over_slow_worker(self):
        original = core.generate
        started = threading.Event()
        released = threading.Event()
        def slow(**kwargs):
            if kwargs["text"] == "older":
                started.set()
                released.wait(5)
                # Simulate an uninterruptible operation returning an obsolete success.
                kwargs.pop("cancel", None)
            return original(**kwargs)
        with patch.object(core, "generate", side_effect=slow):
            self.app.text.set("older")
            self.app.on_generate()
            self.wait_for(started.is_set)
            self.app.text.set("newer")
            self.app.on_generate()
            self.assertEqual(str(self.app.save_png.cget("state")), "disabled")
            released.set()
            self.wait_for(lambda: self.app._stats is not None)
            self.assertEqual(self.app._stats.text, "newer")


if __name__ == "__main__":
    unittest.main()
