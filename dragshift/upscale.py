"""Photo upscaling: 'AI' (Real-ESRGAN via realesrgan-ncnn-vulkan) and 'Fast' (pure Pillow).

AI mode runs the external realesrgan-ncnn-vulkan program (MIT licence, no PyTorch or CUDA needed).
It works only through Vulkan, so it needs a graphics card with a Vulkan driver; the program has no
CPU mode. Fast mode is the guaranteed fallback that works everywhere without a GPU, but it only
resizes with sharpening and can't invent new detail.

Big pictures never go to the engine in one piece. They are cut into overlapping tiles, each tile is
upscaled separately and pasted into the result, so memory stays bounded and a huge photo can't hang
the app. Every failure is turned into a message that says what to do.

Like convert.py: functions take a source path and return a list of output paths written next to it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageFilter

from .util import UserError

SCALES = (2, 4)
AI_MODELS = {"photo": "realesrgan-x4plus", "illustration": "realesrgan-x4plus-anime"}

MAX_SIDE = 16383           # WebP's limit; JPEG/HEIC/AVIF are no more generous
MAX_PIXELS = 150_000_000   # result size (about 600 MB of RAM at 4 bytes per pixel)
TILE = 1024                # picture pixels handed to the engine at once (before the 4x model)
OVERLAP = 32               # extra context around each tile, cut away after upscaling
TILE_TIMEOUT = 10 * 60     # seconds for one tile

DOWNLOAD_URL = ("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
                "realesrgan-ncnn-vulkan-20220424-windows.zip")
_EXE = "realesrgan-ncnn-vulkan" + (".exe" if sys.platform == "win32" else "")
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

ProgressFn = Optional[Callable[[float], None]]
CancelFn = Optional[Callable[[], bool]]


# --------------------------------------------------------------------------- finding the engine

def engine_dirs() -> list[Path]:
    """Folders searched for realesrgan-ncnn-vulkan(.exe), best first."""
    dirs: list[Path] = []
    env = os.environ.get("DRAGSHIFT_REALESRGAN")      # full path to the exe (handy for testing)
    if env:
        dirs.append(Path(env).parent)
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    dirs += [base / "bin" / "realesrgan", Path(sys.executable).parent / "bin" / "realesrgan"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "DragShift" / "tools" / "realesrgan")
    return dirs


def find_realesrgan() -> Path | None:
    env = os.environ.get("DRAGSHIFT_REALESRGAN")
    if env and Path(env).is_file():
        return Path(env)
    for d in engine_dirs():
        if (d / _EXE).is_file():
            return d / _EXE
    return None


def engine_status(model: str = "photo") -> tuple[str, str]:
    """('ok', '') or (reason, message for the user). Reasons: 'no_exe', 'no_models'."""
    exe = find_realesrgan()
    if exe is None:
        dirs = engine_dirs()
        where = "\n".join(f"  {d}" for d in dict.fromkeys([dirs[0], dirs[-1]]))
        return "no_exe", (
            f"The AI engine ({_EXE}) was not found.\n"
            f"Download {DOWNLOAD_URL.rsplit('/', 1)[1]} (github.com/xinntao/Real-ESRGAN, release v0.2.5.0), "
            f"unzip it, and put the .exe, its .dll files and the models folder into one of:\n{where}\n"
            "Or choose Fast mode, which needs nothing.")
    name = AI_MODELS.get(model, AI_MODELS["photo"])
    models = exe.parent / "models"
    for ext in (".param", ".bin"):
        if not (models / (name + ext)).is_file():
            return "no_models", (
                f"The AI engine was found, but models\\{name}{ext} is missing next to it ({exe.parent}).\n"
                "Copy the whole models folder from the Real-ESRGAN download there, or choose Fast mode.")
    return "ok", ""


def ai_available(model: str = "photo") -> bool:
    return engine_status(model)[0] == "ok"


# --------------------------------------------------------------------------- limits

def output_size(size: tuple[int, int], scale: int) -> tuple[int, int]:
    return size[0] * scale, size[1] * scale


def check_limits(size: tuple[int, int], scale: int, method: str = "fast") -> None:
    """Raise UserError with advice if the result would be too large for memory or for file formats."""
    if scale not in SCALES:
        raise UserError("Scale must be 2× or 4×.")
    w, h = output_size(size, scale)
    if max(w, h) > MAX_SIDE:
        raise UserError(f"The result would be {w}×{h}px. The limit is {MAX_SIDE}px per side. "
                        "Try 2× or a smaller picture.")
    if w * h > MAX_PIXELS:
        raise UserError(f"The result would be {w * h / 1e6:.0f} MP, which is too large "
                        f"(the limit is {MAX_PIXELS / 1e6:.0f} MP). Try 2× or a smaller picture.")


# --------------------------------------------------------------------------- Fast (CPU, always works)

def _sharpen(im: Image.Image, radius: float, percent: int) -> Image.Image:
    f = ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=2)
    if im.mode == "RGBA":                      # never sharpen the alpha channel
        r, g, b, a = im.split()
        out = Image.merge("RGB", (r, g, b)).filter(f)
        out.putalpha(a)
        return out
    return im.filter(f)


def fast(im: Image.Image, scale: int) -> Image.Image:
    """Lanczos in 2x steps with a gentle unsharp mask after each: crisper than one big resize."""
    for _ in range({2: 1, 4: 2}[scale]):
        im = im.resize((im.width * 2, im.height * 2), Image.Resampling.LANCZOS)
        im = _sharpen(im, radius=1.2, percent=55)
    return im


# --------------------------------------------------------------------------- AI engine: failures -> advice

def _tail(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1][:160] if lines else ""


def classify(returncode: int, output: str) -> str:
    """'dll' | 'vulkan' | 'memory' | 'input' | 'unknown' from the engine's exit code and messages."""
    s = output.lower()
    if returncode in (3221225781, -1073741515):          # 0xC0000135 STATUS_DLL_NOT_FOUND
        return "dll"
    if any(k in s for k in ("vkcreateinstance", "no vulkan", "invalid gpu", "vulkan-1", "failed to load vulkan",
                            "vkenumeratephysicaldevices")):
        return "vulkan"
    if any(k in s for k in ("vkallocatememory", "out of memory", "vkqueuesubmit", "vkwaitforfences",
                            "device lost", "vkmapmemory")):
        return "memory"
    if any(k in s for k in ("decode image", "decode failed", "failed to open", "cannot open")):
        return "input"
    return "unknown"


