"""The 'Upscale' photo tool: window + hook for the radial menu.

app.py adds one petal ('Upscale') to the image ring and hands `tool:Upscale` to run_item() below.
The window offers 2x / 4x and two methods:
  AI    Real-ESRGAN (needs realesrgan-ncnn-vulkan and a Vulkan graphics driver)
  Fast  sharper resize, works everywhere
If the AI engine can't be used, the window says why and falls back to Fast.
"""
from __future__ import annotations

from PIL import Image
from PySide6.QtWidgets import QLabel

from . import upscale
from .tools import ToolWindow, section, toggle_row
from .util import UserError

KEY = "Upscale"
GLYPH = "✦"
HINT = "Sharper · 2× or 4×"


def _oriented_size(path) -> tuple[int, int]:
    """Pixel size as the picture is displayed (EXIF rotation applied), header-only read."""
    with Image.open(path) as im:
        w, h = im.size
        if im.getexif().get(274, 1) in (5, 6, 7, 8):
            w, h = h, w
    return w, h


class UpscaleWindow(ToolWindow):
    tool_name = KEY

    def __init__(self, paths, notify):
        super().__init__("Upscale photo", paths, notify, size=(620, 480))
        self.ai_state, self.ai_message = upscale.engine_status()
        self.ai_ok = self.ai_state == "ok"
        sizes = []
        for p in self.paths:
            try:
                sizes.append(_oriented_size(p))
            except Exception:
                pass
        # judge the whole batch by its largest picture
        self.px = max(sizes, key=lambda s: s[0] * s[1]) if sizes else (0, 0)

        self.body.addWidget(section("Scale"))
        row, self.scale_grp = toggle_row(["2×", "4×"], 0)
        self.body.addLayout(row)

        self.body.addWidget(section("Method"))
        row, self.method_grp = toggle_row(["AI · best quality", "Fast"], 0 if self.ai_ok else 1)
        self.body.addLayout(row)
        if not self.ai_ok:
            self.method_grp.button(0).setEnabled(False)

        self.body.addWidget(section("Picture type (AI)"))
        row, self.model_grp = toggle_row(["Photo", "Illustration / anime"], 0)
        self.body.addLayout(row)

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.body.addWidget(self.info)
        self.note = QLabel(
            "AI adds believable detail using your graphics card. Big pictures are processed in tiles, "
            "so they won't freeze the app, but can take a while." if self.ai_ok else
            f"AI is not available: {self.ai_message}\nFast mode (a sharper resize, no new detail) still works.")
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        self.body.addWidget(self.note)
        self.body.addStretch(1)
        self.body.addLayout(self.footer("Upscale", self.save))

        for grp in (self.scale_grp, self.method_grp, self.model_grp):
            grp.idClicked.connect(lambda *_: self._refresh())
        self._refresh()

    # ---- state ---------------------------------------------------------
    def _state(self) -> tuple[int, str, str]:
        scale = (2, 4)[max(0, self.scale_grp.checkedId())]
        method = "ai" if (self.ai_ok and self.method_grp.checkedId() == 0) else "fast"
        model = ("photo", "illustration")[max(0, self.model_grp.checkedId())]
        return scale, method, model

    def _refresh(self) -> None:
        scale, method, model = self._state()
        for b in self.model_grp.buttons():
            b.setEnabled(method == "ai")
        w, h = self.px
        if not (w and h):
            self.info.setText("Couldn't read the picture size.")
            return
        nw, nh = upscale.output_size((w, h), scale)
        label = f"{w}×{h}  →  {nw}×{nh}   ({w * h / 1e6:.1f} MP → {nw * nh / 1e6:.1f} MP)"
        if len(self.paths) > 1:
            label = f"Largest of {len(self.paths)} pictures:  " + label
        problem = ""
        if method == "ai" and model != "photo":
            status, message = upscale.engine_status(model)       # e.g. the anime model file is missing
            if status != "ok":
                problem = message
        try:
            upscale.check_limits((w, h), scale, method)
        except UserError as e:
            problem = str(e)
        self.info.setText(f"{label}\n{problem}" if problem else label)
        self.primary_btn.setEnabled(not problem)

    # ---- action --------------------------------------------------------
    def save(self) -> None:
        scale, method, model = self._state()
        paths = [str(p) for p in self.paths]       # plain values: this runs off the UI thread

        def work():
            return [o for p in paths for o in upscale.upscale_file(p, scale, method, model)]

        self.run(work, f"Upscaling {scale}×…", f"{scale}×")


def run_item(app, paths) -> None:
    """Called by app.run_item for the 'tool:Upscale' petal."""
    try:
        app.open_window(UpscaleWindow(paths, app.notify))
    except UserError as e:
        app.toasts.error("Couldn't open Upscale", str(e))
    except Exception as e:                           # never take the tray app down
        import traceback
        traceback.print_exc()
        app.toasts.error("Couldn't open Upscale", f"Unexpected error: {e}")
