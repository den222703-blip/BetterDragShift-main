"""Tells a real file drag from every other "button held + Shift" gesture.

Holding the left button and Shift happens all the time in other programs: extending a text
selection, moving or resizing a window, rubber-band selecting. The menu must appear only when a
file is actually being dragged. The watcher (trigger.py) therefore does not show the menu at once.
It "arms" a gate and waits for proof:

1. DragProbe: a small, almost invisible window that follows the cursor and is a drop target. A real
   drag (Explorer, the desktop, a browser, a chat app, ...) delivers its data to the window under the
   cursor; if that data is local files, the probe says so. Selections and window moves never deliver
   drag data, so the probe stays silent and nothing appears.
2. drag_image_near(): fallback. While a drag runs, Windows shows a drag-picture window (class
   "SysDragImage"). If one stays near the cursor for a moment, that is a drag too (covers apps whose
   drag data the probe can't read).

Windows only. macOS and Linux keep the old behaviour. To get the old behaviour back on Windows too,
set the environment variable DRAGSHIFT_ANY_DRAG=1.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

PROBE_SIZE = 160            # px; it is re-centred on the cursor every tick, so it need not be large
IMAGE_CONFIRM_S = 0.25      # a drag picture must stay near the cursor this long
IMAGE_CLASSES = ("SysDragImage", "DragImageWindow", "DragWindow")


def gate_enabled() -> bool:
    """True when the menu must wait for a real file drag (Windows, unless switched off)."""
    if sys.platform != "win32":
        return False
    return os.environ.get("DRAGSHIFT_ANY_DRAG", "").strip().lower() not in ("1", "true", "yes", "on")


class FileDragGate:
    """The decision logic, free of any window or Windows API so it can be tested on its own."""

    IDLE, ARMED, CONFIRMED = "idle", "armed", "confirmed"

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.state = self.IDLE
        self._image_since: float | None = None

    @property
    def armed(self) -> bool:
        return self.state == self.ARMED

    def arm(self) -> None:
        self.state = self.ARMED
        self._image_since = None

    def disarm(self) -> None:
        self.state = self.IDLE
        self._image_since = None

    def files_seen(self) -> bool:
        """The probe received local files. True the first time while armed."""
        if self.state != self.ARMED:
            return False
        self.state = self.CONFIRMED
        return True

    def tick(self, image_present: bool) -> bool:
        """Call periodically with 'is a drag picture near the cursor'. True when that has held long enough."""
        if self.state != self.ARMED:
            return False
        if not image_present:
            self._image_since = None
            return False
        now = self._clock()
        if self._image_since is None:
            self._image_since = now
            return False
        if now - self._image_since >= IMAGE_CONFIRM_S:
            self.state = self.CONFIRMED
            return True
        return False


def drag_image_near(x: int, y: int, margin: int = 40) -> bool:
    """Is a visible drag-picture window (of another process) within `margin` px of (x, y)? Windows only."""
    if sys.platform != "win32":
        return False
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        mypid = os.getpid()
        hit: list[int] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(hwnd, _):
            h = ctypes.c_void_p(hwnd)
            if not user32.IsWindowVisible(h):
                return True
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(h, name, 64)
            if name.value not in IMAGE_CLASSES:
                return True
            r = wintypes.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            if not (r.left - margin <= x <= r.right + margin and r.top - margin <= y <= r.bottom + margin):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value == mypid:
                return True
            hit.append(hwnd)
            return False                      # found one: stop enumerating

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return bool(hit)
    except Exception:
        return False


class DragProbe(QWidget):
    """A nearly invisible drop target that follows the cursor while the gate is armed."""

    file_drag = Signal(list)      # local file paths that entered the probe

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint |
                            Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.resize(PROBE_SIZE, PROBE_SIZE)

    def _centre_on(self, pos) -> None:
        self.move(pos.x() - PROBE_SIZE // 2, pos.y() - PROBE_SIZE // 2)

    def arm(self, pos) -> None:
        self._centre_on(pos)
        self.show()

    def follow(self, pos) -> None:
        if self.isVisible():
            self._centre_on(pos)

    def disarm(self) -> None:
        self.hide()

    def paintEvent(self, _e):
        # alpha 1 of 255: invisible, but not zero. Windows lets the mouse (and drags) pass through
        # pixels whose alpha is 0, and then the window could never be a drop target.
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        p.end()

    def dragEnterEvent(self, e):
        md = e.mimeData()
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile() and u.toLocalFile()] if md.hasUrls() else []
        e.ignore()                            # we only look; the real menu does the dropping
        if paths:
            self.file_drag.emit(paths)