def explain(kind: str, returncode: int, output: str) -> str:
    tail = _tail(output)
    said = f"\n(The engine said: {tail})" if tail else ""
    return {
        "dll": ("Windows couldn't load a file that realesrgan-ncnn-vulkan.exe needs (error 0xC0000135). "
                "Keep all .dll files from the download next to the exe, and install the latest "
                "Microsoft Visual C++ Redistributable (x64)." + said),
        "vulkan": ("The AI engine needs Vulkan, and it isn't available on this computer. Update your graphics "
                   "driver (NVIDIA, AMD or Intel). Vulkan is often missing in virtual machines and remote "
                   "desktop sessions. Fast mode works without a GPU." + said),
        "memory": ("The graphics card ran out of video memory, even with small tiles. Close other programs "
                   "that use the GPU, or use Fast mode, or a smaller picture." + said),
        "input": "The AI engine couldn't read this picture. Save it as PNG first, or use Fast mode." + said,
    }.get(kind, f"The AI engine failed (exit code {returncode}). Use Fast mode if this keeps happening." + said)


def explain_oserror(e: OSError) -> str:
    code = getattr(e, "winerror", None)
    if code == 193:
        return ("realesrgan-ncnn-vulkan.exe is damaged or isn't the Windows version. "
                "Download realesrgan-ncnn-vulkan-20220424-windows.zip again.")
    if code in (5, 225, 1260) or isinstance(e, PermissionError):
        return ("Windows or your antivirus blocked realesrgan-ncnn-vulkan.exe. Right-click the exe, open "
                "Properties and tick Unblock, or allow it in your antivirus.")
    return f"Couldn't start the AI engine: {e}"


