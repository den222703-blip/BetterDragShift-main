"""Video tools (ffmpeg backend): convert, compress, trim, merge, mute, rotate, speed, frame.

Like convert.py, every public function takes a source path and returns a list of output
paths written next to the source (via util.sibling, so the "output folder" setting is honoured
and nothing is ever overwritten). Errors are raised as UserError so the toast shows the text.

The encoder settings (CRF, presets, GIF palette, "no audio track" handling) follow the
Sweetch project's FfmpegBackend, which this module replaces for DragShift.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .util import UserError, sibling

# '.ts' / '.mts' are left out on purpose: '.ts' is also TypeScript and would hijack text files.
VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv",
    ".mpg", ".mpeg", ".3gp", ".m2ts",
}
# Convert ring, same set as Sweetch: container/codec presets below.
VIDEO_TARGETS = ["MP4", "MOV", "MKV", "WEBM", "GIF", "MP3"]

VIDEO_CRF = 23           # Sweetch default for re-encodes
COMPRESS_CRF = 28        # Sweetch default for "Compress"
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_SAFE_COPY = {"mp4", "m4v", "mov", "mkv", "webm"}   # containers where stream copy is reliable

ProgressFn = Optional[Callable[[float], None]]
CancelFn = Optional[Callable[[], bool]]


# --------------------------------------------------------------------------- tools

def _candidates(name: str) -> list[Path]:
    exe = name + (".exe" if sys.platform == "win32" else "")
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    out = [base / "bin" / exe, base / exe, Path(sys.executable).parent / "bin" / exe]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "DragShift" / "tools" / exe)
        out.append(Path(local) / "Microsoft" / "WinGet" / "Links" / exe)   # winget install Gyan.FFmpeg
    return out


def find_tool(name: str) -> str:
    """ffmpeg / ffprobe: bundled first, then user tools folder, then PATH."""
    for c in _candidates(name):
        if c.is_file():
            return str(c)
    found = shutil.which(name)
    if found:
        return found
    raise UserError(
        f"{name} not found. Video tools need FFmpeg. "
        "Install it with:  winget install Gyan.FFmpeg  (then restart DragShift)")


def ffmpeg_available() -> bool:
    try:
        find_tool("ffmpeg")
        find_tool("ffprobe")
        return True
    except UserError:
        return False


# --------------------------------------------------------------------------- probe

@dataclass
class Info:
    duration: float      # seconds
    width: int
    height: int
    has_audio: bool
    vcodec: str
    fps: float


def probe(path: str | Path) -> Info:
    p = Path(path)
    if not p.is_file():
        raise UserError("The video file is missing.")
    r = subprocess.run(
        [find_tool("ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(p)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW)
    if r.returncode != 0 or not r.stdout.strip():
        raise UserError("This video is damaged or its format isn't supported.")
    data = json.loads(r.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is None:
        raise UserError("This file has no video track.")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    try:
        dur = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    fps = 0.0
    try:
        n, d = (v.get("avg_frame_rate") or "0/1").split("/")
        fps = float(n) / float(d) if float(d) else 0.0
    except (ValueError, ZeroDivisionError):
        pass
    return Info(dur, int(v.get("width") or 0), int(v.get("height") or 0),
                has_audio, v.get("codec_name") or "", fps)


# --------------------------------------------------------------------------- running ffmpeg

def friendly_error(stderr: str, returncode: int) -> str:
    s = stderr.lower()
    if "does not contain any stream" in s or "matches no streams" in s:
        return "The file has no audio track."
    if "no space left" in s:
        return "Not enough disk space to save the video."
    if "permission denied" in s or "access is denied" in s:
        return "No permission to write there. Check the output folder."
    if "invalid data found" in s or "moov atom not found" in s:
        return "The video is damaged or incomplete."
    if "unknown encoder" in s or "encoder not found" in s or "unrecognized option" in s:
        return "This FFmpeg build can't encode that format. Install the full build (winget install Gyan.FFmpeg)."
    tail = [ln for ln in stderr.strip().splitlines() if ln.strip()][-1:] or [f"exit code {returncode}"]
    return f"FFmpeg failed: {tail[0][:160]}"


def run_ffmpeg(args: list[str], out: Path, *, pre: list[str] | None = None,
               duration: float = 0.0, progress: ProgressFn = None,
               cancelled: CancelFn = None) -> Path:
    """Run `ffmpeg [pre] -i <inputs in args> ... out` into a hidden temp file, then rename.

    `args` must already contain the -i options. Half-written files never appear in Explorer.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.{uuid.uuid4().hex[:8]}.part{out.suffix}")
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", *(pre or []), *args, str(tmp)]
    err = tempfile.TemporaryFile()
    try:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True,
                                    encoding="utf-8", errors="replace",
                                    creationflags=_NO_WINDOW)
        except OSError as e:
            raise UserError(f"Couldn't start FFmpeg: {e}") from e
        try:
            for line in proc.stdout:                       # key=value progress stream
                if cancelled and cancelled():
                    proc.kill()
                    raise UserError("Cancelled.")
                m = re.match(r"out_time_us=(\d+)", line)
                if m and duration > 0 and progress:
                    progress(min(0.99, int(m.group(1)) / 1e6 / duration))
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
        if proc.returncode != 0:
            err.seek(0)
            raise UserError(friendly_error(err.read().decode("utf-8", "replace"), proc.returncode))
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise UserError("FFmpeg produced no output.")
        os.replace(tmp, out)
        if progress:
            progress(1.0)
        return out
    finally:
        err.close()
        try:
            tmp.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- codec presets (from Sweetch)

