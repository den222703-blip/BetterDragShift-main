"""Tests for dragshift.upscale.

The AI path is exercised with a stand-in 'realesrgan-ncnn-vulkan' script that has the same command
line (-i -o -n -s -f -m -t). The stand-in enlarges 4x with NEAREST, which has no neighbourhood, so a
correctly tiled result must be *identical* to enlarging the whole picture in one go. That proves the
tile cutting/pasting math. These tests cover DragShift's wrapper (tiles, retries, messages), not the
neural network itself. The stand-in is a script, so those tests are skipped on Windows.
"""
import contextlib
import os
import stat
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from dragshift import upscale
from dragshift.util import UserError

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="stand-in engine is a script")


def _picture(size=(120, 80), mode="RGB"):
    im = Image.new("RGB", size, (30, 30, 40))
    d = ImageDraw.Draw(im)
    d.rectangle([10, 10, size[0] - 10, size[1] - 10], outline=(240, 240, 240), width=2)
    d.line([0, 0, size[0], size[1]], fill=(255, 80, 60), width=2)
    d.ellipse([size[0] // 3, size[1] // 3, size[0] // 2, size[1] // 2], fill=(80, 200, 255))
    return im.convert(mode)


def _lap_var(im: Image.Image) -> float:
    k = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1)
    px = list(im.convert("L").filter(k).getdata())
    mean = sum(px) / len(px)
    return sum((p - mean) ** 2 for p in px) / len(px)


@contextlib.contextmanager
def _env(name, value):
    old = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


@contextlib.contextmanager
def _tiles(tile, overlap):
    old = upscale.TILE, upscale.OVERLAP
    upscale.TILE, upscale.OVERLAP = tile, overlap
    try:
        yield
    finally:
        upscale.TILE, upscale.OVERLAP = old


def _fake_engine(root: Path, mode="ok", models=True) -> Path:
    """A script with realesrgan-ncnn-vulkan's CLI. mode.txt picks its behaviour; calls.log records runs.

    modes: ok | vulkan | mem_unless_small_tile | mem_always | crash
    """
    d = root / "realesrgan"
    (d / "models").mkdir(parents=True)
    if models:
        for name in ("realesrgan-x4plus", "realesrgan-x4plus-anime"):
            for ext in (".param", ".bin"):
                (d / "models" / (name + ext)).write_bytes(b"x")
    (d / "mode.txt").write_text(mode)
    exe = d / "realesrgan-ncnn-vulkan"
    exe.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys, pathlib
        from PIL import Image
        a = sys.argv[1:]
        here = pathlib.Path(__file__).parent
        with open(here / "calls.log", "a") as f:
            f.write(" ".join(a) + "\\n")
        mode = (here / "mode.txt").read_text().strip()
        tile = int(a[a.index("-t") + 1]) if "-t" in a else 0
        if mode == "vulkan":
            print("[0 NVIDIA] vkCreateInstance failed -9", file=sys.stderr); sys.exit(1)
        if mode == "mem_always" or (mode == "mem_unless_small_tile" and not (0 < tile <= 128)):
            print("vkAllocateMemory failed -2", file=sys.stderr); sys.exit(1)
        if mode == "crash":
            print("something unexpected", file=sys.stderr); sys.exit(2)
        im = Image.open(a[a.index("-i") + 1]).convert("RGB")
        im.resize((im.width * 4, im.height * 4), Image.NEAREST).save(a[a.index("-o") + 1])
    """))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def _calls(exe: Path) -> list[str]:
    log = exe.parent / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


# ---------------------------------------------------------------- Fast

@pytest.mark.parametrize("scale", [2, 4])
def test_fast_sizes(scale):
    assert upscale.fast(_picture(), scale).size == (120 * scale, 80 * scale)


def test_fast_is_not_blurrier_than_plain_resize():
    src = _picture()
    plain = src.resize((240, 160), Image.Resampling.LANCZOS)
    assert _lap_var(upscale.fast(src, 2)) > _lap_var(plain)


def test_fast_keeps_alpha_untouched():
    src = _picture(mode="RGBA")
    src.putpixel((0, 0), (10, 20, 30, 0))
    out = upscale.fast(src, 2)
    assert out.mode == "RGBA" and out.getpixel((0, 0))[3] == 0 and out.getpixel((100, 70))[3] == 255


def test_fast_grayscale():
    assert upscale.fast(_picture(mode="L"), 2).mode == "L"


# ---------------------------------------------------------------- limits

def test_limits_side_and_pixels():
    with pytest.raises(UserError, match="limit is"):
        upscale.check_limits((9000, 100), 2)
    with pytest.raises(UserError, match="too large"):
        upscale.check_limits((7000, 7000), 2)                  # 196 MP, sides are fine
    with pytest.raises(UserError, match="too large"):
        upscale.check_limits((4000, 3000), 4, "ai")            # 192 MP
    upscale.check_limits((4000, 3000), 2, "ai")                # 12 MP photo at 2x: fine, tiles handle it
    upscale.check_limits((3000, 2000), 4, "fast")


def test_limits_bad_scale():
    with pytest.raises(UserError, match="2× or 4×"):
        upscale.check_limits((10, 10), 3)


# ---------------------------------------------------------------- tiling math

def test_splits_are_balanced_and_cover_everything():
    for length in (1, 31, 1024, 1025, 3000, 4032):
        spans = upscale._splits(length, 1024)
        assert spans[0][0] == 0 and spans[-1][1] == length
        assert all(a1 == b0 for (_, a1), (b0, _) in zip(spans, spans[1:]))     # contiguous
        assert all(b - a <= 1024 for a, b in spans)
        if len(spans) > 1:
            assert min(b - a for a, b in spans) > 1024 // 2 - 2                # no tiny leftover strip


# ---------------------------------------------------------------- AI wrapper

@posix_only
@pytest.mark.parametrize("tile,overlap", [(100, 16), (64, 8), (1024, 32), (37, 5)])
def test_tiled_result_equals_untiled_result(tmp_path, tile, overlap):
    exe = _fake_engine(tmp_path)
    src = _picture((300, 210))
    expected = src.resize((1200, 840), Image.NEAREST)
    with _env("DRAGSHIFT_REALESRGAN", str(exe)), _tiles(tile, overlap):
        out = upscale.ai(src, 4)
    assert out.size == (1200, 840)
    assert ImageChops.difference(out, expected).getbbox() is None              # bit-identical


@posix_only
def test_tiled_2x_matches_whole_image_within_rounding(tmp_path):
    exe = _fake_engine(tmp_path)
    src = _picture((300, 210))
    whole = src.resize((1200, 840), Image.NEAREST).resize((600, 420), Image.Resampling.LANCZOS)
    with _env("DRAGSHIFT_REALESRGAN", str(exe)), _tiles(100, 32):
        out = upscale.ai(src, 2)
    assert out.size == (600, 420)
    diff = ImageChops.difference(out, whole)
    assert max(hi for _, hi in diff.getextrema()) <= 1


@posix_only
def test_big_picture_is_split_into_tiles_and_small_one_is_not(tmp_path):
    exe = _fake_engine(tmp_path)
    with _env("DRAGSHIFT_REALESRGAN", str(exe)):
        with _tiles(100, 16):
            upscale.ai(_picture((300, 210)), 4)
        assert len(_calls(exe)) == 9                                           # 3 x 3 tiles
        (exe.parent / "calls.log").unlink()
        upscale.ai(_picture((120, 80)), 2)                                     # default 1024 tile
        assert len(_calls(exe)) == 1


@posix_only
def test_progress_and_cancel(tmp_path):
    exe = _fake_engine(tmp_path)
    seen = []
    with _env("DRAGSHIFT_REALESRGAN", str(exe)), _tiles(100, 16):
        upscale.ai(_picture((300, 210)), 4, progress=seen.append)
        assert seen[-1] == 1.0 and len(seen) == 9 and seen == sorted(seen)
        n = {"v": 0}

        def cancel():
            n["v"] += 1
            return n["v"] > 2
        with pytest.raises(UserError, match="Cancelled"):
            upscale.ai(_picture((300, 210)), 4, cancelled=cancel)


@posix_only
def test_engine_arguments(tmp_path):
    exe = _fake_engine(tmp_path)
    with _env("DRAGSHIFT_REALESRGAN", str(exe)):
        upscale.ai(_picture(), 4, "illustration")
    args = _calls(exe)[0]
    assert "-n realesrgan-x4plus-anime" in args and "-s 4" in args and "-f png" in args
    assert "-m " in args and "models" in args and "-t" not in args.split()     # automatic tile size first


@posix_only
def test_alpha_and_grayscale_survive(tmp_path):
    exe = _fake_engine(tmp_path)
    rgba = _picture(mode="RGBA")
    rgba.paste((0, 0, 0, 0), (0, 0, 8, 8))
    with _env("DRAGSHIFT_REALESRGAN", str(exe)):
        out = upscale.ai(rgba, 2)
        gray = upscale.ai(_picture(mode="L"), 2)
    assert out.mode == "RGBA" and out.size == (240, 160)
    assert out.getpixel((2, 2))[3] == 0 and out.getpixel((200, 100))[3] == 255
    assert gray.mode == "L"


@posix_only
def test_no_temp_files_left_even_after_failure(tmp_path):
    exe = _fake_engine(tmp_path, mode="crash")
    before = set(Path(tempfile.gettempdir()).glob("dragshift_ai_*"))
    with _env("DRAGSHIFT_REALESRGAN", str(exe)):
        with pytest.raises(UserError):
            upscale.ai(_picture(), 4)
    assert set(Path(tempfile.gettempdir()).glob("dragshift_ai_*")) == before


# ---------------------------------------------------------------- retries and messages

@posix_only
def test_low_video_memory_retries_with_smaller_tiles_then_remembers(tmp_path):
    exe = _fake_engine(tmp_path, mode="mem_unless_small_tile")
    with _env("DRAGSHIFT_REALESRGAN", str(exe)), _tiles(100, 16):
        out = upscale.ai(_picture((300, 210)), 4)
    calls = _calls(exe)
    assert out.size == (1200, 840)
    assert "-t" not in calls[0].split() and "-t 128" in calls[1]               # first fails, second succeeds
    assert all("-t 128" in c for c in calls[1:]) and len(calls) == 1 + 9       # later tiles go straight to 128


@posix_only
def test_out_of_video_memory_everywhere_gives_clear_message(tmp_path):
    exe = _fake_engine(tmp_path, mode="mem_always")
    with _env("DRAGSHIFT_REALESRGAN", str(exe)):
        with pytest.raises(UserError, match="video memory") as e:
            upscale.ai(_picture(), 4)
    assert "Fast" in str(e.value) and len(_calls(exe)) == 3                    # auto, 128, 64


@posix_only
def test_no_vulkan_gives_clear_message_without_pointless_retries(tmp_path):
    exe = _fake_engine(tmp_path, mode="vulkan")
    with _env("DRAGSHIFT_REALESRGAN", str(exe)):
        with pytest.raises(UserError) as e:
            upscale.ai(_picture(), 4)
    msg = str(e.value)
    assert "Vulkan" in msg and "graphics driver" in msg and "Fast" in msg and "vkCreateInstance" in msg
    assert len(_calls(exe)) == 1


@posix_only
def test_unknown_crash_is_reported_with_exit_code(tmp_path):
    exe = _fake_engine(tmp_path, mode="crash")
    with _env("DRAGSHIFT_REALESRGAN", str(exe)):
        with pytest.raises(UserError, match="exit code 2"):
            upscale.ai(_picture(), 4)


def test_classify_known_failures():
    assert upscale.classify(3221225781, "") == "dll"
    assert upscale.classify(-1073741515, "") == "dll"
    assert upscale.classify(1, "vkCreateInstance failed") == "vulkan"
    assert upscale.classify(1, "invalid gpu device") == "vulkan"
    assert upscale.classify(1, "vkAllocateMemory failed -2") == "memory"
    assert upscale.classify(1, "decode image /x/in.png failed") == "input"
    assert upscale.classify(1, "who knows") == "unknown"
    assert "Visual C++" in upscale.explain("dll", 3221225781, "")


def test_oserror_messages():
    assert "Unblock" in upscale.explain_oserror(PermissionError(13, "denied"))
    e = OSError(193, "not a valid Win32 application")
    e.winerror = 193
    assert "Windows version" in upscale.explain_oserror(e)
    assert "Couldn't start" in upscale.explain_oserror(OSError(2, "gone"))


# ---------------------------------------------------------------- engine lookup messages

def test_engine_not_found_message_names_file_and_folders(tmp_path):
    with _env("DRAGSHIFT_REALESRGAN", str(tmp_path / "nope" / "realesrgan-ncnn-vulkan")):
        status, msg = upscale.engine_status()
        assert not upscale.ai_available()
        with pytest.raises(UserError) as e:
            upscale.ai(_picture(), 4)
    assert status == "no_exe" and str(e.value) == msg
    assert "was not found" in msg and "realesrgan-ncnn-vulkan-20220424-windows.zip" in msg
    assert "Fast" in msg and "bin" in msg


@posix_only
def test_missing_models_folder_is_a_different_message(tmp_path):
    exe = _fake_engine(tmp_path, models=False)
    with _env("DRAGSHIFT_REALESRGAN", str(exe)):
        status, msg = upscale.engine_status()
    assert status == "no_models" and "realesrgan-x4plus.param" in msg and "models folder" in msg


@posix_only
def test_missing_one_model_names_it(tmp_path):
    exe = _fake_engine(tmp_path)
    (exe.parent / "models" / "realesrgan-x4plus-anime.bin").unlink()
    with _env("DRAGSHIFT_REALESRGAN", str(exe)):
        assert upscale.engine_status("photo") == ("ok", "")
        status, msg = upscale.engine_status("illustration")
    assert status == "no_models" and "realesrgan-x4plus-anime.bin" in msg


# ---------------------------------------------------------------- files

def test_upscale_file_writes_next_to_source_without_overwriting(tmp_path):
    src = tmp_path / "photo.jpg"
    _picture((200, 150)).save(src, "JPEG", quality=95)
    (a,) = upscale.upscale_file(src, 2, "fast")
    (b,) = upscale.upscale_file(src, 2, "fast")
    assert a.parent == tmp_path and a.suffix == ".jpg" and a != b and "Upscaled 2x" in a.name
    assert Image.open(a).size == (400, 300) and src.exists()


def test_upscale_file_png_keeps_transparency(tmp_path):
    src = tmp_path / "logo.png"
    im = _picture((60, 40), "RGBA")
    im.paste((0, 0, 0, 0), (0, 0, 8, 8))
    im.save(src)
    (out,) = upscale.upscale_file(src, 4, "fast")
    res = Image.open(out)
    assert res.size == (240, 160) and res.mode == "RGBA" and res.getpixel((2, 2))[3] == 0


@posix_only
def test_upscale_file_with_ai_engine(tmp_path):
    exe = _fake_engine(tmp_path / "eng")
    src = tmp_path / "photo.png"
    _picture((90, 60)).save(src)
    with _env("DRAGSHIFT_REALESRGAN", str(exe)):
        (out,) = upscale.upscale_file(src, 2, "ai")
    assert Image.open(out).size == (180, 120) and "Upscaled 2x" in out.name