class _Engine:
    """Runs realesrgan-ncnn-vulkan on one picture at a time (always 4x).

    If a run fails because of video memory (or for an unknown reason) it retries with smaller engine
    tiles (-t, the documented way to use less GPU memory) and then keeps using the size that worked.
    Failures that retrying can't fix (no Vulkan, missing DLL, unreadable input) stop immediately.
    """
    ENGINE_TILES = (0, 128, 64)          # 0 = the engine picks automatically

    def __init__(self, exe: Path, model_name: str):
        self.exe, self.model_name, self._i = exe, model_name, 0

    def _spawn(self, src: Path, dst: Path, tile: int) -> subprocess.CompletedProcess:
        cmd = [str(self.exe), "-i", str(src), "-o", str(dst), "-n", self.model_name, "-s", "4",
               "-f", "png", "-m", str(self.exe.parent / "models")]
        if tile:
            cmd += ["-t", str(tile)]
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              cwd=str(self.exe.parent), creationflags=_NO_WINDOW, timeout=TILE_TIMEOUT)

    def run(self, rgb: Image.Image, workdir: Path) -> Image.Image:
        src, dst = workdir / "in.png", workdir / "out.png"
        rgb.save(src, "PNG")
        last = None
        for i in range(self._i, len(self.ENGINE_TILES)):
            dst.unlink(missing_ok=True)
            try:
                last = self._spawn(src, dst, self.ENGINE_TILES[i])
            except subprocess.TimeoutExpired:
                raise UserError("AI upscaling took too long and was stopped. Use a smaller picture "
                                "or Fast mode.") from None
            except OSError as e:
                raise UserError(explain_oserror(e)) from e
            if last.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
                self._i = i
                out = Image.open(dst)
                out.load()
                return out.convert("RGB")
            if classify(last.returncode, (last.stderr or "") + (last.stdout or "")) not in ("memory", "unknown"):
                break
        text = (last.stderr or "") + (last.stdout or "")
        raise UserError(explain(classify(last.returncode, text), last.returncode, text))


# --------------------------------------------------------------------------- tiling

def _splits(length: int, tile: int) -> list[tuple[int, int]]:
    """Equal-sized spans covering `length`, none larger than `tile` (so no tiny leftover strips)."""
    n = max(1, -(-length // tile))
    size = -(-length // n)
    return [(i * size, min(length, (i + 1) * size)) for i in range(n) if i * size < length]


def ai(im: Image.Image, scale: int, model: str = "photo", progress: ProgressFn = None,
       cancelled: CancelFn = None) -> Image.Image:
    """Real-ESRGAN, tile by tile. The network is a fixed 4x model, so 2x is each 4x tile halved."""
    status, message = engine_status(model)
    if status != "ok":
        raise UserError(message)
    engine = _Engine(find_realesrgan(), AI_MODELS.get(model, AI_MODELS["photo"]))

    mode = im.mode
    alpha = im.getchannel("A") if mode == "RGBA" else None
    rgb = im.convert("RGB")
    w, h = rgb.size
    canvas = Image.new("RGB", (w * scale, h * scale))
    boxes = [(x0, y0, x1, y1) for (y0, y1) in _splits(h, TILE) for (x0, x1) in _splits(w, TILE)]

    with tempfile.TemporaryDirectory(prefix="dragshift_ai_") as td:
        for n, (x0, y0, x1, y1) in enumerate(boxes):
            if cancelled and cancelled():
                raise UserError("Cancelled.")
            px0, py0 = max(0, x0 - OVERLAP), max(0, y0 - OVERLAP)       # padded crop = the tile + context
            px1, py1 = min(w, x1 + OVERLAP), min(h, y1 + OVERLAP)
            up = engine.run(rgb.crop((px0, py0, px1, py1)), Path(td))
            if up.size != ((px1 - px0) * 4, (py1 - py0) * 4):
                raise UserError("The AI engine returned a picture of the wrong size. Try Fast mode.")
            if scale == 2:                                              # halve before cutting: keeps context
                up = up.resize(((px1 - px0) * 2, (py1 - py0) * 2), Image.Resampling.LANCZOS)
            left, top = (x0 - px0) * scale, (y0 - py0) * scale
            canvas.paste(up.crop((left, top, left + (x1 - x0) * scale, top + (y1 - y0) * scale)),
                         (x0 * scale, y0 * scale))
            if progress:
                progress((n + 1) / len(boxes))

    if alpha is not None:                                               # the model only sees colour
        canvas.putalpha(alpha.resize(canvas.size, Image.Resampling.LANCZOS))
    elif mode == "L":
        canvas = canvas.convert("L")
    return canvas


# --------------------------------------------------------------------------- public API

def enhance(im: Image.Image, scale: int, method: str = "fast", model: str = "photo",
            progress: ProgressFn = None, cancelled: CancelFn = None) -> Image.Image:
    check_limits(im.size, scale, method)
    if method == "ai":
        return ai(im, scale, model, progress, cancelled)
    return fast(im, scale)


def upscale_file(src, scale: int = 2, method: str = "fast", model: str = "photo",
                 progress: ProgressFn = None, cancelled: CancelFn = None) -> list[Path]:
    """Upscale one picture; writes '<name> Upscaled 2x.<ext>' next to it (never overwrites)."""
    from . import convert, imaging          # lazy: keeps this module importable without PDF libs
    im = convert.open_image(src)
    out = enhance(im, scale, method, model, progress, cancelled)
    return [imaging.save_like(out, src, f"Upscaled {scale}x")]