def h264(crf: int = VIDEO_CRF, preset: str = "medium", vf: str | None = None,
         audio_bitrate: str | None = "160k") -> list[str]:
    chain = ([vf] if vf else []) + ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]   # yuv420p needs even sizes
    args = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-vf", ",".join(chain), "-movflags", "+faststart"]
    if audio_bitrate:
        args += ["-c:a", "aac", "-b:a", audio_bitrate]
    return args


def codec_args(fmt: str, crf: int = VIDEO_CRF) -> list[str]:
    fmt = fmt.lower()
    base = h264(crf)
    return {
        "mp4": base,
        "mov": base,
        "mkv": base,
        "webm": ["-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", "-crf", "32", "-b:v", "0",
                 "-row-mt", "1", "-deadline", "good", "-cpu-used", "4",
                 "-c:a", "libopus", "-b:a", "128k"],
        "gif": ["-vf", "fps=12,scale='min(640,iw)':-2:flags=lanczos,split[a][b];"
                       "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                "-loop", "0"],
        "mp3": ["-vn", "-c:a", "libmp3lame", "-q:a", "2"],
        "m4a": ["-vn", "-c:a", "aac", "-b:a", "192k"],
        "wav": ["-vn", "-c:a", "pcm_s16le"],
    }.get(fmt, base)


def _src(path: str | Path) -> Path:
    p = Path(path)
    if p.suffix.lower() not in VIDEO_EXTS:
        raise UserError(f"{p.suffix or 'This file'} isn't a supported video format.")
    return p


# --------------------------------------------------------------------------- operations

def convert(src, target: str, *, progress: ProgressFn = None, cancelled: CancelFn = None) -> list[Path]:
    """MP4/MOV/MKV/WEBM/GIF re-encode, or MP3 audio-only."""
    p = _src(src)
    info = probe(p)
    fmt = target.lower()
    if fmt == "mp3" and not info.has_audio:
        raise UserError("The video has no audio track.")
    out = sibling(p, "", "." + fmt)
    run_ffmpeg(["-i", str(p), *codec_args(fmt)], out, duration=info.duration,
               progress=progress, cancelled=cancelled)
    return [out]


def compress(src, *, max_height: int | None = None, label: str = "compressed",
             progress: ProgressFn = None, cancelled: CancelFn = None) -> list[Path]:
    """H.264, CRF 28 / slow (Sweetch 'Compress'); max_height=720 gives the '720p' variant."""
    p = _src(src)
    info = probe(p)
    vf = f"scale=-2:'min({max_height},ih)'" if max_height else None
    crf, preset, abr = (VIDEO_CRF, "medium", "160k") if max_height else (COMPRESS_CRF, "slow", "128k")
    out = sibling(p, label, ".mp4")
    run_ffmpeg(["-i", str(p), *h264(crf, preset, vf, abr if info.has_audio else None)], out,
               duration=info.duration, progress=progress, cancelled=cancelled)
    return [out]


def mute(src, *, progress: ProgressFn = None, cancelled: CancelFn = None) -> list[Path]:
    p = _src(src)
    info = probe(p)
    ext = p.suffix.lower().lstrip(".")
    if ext in _SAFE_COPY:
        out = sibling(p, "muted", p.suffix)
        args = ["-i", str(p), "-map", "0:v", "-c:v", "copy", "-an"]
    else:
        out = sibling(p, "muted", ".mp4")
        args = ["-i", str(p), *h264(audio_bitrate=None), "-an"]
    run_ffmpeg(args, out, duration=info.duration, progress=progress, cancelled=cancelled)
    return [out]


def rotate(src, *, clockwise: bool = True, progress: ProgressFn = None,
           cancelled: CancelFn = None) -> list[Path]:
    p = _src(src)
    info = probe(p)
    out = sibling(p, "rotated", ".mp4")
    vf = "transpose=1" if clockwise else "transpose=2"
    run_ffmpeg(["-i", str(p), *h264(vf=vf, audio_bitrate="160k" if info.has_audio else None)], out,
               duration=info.duration, progress=progress, cancelled=cancelled)
    return [out]


def speed(src, factor: float = 2.0, *, progress: ProgressFn = None,
          cancelled: CancelFn = None) -> list[Path]:
    """2x by default. atempo only takes 0.5..2.0 per instance, so chain it for other factors."""
    if not 0.25 <= factor <= 4:
        raise UserError("Speed must be between 0.25x and 4x.")
    p = _src(src)
    info = probe(p)
    tempos, f = [], factor
    while f > 2.0:
        tempos.append(2.0)
        f /= 2.0
    while f < 0.5:
        tempos.append(0.5)
        f /= 0.5
    tempos.append(f)
    args = ["-i", str(p), *h264(vf=f"setpts={1 / factor:.6f}*PTS",
                                audio_bitrate="160k" if info.has_audio else None)]
    if info.has_audio:
        args += ["-af", ",".join(f"atempo={t:.6f}" for t in tempos)]
    out = sibling(p, f"{factor:g}x", ".mp4")
    run_ffmpeg(args, out, duration=info.duration / factor, progress=progress, cancelled=cancelled)
    return [out]


def trim(src, start: float, end: float | None = None, *, precise: bool = True,
         progress: ProgressFn = None, cancelled: CancelFn = None) -> list[Path]:
    """Cut [start, end] seconds. precise=True re-encodes (frame-accurate); False copies streams
    (instant, lossless, but starts at the previous keyframe)."""
    p = _src(src)
    info = probe(p)
    if start < 0 or (info.duration and start >= info.duration):
        raise UserError("The start is outside the video.")
    if end is not None and end <= start:
        raise UserError("The end must come after the start.")
    stop = end if end is not None else info.duration
    if info.duration:
        stop = min(stop, info.duration)
    length = stop - start
    if length <= 0:
        raise UserError("The selected range is empty.")
    ext = p.suffix.lower().lstrip(".")
    pre = ["-ss", f"{start:.3f}"]
    if not precise and ext in _SAFE_COPY:
        out = sibling(p, "trimmed", p.suffix)
        args = ["-i", str(p), "-t", f"{length:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        out = sibling(p, "trimmed", ".mp4")
        args = ["-i", str(p), "-t", f"{length:.3f}",
                *h264(audio_bitrate="160k" if info.has_audio else None)]
    run_ffmpeg(args, out, pre=pre, duration=length, progress=progress, cancelled=cancelled)
    return [out]


def frame(src, t: float | None = None) -> list[Path]:
    """Save one frame as PNG. t=None picks a representative frame (ffmpeg 'thumbnail' filter,
    like Sweetch); otherwise the frame at t seconds. The PNG then opens in the photo tools."""
    p = _src(src)
    info = probe(p)
    out = sibling(p, "frame" if t is None else f"frame {t:.1f}s", ".png")
    if t is None:
        run_ffmpeg(["-i", str(p), "-vf", "thumbnail=150", "-frames:v", "1", "-update", "1"], out)
    else:
        t = max(0.0, min(t, max(0.0, info.duration - 0.05)))
        run_ffmpeg(["-i", str(p), "-frames:v", "1", "-update", "1"], out, pre=["-ss", f"{t:.3f}"])
    return [out]


def frame_png_bytes(src, t: float, width: int = 960) -> bytes:
    """One frame as PNG bytes for the on-screen preview (no file written)."""
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error",
           "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1",
           "-vf", f"scale='min({width},iw)':-2", "-f", "image2pipe", "-vcodec", "png", "-"]
    r = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
    if r.returncode != 0 or not r.stdout:
        raise UserError("Couldn't read a frame from this video.")
    return r.stdout


def _natural_key(path: str | Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(path).name)]


