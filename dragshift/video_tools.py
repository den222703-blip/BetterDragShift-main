"""Video in the radial menu and the Trim window.

Plugs into the existing flow without touching the photo tools:
    util.category(path) == 'video'  ->  video_tools.build_rings(paths)   (app.build_rings)
                                    ->  video_tools.run_item(app, item, paths)   (app.run_item)

Preview/playback uses QtMultimedia's QVideoSink and paints frames itself. DragShift windows are
frameless + translucent, where a native QVideoWidget can render black on Windows. If QtMultimedia
isn't bundled (or the codec fails), the window falls back to ffmpeg still frames while scrubbing,
so Trim keeps working either way, just without the Play button.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QImage, QKeySequence, QLinearGradient, QPainter,
                           QPainterPath, QPen, QShortcut)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from . import video
from .radial import Item
from .tools import ToolWindow, toggle_row
from .util import UserError, category

try:  # optional: keeps the build small if QtMultimedia is excluded in the .spec
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
    HAVE_QTMM = True
except Exception:  # pragma: no cover - depends on the build
    HAVE_QTMM = False


# --------------------------------------------------------------------------- radial menu data
# (label, action key, glyph, hint). The *key* is what report.summarize() sees as the notification
# title. 'Rotate' and 'Merge' are PDF-specific there, hence 'Rotate video' / 'Join'.
_TOOLS = [
    ("Trim",     "Trim",         "✂", "Cut the start & end"),
    ("Compress", "Compress",     "⇲", "Shrink file size"),
    ("720p",     "720p",         "⤢", "Smaller resolution"),
    ("Mute",     "Mute",         "⊘", "Remove the sound"),
    ("Rotate",   "Rotate video", "⟳", "Turn 90° right"),
    ("Speed",    "Speed 2×",     "»", "Twice as fast"),
    ("Frame",    "Frame",        "◎", "Save a still as PNG"),
]
_BATCH_OK = {"Compress", "720p", "Mute", "Rotate video", "Speed 2×", "Frame"}   # no window needed
_JOIN = ("Join", "Join", "⧉", "Merge into one video")
_FORMAT_HINTS = {
    "MP4": "Small, universal", "MOV": "Apple · editing", "MKV": "Flexible container",
    "WEBM": "Web · open codec", "GIF": "Looping animation", "MP3": "Audio only",
}
_SAME = {"M4V": "MP4"}


def _is_video(p) -> bool:
    return category(p) == "video"


def build_rings(paths) -> dict[str, list[Item]]:
    """Same shape as DragShiftApp.build_rings: {ring name: [Item, ...]}."""
    same = [p for p in paths if _is_video(p)]
    cur = Path(same[0]).suffix.lstrip(".").upper() if same else ""
    cur = _SAME.get(cur, cur)
    convert = [Item(t, key=f"convert:{t}", hint=_FORMAT_HINTS.get(t, ""))
               for t in video.VIDEO_TARGETS if t != cur]
    tools = []
    if len(same) > 1:
        tools.append(Item(_JOIN[0], _JOIN[2], key=f"tool:{_JOIN[1]}", hint=_JOIN[3]))
    for label, key, glyph, hint in _TOOLS:
        if len(same) > 1 and key not in _BATCH_OK:
            continue
        tools.append(Item(label, glyph, key=f"tool:{key}", hint=hint))
    return {"Convert": convert, "Tools": tools}


# --------------------------------------------------------------------------- dispatcher

def _each(fn, paths, **kw):
    """fn(path, **kw) -> list[Path], applied to every file, flattened. Runs in the worker thread."""
    paths = list(paths)
    return lambda: [o for p in paths for o in fn(p, **kw)]


def run_item(app, item, paths) -> None:
    """Handle a click/drop on a video ring item. Mirrors DragShiftApp.run_item."""
    paths = [str(p) for p in paths if _is_video(p)]
    kind, name = item.key.split(":", 1)
    try:
        video.find_tool("ffmpeg")                  # fail early, with the install hint
        if kind == "convert":
            app.submit(item.key, paths, _each(video.convert, paths, target=name),
                       f"Converting to {name}…")
        elif name == "Trim":
            info = video.probe(paths[0])           # bad file -> toast, no half-built window
            app.open_window(TrimWindow(paths[0], app.notify, info))
        elif name == "Join":
            app.submit("tool:Join", paths, lambda: video.merge(paths), f"Joining {len(paths)} videos…")
        else:
            jobs = {
                "Compress":     (_each(video.compress, paths), "Compressing…"),
                "720p":         (_each(video.compress, paths, max_height=720, label="720p"), "Scaling to 720p…"),
                "Mute":         (_each(video.mute, paths), "Removing sound…"),
                "Rotate video": (_each(video.rotate, paths), "Rotating…"),
                "Speed 2×":     (_each(video.speed, paths, factor=2.0), "Speeding up…"),
                "Frame":        (_each(video.frame, paths), "Saving frame…"),
            }
            fn, verb = jobs[name]
            app.submit(item.key, paths, fn, verb)
    except UserError as e:
        app.toasts.error(f"Couldn't open {name}", str(e))
    except Exception as e:  # never take the tray app down
        import traceback
        traceback.print_exc()
        app.toasts.error(f"Couldn't open {name}", f"Unexpected error: {e}")


# --------------------------------------------------------------------------- widgets

def _fmt(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s = rem / 1000
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


class FrameView(QWidget):
    """Paints the current video frame, letterboxed, on a dark rounded well."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._img: QImage | None = None
        self.setMinimumHeight(250)

    def set_image(self, img: QImage) -> None:
        self._img = img
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        well = QPainterPath()
        well.addRoundedRect(r, 12, 12)
        p.fillPath(well, QColor(0, 0, 0, 120))
        if self._img is not None and not self._img.isNull():
            p.setClipPath(well)
            s = self._img.size().scaled(self.rect().size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(QPoint((self.width() - s.width()) // 2, (self.height() - s.height()) // 2), s)
            p.drawImage(target, self._img)


class RangeBar(QWidget):
    """Timeline: drag the two handles to choose the part to keep; click anywhere to seek."""

    rangeChanged = Signal(int, int)     # start_ms, end_ms
    seekRequested = Signal(int)         # ms

    PAD, GRAB, MIN_MS = 12, 10, 100

    def __init__(self, duration_ms: int, parent=None):
        super().__init__(parent)
        self.duration = max(1, duration_ms)
        self.a, self.b, self.head = 0, self.duration, 0
        self._drag: str | None = None
        self.setMinimumHeight(54)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # geometry
    def _x(self, ms: int) -> float:
        w = max(1, self.width() - 2 * self.PAD)
        return self.PAD + w * ms / self.duration

    def _ms(self, x: float) -> int:
        w = max(1, self.width() - 2 * self.PAD)
        return int(min(max((x - self.PAD) / w, 0.0), 1.0) * self.duration)

    def set_range(self, a: int, b: int) -> None:
        self.a, self.b = a, b
        self.update()

    def set_head(self, ms: int) -> None:
        self.head = ms
        self.update()

    # painting
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy, h = self.height() / 2, 14
        track = QRectF(self.PAD, cy - h / 2, self.width() - 2 * self.PAD, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 38))
        p.drawRoundedRect(track, 7, 7)
        xa, xb = self._x(self.a), self._x(self.b)
        grad = QLinearGradient(xa, 0, xb, 0)
        grad.setColorAt(0, QColor("#6bd5ff"))
        grad.setColorAt(1, QColor("#8a7bff"))
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(xa, cy - h / 2, max(2.0, xb - xa), h), 7, 7)
        p.setBrush(QColor(255, 255, 255, 240))
        for x in (xa, xb):
            p.drawRoundedRect(QRectF(x - 5, cy - 14, 10, 28), 4, 4)
        hx = self._x(self.head)
        p.setPen(QPen(QColor("#ffe66b"), 2))
        p.drawLine(QPointF(hx, cy - 19), QPointF(hx, cy + 19))

    # interaction
    def mousePressEvent(self, e):
        x = e.position().x()
        if abs(x - self._x(self.a)) <= self.GRAB:
            self._drag = "a"
        elif abs(x - self._x(self.b)) <= self.GRAB:
            self._drag = "b"
        else:
            self._drag = "head"
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        ms = self._ms(e.position().x())
        if self._drag == "a":
            self.a = min(ms, self.b - self.MIN_MS)
            self.a = max(0, self.a)
            self.rangeChanged.emit(self.a, self.b)
            ms = self.a
        elif self._drag == "b":
            self.b = max(ms, self.a + self.MIN_MS)
            self.b = min(self.duration, self.b)
            self.rangeChanged.emit(self.a, self.b)
            ms = self.b
        self.head = ms
        self.seekRequested.emit(ms)
        self.update()

    def mouseReleaseEvent(self, _e):
        self._drag = None


# --------------------------------------------------------------------------- Trim window

class TrimWindow(ToolWindow):
    tool_name = "Trim"

    def __init__(self, path, notify, info: "video.Info | None" = None):
        super().__init__("Trim video", path, notify, size=(780, 640))
        self.path = Path(path)
        info = info or video.probe(self.path)
        self.dur = max(1, int(info.duration * 1000))
        self.a, self.b, self.cur = 0, self.dur, 0
        self.player = None
        self._grab_only = True

        # picture
        self.view = FrameView()
        self.body.addWidget(self.view, 1)

        # transport
        self.play = QPushButton("▶")
        self.play.setFixedWidth(48)
        self.play.clicked.connect(self._toggle)
        self.clock = QLabel()
        self.clock.setObjectName("dim")
        row = QHBoxLayout()
        row.addWidget(self.play)
        row.addWidget(self.clock, 1)
        self.body.addLayout(row)

        # timeline
        self.bar = RangeBar(self.dur)
        self.bar.rangeChanged.connect(self._on_range)
        self.bar.seekRequested.connect(self._seek)
        self.body.addWidget(self.bar)

        # start / end at playhead + frame grab
        b_start, b_end, b_frame = QPushButton("[  Start here"), QPushButton("End here  ]"), QPushButton("Save frame")
        b_start.clicked.connect(lambda: self._set_edge("a"))
        b_end.clicked.connect(lambda: self._set_edge("b"))
        b_frame.setToolTip("Save the current frame as a PNG you can edit with the photo tools")
        b_frame.clicked.connect(self._save_frame)
        self.range_label = QLabel()
        self.range_label.setObjectName("dim")
        edges = QHBoxLayout()
        for w in (b_start, b_end, b_frame):
            edges.addWidget(w)
        edges.addStretch(1)
        edges.addWidget(self.range_label)
        self.body.addLayout(edges)

        # precise vs fast
        mode_row, self.mode = toggle_row(["Precise (re-encode)", "Fast (no re-encode)"], 0)
        self.body.addLayout(mode_row)
        self.msg = QLabel("Precise cuts exactly where you chose. Fast is instant and lossless, "
                          "but may start a little earlier (at the previous keyframe).")
        self.msg.setObjectName("dim")
        self.msg.setWordWrap(True)
        self.body.addWidget(self.msg)

        self.body.addLayout(self.footer("Trim", self.save, self.reset))

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._toggle)

        self._grab = QTimer(self)
        self._grab.setSingleShot(True)
        self._grab.setInterval(80)
        self._grab.timeout.connect(self._grab_now)

        self._setup_player()
        self._on_range(self.a, self.b)
        self._seek(0)

    # ---- player -------------------------------------------------------
    def _setup_player(self) -> None:
        if not HAVE_QTMM:
            self.play.setEnabled(False)
            self.play.setToolTip("Playback isn't available in this build; drag the timeline to preview")
            return
        try:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.sink = QVideoSink(self)
            self.sink.videoFrameChanged.connect(self._on_frame)
            self.player.setVideoSink(self.sink)
            self.player.positionChanged.connect(self._on_position)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.setSource(QUrl.fromLocalFile(str(self.path)))
            self.player.pause()                       # prerolls the first frame
            self._grab_only = False
        except Exception:
            self._fallback()

    def _fallback(self, *_args) -> None:
        """No playback (codec/backend problem): keep trimming with ffmpeg still frames."""
        self._grab_only = True
        self.play.setEnabled(False)
        self.play.setText("▶")
        self.play.setToolTip("This video can't be played here; drag the timeline to preview")
        self._grab.start()

    def _on_player_error(self, *_args) -> None:
        self._fallback()

    def _on_frame(self, frame) -> None:
        img = frame.toImage()
        if not img.isNull():
            self.view.set_image(img)

    def _on_position(self, ms: int) -> None:
        self.cur = ms
        self.bar.set_head(ms)
        self._update_clock()
        if self.player is not None and self.player.isPlaying() and ms >= self.b:
            self.player.pause()
            self.player.setPosition(self.b)
            self.play.setText("▶")

    def _toggle(self) -> None:
        if self._grab_only or self.player is None:
            return
        if self.player.isPlaying():
            self.player.pause()
            self.play.setText("▶")
            return
        if self.cur < self.a or self.cur >= self.b - 30:
            self.player.setPosition(self.a)
        self.player.play()
        self.play.setText("⏸")

    # ---- seeking / range ----------------------------------------------
    def _seek(self, ms: int) -> None:
        self.cur = max(0, min(ms, self.dur))
        self.bar.set_head(self.cur)
        self._update_clock()
        if self._grab_only:
            self._grab.start()
        else:
            self.player.setPosition(self.cur)

    def _grab_now(self) -> None:
        try:
            data = video.frame_png_bytes(self.path, self.cur / 1000)
            img = QImage.fromData(data)
            if not img.isNull():
                self.view.set_image(img)
        except UserError:
            pass                                      # keep the previous frame

    def _on_range(self, a: int, b: int) -> None:
        self.a, self.b = a, b
        self.range_label.setText(f"{_fmt(a)} → {_fmt(b)}   ·   {_fmt(b - a)} long")

    def _set_edge(self, which: str) -> None:
        if which == "a":
            self.a = min(self.cur, self.b - RangeBar.MIN_MS)
        else:
            self.b = max(self.cur, self.a + RangeBar.MIN_MS)
        self.bar.set_range(self.a, self.b)
        self._on_range(self.a, self.b)

    def _update_clock(self) -> None:
        self.clock.setText(f"{_fmt(self.cur)} / {_fmt(self.dur)}")

    # ---- actions ------------------------------------------------------
    def reset(self) -> None:
        self.a, self.b = 0, self.dur
        self.bar.set_range(self.a, self.b)
        self._on_range(self.a, self.b)
        self._seek(0)

    def _save_frame(self) -> None:
        try:
            outs = video.frame(self.path, self.cur / 1000)
        except UserError as e:
            self.msg.setText(str(e))
            return
        self.notify("tool:Frame", [str(self.path)], [str(o) for o in outs], "")

    def save(self) -> None:
        if self.player is not None:
            self.player.pause()
        path = self.path                                # plain values only: this runs off the UI thread
        start = self.a / 1000
        end = None if self.b >= self.dur - 50 else self.b / 1000
        precise = self.mode.checkedId() == 0
        label = f"{_fmt(self.a)}–{_fmt(self.b)}"
        self.run(lambda: video.trim(path, start, end, precise=precise), "Trimming…", label)

    def closeEvent(self, e) -> None:
        if self.player is not None:                     # release the file handle (Windows locks it)
            self.player.stop()
            self.player.setSource(QUrl())
        super().closeEvent(e)
