"""The menu must appear for real file drags only, not for every "left button + Shift" gesture.

These tests drive the real DragShiftWatcher._tick with a scripted mouse and keyboard. The cursor,
the probe window and the "drag picture" look-up are replaced by simple fakes, so no display is needed.
"""
import contextlib

from dragshift import dragprobe, trigger
from dragshift.dragprobe import FileDragGate


class Pt:
    """Just enough of QPoint for the watcher."""
    def __init__(self, x=0, y=0):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y

    def __sub__(self, o):
        return Pt(self._x - o._x, self._y - o._y)

    def manhattanLength(self):
        return abs(self._x) + abs(self._y)


class FakeProbe:
    def __init__(self):
        self.visible, self.armed_at, self.follows = False, None, 0

    def arm(self, pos):
        self.visible, self.armed_at = True, (pos.x(), pos.y())

    def follow(self, pos):
        if self.visible:
            self.follows += 1

    def disarm(self):
        self.visible = False


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Rig:
    """A watcher wired to fakes, plus a way to play 'frames' of mouse/keyboard state."""

    def __init__(self, gate=True):
        self.shown, self.image, self.clock = [], False, Clock()
        self.cursor = Pt(100, 100)
        self.saved = {n: getattr(trigger, n) for n in ("QCursor", "QPoint", "drag_image_near", "gate_enabled")}
        rig = self

        class FakeCursor:
            @staticmethod
            def pos():
                return rig.cursor

        trigger.QCursor, trigger.QPoint = FakeCursor, Pt
        trigger.drag_image_near = lambda x, y: rig.image
        trigger.gate_enabled = lambda: gate
        self.w = trigger.DragShiftWatcher(lambda: False, lambda: False)
        self.probe = FakeProbe()
        self.w._probe = self.probe
        self.w._gate = FileDragGate(clock=self.clock)
        self.w._emit_show = self.shown.append
        self.w._defer = lambda fn: fn()          # run deferred work at once

    def close(self):
        for n, v in self.saved.items():
            setattr(trigger, n, v)

    def frame(self, lbtn=True, shift=True, to=None, dt=0.015):
        if to is not None:
            self.cursor = Pt(*to)
        self.w._state = lambda: (lbtn, shift, False)
        self.clock.t += dt
        self.w._tick()

    def start_drag(self, x=100, y=100):
        """Press the button, move 40 px (past the 10 px threshold), Shift already held."""
        self.frame(True, False, (x, y))
        self.frame(True, False, (x + 40, y))
        self.frame(True, True, (x + 40, y))


@contextlib.contextmanager
def rig(gate=True):
    r = Rig(gate)
    try:
        yield r
    finally:
        r.close()


# ---------------------------------------------------------------- the gate's own logic

def test_gate_confirmed_by_files_once():
    g = FileDragGate()
    assert not g.files_seen()                  # idle: ignored
    g.arm()
    assert g.files_seen() and not g.files_seen()


def test_gate_image_must_persist():
    c = Clock()
    g = FileDragGate(clock=c)
    g.arm()
    assert not g.tick(True)                    # first sighting
    c.t = 0.1
    assert not g.tick(True)                    # not long enough
    c.t = 0.1
    assert not g.tick(False)                   # picture vanished: reset
    c.t = 0.2
    assert not g.tick(True)
    c.t = 0.2 + dragprobe.IMAGE_CONFIRM_S + 0.01
    assert g.tick(True) and g.state == g.CONFIRMED


def test_gate_disarm_resets_everything():
    g = FileDragGate()
    g.arm()
    g.disarm()
    assert not g.armed and not g.files_seen() and not g.tick(True)


# ---------------------------------------------------------------- the watcher

def test_selection_or_window_move_never_shows_the_menu():
    with rig() as r:
        r.start_drag()
        for i in range(300):                   # a long Shift+drag that delivers no file data
            r.frame(True, True, (140 + i, 100 + i // 2))
        assert r.shown == []
        assert r.probe.visible and r.probe.follows > 100          # it kept following the cursor
        r.frame(False, True)                                      # button released
        assert r.shown == [] and not r.probe.visible


def test_real_file_drag_shows_the_menu_once_at_the_cursor():
    with rig() as r:
        r.start_drag()
        r.frame(True, True, (200, 150))
        r.w._on_probe_files(["C:/photo.jpg"])                     # the probe received local files
        assert len(r.shown) == 1 and (r.shown[0].x(), r.shown[0].y()) == (200, 150)
        assert not r.probe.visible                                 # the probe steps aside for the menu
        for i in range(50):
            r.frame(True, True, (200 + i, 150))
        assert len(r.shown) == 1                                   # no second pop-up during the same drag


def test_drag_picture_is_the_fallback_when_the_probe_hears_nothing():
    with rig() as r:
        r.start_drag()
        r.image = True
        for _ in range(60):                                        # about 0.9 s of ticks
            r.frame(True, True, (150, 120))
        assert len(r.shown) == 1


def test_short_lived_drag_picture_does_not_count():
    with rig() as r:
        r.start_drag()
        for i in range(80):
            r.image = (i % 12 == 0)                                # flickers, never persists
            r.frame(True, True, (150, 120))
        assert r.shown == []


def test_late_probe_event_after_release_is_ignored():
    with rig() as r:
        r.start_drag()
        r.frame(False, True)                                       # dropped somewhere else
        r.w._on_probe_files(["C:/photo.jpg"])
        assert r.shown == []


def test_deferred_confirmation_is_cancelled_if_the_button_is_released_first():
    with rig() as r:
        pending = []
        r.w._defer = pending.append                                # run later, like QTimer.singleShot(0)
        r.start_drag()
        r.w._on_probe_files(["C:/photo.jpg"])
        r.frame(False, True)                                       # released before the deferred call runs
        for fn in pending:
            fn()
        assert r.shown == []


def test_no_probe_without_shift_or_without_movement():
    with rig() as r:
        r.frame(True, True, (100, 100))
        r.frame(True, True, (103, 101))                            # 5 px: below the threshold
        assert not r.probe.visible and r.shown == []
        r.frame(True, False, (300, 300))                           # moved, but no Shift
        assert not r.probe.visible and r.shown == []


def test_letting_go_of_shift_stops_waiting_and_pressing_it_again_rearms():
    with rig() as r:
        r.start_drag()
        assert r.probe.visible
        r.frame(True, False, (180, 100))
        assert not r.probe.visible                                 # Shift released
        r.frame(True, True, (190, 100))
        assert r.probe.visible                                     # pressed again: waiting again
        r.w._on_probe_files(["C:/photo.jpg"])
        assert len(r.shown) == 1


def test_a_new_drag_after_a_finished_one_works():
    with rig() as r:
        r.start_drag()
        r.w._on_probe_files(["C:/a.jpg"])
        r.frame(False, True)
        r.start_drag(300, 300)
        r.w._on_probe_files(["C:/b.jpg"])
        assert len(r.shown) == 2


def test_disabled_gate_keeps_the_old_behaviour():
    with rig(gate=False) as r:
        r.start_drag()
        assert len(r.shown) == 1 and not r.probe.visible           # shown at once, no probe involved