def merge(srcs, *, progress: ProgressFn = None, cancelled: CancelFn = None) -> list[Path]:
    """Join videos in filename order. Clips of different size/codec/audio are normalised to the
    first clip's size, 30 fps, H.264 + stereo AAC (silence is added where a clip has no audio)."""
    paths = sorted((_src(s) for s in srcs), key=_natural_key)
    if len(paths) < 2:
        raise UserError("Select at least two videos to merge.")
    infos = [probe(p) for p in paths]
    w, h = infos[0].width // 2 * 2, infos[0].height // 2 * 2
    total = sum(i.duration for i in infos) or 1.0
    out = sibling(paths[0], "merged", ".mp4")
    work = Path(tempfile.mkdtemp(prefix="dragshift_merge_"))
    try:
        parts, done = [], 0.0
        for idx, (p, info) in enumerate(zip(paths, infos)):
            part = work / f"p{idx:03d}.mp4"
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p")
            if info.has_audio:
                args = ["-i", str(p), "-map", "0:v:0", "-map", "0:a:0"]
            else:
                args = ["-i", str(p), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
            base = done
            run_ffmpeg([*args, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-c:a", "aac", "-ar", "48000", "-ac", "2"], part,
                       duration=info.duration,
                       progress=(lambda f, b=base, d=info.duration:
                                 progress(0.9 * (b + f * d) / total)) if progress else None,
                       cancelled=cancelled)
            parts.append(part)
            done += info.duration
        listing = work / "list.txt"
        listing.write_text(
            "".join("file '" + str(x).replace("\\", "/").replace("'", r"'\''") + "'\n" for x in parts),
            encoding="utf-8")
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
                    "-movflags", "+faststart"], out, cancelled=cancelled)
        if progress:
            progress(1.0)
        return [out]
    finally:
        shutil.rmtree(work, ignore_errors=True)
