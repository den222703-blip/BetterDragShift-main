"""The radial menu is only useful if app.py hands it rings and routes every petal.

RadialMenu contract (see radial.py): build_rings(paths) -> {ring name: [Item, ...]} and an
`activated(item, paths)` signal that feeds run_item. If either side breaks, the menu opens with
empty petals and says "Can't convert .jpeg". These tests catch that without a display.
"""
import contextlib
from pathlib import Path

import pytest

from dragshift import app as ds_app
from dragshift import tools, upscale_tools, video, video_tools
from dragshift.radial import Item


def rings_for(*names):
    return ds_app.DragShiftApp.build_rings(None, [str(Path("/tmp") / n) for n in names])


def labels(rings, ring):
    return [i.label for i in rings.get(ring, [])]


@pytest.mark.parametrize("name", ["a.jpeg", "a.jpg", "a.png", "a.webp", "a.heic", "a.bmp", "a.tiff"])
def test_image_rings_are_not_empty(name):
    rings = rings_for(name)
    assert rings["Convert"], f"no convert targets for {name}"
    tool_labels = labels(rings, "Tools")
    assert "Compress" in tool_labels and "Crop" in tool_labels and "Upscale" in tool_labels
    assert tool_labels.index("Upscale") == tool_labels.index("Resize") + 1


@pytest.mark.parametrize("name", ["a.mp4", "a.mov", "a.mkv", "a.webm", "a.avi"])
def test_video_rings_are_video_rings(name):
    rings = rings_for(name)
    tool_labels = labels(rings, "Tools")
    assert "Trim" in tool_labels and "Compress" in tool_labels
    assert "Crop" not in tool_labels and "Upscale" not in tool_labels   # photo tools stay off videos
    assert Path(name).suffix.lstrip(".").upper() not in labels(rings, "Convert")


def test_pdf_ring_and_merge_only_for_several_files():
    one = rings_for("a.pdf")
    assert "Split" in labels(one, "Tools") and "Merge" not in labels(one, "Tools")
    many = rings_for("a.pdf", "b.pdf")
    assert "Merge" in labels(many, "Tools")


def test_documents_have_convert_only():
    for name in ("a.docx", "a.txt"):
        rings = rings_for(name)
        assert rings["Convert"] and "Tools" not in rings


def test_unknown_file_is_unsupported_not_a_crash():
    assert rings_for("a.xyz") == {"Unsupported": []}


def test_several_images_offer_one_pdf():
    rings = rings_for("a.png", "b.png")
    pdf = next(i for i in rings["Convert"] if i.label == "PDF")
    assert "2 images" in pdf.hint


# ------------------------------------------------------------------ run_item routing

class _Toasts:
    def __init__(self):
        self.errors = []

    def error(self, title, detail):
        self.errors.append((title, detail))


class FakeApp:
    """Records what run_item asks the real DragShiftApp to do (no Qt needed)."""

    def __init__(self):
        self.submitted, self.windows = [], []
        self.toasts = _Toasts()
        self.notify = lambda *a: None

    def submit(self, action, srcs, fn, working="", extra=""):
        self.submitted.append((action, list(srcs)))

    def open_window(self, w):
        self.windows.append(w)


class _Window:
    def __init__(self, *args, **kwargs):
        self.args = args


@contextlib.contextmanager
def fake_windows():
    """Swap every window class for a recorder so no QApplication is required."""
    swaps = [(tools, n) for n in ("CropWindow", "AddBgWindow", "AnnotateWindow", "RedactWindow", "EditWindow",
                                  "MetadataWindow", "CompressWindow", "ResizeWindow", "ProtectWindow")]
    swaps += [(upscale_tools, "UpscaleWindow"), (video_tools, "TrimWindow")]
    swaps += [(video, "find_tool"), (video, "probe")]
    old = [(m, n, getattr(m, n)) for m, n in swaps]
    for m, n in swaps:
        setattr(m, n, (lambda *a, **k: "ffmpeg") if n == "find_tool" else
                (lambda *a, **k: None) if n == "probe" else _Window)
    try:
        yield
    finally:
        for m, n, v in old:
            setattr(m, n, v)


@pytest.mark.parametrize("names", [("a.jpeg",), ("a.png", "b.png"), ("a.pdf",), ("a.pdf", "b.pdf"),
                                   ("a.mp4",), ("a.mp4", "b.mov")])
def test_every_petal_is_routed(names):
    paths = [str(Path("/tmp") / n) for n in names]
    rings = ds_app.DragShiftApp.build_rings(None, paths)
    with fake_windows():
        for ring, items in rings.items():
            for item in items:
                fake = FakeApp()
                ds_app.DragShiftApp.run_item(fake, item, paths)
                assert not fake.toasts.errors, f"{ring}/{item.label}: {fake.toasts.errors}"
                assert fake.submitted or fake.windows, f"{ring}/{item.label} did nothing"


def test_upscale_petal_opens_the_upscale_window():
    with fake_windows():
        fake = FakeApp()
        ds_app.DragShiftApp.run_item(fake, Item("Upscale", key="tool:Upscale"), ["/tmp/a.jpeg"])
        assert len(fake.windows) == 1 and isinstance(fake.windows[0], upscale_tools.UpscaleWindow)


def test_trim_petal_opens_the_trim_window():
    with fake_windows():
        fake = FakeApp()
        ds_app.DragShiftApp.run_item(fake, Item("Trim", key="tool:Trim"), ["/tmp/a.mp4"])
        assert len(fake.windows) == 1 and isinstance(fake.windows[0], video_tools.TrimWindow)
