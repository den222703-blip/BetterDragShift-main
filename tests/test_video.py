"""Tests for dragshift.video. They run real ffmpeg on tiny generated clips (skipped if absent)."""
import subprocess
from pathlib import Path

import pytest

from dragshift import video
from dragshift.util import UserError

pytestmark = pytest.mark.skipif(not video.ffmpeg_available(), reason="ffmpeg/ffprobe not installed")


def _make(path: Path, seconds=3, size="320x240", audio=True, fps=25):
    cmd = [video.find_tool("ffmpeg"), "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate={fps}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    subprocess.run(cmd + [str(path)], check=True)
    return path


@pytest.fixture()
def clip(tmp_path):
    return _make(tmp_path / "clip.mp4")


def test_probe(clip):
    i = video.probe(clip)
    assert (i.width, i.height) == (320, 240)
    assert i.has_audio and 2.8 < i.duration < 3.3


def test_probe_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video at all")
    with pytest.raises(UserError, match="damaged|isn't supported"):
        video.probe(bad)


def test_unsupported_extension(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("x")
    with pytest.raises(UserError, match="supported video"):
        video.compress(f)


def test_trim_precise_and_fast(clip):
    (out,) = video.trim(clip, 1.0, 2.0, precise=True)
    assert out.name == "clip trimmed.mp4" and 0.8 < video.probe(out).duration < 1.3
    (out2,) = video.trim(clip, 0.5, 2.5, precise=False)
    assert out2.exists() and out2 != out and out2.suffix == ".mp4"


def test_trim_validation(clip):
    with pytest.raises(UserError, match="after the start"):
        video.trim(clip, 2.0, 1.0)
    with pytest.raises(UserError, match="outside"):
        video.trim(clip, 99.0, None)


def test_trim_never_overwrites(clip):
    a = video.trim(clip, 0, 1)[0]
    b = video.trim(clip, 0, 1)[0]
    assert a != b and a.exists() and b.exists()


@pytest.mark.parametrize("fmt", ["MP4", "MOV", "MKV", "WEBM", "GIF", "MP3"])
def test_convert_targets(clip, fmt):
    (out,) = video.convert(clip, fmt)
    assert out.suffix == "." + fmt.lower() and out.stat().st_size > 0


def test_convert_mp3_needs_audio(tmp_path):
    silent = _make(tmp_path / "silent.mp4", audio=False)
    with pytest.raises(UserError, match="no audio"):
        video.convert(silent, "MP3")


def test_compress_and_720p(tmp_path):
    big = _make(tmp_path / "big.mp4", size="1920x1080", seconds=2)
    (small,) = video.compress(big)
    assert small.exists()
    (hd,) = video.compress(big, max_height=720, label="720p")
    assert video.probe(hd).height == 720


def test_mute_rotate_speed(clip):
    (m,) = video.mute(clip)
    assert not video.probe(m).has_audio
    (r,) = video.rotate(clip)
    assert (video.probe(r).width, video.probe(r).height) == (240, 320)
    (s,) = video.speed(clip, 2.0)
    assert video.probe(s).duration < 2.0
    with pytest.raises(UserError):
        video.speed(clip, 10)


def test_speed_without_audio_does_not_fail(tmp_path):
    silent = _make(tmp_path / "silent.mp4", audio=False)
    (s,) = video.speed(silent, 2.0)
    assert s.exists()


def test_frame_png(clip):
    (p,) = video.frame(clip)
    (q,) = video.frame(clip, 1.5)
    from PIL import Image
    assert Image.open(p).size == (320, 240) and Image.open(q).size == (320, 240)


def test_frame_bytes_for_preview(clip):
    data = video.frame_png_bytes(clip, 1.0, width=160)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_merge_mixed_sizes_and_audio(tmp_path):
    a = _make(tmp_path / "part1.mp4", seconds=2, size="320x240")
    b = _make(tmp_path / "part2.mp4", seconds=2, size="640x360", audio=False)   # different size, no audio
    (out,) = video.merge([b, a])                                                # order comes from names
    info = video.probe(out)
    assert out.name == "part1 merged.mp4"
    assert (info.width, info.height) == (320, 240) and info.has_audio
    assert 3.5 < info.duration < 4.6


def test_merge_needs_two(clip):
    with pytest.raises(UserError, match="at least two"):
        video.merge([clip])


def test_progress_and_cancel(clip):
    seen = []
    video.convert(clip, "MKV", progress=seen.append)
    assert seen and seen[-1] == 1.0
    with pytest.raises(UserError, match="Cancelled"):
        video.convert(clip, "MOV", cancelled=lambda: True)
    assert not list(clip.parent.glob(".*part*"))          # no temp leftovers


def test_failed_run_leaves_nothing(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"\x00" * 2048)
    with pytest.raises(UserError):
        video.compress(bad)
    assert not list(tmp_path.glob("*compressed*")) and not list(tmp_path.glob(".*part*"))
